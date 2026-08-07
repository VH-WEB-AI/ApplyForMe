"""
Background job: parse an uploaded resume, run it through Engine 1 (Resume
Intelligence) via the orchestrator, embed it, and persist the results.
Keeps the upload endpoint fast (returns immediately after saving the file).
"""
import asyncio
import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import db_session_ctx
from app.models.resume import Resume, ResumeStatus
from app.orchestrator.ai_orchestrator import ai_orchestrator
from app.orchestrator.context_manager import context_manager
from app.shared_services.embedding_service import embedding_service
from app.shared_services.resume_parser import resume_parser
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="process_resume", bind=True, max_retries=3, default_retry_delay=15)
def process_resume_task(self, resume_id: str):
    try:
        asyncio.run(_process_resume(resume_id))
    except Exception as exc:
        logger.error("resume_processing_failed", resume_id=resume_id, error=str(exc))
        raise self.retry(exc=exc)


async def _process_resume(resume_id: str) -> None:
    async with db_session_ctx() as db:
        result = await db.execute(select(Resume).where(Resume.id == uuid.UUID(resume_id)))
        resume = result.scalar_one()

        resume.status = ResumeStatus.PARSING
        await db.flush()

        raw_text = resume_parser.parse(resume.file_path)
        resume.raw_text = raw_text
        resume.status = ResumeStatus.PARSED
        await db.flush()

        # Run Engine 1 through the orchestrator (keeps audit logging consistent)
        orchestrator_response = await ai_orchestrator.dispatch(
            intent="score_resume",
            payload={"resume_text": raw_text},
            db=db,
            user_id=resume.user_id,
            load_context=False,
        )
        analysis = orchestrator_response.result

        resume.ats_score = analysis["ats_score"]
        resume.resume_score = analysis["resume_score"]
        resume.extracted_skills = analysis["extracted_skills"]
        resume.suggestions = analysis["suggestions"]
        resume.structured_data = {
            "work_history": analysis["work_history"],
            "education": analysis["education"],
        }
        resume.status = ResumeStatus.SCORED

        # Embed for semantic job matching / RAG
        try:
            resume.embedding = await embedding_service.get_embedding(raw_text)
        except Exception as exc:
            logger.warning("resume_embedding_skipped", resume_id=resume_id, error=str(exc))
            resume.embedding = None

        await context_manager.invalidate(resume.user_id)
        await db.flush()
