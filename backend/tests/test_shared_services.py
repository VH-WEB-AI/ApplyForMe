import pytest
from pydantic import BaseModel

from app.services.document_chunking import chunk_text
from app.services.json_formatter import parse_llm_json
from app.services.keyword_extractor import extract_keywords, keyword_overlap_score
from app.services.pii_redaction import redact_pii
from app.services.resume_parser import identify_sections
from app.services.response_validator import ResponseValidationError, validate_with_retry
from app.services.skill_extractor import extract_skills, normalize_skill, skill_gap


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


def test_keyword_overlap_score():
    resume = "Experienced with Python, FastAPI, and PostgreSQL databases."
    jd = "Looking for Python and PostgreSQL experience with FastAPI."
    score = keyword_overlap_score(resume, jd)
    assert score > 0.5


def test_extract_keywords_excludes_stopwords():
    keywords = extract_keywords("The candidate will work with the team and the manager")
    assert "the" not in keywords
    assert "and" not in keywords


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
