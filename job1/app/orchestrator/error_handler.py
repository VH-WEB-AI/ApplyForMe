"""
Error Handler & Retry component. Wraps engine execution with bounded
retries for transient failures (LLM timeouts, malformed JSON) and
converts anything unrecoverable into a clean EngineError.
"""
from typing import Awaitable, Callable, TypeVar

from app.core.exceptions import AppError, EngineError, ValidationFailedError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

RETRIABLE_EXCEPTIONS = (ValidationFailedError,)
MAX_RETRIES = 2


class OrchestratorErrorHandler:
    async def run_with_retry(self, engine_name: str, action: str, fn: Callable[[], Awaitable[T]]) -> T:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                return await fn()
            except RETRIABLE_EXCEPTIONS as exc:
                last_exc = exc
                logger.warning(
                    "engine_call_retry",
                    engine=engine_name,
                    action=action,
                    attempt=attempt,
                    error=str(exc),
                )
                continue
            except AppError:
                raise
            except Exception as exc:  # unexpected, non-retriable
                logger.error("engine_call_unexpected_error", engine=engine_name, action=action, error=str(exc))
                raise EngineError(f"{engine_name} failed unexpectedly", {"reason": str(exc)}) from exc

        raise EngineError(
            f"{engine_name} failed after {MAX_RETRIES + 1} attempts",
            {"reason": str(last_exc)},
        )


error_handler = OrchestratorErrorHandler()
