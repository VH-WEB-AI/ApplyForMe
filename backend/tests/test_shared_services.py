from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from app.services.document_chunking import chunk_text
from app.services.experience_estimator import estimate_total_experience_years
from app.services.json_formatter import parse_llm_json
from app.services.pii_redaction import redact_pii
from app.services.resume_parser import extract_text, identify_sections
from app.services.response_validator import ResponseValidationError, validate_with_retry
from app.services.skill_extractor import extract_skills, normalize_skill, skill_gap
from app.services.tag_extractor import extract_tags, tag_overlap_score


def test_extract_text_strips_nul_bytes_from_pdf():
    # pdfplumber/pdfminer occasionally return NUL chars for PDFs with malformed
    # embedded font glyph maps; Postgres text/jsonb columns reject them outright,
    # so extract_text must sanitize before this reaches the database.
    page = MagicMock()
    page.extract_text.return_value = "Jane Doe\x00\nExperience\x00 at Acme Corp"
    pdf_context = MagicMock()
    pdf_context.pages = [page]
    pdf_context.__enter__.return_value = pdf_context
    pdf_context.__exit__.return_value = False

    with patch("app.services.resume_parser.pdfplumber.open", return_value=pdf_context):
        text = extract_text(b"fake-pdf-bytes", "resume.pdf")

    assert "\x00" not in text
    assert text == "Jane Doe\nExperience at Acme Corp"


def test_extract_skills_finds_canonical_names():
    text = "Built services using Python programming, FastAPI, and postgres. Deployed with k8s."
    skills = extract_skills(text)
    assert "Python" in skills
    assert "FastAPI" in skills
    assert "PostgreSQL" in skills
    assert "Kubernetes" in skills


def test_extract_skills_no_false_positive_substring():
    # "go" as a common word shouldn't match Go the language; taxonomy alias is "golang"/"go lang"
    text = "I will go to the store"
    assert "Go" not in extract_skills(text)


def test_normalize_skill():
    assert normalize_skill("Python Programming") == "Python"
    assert normalize_skill("unknown-thing-xyz") is None


def test_skill_gap():
    missing = skill_gap(candidate_skills=["Python", "AWS"], required_skills=["Python", "Docker", "AWS"])
    assert missing == ["Docker"]


def test_identify_sections_splits_headers():
    resume_text = (
        "Jane Doe\n"
        "Summary\n"
        "Experienced engineer.\n"
        "Experience\n"
        "Built things at Acme.\n"
        "Education\n"
        "BS Computer Science\n"
        "Skills\n"
        "Python, SQL\n"
    )
    sections = identify_sections(resume_text)
    assert "summary" in sections
    assert "experience" in sections
    assert "education" in sections
    assert "skills" in sections
    assert "Acme" in sections["experience"]


def test_extract_tags_returns_relevant_keyphrases():
    text = "Senior Backend Engineer with deep PostgreSQL and Kubernetes experience."
    tags = extract_tags(text)
    assert tags  # non-empty for real content
    assert all(tag == tag.lower() for tag in tags)  # normalized for case-insensitive overlap


def test_extract_tags_empty_for_blank_input():
    assert extract_tags("") == []
    assert extract_tags("   ") == []


def test_tag_overlap_score():
    resume_tags = ["python", "postgresql", "docker", "fastapi"]
    job_tags = ["python", "postgresql", "aws"]
    # 2 of the job's 3 tags (python, postgresql) are in the resume's tags
    assert tag_overlap_score(resume_tags, job_tags) == pytest.approx(2 / 3)


def test_tag_overlap_score_empty_job_tags():
    assert tag_overlap_score(["python"], []) == 0.0


def test_estimate_experience_ignores_unrelated_numbers_outside_date_ranges():
    # Regression test: a phone-number-like fragment ("20064...") must never be
    # misread as the year 2006 just because it contains that substring.
    text = "Senior Engineer at Acme, 2020 - 2023\nContact: 20064155512\nCert year: 1999"
    assert estimate_total_experience_years(text) == 3.0


def test_estimate_experience_present_uses_current_year():
    current_year = datetime.now(timezone.utc).year
    text = "Backend Engineer at Acme, 2020 - Present"
    assert estimate_total_experience_years(text) == float(current_year - 2020)


def test_estimate_experience_spans_multiple_ranges():
    text = "Engineer at A, 2015 - 2018\nSenior Engineer at B, 2019 - 2022"
    assert estimate_total_experience_years(text) == 7.0


def test_estimate_experience_no_dates_returns_zero():
    assert estimate_total_experience_years("Worked on various projects.") == 0.0


def test_estimate_experience_empty_text_returns_zero():
    assert estimate_total_experience_years("") == 0.0


def test_estimate_experience_word_separator():
    text = "Engineer at Acme, 2018 to 2021"
    assert estimate_total_experience_years(text) == 3.0


def test_chunk_text_respects_overlap():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 100 for c in chunks)


def test_redact_pii_removes_email_and_phone():
    text = "Contact me at jane.doe@example.com or 555-123-4567."
    redacted = redact_pii(text)
    assert "jane.doe@example.com" not in redacted
    assert "555-123-4567" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted


def test_parse_llm_json_strips_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert parse_llm_json(raw) == {"a": 1}


class _Schema(BaseModel):
    score: int


def test_validate_with_retry_succeeds_first_try():
    model, retries = validate_with_retry(_Schema, lambda feedback: '{"score": 10}')
    assert model.score == 10
    assert retries == 0


def test_validate_with_retry_recovers_after_bad_json():
    calls = {"n": 0}

    def generate(feedback):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json"
        return '{"score": 5}'

    model, retries = validate_with_retry(_Schema, generate, max_retries=2)
    assert model.score == 5
    assert retries == 1


def test_validate_with_retry_raises_after_exhausting_retries():
    with pytest.raises(ResponseValidationError):
        validate_with_retry(_Schema, lambda feedback: "never valid", max_retries=1)
