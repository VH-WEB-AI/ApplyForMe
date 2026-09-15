import io
from unittest.mock import patch

import docx

from sqlalchemy import select

from app.db.models.ai_ops import AIResponseLog
from app.db.models.core import CandidateProfile, User
from app.db.models.resume import ResumeScore, ResumeUpload, ResumeVersion
from app.engines.resume_intelligence.engine import ResumeIntelligenceEngine
from app.engines.resume_intelligence.schemas import ResumeLLMOutput
from app.orchestrator.orchestrator import AIOrchestrator
from app.services.llm_gateway import ChatResult, EmbeddingResult

FAKE_EMBEDDING = EmbeddingResult(vector=[0.01] * 1536, model="test-embedding-model", latency_ms=1.0, tokens=40)

FAKE_LLM_OUTPUT = ResumeLLMOutput(
    tags=["python", "fastapi", "kubernetes"],
    resume_score=72,
    ats_score=68,
    section_scores={"summary": 80, "experience": 65, "education": 80, "skills": 60},
    weak_sections=["experience"],
    total_experience_years=3.0,
    education=["Bachelor of Science in Computer Science, State University, 2018"],
    certifications=["AWS Certified Solutions Architect"],
    missing_skills=["Terraform"],
    recommendations=["Quantify the 'worked on stuff' bullet with a measurable outcome."],
    rewrite_suggestions={"experience": "Owned X, resulting in Y% improvement in Z."},
)


def _make_resume_docx() -> bytes:
    document = docx.Document()
    document.add_paragraph("Jane Doe")
    document.add_paragraph("jane.doe@example.com | 555-123-4567")
    document.add_paragraph("Summary")
    document.add_paragraph("Backend engineer with a focus on distributed systems and APIs.")
    document.add_paragraph("Experience")
    document.add_paragraph("- Led migration of 12 services to Kubernetes, reducing costs by 30%")
    document.add_paragraph("- Built a FastAPI platform used by 200 engineers")
    document.add_paragraph("- worked on stuff")
    document.add_paragraph("Education")
    document.add_paragraph("Bachelor of Science in Computer Science, State University, 2018")
    document.add_paragraph("Skills")
    document.add_paragraph("Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS")
    document.add_paragraph("Certifications")
    document.add_paragraph("AWS Certified Solutions Architect")

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _make_candidate(db) -> CandidateProfile:
    user = User(email="jane@example.com", full_name="Jane Doe")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, target_role="Backend Engineer")
    db.add(profile)
    db.flush()
    return profile


def test_gather_context_has_no_pii_and_defers_tags_to_llm(db):
    candidate = _make_candidate(db)
    engine = ResumeIntelligenceEngine()

    with patch("app.services.embedding_generator.llm_gateway.create_embedding", return_value=FAKE_EMBEDDING):
        context = engine.gather_context(
            db,
            {
                "candidate_id": candidate.id,
                "file_bytes": _make_resume_docx(),
                "filename": "resume.docx",
                "target_role": "Backend Engineer",
                "target_industry": "Software",
            },
        )

    assert context["resume_version_id"] is not None
    # PII must not leak into what eventually reaches the LLM
    assert "jane.doe@example.com" not in context["redacted_resume_text"]
    # tags aren't known until the LLM call in postprocess() runs
    resume_version = db.get(ResumeVersion, context["resume_version_id"])
    assert resume_version.tags == []


def test_resume_engine_full_orchestrator_flow(db):
    candidate = _make_candidate(db)

    fake_chat_result = ChatResult(
        content=FAKE_LLM_OUTPUT.model_dump_json(),
        model="test-model",
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=250.0,
    )

    payload = {
        "candidate_id": candidate.id,
        "file_bytes": _make_resume_docx(),
        "filename": "resume.docx",
        "target_role": "Backend Engineer",
        "target_industry": "Software",
    }

    with (
        patch("app.orchestrator.orchestrator.chat_completion", return_value=fake_chat_result),
        patch("app.services.embedding_generator.llm_gateway.create_embedding", return_value=FAKE_EMBEDDING),
    ):
        result = AIOrchestrator().handle_request("resume_intelligence", db, payload)

    assert result["resumeScore"] == 72
    assert result["atsScore"] == 68
    assert result["tags"] == ["python", "fastapi", "kubernetes"]
    assert result["sectionScores"]["experience"] == 65
    assert result["weakSections"] == ["experience"]
    assert result["totalExperienceYears"] == 3.0
    assert any("AWS Certified" in c for c in result["certifications"])
    assert "Bachelor" in " ".join(result["education"])
    assert result["missingSkills"] == ["Terraform"]
    assert "recommendations" in result
    assert result["rewriteSuggestions"]["experience"].startswith("Owned")

    # tags are persisted back onto the resume version row once the LLM call returns
    resume_version = db.get(ResumeVersion, result["resumeVersionId"])
    assert resume_version.tags == ["python", "fastapi", "kubernetes"]

    # every API call this upload made (embedding + chat) is stored against its resume version
    logs = db.scalars(
        select(AIResponseLog).where(AIResponseLog.resume_version_id == resume_version.id)
    ).all()
    by_model = {log.model: (log.prompt_tokens, log.completion_tokens, log.total_tokens) for log in logs}
    assert by_model == {"test-embedding-model": (40, 0, 40), "test-model": (100, 50, 150)}

    # the same totals are returned to the caller and stored on the analysis row
    expected_usage = {
        "promptTokens": 100,
        "completionTokens": 50,
        "embeddingTokens": 40,
        "totalTokens": 190,
        "apiCalls": 2,
        "cached": False,
    }
    assert result["tokenUsage"] == expected_usage
    score_row = db.scalar(select(ResumeScore).where(ResumeScore.resume_version_id == resume_version.id))
    assert score_row.token_usage == expected_usage


def test_ats_check_without_candidate_id_still_returns_full_analysis(db):
    fake_chat_result = ChatResult(
        content=FAKE_LLM_OUTPUT.model_dump_json(),
        model="test-model",
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=250.0,
    )

    payload = {
        "endpoint": "ats-check",
        "candidate_id": None,
        "file_bytes": _make_resume_docx(),
        "filename": "resume.docx",
        "target_role": "Backend Engineer",
        "target_industry": "Software",
    }

    # Bypass Redis: an earlier run of this test caches this exact resume, and a
    # cache hit would (correctly) report zero token usage.
    with (
        patch("app.orchestrator.orchestrator.chat_completion", return_value=fake_chat_result),
        patch("app.orchestrator.orchestrator.cache.get_cached", return_value=None),
        patch("app.orchestrator.orchestrator.cache.set_cached"),
    ):
        result = AIOrchestrator().handle_request("resume_intelligence", db, payload)

    assert result["resumeVersionId"] is None
    assert result["tags"] == ["python", "fastapi", "kubernetes"]
    assert result["tokenUsage"] == {
        "promptTokens": 100,
        "completionTokens": 50,
        "embeddingTokens": 0,
        "totalTokens": 150,
        "apiCalls": 1,
        "cached": False,
    }

    # the ats-check upload is recorded with its usage, and its API call points at it
    upload = db.get(ResumeUpload, result["uploadId"])
    assert upload.endpoint == "ats-check"
    assert upload.file_name == "resume.docx"
    assert (upload.status, upload.cached) == ("success", False)
    assert (upload.prompt_tokens, upload.completion_tokens, upload.embedding_tokens,
            upload.total_tokens, upload.api_calls) == (100, 50, 0, 150, 1)
    log = db.scalar(select(AIResponseLog).where(AIResponseLog.resume_upload_id == upload.id))
    assert (log.prompt_tokens, log.completion_tokens, log.total_tokens) == (100, 50, 150)

    assert result["resumeScore"] == 72


def test_ats_check_cache_hit_is_recorded_with_zero_usage(db):
    fake_chat_result = ChatResult(
        content=FAKE_LLM_OUTPUT.model_dump_json(),
        model="test-model",
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=250.0,
    )
    payload = {
        "endpoint": "ats-check",
        "candidate_id": None,
        "file_bytes": _make_resume_docx(),
        "filename": "resume.docx",
        "target_role": "Backend Engineer",
        "target_industry": "Software",
    }
    store = {}

    with (
        patch("app.orchestrator.orchestrator.chat_completion", return_value=fake_chat_result) as chat,
        patch("app.orchestrator.orchestrator.cache.get_cached", side_effect=lambda e, k: store.get(k)),
        patch("app.orchestrator.orchestrator.cache.set_cached", side_effect=lambda e, k, v: store.__setitem__(k, v)),
    ):
        first = AIOrchestrator().handle_request("resume_intelligence", db, payload)
        second = AIOrchestrator().handle_request("resume_intelligence", db, payload)

    assert chat.call_count == 1
    assert second["uploadId"] != first["uploadId"]
    assert second["tokenUsage"]["cached"] is True
    assert second["tokenUsage"]["totalTokens"] == 0
    upload = db.get(ResumeUpload, second["uploadId"])
    assert (upload.status, upload.cached, upload.total_tokens, upload.api_calls) == ("cached", True, 0, 0)
