"""Convenience layer over the Job Match Engine: matches a candidate against every
active job posting already in the system (there is no admin authoring flow in
Phase 1 -- job postings are assumed to already exist)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.jobs import JobPosting
from app.orchestrator.orchestrator import orchestrator


def match_candidate_to_active_jobs(db: Session, candidate_id: int) -> list[dict]:
    active_job_ids = db.scalars(
        select(JobPosting.id).where(JobPosting.is_active.is_(True))
    ).all()

    matches = [
        orchestrator.handle_request(
            "job_match", db, {"candidate_id": candidate_id, "job_posting_id": job_id}
        )
        for job_id in active_job_ids
    ]
    return sorted(matches, key=lambda m: m["matchScore"], reverse=True)
