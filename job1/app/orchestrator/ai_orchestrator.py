"""
AI ORCHESTRATOR — "Brain of the System" (Section 3 of the architecture).

Pipeline for every request:
  Request Classifier -> Context Manager -> Engine Router -> Engine ->
  Response Validator (inside engine) -> Result Aggregator -> Audit Logger

Wrapped end-to-end by the Error Handler & Retry component.
"""
import time
import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.engines.base_engine import BaseEngine
from app.engines.career_copilot.engine import career_copilot_engine
from app.engines.career_health.engine import career_health_engine
from app.engines.job_match.engine import job_match_engine
from app.engines.resume_intelligence.engine import resume_intelligence_engine
from app.orchestrator.context_manager import context_manager
from app.orchestrator.error_handler import error_handler
from app.orchestrator.request_classifier import EngineType, request_classifier
from app.shared_services.audit_logger import audit_logger

logger = get_logger(__name__)

ENGINE_REGISTRY: dict[EngineType, BaseEngine] = {
    EngineType.RESUME_INTELLIGENCE: resume_intelligence_engine,
    EngineType.JOB_MATCH: job_match_engine,
    EngineType.CAREER_HEALTH: career_health_engine,
    EngineType.CAREER_COPILOT: career_copilot_engine,
}


class OrchestratorResponse:
    def __init__(self, request_id: str, engine: str, result: dict[str, Any], latency_ms: float):
        self.request_id = request_id
        self.engine = engine
        self.result = result
        self.latency_ms = latency_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "engine": self.engine,
            "result": self.result,
            "latency_ms": round(self.latency_ms, 2),
        }


class AIOrchestrator:
    """Single entrypoint used by every API endpoint that needs AI capability."""

    async def dispatch(
        self,
        *,
        intent: str,
        payload: dict[str, Any],
        db: AsyncSession,
        user_id: Optional[uuid.UUID] = None,
        load_context: bool = True,
    ) -> OrchestratorResponse:
        request_id = str(uuid.uuid4())
        start = time.perf_counter()

        # 1. Request Classifier -> which engine handles this?
        engine_type = request_classifier.classify(intent)
        engine = ENGINE_REGISTRY[engine_type]

        # 2. Context Manager -> load candidate context (Context Service)
        context = None
        if load_context and user_id:
            context = await context_manager.load(db, user_id)

        # 3. Engine Router + Error Handler & Retry -> execute engine
        async def _execute():
            return await engine.run(payload, context)

        try:
            engine_output = await error_handler.run_with_retry(engine.name, intent, _execute)
            status = "success"
            error_message = None
        except Exception as exc:
            status = "error"
            error_message = str(exc)
            latency_ms = (time.perf_counter() - start) * 1000
            await audit_logger.log(
                request_id=request_id,
                engine=engine.name,
                action=intent,
                user_id=user_id,
                status=status,
                error_message=error_message,
                latency_ms=latency_ms,
            )
            raise

        latency_ms = (time.perf_counter() - start) * 1000

        # 4. Result Aggregator -> shape into the standard envelope
        usage = engine_output.get("usage", {})

        # 5. Audit Logger
        await audit_logger.log(
            request_id=request_id,
            engine=engine.name,
            action=intent,
            user_id=user_id,
            model=usage.get("model"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            status=status,
        )

        return OrchestratorResponse(
            request_id=request_id,
            engine=engine.name,
            result=engine_output["result"],
            latency_ms=latency_ms,
        )


ai_orchestrator = AIOrchestrator()
