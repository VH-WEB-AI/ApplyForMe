"""
Shared Response Validator service. Validates a parsed engine JSON payload
against a Pydantic schema before it leaves the orchestrator, and surfaces
clear errors that the orchestrator's Error Handler & Retry component can
act on (e.g. re-prompt with the validation error appended).
"""
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.exceptions import ValidationFailedError

T = TypeVar("T", bound=BaseModel)


class ResponseValidator:
    def validate(self, data: dict, schema: Type[T]) -> T:
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise ValidationFailedError(
                "Engine response failed schema validation",
                {"errors": exc.errors()},
            ) from exc


response_validator = ResponseValidator()
