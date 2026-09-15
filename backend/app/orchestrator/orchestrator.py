"""AI Orchestrator: the brain of the system (see spec section 3 & 6).

Drives every engine through the same lifecycle: identify engine -> collect context
-> build prompt -> invoke LLM -> validate -> retry -> store output -> return JSON.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.orchestrator.registry import get_engine
from app.services import audit_logger, cache
from app.services.llm_gateway import ChatResult, chat_completion
from app.services.prompt_builder import build_prompt
from app.services.response_validator import validate_with_retry


class AIOrchestrator:
    def handle_request(self, engine_name: str, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        engine = get_engine(engine_name)
        settings = get_settings()

        context = engine.gather_context(db, payload)

        cache_key = engine.cache_key(context)
        if cache_key:
            cached = cache.get_cached(engine_name, cache_key)
            if cached is not None:
                if engine.reports_token_usage:
                    # A cached body carries the usage of the request that filled the cache;
                    # this request made no chat call, so record and report what it actually spent.
                    token_usage = _Usage().as_response(context, cached=True)
                    extra = engine.record_token_usage(db, context, token_usage, "cached", None)
                    db.commit()
                    cached = {**cached, "tokenUsage": token_usage, **extra}
                return cached

        prompt_spec = engine.build_prompt_spec(context)
        # Every attempt is billed, including ones validate_with_retry rejects, so
        # token counts accumulate across attempts instead of keeping only the last.
        usage = _Usage()

        def generate(feedback: str | None) -> str:
            system_prompt, user_prompt = build_prompt(prompt_spec, feedback=feedback)
            result = chat_completion(system_prompt, user_prompt)
            usage.add(result)
            return result.content

        try:
            llm_output, retry_count = validate_with_retry(
                engine.response_schema,
                generate,
                max_retries=settings.response_validation_max_retries,
            )
        except Exception as exc:  # noqa: BLE001 -- must still audit-log and re-raise
            self._audit(db, engine_name, payload, context, usage, "error", str(exc), max(usage.attempts - 1, 0))
            if engine.reports_token_usage:
                engine.record_token_usage(
                    db, context, usage.as_response(context, cached=False), "error", str(exc)
                )
            db.commit()
            raise

        self._audit(db, engine_name, payload, context, usage, "success", None, retry_count)
        if engine.reports_token_usage:
            context["token_usage"] = usage.as_response(context, cached=False)
        response = engine.postprocess(db, payload, context, llm_output)
        if engine.reports_token_usage:
            extra = engine.record_token_usage(db, context, context["token_usage"], "success", None)
            response = {**response, "tokenUsage": context["token_usage"], **extra}
        db.commit()

        if cache_key:
            cache.set_cached(engine_name, cache_key, response)

        return response

    @staticmethod
    def _audit(
        db: Session,
        engine_name: str,
        payload: dict[str, Any],
        context: dict[str, Any],
        usage: "_Usage",
        status: str,
        error: str | None,
        retry_count: int,
    ) -> None:
        last = usage.last
        audit_logger.log_ai_call(
            db,
            engine=engine_name,
            model=last.model if last else "unknown",
            prompt="",
            response=last.content if last else "",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            latency_ms=usage.latency_ms,
            candidate_id=payload.get("candidate_id"),
            resume_version_id=context.get("resume_version_id"),
            resume_upload_id=context.get("resume_upload_id"),
            status=status,
            error=error,
            retry_count=retry_count,
        )


class _Usage:
    """Running totals over every chat_completion attempt for one request."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latency_ms = 0.0
        self.attempts = 0
        self.last: ChatResult | None = None

    def add(self, result: ChatResult) -> None:
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        self.latency_ms += result.latency_ms
        self.attempts += 1
        self.last = result

    def as_response(self, context: dict[str, Any], *, cached: bool) -> dict[str, Any]:
        """Chat usage plus any embedding usage the engine recorded in context["embedding_usage"]."""
        embedding = context.get("embedding_usage") or {"tokens": 0, "calls": 0}
        return {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "embeddingTokens": embedding["tokens"],
            "totalTokens": self.prompt_tokens + self.completion_tokens + embedding["tokens"],
            "apiCalls": self.attempts + embedding["calls"],
            "cached": cached,
        }


orchestrator = AIOrchestrator()
