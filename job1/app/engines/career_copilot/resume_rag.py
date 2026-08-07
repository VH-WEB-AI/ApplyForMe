"""
Resume retrieval for the Career Copilot.

The current schema stores one embedding per uploaded resume. This service uses
that vector to find the user's most relevant resumes, then extracts compact
text chunks from those resumes for the copilot prompt.
"""
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import Resume, ResumeStatus
from app.shared_services.embedding_service import embedding_service


class ResumeRAG:
    BROAD_REVIEW_TERMS = {
        "area",
        "areas",
        "gap",
        "gaps",
        "improve",
        "improvement",
        "missing",
        "weak",
        "weakness",
        "weaknesses",
    }
    RESUME_QUERY_TERMS = {
        "ats",
        "bullet",
        "bullets",
        "cv",
        "experience",
        "resume",
        "score",
        "skills",
    } | BROAD_REVIEW_TERMS

    async def retrieve_resume_chunks(
        self, db: AsyncSession, user_id: uuid.UUID, query: str, top_k: int = 5
    ) -> list[dict[str, Any]]:
        if self.is_resume_query(query):
            latest_resume = await self._latest_uploaded_resume(db, user_id)
            return self._chunks_for_resume(latest_resume, query, top_k=top_k)

        query_embedding = await embedding_service.get_embedding(query)
        resumes = await self._retrieve_ranked_resumes(db, user_id, query, query_embedding, top_k)

        return self._chunks_for_resumes(resumes, query, top_k)

    async def _retrieve_ranked_resumes(
        self, db: AsyncSession, user_id: uuid.UUID, query: str, query_embedding: list[float], top_k: int
    ) -> list[Resume]:
        base_stmt = select(Resume).where(
            Resume.user_id == user_id,
            Resume.raw_text.is_not(None),
            Resume.status == ResumeStatus.SCORED,
        )

        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = (
                base_stmt.where(Resume.embedding.is_not(None))
                .order_by(Resume.embedding.cosine_distance(query_embedding))
                .limit(top_k)
            )
            result = await db.execute(stmt)
            ranked = list(result.scalars().all())
            if ranked:
                return ranked

        result = await db.execute(base_stmt)
        resumes = list(result.scalars().all())
        query_terms = self._query_terms(query)
        resumes.sort(
            key=lambda resume: (
                self._keyword_score(resume.raw_text or "", query_terms)
                + embedding_service.cosine_similarity(query_embedding, resume.embedding or [])
            ),
            reverse=True,
        )
        return resumes[:top_k]

    async def _latest_uploaded_resume(self, db: AsyncSession, user_id: uuid.UUID) -> Resume | None:
        result = await db.execute(
            select(Resume)
            .where(Resume.user_id == user_id)
            .order_by(Resume.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _chunks_for_resumes(self, resumes: list[Resume], query: str, top_k: int) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for resume in resumes:
            chunks.extend(self._chunks_for_resume(resume, query, top_k=top_k - len(chunks)))
            if len(chunks) >= top_k:
                break
        return chunks

    def _chunks_for_resume(self, resume: Resume | None, query: str, top_k: int) -> list[dict[str, Any]]:
        if resume is None:
            return []

        if not resume.raw_text:
            return [
                {
                    "resume_id": str(resume.id),
                    "file_name": resume.file_name,
                    "status": resume.status.value,
                    "text": (
                        "The latest uploaded resume has not been parsed yet. "
                        "Do not use older resumes to answer this resume-specific question."
                    ),
                    "skills": resume.extracted_skills or [],
                    "suggestions": resume.suggestions or [],
                    "ats_score": resume.ats_score,
                    "resume_score": resume.resume_score,
                }
            ]

        chunks: list[dict[str, Any]] = []
        for chunk in self._select_chunks(resume.raw_text, query, max_chunks=top_k):
            chunks.append(
                {
                    "resume_id": str(resume.id),
                    "file_name": resume.file_name,
                    "status": resume.status.value,
                    "text": chunk,
                    "skills": resume.extracted_skills or [],
                    "suggestions": resume.suggestions or [],
                    "ats_score": resume.ats_score,
                    "resume_score": resume.resume_score,
                }
            )
        return chunks

    def is_resume_query(self, query: str) -> bool:
        terms = self._query_terms(query)
        return bool(terms & self.RESUME_QUERY_TERMS)

    def _select_chunks(self, text: str, query: str, max_chunks: int) -> list[str]:
        chunks = self._split_text(text)
        if not chunks:
            return []

        query_terms = self._query_terms(query)
        if query_terms & self.BROAD_REVIEW_TERMS:
            return chunks[:max_chunks]

        if query_terms:
            chunks.sort(key=lambda chunk: self._keyword_score(chunk, query_terms), reverse=True)

        return chunks[:max_chunks]

    def _split_text(self, text: str, chunk_size: int = 1400, overlap: int = 180) -> list[str]:
        normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
        if not normalized:
            return []

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
        chunks: list[str] = []
        current = ""

        for paragraph in paragraphs:
            if len(paragraph) > chunk_size:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._split_long_text(paragraph, chunk_size, overlap))
                continue

            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                chunks.append(current)
                current = paragraph

        if current:
            chunks.append(current)

        return chunks

    def _split_long_text(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end].strip())
            if end == len(text):
                break
            start = max(end - overlap, start + 1)
        return [chunk for chunk in chunks if chunk]

    def _keyword_score(self, chunk: str, query_terms: set[str]) -> int:
        chunk_terms = set(re.findall(r"[a-zA-Z0-9+#.]+", chunk.lower()))
        return len(query_terms & chunk_terms)

    def _query_terms(self, text: str) -> set[str]:
        return {term for term in re.findall(r"[a-zA-Z0-9+#.]+", text.lower()) if len(term) > 2}


resume_rag = ResumeRAG()
