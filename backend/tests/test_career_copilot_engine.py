import hashlib
from unittest.mock import patch

from app.db.models.career_health import CareerHealthSnapshot
from app.db.models.core import CandidateProfile, User
from app.db.models.resume import ResumeScore, ResumeVersion
from app.engines.career_copilot.analysis import detect_intent
from app.engines.career_copilot.engine import CareerCopilotEngine
from app.engines.career_copilot.schemas import CopilotLLMOutput
from app.orchestrator.orchestrator import AIOrchestrator
from app.services.llm_gateway import ChatResult, EmbeddingResult

EMBED_DIM = 1536


def _bag_of_words_embedding(text: str) -> EmbeddingResult:
    """Deterministic fake embedding: shared words => higher cosine similarity,
    so RAG top-k ordering in tests is meaningful instead of arbitrary."""
    vector = [0.0] * EMBED_DIM
    for word in text.lower().split():
        index = int(hashlib.sha256(word.encode()).hexdigest(), 16) % EMBED_DIM
        vector[index] += 1.0
    return EmbeddingResult(vector=vector, model="fake", latency_ms=1.0)


def test_detect_intent():
    assert detect_intent("Why is my resume score so low?") == "explain_resume_score"
    assert detect_intent("How is my career health looking?") == "explain_career_health"
    assert detect_intent("What should I do next?") == "recommendation"
    assert detect_intent("Tell me a joke") == "general_advice"


def _make_candidate(db) -> CandidateProfile:
    user = User(email="copilot@example.com", full_name="Jane Doe")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, target_role="Backend Engineer")
    db.add(profile)
    db.flush()
    return profile


def _make_resume(db, candidate_id: int) -> ResumeVersion:
    raw_text = (
        "Experience\n"
        "Led backend development using Python and FastAPI at Acme Corp.\n"
        "Managed a Kubernetes migration reducing infrastructure costs.\n"
        "Skills\n"
        "Python, FastAPI, Kubernetes, PostgreSQL\n"
    )
    version = ResumeVersion(
        candidate_id=candidate_id,
        file_name="resume.docx",
        content_hash="h1",
        raw_text=raw_text,
        sections={},
        parsed_data={},
    )
    db.add(version)
    db.flush()
    db.add(
        ResumeScore(
            resume_version_id=version.id,
            resume_score=75,
            ats_score=65,
            section_scores={},
            missing_skills=["AWS"],
            weak_sections=["summary"],
            recommendations=[],
        )
    )
    db.flush()
    return version


def test_gather_context_flags_missing_data_for_candidate_without_resume(db):
    candidate = _make_candidate(db)
    engine = CareerCopilotEngine()

    with patch("app.services.embedding_generator.llm_gateway.create_embedding", side_effect=_bag_of_words_embedding):
        context = engine.gather_context(
            db, {"candidate_id": candidate.id, "conversation_id": None, "question": "How is my resume?"}
        )

    assert context["has_resume"] is False
    assert context["has_career_health"] is False
    assert context["retrieved_snippets"] == []


def test_gather_context_retrieves_relevant_resume_chunk(db):
    candidate = _make_candidate(db)
    _make_resume(db, candidate.id)
    engine = CareerCopilotEngine()

    with patch("app.services.embedding_generator.llm_gateway.create_embedding", side_effect=_bag_of_words_embedding):
        context = engine.gather_context(
            db,
            {
                "candidate_id": candidate.id,
                "conversation_id": None,
                "question": "Tell me about my Kubernetes experience",
            },
        )

    assert context["has_resume"] is True
    assert context["resume_score"] == 75
    assert any("Kubernetes" in snippet for snippet in context["retrieved_snippets"])


def test_full_orchestrator_flow_persists_conversation(db):
    candidate = _make_candidate(db)
    _make_resume(db, candidate.id)
    db.add(CareerHealthSnapshot(candidate_id=candidate.id, overall_score=70, component_scores={}, weak_areas=[], todays_priorities=[], advice=""))
    db.flush()

    fake_llm_output = CopilotLLMOutput(
        answer="Your resume score is 75 out of 100.",
        follow_up_suggestions=["Want tips to raise it above 80?"],
    )
    fake_chat_result = ChatResult(
        content=fake_llm_output.model_dump_json(),
        model="test-model",
        prompt_tokens=10,
        completion_tokens=10,
        latency_ms=5.0,
    )

    payload = {"candidate_id": candidate.id, "conversation_id": None, "question": "What is my resume score?"}

    with (
        patch("app.orchestrator.orchestrator.chat_completion", return_value=fake_chat_result),
        patch("app.services.embedding_generator.llm_gateway.create_embedding", side_effect=_bag_of_words_embedding),
    ):
        result = AIOrchestrator().handle_request("career_copilot", db, payload)

    assert result["answer"] == "Your resume score is 75 out of 100."
    assert result["intent"] == "explain_resume_score"
    assert result["conversationId"] is not None

    from app.db.models.conversation import ConversationMessage
    from sqlalchemy import select

    messages = db.scalars(
        select(ConversationMessage).where(ConversationMessage.conversation_id == result["conversationId"])
    ).all()
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant"]
