"""Response Validator: validates LLM JSON output against a pydantic schema, with retries."""

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.services.json_formatter import parse_llm_json

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ResponseValidationError(Exception):
    pass


def validate_with_retry(
    schema: type[SchemaT],
    generate: Callable[[str | None], str],
    *,
    max_retries: int = 2,
) -> tuple[SchemaT, int]:
    """Calls `generate(feedback)` for a raw JSON string, validates it against `schema`,
    and retries (passing the failure back in as feedback) up to `max_retries` times.
    Returns (validated_model, retry_count)."""
    feedback: str | None = None

    for attempt in range(max_retries + 1):
        raw = generate(feedback)
        try:
            data = parse_llm_json(raw)
            return schema.model_validate(data), attempt
        except (ValueError, ValidationError) as exc:
            feedback = f"Your previous response was invalid: {exc}. Return ONLY valid JSON matching the required schema."
            if attempt == max_retries:
                raise ResponseValidationError(
                    f"LLM response failed validation after {max_retries + 1} attempts: {exc}"
                ) from exc

    raise ResponseValidationError("unreachable")
