from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.resume import ResumeScore, ResumeVersion
from app.engines.resume_intelligence import analysis
from app.engines.resume_intelligence.schemas import RESUME_LLM_JSON_SCHEMA, ResumeLLMOutput
from app.orchestrator.engine_base import Engine
from app.orchestrator.registry import register_engine
from app.services import embedding_generator
from app.services.pii_redaction import redact_pii
from app.services.prompt_builder import PromptSpec
from app.services.resume_parser import parse_resume

SYSTEM_PROMPT = (
    "You are the Resume Intelligence Engine inside ApplyForMe's Career Command Center. "
    "You analyze real resumes for real job seekers. You are not a general-purpose chatbot: "
    "every suggestion must be grounded in the resume text and target role provided."
)

BUSINESS_RULES = [
    "Do not invent skills, employers, or experience that are not present in the resume text.",
    "Recommendations must be specific and actionable (reference the exact weak section).",
    "missing_skills should reflect skills expected for the target role/industry but absent from the resume.",
    "rewrite_suggestions keys must be section names from the weak_sections list provided.",
]


class ResumeIntelligenceEngine(Engine):
    name = "resume_intelligence"
    response_schema = ResumeLLMOutput

    def gather_context(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = payload["candidate_id"]
        target_role = payload.get("target_role") or ""
        target_industry = payload.get("target_industry") or ""

        parsed = parse_resume(payload["file_bytes"], payload["filename"])
        content_hash = embedding_generator.content_hash(parsed.raw_text)

        resume_version = db.scalar(
            select(ResumeVersion)
            .where(
                ResumeVersion.candidate_id == candidate_id,
                ResumeVersion.content_hash == content_hash,
            )
            .order_by(ResumeVersion.id.desc())
        )
        if resume_version is None:
            resume_version = ResumeVersion(
                candidate_id=candidate_id,
                file_name=payload["filename"],
                content_hash=content_hash,
                raw_text=parsed.raw_text,
                sections=parsed.sections,
                parsed_data={},
            )
            db.add(resume_version)
            db.flush()

        target_text = f"{target_role} {target_industry}".strip()
        result = analysis.analyze(parsed.sections, raw_text=parsed.raw_text, target_role_text=target_text)

        embedding_generator.get_or_create_embedding(
            db, owner_type="resume", owner_id=resume_version.id, text=parsed.raw_text
        )

        return {
            "candidate_id": candidate_id,
            "resume_version_id": resume_version.id,
            "content_hash": content_hash,
            "target_role": target_role,
            "target_industry": target_industry,
            "redacted_resume_text": redact_pii(parsed.raw_text),
            "sections": parsed.sections,
            "resume_score": result.resume_score,
            "ats_score": result.ats_score,
            "section_scores": result.section_scores,
            "weak_sections": result.weak_sections,
            "total_experience_years": result.total_experience_years,
            "education": result.education,
            "certifications": result.certifications,
        }

    def cache_key(self, context: dict[str, Any]) -> str | None:
        # Same resume + same target role => identical analysis; skip a redundant LLM call.
        return f"{context['content_hash']}:{context['target_role']}:{context['target_industry']}"

    def build_prompt_spec(self, context: dict[str, Any]) -> PromptSpec:
        instructions = (
            "Given the resume text, deterministic scores, and target role below, produce: "
            "missing_skills (skills expected for the target role/industry that are absent), "
            "recommendations (3-6 specific, actionable improvements), and rewrite_suggestions "
            "(one improved example per weak section)."
        )
        return PromptSpec(
            system_prompt=SYSTEM_PROMPT,
            business_rules=BUSINESS_RULES,
            engine_instructions=instructions,
            json_schema=RESUME_LLM_JSON_SCHEMA,
            candidate_context={
                "target_role": context["target_role"],
                "target_industry": context["target_industry"],
                "resume_score": context["resume_score"],
                "ats_score": context["ats_score"],
                "section_scores": context["section_scores"],
                "weak_sections": context["weak_sections"],
                "total_experience_years": context["total_experience_years"],
                "certifications": context["certifications"],
            },
            extra_context={"resume_text": context["redacted_resume_text"]},
        )

    def postprocess(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
        llm_output: ResumeLLMOutput,
    ) -> dict[str, Any]:
        score_row = ResumeScore(
            resume_version_id=context["resume_version_id"],
            resume_score=context["resume_score"],
            ats_score=context["ats_score"],
            section_scores=context["section_scores"],
            missing_skills=llm_output.missing_skills,
            weak_sections=context["weak_sections"],
            recommendations=llm_output.recommendations,
            rewrite_suggestions=llm_output.rewrite_suggestions,
        )
        db.add(score_row)
        db.flush()

        return {
            "resumeVersionId": context["resume_version_id"],
            "resumeScore": context["resume_score"],
            "atsScore": context["ats_score"],
            "sectionScores": context["section_scores"],
            "weakSections": context["weak_sections"],
            "missingSkills": llm_output.missing_skills,
            "recommendations": llm_output.recommendations,
            "rewriteSuggestions": llm_output.rewrite_suggestions,
            "totalExperienceYears": context["total_experience_years"],
            "education": context["education"],
            "certifications": context["certifications"],
        }


register_engine(ResumeIntelligenceEngine())
