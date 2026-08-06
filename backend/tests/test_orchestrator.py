from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from app.orchestrator.engine_base import Engine
from app.orchestrator.orchestrator import AIOrchestrator
from app.orchestrator.registry import register_engine
from app.services.llm_gateway import ChatResult
from app.services.prompt_builder import PromptSpec


class _EchoSchema(BaseModel):
    advice: str


class _FakeEngine(Engine):
    name = "fake_engine"
    response_schema = _EchoSchema

    def gather_context(self, db, payload):
        return {"candidate_id": payload["candidate_id"], "score": 42}

    def build_prompt_spec(self, context):
        return PromptSpec(
            system_prompt="You are a test engine.",
            business_rules=["Always be deterministic."],
            engine_instructions="Give advice.",
            json_schema={"advice": "string"},
            candidate_context=context,
        )

    def postprocess(self, db, payload, context, llm_output):
        return {"score": context["score"], "advice": llm_output.advice}


def test_orchestrator_happy_path():
    register_engine(_FakeEngine())
    db = MagicMock()

    fake_chat_result = ChatResult(
        content='{"advice": "add more metrics"}',
        model="test-model",
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=12.3,
    )

    with patch("app.orchestrator.orchestrator.chat_completion", return_value=fake_chat_result):
        result = AIOrchestrator().handle_request(
            "fake_engine", db, {"candidate_id": 1}
        )

    assert result == {"score": 42, "advice": "add more metrics"}
    assert db.add.called  # audit log row was staged
    assert db.commit.called


def test_orchestrator_retries_on_invalid_json_then_succeeds():
    register_engine(_FakeEngine())
    db = MagicMock()

    responses = [
        ChatResult(content="not json", model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1.0),
        ChatResult(content='{"advice": "ok now"}', model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1.0),
    ]

    with patch("app.orchestrator.orchestrator.chat_completion", side_effect=responses):
        result = AIOrchestrator().handle_request("fake_engine", db, {"candidate_id": 1})

    assert result == {"score": 42, "advice": "ok now"}
