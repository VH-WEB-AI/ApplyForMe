"""Maps scraped job rows (see scraper/job_scraper.py) onto JobPosting rows."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.jobs import JobPosting
from app.schemas.api import ScrapedJobRow
from app.services.skill_extractor import extract_skills

_PAY_NUMBER_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_SENIOR_RE = re.compile(r"\b(senior|sr\.?|staff|principal|lead)\b", re.IGNORECASE)
_JUNIOR_RE = re.compile(r"\b(junior|jr\.?|entry[- ]level|intern(?:ship)?)\b", re.IGNORECASE)


def _parse_pay(pay: str) -> tuple[int | None, int | None]:
    """Best-effort parse of free-text pay strings like "$110,000-$140,000" or "$65/hr"."""
    if not pay:
        return None, None
    numbers = [float(n.replace(",", "")) for n in _PAY_NUMBER_RE.findall(pay)]
    if not numbers:
        return None, None
    if "k" in pay.lower():
        numbers = [n * 1000 for n in numbers]
    values = [int(n) for n in numbers]
    return min(values), max(values)


def _infer_seniority(title: str) -> str | None:
    if _SENIOR_RE.search(title):
        return "senior"
    if _JUNIOR_RE.search(title):
        return "junior"
    return None


def _build_description(row: ScrapedJobRow) -> str:
    parts = [row.about_the_job or row.summary]
    if row.responsibilities:
        parts.append("Responsibilities:\n" + row.responsibilities)
    if row.requirements:
        parts.append("Requirements:\n" + row.requirements)
    if row.benefits:
        parts.append("Benefits:\n" + row.benefits)
    return "\n\n".join(part for part in parts if part).strip()


def row_to_job_posting_kwargs(row: ScrapedJobRow) -> dict | None:
    title = row.job_title.strip()
    company = row.company.strip()
    if not title or not company:
        return None

    description = _build_description(row)
    salary_min, salary_max = _parse_pay(row.pay)

    return dict(
        title=title,
        company=company,
        description=description or title,
        location=row.location or None,
        remote="remote" in row.location.lower(),
        seniority=_infer_seniority(title),
        salary_min=salary_min,
        salary_max=salary_max,
        visa_sponsorship=False,
        required_skills=extract_skills(description),
        min_experience_years=None,
        is_active=True,
    )


def ingest_scraped_rows(db: Session, rows: list[ScrapedJobRow]) -> tuple[int, int]:
    """Creates a JobPosting for each row not already present (matched on title+company).

    Returns (created, skipped).
    """
    existing = {(jp.title, jp.company) for jp in db.scalars(select(JobPosting)).all()}
    created = 0
    skipped = 0
    for row in rows:
        kwargs = row_to_job_posting_kwargs(row)
        if kwargs is None:
            skipped += 1
            continue
        key = (kwargs["title"], kwargs["company"])
        if key in existing:
            skipped += 1
            continue
        db.add(JobPosting(**kwargs))
        existing.add(key)
        created += 1
    db.commit()
    return created, skipped
