"""Deterministic job-match scoring: the Business Rule Engine from spec Engine 2.
The LLM only ever explains these numbers afterward -- it never computes them."""

from dataclasses import dataclass, field

from app.services.skill_extractor import skill_gap
from app.services.tag_extractor import tag_overlap_score

_US_STATE_ABBR = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca",
    "colorado": "co", "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga",
    "hawaii": "hi", "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia",
    "kansas": "ks", "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut", "vermont": "vt",
    "virginia": "va", "washington": "wa", "west virginia": "wv", "wisconsin": "wi",
    "wyoming": "wy", "district of columbia": "dc",
}


def _parse_location(location: str) -> tuple[str, str | None]:
    """Splits "City, State" (full or abbreviated) into normalized (city, state_abbr).
    State is None when the string has no comma-separated region part."""
    parts = [p.strip().lower() for p in location.split(",") if p.strip()]
    if not parts:
        return "", None
    city = parts[0]
    if len(parts) < 2:
        return city, None
    region = parts[1]
    state = region if len(region) == 2 else _US_STATE_ABBR.get(region, region)
    return city, state

# Suggested initial weighting; admin-configurable in the future (see design principle).
# Tag overlap (precomputed keyphrases, see tag_extractor.py) is the sole
# text-matching signal (no semantic/embedding score, no live keyword
# extraction) -- highest priority, carrying the biggest single weight.
WEIGHTS = {
    "tags": 0.45,
    "experience": 0.20,
    "location": 0.15,
    "visa": 0.10,
    "salary": 0.10,
}


@dataclass
class JobMatchScores:
    tags_score: float = 0.0
    experience_score: float = 0.0
    location_score: float = 0.0
    visa_score: float = 0.0
    salary_score: float = 0.0
    missing_skills: list[str] = field(default_factory=list)
    match_score: int = 0
    priority_badge: str = "normal"
    interview_readiness: str = "unknown"


def experience_score(candidate_years: float, min_required_years: int | None) -> float:
    if not min_required_years:
        return 1.0
    if candidate_years >= min_required_years:
        return 1.0
    return max(0.0, candidate_years / min_required_years)


def location_score(candidate_location: str | None, job_location: str | None, job_remote: bool) -> float:
    """Tiered match instead of a brittle exact-string comparison: same city (state
    optional/ignored if either side omits it) scores full credit; same state but a
    different city scores partial credit (same commute region); anything else is a
    real mismatch. Handles "Austin, TX" vs "Austin, Texas" vs "Austin" alike."""
    if job_remote:
        return 1.0
    if not candidate_location or not job_location:
        return 0.5  # unknown; neutral rather than penalizing
    candidate_city, candidate_state = _parse_location(candidate_location)
    job_city, job_state = _parse_location(job_location)

    if candidate_city == job_city and (not candidate_state or not job_state or candidate_state == job_state):
        return 1.0
    if candidate_state and job_state and candidate_state == job_state:
        return 0.6
    return 0.2


def visa_score(candidate_visa_status: str | None, job_visa_sponsorship: bool) -> float:
    if not candidate_visa_status or candidate_visa_status.strip().lower() in {
        "citizen", "permanent resident", "no sponsorship needed", "authorized",
    }:
        return 1.0
    return 1.0 if job_visa_sponsorship else 0.0


def salary_score(
    candidate_min: int | None,
    candidate_max: int | None,
    job_min: int | None,
    job_max: int | None,
) -> float:
    if not candidate_min or not job_max:
        return 0.5  # unknown; neutral rather than penalizing
    return 1.0 if job_max >= candidate_min else max(0.0, job_max / candidate_min)


def priority_badge(match_score: int) -> str:
    if match_score >= 80:
        return "high"
    if match_score >= 55:
        return "normal"
    return "low"


def interview_readiness(match_score: int, resume_score: int) -> str:
    combined = (match_score + resume_score) / 2
    if combined >= 75:
        return "ready"
    if combined >= 50:
        return "needs_prep"
    return "not_ready"


def compute(
    *,
    resume_tags: list[str],
    job_tags: list[str],
    candidate_skills: list[str],
    required_skills: list[str],
    candidate_years: float,
    min_required_years: int | None,
    candidate_location: str | None,
    job_location: str | None,
    job_remote: bool,
    candidate_visa_status: str | None,
    job_visa_sponsorship: bool,
    candidate_salary_min: int | None,
    candidate_salary_max: int | None,
    job_salary_min: int | None,
    job_salary_max: int | None,
    resume_score: int,
) -> JobMatchScores:
    scores = JobMatchScores(
        tags_score=round(tag_overlap_score(resume_tags, job_tags), 4),
        experience_score=round(experience_score(candidate_years, min_required_years), 4),
        location_score=round(location_score(candidate_location, job_location, job_remote), 4),
        visa_score=round(visa_score(candidate_visa_status, job_visa_sponsorship), 4),
        salary_score=round(
            salary_score(candidate_salary_min, candidate_salary_max, job_salary_min, job_salary_max), 4
        ),
        missing_skills=skill_gap(candidate_skills, required_skills),
    )

    weighted = (
        scores.tags_score * WEIGHTS["tags"]
        + scores.experience_score * WEIGHTS["experience"]
        + scores.location_score * WEIGHTS["location"]
        + scores.visa_score * WEIGHTS["visa"]
        + scores.salary_score * WEIGHTS["salary"]
    )
    scores.match_score = round(weighted * 100)
    scores.priority_badge = priority_badge(scores.match_score)
    scores.interview_readiness = interview_readiness(scores.match_score, resume_score)
    return scores
