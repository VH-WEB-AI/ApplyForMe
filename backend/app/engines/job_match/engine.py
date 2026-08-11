from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core import CandidateProfile
from app.db.models.jobs import JobMatch, JobPosting
from app.db.models.resume import ResumeScore, ResumeVersion
from app.engines.job_match import analysis
from app.engines.job_match.schemas import JOB_MATCH_LLM_JSON_SCHEMA, JobMatchLLMOutput
from app.orchestrator.engine_base import Engine
from app.orchestrator.registry import register_engine
from app.services import embedding_generator
from app.services.experience_estimator import estimate_total_experience_years
from app.services.pii_redaction import redact_pii
from app.services.prompt_builder import PromptSpec
from app.services.skill_extractor import extract_skills

SYSTEM_PROMPT = (
    "You are the Job Match Engine inside ApplyForMe's Career Command Center. "
    "Every match score you see was already computed deterministically -- your only "
    "job is to explain it in plain language and suggest resume changes."
)

BUSINESS_RULES = [
    "Never change or contradict the provided match_score or component scores.",
    "Reference the actual missing_skills and component scores in your explanation.",
    "resume_changes must be specific, actionable edits tied to the missing skills or weak components.",
]


def _latest_resume_version(db: Session, candidate_id: int) -> ResumeVersion:
    resume_version = db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.candidate_id == candidate_id)
        .order_by(ResumeVersion.id.desc())
    )
    if resume_version is None:
        raise ValueError(f"Candidate {candidate_id} has no resume on file yet.")
    return resume_version


def _latest_resume_score(db: Session, resume_version_id: int) -> int:
    score_row = db.scalar(
        select(ResumeScore)
        .where(ResumeScore.resume_version_id == resume_version_id)
        .order_by(ResumeScore.id.desc())
    )
    return score_row.resume_score if score_row else 0


def _job_relevant_text(sections: dict[str, str]) -> str:
    """Full resume text minus the header (name/email/phone/links) — pure contact-info
    noise that carries no job-fit signal but dilutes the keyword overlap against the
    job description."""
    return "\n".join(text for name, text in sections.items() if name != "header")


class JobMatchEngine(Engine):
    name = "job_match"
    response_schema = JobMatchLLMOutput

    def gather_context(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = payload["candidate_id"]
        job_posting_id = payload["job_posting_id"]

        job = db.get(JobPosting, job_posting_id)
        if job is None or not job.is_active:
            raise ValueError(f"Job posting {job_posting_id} not found or inactive.")

        resume_version = _latest_resume_version(db, candidate_id)
        resume_score = _latest_resume_score(db, resume_version.id)

        candidate_profile = db.get(CandidateProfile, candidate_id)
        if candidate_profile is None:
            raise ValueError(f"Candidate profile {candidate_id} not found.")

        candidate_skills = extract_skills(resume_version.raw_text)
        candidate_years = estimate_total_experience_years(
            resume_version.sections.get("experience", "")
        )

        job_relevant_text = _job_relevant_text(resume_version.sections)

        scores = analysis.compute(
            resume_text=job_relevant_text,
            job_description=job.description,
            candidate_skills=candidate_skills,
            required_skills=job.required_skills,
            candidate_years=candidate_years,
            min_required_years=job.min_experience_years,
            candidate_location=candidate_profile.location,
            job_location=job.location,
            job_remote=job.remote,
            candidate_visa_status=candidate_profile.visa_status,
            job_visa_sponsorship=job.visa_sponsorship,
            candidate_salary_min=candidate_profile.desired_salary_min,
            candidate_salary_max=candidate_profile.desired_salary_max,
            job_salary_min=job.salary_min,
            job_salary_max=job.salary_max,
            resume_score=resume_score,
        )

        return {
            "candidate_id": candidate_id,
            "job_posting_id": job_posting_id,
            "resume_version_id": resume_version.id,
            "job_content_hash": embedding_generator.content_hash(job.description),
            "job_title": job.title,
            "job_company": job.company,
            "redacted_resume_text": redact_pii(resume_version.raw_text),
            "job_description": job.description,
            "scores": scores,
        }

    def cache_key(self, context: dict[str, Any]) -> str | None:
        # Same resume + same job content => identical scores/explanation; skip a redundant LLM call.
        return f"{context['resume_version_id']}:{context['job_posting_id']}:{context['job_content_hash']}"

    def build_prompt_spec(self, context: dict[str, Any]) -> PromptSpec:
        scores: analysis.JobMatchScores = context["scores"]
        instructions = (
            "Given the deterministic match scores below and the resume/job description, "
            "write a short explanation of why this job matches (or doesn't) for the candidate, "
            "and 2-5 specific resume_changes that would improve the match."
        )
        return PromptSpec(
            system_prompt=SYSTEM_PROMPT,
            business_rules=BUSINESS_RULES,
            engine_instructions=instructions,
            json_schema=JOB_MATCH_LLM_JSON_SCHEMA,
            candidate_context={
                "job_title": context["job_title"],
                "job_company": context["job_company"],
                "match_score": scores.match_score,
                "keyword_score": scores.keyword_score,
                "experience_score": scores.experience_score,
                "location_score": scores.location_score,
                "visa_score": scores.visa_score,
                "salary_score": scores.salary_score,
                "missing_skills": scores.missing_skills,
                "priority_badge": scores.priority_badge,
                "interview_readiness": scores.interview_readiness,
            },
            extra_context={
                "resume_text": context["redacted_resume_text"],
                "job_description": context["job_description"],
            },
        )

    def postprocess(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
        llm_output: JobMatchLLMOutput,
    ) -> dict[str, Any]:
        scores: analysis.JobMatchScores = context["scores"]

        match_row = JobMatch(
            candidate_id=context["candidate_id"],
            job_posting_id=context["job_posting_id"],
            resume_version_id=context["resume_version_id"],
            match_score=scores.match_score,
            keyword_score=scores.keyword_score,
            experience_score=scores.experience_score,
            location_score=scores.location_score,
            visa_score=scores.visa_score,
            salary_score=scores.salary_score,
            missing_skills=scores.missing_skills,
            resume_changes=llm_output.resume_changes,
            interview_readiness=scores.interview_readiness,
            priority_badge=scores.priority_badge,
            explanation=llm_output.explanation,
        )
        db.add(match_row)
        db.flush()

        return {
            "jobPostingId": context["job_posting_id"],
            "jobTitle": context["job_title"],
            "jobCompany": context["job_company"],
            "matchScore": scores.match_score,
            "keywordScore": scores.keyword_score,
            "experienceScore": scores.experience_score,
            "locationScore": scores.location_score,
            "visaScore": scores.visa_score,
            "salaryScore": scores.salary_score,
            "missingSkills": scores.missing_skills,
            "resumeChanges": llm_output.resume_changes,
            "interviewReadiness": scores.interview_readiness,
            "priorityBadge": scores.priority_badge,
            "explanation": llm_output.explanation,
        }


register_engine(JobMatchEngine())
