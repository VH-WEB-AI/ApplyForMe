"""
Engine 3: Career Health Engine
Aggregate data analysis, career health scoring, trend & benchmarking,
weak areas identification, personalized recommendations.
"""
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.engines.base_engine import BaseEngine
from app.orchestrator.context_manager import CandidateContext
from app.core.logging import get_logger
from app.shared_services.json_formatter import json_formatter
from app.shared_services.llm_client import llm_client
from app.shared_services.prompt_builder import CAREER_HEALTH_PROMPT, prompt_builder
from app.shared_services.response_validator import response_validator

logger = get_logger(__name__)
settings = get_settings()


class CareerHealthOutput(BaseModel):
    career_health_score: float = Field(ge=0, le=100)
    trend: str = "stable"
    weak_areas: list[str] = Field(default_factory=list)
    benchmarks: dict = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)


class CareerHealthEngine(BaseEngine):
    name = "career_health"

    async def run(self, payload: dict[str, Any], context: CandidateContext | None = None) -> dict[str, Any]:
        aggregate = self._build_aggregate(context, payload)
        fallback = self._build_local_output(aggregate)

        if not self._has_signal(aggregate):
            return {
                "result": fallback.model_dump(),
                "usage": self._local_usage(),
            }

        if not settings.AI_ENABLED:
            return {
                "result": fallback.model_dump(),
                "usage": self._local_usage(),
            }

        try:
            messages = prompt_builder.build(CAREER_HEALTH_PROMPT, aggregate_data=str(aggregate))
            response = await llm_client.chat_completion(
                messages, temperature=0.3, response_format={"type": "json_object"}
            )
            parsed = json_formatter.parse(response.content)
            validated = response_validator.validate(parsed, CareerHealthOutput)
            usage = {
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_ms": response.latency_ms,
            }
        except Exception as exc:
            logger.warning("career_health_local_fallback", error=str(exc))
            validated = fallback
            usage = self._local_usage()

        return {
            "result": validated.model_dump(),
            "usage": usage,
        }

    def _build_aggregate(self, context: CandidateContext | None, payload: dict[str, Any]) -> dict:
        if not context:
            return payload.get("aggregate_override", {})

        resume_scores = [r["resume_score"] for r in context.resumes if r.get("resume_score") is not None]
        match_scores = [a["match_score"] for a in context.applications if a.get("match_score") is not None]
        status_counts: dict[str, int] = {}
        for app in context.applications:
            status_counts[app["status"]] = status_counts.get(app["status"], 0) + 1

        return {
            "resume_score_history": resume_scores,
            "match_score_history": match_scores,
            "application_status_breakdown": status_counts,
            "total_applications": len(context.applications),
            "profile": context.profile,
        }

    def _build_local_output(self, aggregate: dict) -> CareerHealthOutput:
        resume_scores = [float(score) for score in aggregate.get("resume_score_history", []) if score is not None]
        match_scores = [float(score) for score in aggregate.get("match_score_history", []) if score is not None]
        status_counts = aggregate.get("application_status_breakdown", {}) or {}
        profile = aggregate.get("profile", {}) or {}

        latest_resume = resume_scores[0] if resume_scores else None
        avg_match = sum(match_scores) / len(match_scores) if match_scores else None
        total_applications = int(aggregate.get("total_applications") or 0)

        score_parts = []
        if latest_resume is not None:
            score_parts.append(latest_resume)
        if avg_match is not None:
            score_parts.append(avg_match * 100 if avg_match <= 1 else avg_match)
        if total_applications:
            activity_score = min(100, 45 + total_applications * 10)
            score_parts.append(activity_score)
        if profile:
            profile_fields = [
                bool(profile.get("headline")),
                bool(profile.get("location")),
                bool(profile.get("years_experience") is not None),
                bool(profile.get("skills")),
                bool(profile.get("preferences")),
            ]
            score_parts.append(sum(profile_fields) / len(profile_fields) * 100)

        score = round(sum(score_parts) / len(score_parts), 1) if score_parts else 50.0

        weak_areas: list[str] = []
        recommendations: list[str] = []
        if latest_resume is None:
            weak_areas.append("Resume scoring")
            recommendations.append("Upload and process a resume to establish your baseline score.")
        elif latest_resume < 70:
            weak_areas.append("Resume strength")
            recommendations.append("Improve resume clarity, keywords, and measurable impact bullets.")
        if avg_match is None:
            weak_areas.append("Job match history")
            recommendations.append("Run job matching against a target role to benchmark fit.")
        elif (avg_match * 100 if avg_match <= 1 else avg_match) < 65:
            weak_areas.append("Role alignment")
            recommendations.append("Target roles closer to your strongest skills or close the missing-skill gaps.")
        if not total_applications:
            weak_areas.append("Application activity")
            recommendations.append("Save or apply to roles so progress and outcomes can be tracked.")
        if not profile.get("skills"):
            weak_areas.append("Profile completeness")
            recommendations.append("Add core skills to your profile to improve recommendations.")

        successful_statuses = sum(status_counts.get(status, 0) for status in ["interviewing", "offer"])
        rejected = status_counts.get("rejected", 0)
        trend = "stable"
        if successful_statuses:
            trend = "improving"
        elif rejected and rejected >= max(2, total_applications // 2):
            trend = "declining"

        return CareerHealthOutput(
            career_health_score=score,
            trend=trend,
            weak_areas=weak_areas[:4],
            benchmarks={
                "latest_resume_score": latest_resume,
                "average_match_score": avg_match,
                "total_applications": total_applications,
            },
            recommendations=recommendations[:4],
        )

    @staticmethod
    def _has_signal(aggregate: dict) -> bool:
        return bool(
            aggregate.get("resume_score_history")
            or aggregate.get("match_score_history")
            or aggregate.get("application_status_breakdown")
            or aggregate.get("profile")
        )

    @staticmethod
    def _local_usage() -> dict[str, Any]:
        return {
            "model": "local-career-health",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
        }


career_health_engine = CareerHealthEngine()
