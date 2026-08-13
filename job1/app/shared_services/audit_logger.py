"""
Shared Audit Logger service. Every orchestrator call is logged here —
persisted to Postgres (AuditLog) and emitted as a structured log line for
Prometheus/Grafana/Langfuse-style pipelines to scrape.
"""
import uuid
from typing import Optional

from app.core.logging import get_logger
from app.db.session import db_session_ctx
from app.models.audit import AuditLog

logger = get_logger("audit")


class AuditLogger:
    async def log(
        self,
        *,
        request_id: str,
        engine: str,
        action: str,
        user_id: Optional[uuid.UUID] = None,
        model: Optional[str] = None,
        prompt_version: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "success",
        error_message: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        logger.info(
            "engine_invocation",
            request_id=request_id,
            engine=engine,
            action=action,
            user_id=str(user_id) if user_id else None,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            error=error_message,
        )
        async with db_session_ctx() as session:
            session.add(
                AuditLog(
                    user_id=user_id,
                    engine=engine,
                    action=action,
                    request_id=request_id,
                    model=model,
                    prompt_version=prompt_version,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    status=status,
                    error_message=error_message,
                    metadata_=metadata or {},
                )
            )


audit_logger = AuditLogger()
