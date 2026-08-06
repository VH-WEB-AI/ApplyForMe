from unittest.mock import patch

from app.db.models.career_health import CareerHealthSnapshot
from app.db.models.core import CandidateProfile, User
from app.db.models.jobs import Application, Interview
from app.db.models.resume import ResumeScore, ResumeVersion
from app.engines.career_health.engine import CareerHealthEngine
from app.engines.career_health.schemas import CareerHealthLLMOutput
from app.orchestrator.orchestrator import AIOrchestrator
from app.services.llm_gateway import ChatResult


def _make_candidate(db, **kwargs) -> CandidateProfile:
    user = User(email="health@example.com", full_name="Jane Doe")
    db.add(user)
    db.flush()
    profile = CandidateProfile(
        user_id=user.id,
        target_role="Backend Engineer",
        target_industry="Software",
        experience_level="mid",
        visa_status="citizen",
        location="Remote",
        desired_salary_min=100000,
        desired_salary_max=140000,
        linkedin_url="https://linkedin.com/in/jane",
        github_url="https://github.com/jane",
        portfolio_url=None,
        **kwargs,
    )
    db.add(profile)
    db.flush()
    return profile


def _make_resume_score(db, candidate_id: int, resume_score=80, ats_score=70, missing=("AWS",)) -> None:
    version = ResumeVersion(
        candidate_id=candidate_id,
        file_name="resume.docx",
        content_hash="h1",
        raw_text="text",
        sections={},
        parsed_data={},
    )
    db.add(version)
    db.flush()
    score = ResumeScore(
        resume_version_id=version.id,
        resume_score=resume_score,
        ats_score=ats_score,
        section_scores={},
        missing_skills=list(missing),
        weak_sections=[],
        recommendations=[],
    )
    db.add(score)
    db.flush()


def test_gather_context_computes_overall_score(db):
    candidate = _make_candidate(db)
    _make_resume_score(db, candidate.id)
    application = Application(candidate_id=candidate.id, company="Acme", role="Engineer", status="applied")
    db.add(application)
    db.flush()
    db.add(Interview(candidate_id=candidate.id, application_id=application.id, stage="phone_screen"))
    db.flush()

    engine = CareerHealthEngine()
    context = engine.gather_context(db, {"candidate_id": candidate.id})

    assert 0 <= context["overall_score"] <= 100
    assert context["components"].resume_quality == 80
    assert context["components"].ats_compatibility == 70
    assert context["trend_delta"] == 0  # no previous snapshot


def test_trend_delta_uses_previous_snapshot(db):
    candidate = _make_candidate(db)
    _make_resume_score(db, candidate.id)
    db.add(
        CareerHealthSnapshot(
            candidate_id=candidate.id,
            overall_score=40,
            component_scores={},
            weak_areas=[],
            todays_priorities=[],
            advice="",
        )
    )
    db.flush()

    engine = CareerHealthEngine()
    context = engine.gather_context(db, {"candidate_id": candidate.id})

    assert context["trend_delta"] == context["overall_score"] - 40


def test_full_orchestrator_flow(db):
    candidate = _make_candidate(db)
    _make_resume_score(db, candidate.id, resume_score=90, ats_score=85, missing=())

    fake_llm_output = CareerHealthLLMOutput(
        advice="Keep up the strong momentum.",
        todays_priorities=["Apply to 3 more roles", "Add a portfolio link"],
    )
    fake_chat_result = ChatResult(
        content=fake_llm_output.model_dump_json(),
        model="test-model",
        prompt_tokens=10,
        completion_tokens=10,
        latency_ms=5.0,
    )

    with patch("app.orchestrator.orchestrator.chat_completion", return_value=fake_chat_result):
        result = AIOrchestrator().handle_request("career_health", db, {"candidate_id": candidate.id})

    assert result["careerHealthScore"] > 0
    assert result["advice"] == "Keep up the strong momentum."
    assert "Apply to 3 more roles" in result["todaysPriorities"]
    assert "professional_presence" in result["componentScores"]
