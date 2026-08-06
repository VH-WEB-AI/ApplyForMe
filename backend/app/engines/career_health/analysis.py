"""Deterministic Career Health scoring (see spec Engine 3: suggested weighting is
admin-configurable via Settings.career_health_weights)."""

from dataclasses import dataclass, field

from app.db.models.core import CandidateProfile

_PROFILE_FIELDS = [
    "target_role", "target_industry", "experience_level", "visa_status", "location",
    "desired_salary_min", "desired_salary_max", "linkedin_url", "github_url", "portfolio_url",
]


@dataclass
class CareerHealthComponents:
    resume_quality: float = 0.0
    ats_compatibility: float = 0.0
    profile_completeness: float = 0.0
    skill_relevance: float = 0.0
    application_activity: float = 0.0
    interview_progress: float = 0.0
    market_alignment: float = 0.0
    professional_presence: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "resume_quality": self.resume_quality,
            "ats_compatibility": self.ats_compatibility,
            "profile_completeness": self.profile_completeness,
            "skill_relevance": self.skill_relevance,
            "application_activity": self.application_activity,
            "interview_progress": self.interview_progress,
            "market_alignment": self.market_alignment,
            "professional_presence": self.professional_presence,
        }


def profile_completeness_score(profile: CandidateProfile) -> float:
    filled = sum(1 for field_name in _PROFILE_FIELDS if getattr(profile, field_name))
    return round((filled / len(_PROFILE_FIELDS)) * 100)


def skill_relevance_score(missing_skills_count: int) -> float:
    return max(0.0, 100 - min(100, missing_skills_count * 15))


def application_activity_score(application_count: int) -> float:
    return min(100.0, application_count * 10)


def interview_progress_score(interview_count: int) -> float:
    return min(100.0, interview_count * 20)


def market_alignment_score(job_match_scores: list[int]) -> float:
    if not job_match_scores:
        return 50.0  # neutral: no matches computed yet
    return sum(job_match_scores) / len(job_match_scores)


def professional_presence_score(profile: CandidateProfile) -> float:
    urls = [profile.linkedin_url, profile.github_url, profile.portfolio_url]
    present = sum(1 for u in urls if u)
    return round((present / len(urls)) * 100)


def compute_overall_score(components: CareerHealthComponents, weights: dict[str, float]) -> int:
    total = sum(getattr(components, key) * weight for key, weight in weights.items())
    return round(total)


def weak_areas(components: CareerHealthComponents, threshold: int = 60) -> list[str]:
    return [name for name, score in components.as_dict().items() if score < threshold]
