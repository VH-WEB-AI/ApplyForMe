"""
Context Manager (backs the "CONTEXT SERVICE" box in the architecture
diagram): loads candidate context — profile, resume history, applications,
scores, preferences — so every engine call is personalized without each
engine re-querying the DB independently.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import Application
from app.models.resume import Resume
from app.models.user import CandidateProfile, User
from app.shared_services.cache_service import cache_service


class CandidateContext:
    def __init__(self, profile: dict, resumes: list[dict], applications: list[dict]):
        self.profile = profile
        self.resumes = resumes
        self.applications = applications

    def as_prompt_summary(self) -> str:
        skills = ", ".join(self.profile.get("skills", [])) or "none listed"
        latest_resume = self.resumes[0] if self.resumes else {}
        return (
            f"Headline: {self.profile.get('headline', 'n/a')}\n"
            f"Location: {self.profile.get('location', 'n/a')}\n"
            f"Years experience: {self.profile.get('years_experience', 'n/a')}\n"
            f"Skills: {skills}\n"
            f"Latest resume score: {latest_resume.get('resume_score', 'n/a')}\n"
            f"Applications on file: {len(self.applications)}"
        )


class ContextManager:
    async def load(self, db: AsyncSession, user_id: uuid.UUID) -> CandidateContext:
        cache_key = f"context:{user_id}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            return CandidateContext(**cached)

        profile_result = await db.execute(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        profile_row = profile_result.scalar_one_or_none()
        profile = (
            {
                "headline": profile_row.headline,
                "location": profile_row.location,
                "years_experience": profile_row.years_experience,
                "skills": profile_row.skills,
                "preferences": profile_row.preferences,
            }
            if profile_row
            else {}
        )

        resumes_result = await db.execute(
            select(Resume).where(Resume.user_id == user_id).order_by(Resume.created_at.desc()).limit(5)
        )
        resumes = [
            {
                "id": str(r.id),
                "resume_score": r.resume_score,
                "ats_score": r.ats_score,
                "extracted_skills": r.extracted_skills,
            }
            for r in resumes_result.scalars()
        ]

        apps_result = await db.execute(select(Application).where(Application.user_id == user_id))
        applications = [
            {"id": str(a.id), "status": a.status.value, "match_score": a.match_score}
            for a in apps_result.scalars()
        ]

        payload = {"profile": profile, "resumes": resumes, "applications": applications}
        await cache_service.set_json(cache_key, payload, ttl=300)
        return CandidateContext(**payload)

    async def invalidate(self, user_id: uuid.UUID) -> None:
        await cache_service.delete(f"context:{user_id}")


context_manager = ContextManager()
