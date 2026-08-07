"""
Domain-level exceptions and a single FastAPI exception handler registrar.
Keeps error shape consistent across the whole API.
"""
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for all predictable, handled application errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    error_code: str = "app_error"

    def __init__(self, message: str, details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "unauthorized"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "forbidden"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"


class ValidationFailedError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "validation_failed"


class EngineError(AppError):
    """Raised when one of the four AI engines fails irrecoverably."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "engine_error"


class LLMProviderError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "llm_provider_error"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        logger.warning(
            "handled_app_error",
            path=str(request.url.path),
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.error("unhandled_exception", path=str(request.url.path), error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
        )
