from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models.jobs import JobPosting
from app.engines.job_match import engine as job_match_engine  # noqa: F401 -- registers the engine
from app.engines.job_match.service import match_candidate_to_active_jobs
from app.orchestrator.orchestrator import orchestrator
from app.schemas.api import JobIngestRequest, JobMatchRequest, JobPostingResponse
from app.services.job_ingest import ingest_scraped_rows

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobPostingResponse])
def list_active_jobs(db: Session = Depends(get_db)) -> list[JobPosting]:
    return list(db.scalars(select(JobPosting).where(JobPosting.is_active.is_(True))).all())


@router.post("/ingest")
def ingest_jobs(body: JobIngestRequest, db: Session = Depends(get_db)) -> dict:
    """Receives scraped job rows (scraper/job_scraper.py's post_rows_to_backend payload
    shape: {"source": ..., "jobs": [...]}) and creates a JobPosting for each new one."""
    created, skipped = ingest_scraped_rows(db, body.jobs)
    return {"received": len(body.jobs), "created": created, "skipped": skipped}


@router.post("/match")
def match_single_job(body: JobMatchRequest, db: Session = Depends(get_db)) -> dict:
    return orchestrator.handle_request(
        "job_match", db, {"candidate_id": body.candidate_id, "job_posting_id": body.job_posting_id}
    )


@router.get("/matches/{candidate_id}")
def get_matches_for_candidate(candidate_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Matches the candidate against every active job posting already in the system
    and returns them ranked by match score, highest first."""
    return match_candidate_to_active_jobs(db, candidate_id)
