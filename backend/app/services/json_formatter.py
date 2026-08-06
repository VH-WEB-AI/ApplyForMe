"""JSON Formatter: turns raw LLM text into a Python dict, tolerating markdown fences."""

import json
import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_llm_json(raw: str) -> dict:
    cleaned = _FENCE_RE.sub("", raw.strip())
    return json.loads(cleaned)
