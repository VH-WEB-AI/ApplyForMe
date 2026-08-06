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
                return cached

        prompt_spec = engine.build_prompt_spec(context)
        last_result: dict[str, ChatResult | None] = {"value": None}

        def generate(feedback: str | None) -> str:
            system_prompt, user_prompt = build_prompt(prompt_spec, feedback=feedback)
            result = chat_completion(system_prompt, user_prompt)
            last_result["value"] = result
            return result.content

        try:
            llm_output, retry_count = validate_with_retry(
                engine.response_schema,
                generate,
                max_retries=settings.response_validation_max_retries,
            )
        except Exception as exc:  # noqa: BLE001 -- must still audit-log and re-raise
            self._audit(db, engine_name, payload, last_result["value"], "error", str(exc), 0)
            db.commit()
            raise

        self._audit(db, engine_name, payload, last_result["value"], "success", None, retry_count)
        response = engine.postprocess(db, payload, context, llm_output)
        db.commit()

        if cache_key:
            cache.set_cached(engine_name, cache_key, response)

        return response

    @staticmethod
    def _audit(
        db: Session,
        engine_name: str,
        payload: dict[str, Any],
        chat_result: ChatResult | None,
        status: str,
        error: str | None,
        retry_count: int,
    ) -> None:
        candidate_id = payload.get("candidate_id")
        if chat_result is None:
            audit_logger.log_ai_call(
                db,
                engine=engine_name,
                model="unknown",
                prompt="",
                response="",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.0,
                candidate_id=candidate_id,
                status=status,
                error=error,
                retry_count=retry_count,
            )
        else:
            audit_logger.log_ai_call(
                db,
                engine=engine_name,
                model=chat_result.model,
                prompt="",
                response=chat_result.content,
                prompt_tokens=chat_result.prompt_tokens,
                completion_tokens=chat_result.completion_tokens,
                latency_ms=chat_result.latency_ms,
                candidate_id=candidate_id,
                status=status,
                error=error,
                retry_count=retry_count,
            )


orchestrator = AIOrchestrator()
