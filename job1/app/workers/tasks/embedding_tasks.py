"""
Background job: bulk-embed newly ingested job descriptions so semantic
matching (Engine 2) has vectors ready ahead of a user's match request.
"""
import asyncio
import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import db_session_ctx
from app.models.resume import JobDescription
from app.shared_services.embedding_service import embedding_service
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="embed_job_description", bind=True, max_retries=3, default_retry_delay=15)
def embed_job_description_task(self, job_id: str):
    try:
        asyncio.run(_embed_job(job_id))
    except Exception as exc:
        logger.error("job_embedding_failed", job_id=job_id, error=str(exc))
        raise self.retry(exc=exc)


async def _embed_job(job_id: str) -> None:
    async with db_session_ctx() as db:
        result = await db.execute(select(JobDescription).where(JobDescription.id == uuid.UUID(job_id)))
        job = result.scalar_one()
        job.embedding = await embedding_service.get_embedding(f"{job.title}\n{job.description}")
        await db.flush()
