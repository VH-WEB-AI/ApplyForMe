"""Shared deterministic estimator for years of experience from resume text,
used by both the Resume Intelligence Engine and the Job Match Engine."""

import re
from datetime import datetime, timezone

_YEAR_RANGE_RE = re.compile(
    r"(19|20)\d{2}\s*[-–—to]{1,4}\s*((19|20)\d{2}|present|current)", re.IGNORECASE
)
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def estimate_total_experience_years(experience_text: str) -> float:
    ranges = _YEAR_RANGE_RE.findall(experience_text)
    if not ranges:
        return 0.0

    years_found = [int(y) for y in _YEAR_RE.findall(experience_text)]
    if not years_found:
        return 0.0

    earliest = min(years_found)
    latest_tokens = experience_text.lower()
    current_year = datetime.now(timezone.utc).year
    latest = current_year if ("present" in latest_tokens or "current" in latest_tokens) else max(years_found)
    return max(0.0, float(latest - earliest))
