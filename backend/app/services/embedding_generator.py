"""Embedding Generator: creates and stores embeddings, and runs semantic similarity search."""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.embeddings import EmbeddingRecord
from app.services import llm_gateway


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_or_create_embedding(
    db: Session, *, owner_type: str, owner_id: int, text: str, store_content: bool = False
) -> EmbeddingRecord:
    """Returns a cached embedding for (owner_type, owner_id) if the content hasn't
    changed, otherwise generates a fresh one via the LLM gateway and stores it.

    `store_content` persists `text` itself on the row (used for RAG chunks, where the
    original passage needs to be retrievable) -- whole-document embeddings (resume/job)
    leave it unset since the full text already lives on the owning row."""
    text_hash = content_hash(text)

    existing = db.scalar(
        select(EmbeddingRecord).where(
            EmbeddingRecord.owner_type == owner_type,
            EmbeddingRecord.owner_id == owner_id,
        )
    )
    if existing and existing.content_hash == text_hash:
        return existing

    result = llm_gateway.create_embedding(text)

    if existing:
        existing.content_hash = text_hash
        existing.embedding = result.vector
        if store_content:
            existing.content = text
        db.flush()
        return existing

    record = EmbeddingRecord(
        owner_type=owner_type,
        owner_id=owner_id,
        content_hash=text_hash,
        content=text if store_content else "",
        embedding=result.vector,
    )
    db.add(record)
    db.flush()
    return record


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def semantic_similarity(db: Session, text_a: str, text_b: str) -> float:
    """One-off similarity between two texts, not persisted (used ad-hoc, e.g. resume-vs-JD)."""
    vec_a = llm_gateway.create_embedding(text_a).vector
    vec_b = llm_gateway.create_embedding(text_b).vector
    return cosine_similarity(vec_a, vec_b)


def top_k_similar(
    db: Session, *, owner_type: str, owner_id_range: tuple[int, int], query_text: str, k: int = 3
) -> list[EmbeddingRecord]:
    """pgvector nearest-neighbor search (RAG retrieval) over embeddings whose owner_id
    falls in `owner_id_range` (inclusive), ordered by cosine distance to `query_text`."""
    query_vector = llm_gateway.create_embedding(query_text).vector
    low, high = owner_id_range
    return list(
        db.scalars(
            select(EmbeddingRecord)
            .where(
                EmbeddingRecord.owner_type == owner_type,
                EmbeddingRecord.owner_id >= low,
                EmbeddingRecord.owner_id <= high,
            )
            .order_by(EmbeddingRecord.embedding.cosine_distance(query_vector))
            .limit(k)
        ).all()
    )
