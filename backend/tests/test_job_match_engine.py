from unittest.mock import patch

from app.db.models.core import CandidateProfile, User
from app.db.models.jobs import JobPosting
from app.db.models.resume import ResumeVersion
from app.engines.job_match.engine import JobMatchEngine
from app.engines.job_match.schemas import JobMatchLLMOutput
from app.engines.job_match.service import match_candidate_to_active_jobs
from app.orchestrator.orchestrator import AIOrchestrator
from app.services.llm_gateway import ChatResult, EmbeddingResult

FAKE_EMBEDDING = EmbeddingResult(vector=[0.02] * 1536, model="test-embedding-model", latency_ms=1.0)


def _make_candidate(db, **profile_kwargs) -> CandidateProfile:
    user = User(email=f"candidate{profile_kwargs.get('location', 'x')}@example.com", full_name="Jane Doe")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, target_role="Backend Engineer", **profile_kwargs)
    db.add(profile)
    db.flush()
    return profile


def _make_resume_version(db, candidate_id: int) -> ResumeVersion:
    raw_text = (
        "Jane Doe\n"
        "Experience\n"
        "- Built services with Python, FastAPI, PostgreSQL, and Docker from 2019 - Present\n"
        "Skills\nPython, FastAPI, PostgreSQL, Docker\n"
    )
    version = ResumeVersion(
        candidate_id=candidate_id,
        file_name="resume.docx",
        content_hash="abc123",
        raw_text=raw_text,
        sections={
            "experience": "- Built services with Python, FastAPI, PostgreSQL, and Docker from 2019 - Present",
            "skills": "Python, FastAPI, PostgreSQL, Docker",
        },
        parsed_data={},
    )
    db.add(version)
    db.flush()
    return version


def _make_job(db, **kwargs) -> JobPosting:
    defaults = dict(
        title="Backend Engineer",
        company="Acme",
        description="Looking for a backend engineer with Python, FastAPI, and AWS experience.",
        location="Remote",
        remote=True,
        seniority="mid",
        salary_min=100000,
        salary_max=140000,
        visa_sponsorship=True,
        required_skills=["Python", "FastAPI", "AWS"],
        min_experience_years=2,
        is_active=True,
    )
    defaults.update(kwargs)
    job = JobPosting(**defaults)
    db.add(job)
    db.flush()
    return job


def test_gather_context_computes_scores(db):
    candidate = _make_candidate(db, location="Remote", visa_status="citizen")
    _make_resume_version(db, candidate.id)
    job = _make_job(db)

    engine = JobMatchEngine()
    with patch("app.services.embedding_generator.llm_gateway.create_embedding", return_value=FAKE_EMBEDDING):
        context = engine.gather_context(db, {"candidate_id": candidate.id, "job_posting_id": job.id})

    scores = context["scores"]
    assert 0 <= scores.match_score <= 100
    assert "AWS" in scores.missing_skills
    assert scores.location_score == 1.0  # remote job
    assert scores.visa_score == 1.0  # candidate is a citizen


def test_gather_context_raises_without_resume(db):
    candidate = _make_candidate(db, location="Remote")
    job = _make_job(db)
    engine = JobMatchEngine()

    try:
        engine.gather_context(db, {"candidate_id": candidate.id, "job_posting_id": job.id})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "no resume on file" in str(exc)


def test_full_orchestrator_flow_and_service_ranking(db):
    candidate = _make_candidate(db, location="Remote", visa_status="citizen")
    _make_resume_version(db, candidate.id)
    good_job = _make_job(db, title="Great Fit", required_skills=["Python", "FastAPI"])
    bad_job = _make_job(
        db,
        title="Bad Fit",
        description="Looking for a Java engineer with Kubernetes and Kafka experience.",
        required_skills=["Java", "Kafka", "Kubernetes"],
        remote=False,
        location="Nowhere",
    )

    fake_llm_output = JobMatchLLMOutput(
        explanation="Strong overlap in backend skills.",
        resume_changes=["Add AWS experience if you have any."],
    )
    fake_chat_result = ChatResult(
        content=fake_llm_output.model_dump_json(),
        model="test-model",
        prompt_tokens=10,
        completion_tokens=10,
        latency_ms=5.0,
    )

    with (
        patch("app.orchestrator.orchestrator.chat_completion", return_value=fake_chat_result),
        patch("app.services.embedding_generator.llm_gateway.create_embedding", return_value=FAKE_EMBEDDING),
    ):
        single = AIOrchestrator().handle_request(
            "job_match", db, {"candidate_id": candidate.id, "job_posting_id": good_job.id}
        )
        ranked = match_candidate_to_active_jobs(db, candidate.id)

    assert single["jobTitle"] == "Great Fit"
    assert single["explanation"] == "Strong overlap in backend skills."
    assert len(ranked) == 2
    assert ranked[0]["jobTitle"] == "Great Fit"
    assert ranked[0]["matchScore"] >= ranked[1]["matchScore"]
