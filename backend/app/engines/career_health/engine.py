from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.career_health import CareerHealthSnapshot
from app.db.models.core import CandidateProfile
from app.db.models.jobs import Application, Interview, JobMatch
from app.db.models.resume import ResumeScore, ResumeVersion
from app.engines.career_health import analysis
from app.engines.career_health.schemas import (
    CAREER_HEALTH_LLM_JSON_SCHEMA,
    CareerHealthLLMOutput,
)
from app.orchestrator.engine_base import Engine
from app.orchestrator.registry import register_engine
from app.services.prompt_builder import PromptSpec

SYSTEM_PROMPT = (
    "You are the Career Health Engine inside ApplyForMe's Career Command Center. "
    "Career Health is ApplyForMe's signature readiness metric. The overall score and "
    "component scores were already computed deterministically -- you only explain them "
    "and recommend next actions."
)

BUSINESS_RULES = [
    "Ground every priority in the specific weak_areas and component scores provided.",
    "todays_priorities must be concrete, doable-today actions, not vague encouragement.",
    "Never invent activity (applications, interviews) not reflected in the provided context.",
]


def _latest_resume_score(db: Session, candidate_id: int) -> ResumeScore | None:
    resume_version = db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.candidate_id == candidate_id)
        .order_by(ResumeVersion.id.desc())
    )
    if resume_version is None:
        return None
    return db.scalar(
        select(ResumeScore)
        .where(ResumeScore.resume_version_id == resume_version.id)
        .order_by(ResumeScore.id.desc())
    )


class CareerHealthEngine(Engine):
    name = "career_health"
    response_schema = CareerHealthLLMOutput

    def gather_context(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = payload["candidate_id"]
        settings = get_settings()

        profile = db.get(CandidateProfile, candidate_id)
        if profile is None:
            raise ValueError(f"Candidate profile {candidate_id} not found.")

        resume_score_row = _latest_resume_score(db, candidate_id)
        missing_skills_count = len(resume_score_row.missing_skills) if resume_score_row else 10

        application_count = db.scalar(
            select(func.count()).select_from(Application).where(Application.candidate_id == candidate_id)
        )
        interview_count = db.scalar(
            select(func.count()).select_from(Interview).where(Interview.candidate_id == candidate_id)
        )
        job_match_scores = list(
            db.scalars(
                select(JobMatch.match_score).where(JobMatch.candidate_id == candidate_id)
            ).all()
        )

        components = analysis.CareerHealthComponents(
            resume_quality=resume_score_row.resume_score if resume_score_row else 0,
            ats_compatibility=resume_score_row.ats_score if resume_score_row else 0,
            profile_completeness=analysis.profile_completeness_score(profile),
            skill_relevance=analysis.skill_relevance_score(missing_skills_count),
            application_activity=analysis.application_activity_score(application_count),
            interview_progress=analysis.interview_progress_score(interview_count),
            market_alignment=analysis.market_alignment_score(job_match_scores),
            professional_presence=analysis.professional_presence_score(profile),
        )

        overall_score = analysis.compute_overall_score(components, settings.career_health_weights)
        weak = analysis.weak_areas(components)

        previous_snapshot = db.scalar(
            select(CareerHealthSnapshot)
            .where(CareerHealthSnapshot.candidate_id == candidate_id)
            .order_by(CareerHealthSnapshot.id.desc())
        )
        trend_delta = overall_score - previous_snapshot.overall_score if previous_snapshot else 0

        return {
            "candidate_id": candidate_id,
            "components": components,
            "overall_score": overall_score,
            "weak_areas": weak,
            "trend_delta": trend_delta,
        }

    def build_prompt_spec(self, context: dict[str, Any]) -> PromptSpec:
        components: analysis.CareerHealthComponents = context["components"]
        instructions = (
            "Given the Career Health component scores and weak areas below, write brief, "
            "personalized advice and a list of 3-5 concrete priorities for today."
        )
        return PromptSpec(
            system_prompt=SYSTEM_PROMPT,
            business_rules=BUSINESS_RULES,
            engine_instructions=instructions,
            json_schema=CAREER_HEALTH_LLM_JSON_SCHEMA,
            candidate_context={
                "overall_score": context["overall_score"],
                "component_scores": components.as_dict(),
                "weak_areas": context["weak_areas"],
                "trend_delta": context["trend_delta"],
            },
        )

    def postprocess(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
        llm_output: CareerHealthLLMOutput,
    ) -> dict[str, Any]:
        components: analysis.CareerHealthComponents = context["components"]

        snapshot = CareerHealthSnapshot(
            candidate_id=context["candidate_id"],
            overall_score=context["overall_score"],
            component_scores=components.as_dict(),
            weak_areas=context["weak_areas"],
            todays_priorities=llm_output.todays_priorities,
            advice=llm_output.advice,
        )
        db.add(snapshot)
        db.flush()

        return {
            "careerHealthScore": context["overall_score"],
            "trendDelta": context["trend_delta"],
            "componentScores": components.as_dict(),
            "weakAreas": context["weak_areas"],
            "todaysPriorities": llm_output.todays_priorities,
            "advice": llm_output.advice,
        }


register_engine(CareerHealthEngine())
