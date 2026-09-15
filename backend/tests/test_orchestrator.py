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


def test_orchestrator_audit_log_sums_tokens_across_retries():
    register_engine(_FakeEngine())
    db = MagicMock()

    responses = [
        ChatResult(content="not json", model="m", prompt_tokens=100, completion_tokens=20, latency_ms=1.0),
        ChatResult(content='{"advice": "ok now"}', model="m", prompt_tokens=130, completion_tokens=25, latency_ms=2.0),
    ]

    with (
        patch("app.orchestrator.orchestrator.chat_completion", side_effect=responses),
        patch("app.orchestrator.orchestrator.audit_logger.log_ai_call") as log_ai_call,
    ):
        AIOrchestrator().handle_request("fake_engine", db, {"candidate_id": 1})

    logged = log_ai_call.call_args.kwargs
    assert logged["prompt_tokens"] == 230
    assert logged["completion_tokens"] == 45
    assert logged["retry_count"] == 1


def test_cached_response_reports_zero_token_usage():
    class _CachedEngine(_FakeEngine):
        name = "cached_fake_engine"
        reports_token_usage = True

        def cache_key(self, context):
            return "same-input"

    register_engine(_CachedEngine())
    stale_usage = {"promptTokens": 100, "completionTokens": 50, "embeddingTokens": 0,
                   "totalTokens": 150, "apiCalls": 1, "cached": False}

    with (
        patch("app.orchestrator.orchestrator.cache.get_cached",
              return_value={"advice": "cached", "tokenUsage": stale_usage}),
        patch("app.orchestrator.orchestrator.chat_completion") as chat,
    ):
        result = AIOrchestrator().handle_request("cached_fake_engine", MagicMock(), {"candidate_id": 1})

    chat.assert_not_called()
    assert result["tokenUsage"] == {"promptTokens": 0, "completionTokens": 0, "embeddingTokens": 0,
                                    "totalTokens": 0, "apiCalls": 0, "cached": True}
