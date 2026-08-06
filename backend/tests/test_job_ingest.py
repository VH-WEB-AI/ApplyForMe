from app.db.models.jobs import JobPosting
from app.schemas.api import ScrapedJobRow
from app.services.job_ingest import ingest_scraped_rows, row_to_job_posting_kwargs


def _row(**overrides) -> ScrapedJobRow:
    defaults = dict(
        portal="Remote OK",
        job_title="Senior Backend Engineer",
        company="Acme Corp",
        url="https://example.com/jobs/1",
        about_the_job="Own our core Python services.",
        pay="$140,000-$180,000",
        location="Remote",
        summary="",
        requirements="5+ years Python and PostgreSQL experience.",
        responsibilities="Design and ship backend APIs.",
        benefits="",
        scraped_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ScrapedJobRow(**defaults)


def test_row_to_job_posting_kwargs_maps_fields():
    kwargs = row_to_job_posting_kwargs(_row())
    assert kwargs["title"] == "Senior Backend Engineer"
    assert kwargs["company"] == "Acme Corp"
    assert kwargs["remote"] is True
    assert kwargs["seniority"] == "senior"
    assert kwargs["salary_min"] == 140000
    assert kwargs["salary_max"] == 180000
    assert "Python" in kwargs["required_skills"]
    assert "PostgreSQL" in kwargs["required_skills"]


def test_row_to_job_posting_kwargs_skips_missing_title_or_company():
    assert row_to_job_posting_kwargs(_row(job_title="")) is None
    assert row_to_job_posting_kwargs(_row(company="")) is None


def test_ingest_scraped_rows_creates_and_dedupes(db):
    rows = [_row(), _row()]  # duplicate title+company
    created, skipped = ingest_scraped_rows(db, rows)
    assert created == 1
    assert skipped == 1

    stored = db.query(JobPosting).filter_by(title="Senior Backend Engineer", company="Acme Corp").one()
    assert stored.location == "Remote"

    # re-ingesting the same row again should skip, not duplicate
    created_again, skipped_again = ingest_scraped_rows(db, [_row()])
    assert created_again == 0
    assert skipped_again == 1
