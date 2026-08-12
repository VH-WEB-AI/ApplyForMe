"""Shared deterministic estimator for years of experience from resume text,
used by both the Resume Intelligence Engine and the Job Match Engine."""

import re
from datetime import datetime, timezone

# Word boundaries matter: without them, "20064" (a phone number fragment, zip
# code, ID, etc.) would match "2006" as a false year anywhere in the substring.
# Only years found inside an actual "YYYY <separator> YYYY|present" range are
# used -- a standalone year elsewhere in the text (e.g. a certification date)
# must never influence the total.
_DATE_RANGE_RE = re.compile(
    r"\b(19\d{2}|20\d{2})\b\s*(?:-|–|—|to)\s*\b(19\d{2}|20\d{2}|present|current)\b",
    re.IGNORECASE,
)


def estimate_total_experience_years(experience_text: str) -> float:
    if not experience_text:
        return 0.0

    current_year = datetime.now(timezone.utc).year
    starts: list[int] = []
    ends: list[int] = []
    for match in _DATE_RANGE_RE.finditer(experience_text):
        start = int(match.group(1))
        end_token = match.group(2).lower()
        end = current_year if end_token in ("present", "current") else int(end_token)
        starts.append(start)
        ends.append(end)

    if not starts:
        return 0.0
    return max(0.0, float(max(ends) - min(starts)))
