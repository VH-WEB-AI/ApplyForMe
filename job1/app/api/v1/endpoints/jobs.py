import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.db.session import get_db
from app.models.resume import Resume
from app.models.user import User
from app.orchestrator.ai_orchestrator import ai_orchestrator
from app.schemas.job import JobMatchRequest, JobMatchResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/match", response_model=JobMatchResponse)
async def match_job(
    payload: JobMatchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    candidate_text = ""
    if payload.resume_id:
        result = await db.execute(
            select(Resume).where(Resume.id == payload.resume_id, Resume.user_id == user.id)
        )
        resume = result.scalar_one_or_none()
        if not resume:
            raise NotFoundError("Resume not found")
        candidate_text = resume.raw_text or ""

    if not candidate_text:
        raise ValidationFailedError("A resume with parsed text is required to compute a match")

    response = await ai_orchestrator.dispatch(
        intent="match_jobs",
        payload={
            "candidate_text": candidate_text,
            "job_description": payload.job_description,
            "job_metadata": payload.job_metadata,
            "candidate_preferences": {},
        },
        db=db,
        user_id=user.id,
    )
    return response.result
