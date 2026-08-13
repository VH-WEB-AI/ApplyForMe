"""
Shared JSON Formatter service. Strips code fences/preamble that models
sometimes add despite instructions, and safely parses the result.
"""
import re

import orjson

from app.core.exceptions import ValidationFailedError


class JSONFormatter:
    _fence_re = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

    def clean(self, raw: str) -> str:
        return self._fence_re.sub("", raw.strip()).strip()

    def parse(self, raw: str) -> dict:
        cleaned = self.clean(raw)
        try:
            return orjson.loads(cleaned)
        except orjson.JSONDecodeError as exc:
            # last-resort: try to find the outermost {...} block
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return orjson.loads(match.group(0))
                except orjson.JSONDecodeError:
                    pass
            raise ValidationFailedError(
                "Model response was not valid JSON", {"raw_snippet": cleaned[:300]}
            ) from exc

    def to_json_str(self, data: dict) -> str:
        return orjson.dumps(data).decode("utf-8")


json_formatter = JSONFormatter()
