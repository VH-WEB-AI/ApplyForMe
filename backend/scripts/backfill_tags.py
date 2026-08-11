"""One-time backfill: extract tags for every existing ResumeVersion and
JobPosting row that doesn't have any yet (creation-time extraction only
covers rows created after that code shipped).

Safe to re-run -- only touches rows where tags is still empty, so it never
overwrites tags a later run already computed.

Usage (inside the backend container or the local venv, from the backend/
directory -- needs -m so `app` resolves on the path):
    python -m scripts.backfill_tags
"""

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.jobs import JobPosting
from app.db.models.resume import ResumeVersion
from app.services.tag_extractor import extract_tags


def _resume_tag_source(resume_version: ResumeVersion) -> str:
    return "\n".join(text for name, text in resume_version.sections.items() if name != "header")


def backfill() -> None:
    db = SessionLocal()
    try:
        resumes = db.scalars(select(ResumeVersion).where(ResumeVersion.tags == [])).all()
        for resume_version in resumes:
            resume_version.tags = extract_tags(_resume_tag_source(resume_version))
        db.commit()
        print(f"Tagged {len(resumes)} resume version(s).")

        jobs = db.scalars(select(JobPosting).where(JobPosting.tags == [])).all()
        for job in jobs:
            job.tags = extract_tags(job.description)
        db.commit()
        print(f"Tagged {len(jobs)} job posting(s).")
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
