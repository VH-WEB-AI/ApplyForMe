"""
Engine 1: Resume Intelligence
Resume parsing & structuring, skill extraction, ATS analysis, resume
scoring, suggestions & improvements.
"""
import re
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.engines.base_engine import BaseEngine
from app.orchestrator.context_manager import CandidateContext
from app.shared_services.json_formatter import json_formatter
from app.shared_services.llm_client import llm_client
from app.shared_services.prompt_builder import RESUME_SCORING_PROMPT, prompt_builder
from app.shared_services.response_validator import response_validator

settings = get_settings()


class WorkHistoryItem(BaseModel):
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    summary: str = ""


class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    year: str = ""


class ResumeIntelligenceOutput(BaseModel):
    ats_score: float = Field(ge=0, le=100)
    resume_score: float = Field(ge=0, le=100)
    extracted_skills: list[str] = Field(default_factory=list)
    work_history: list[WorkHistoryItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ResumeIntelligenceEngine(BaseEngine):
    name = "resume_intelligence"

    async def run(self, payload: dict[str, Any], context: CandidateContext | None = None) -> dict[str, Any]:
        resume_text = payload["resume_text"]
        if not settings.AI_ENABLED:
            validated = self._build_local_output(resume_text)
            return {
                "result": validated.model_dump(),
                "usage": {
                    "model": "local-resume-intelligence",
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "latency_ms": 0,
                },
            }

        messages = prompt_builder.build(RESUME_SCORING_PROMPT, resume_text=resume_text)
        response = await llm_client.chat_completion(
            messages, temperature=0.2, response_format={"type": "json_object"}
        )
        parsed = json_formatter.parse(response.content)
        validated = response_validator.validate(parsed, ResumeIntelligenceOutput)
        usage = {
            "model": response.model,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "latency_ms": response.latency_ms,
        }

        return {
            "result": validated.model_dump(),
            "usage": usage,
        }

    def _build_local_output(self, resume_text: str) -> ResumeIntelligenceOutput:
        text = resume_text or ""
        lower = text.lower()
        words = re.findall(r"[a-zA-Z][a-zA-Z+#.-]+", text)
        sections = {
            "experience": any(term in lower for term in ["experience", "employment", "work history"]),
            "education": "education" in lower,
            "skills": "skills" in lower,
            "projects": "projects" in lower,
        }
        action_verbs = [
            "built",
            "developed",
            "designed",
            "implemented",
            "improved",
            "led",
            "managed",
            "optimized",
            "created",
            "deployed",
        ]
        has_metrics = bool(re.search(r"\b\d+(\.\d+)?\s*(%|percent|x|k|m|users|clients|projects)\b", lower))
        verb_hits = sum(1 for verb in action_verbs if verb in lower)

        known_skills = [
            "python",
            "java",
            "javascript",
            "typescript",
            "react",
            "next.js",
            "node.js",
            "fastapi",
            "django",
            "flask",
            "sql",
            "postgresql",
            "mongodb",
            "redis",
            "docker",
            "kubernetes",
            "aws",
            "azure",
            "gcp",
            "machine learning",
            "deep learning",
            "tensorflow",
            "pytorch",
            "scikit-learn",
            "nlp",
            "llm",
            "rag",
            "git",
            "linux",
        ]
        extracted_skills = []
        for skill in known_skills:
            pattern = r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])"
            if re.search(pattern, lower):
                extracted_skills.append(skill)

        length_score = min(25, len(words) / 20)
        section_score = sum(sections.values()) * 10
        skill_score = min(20, len(extracted_skills) * 2)
        impact_score = min(25, verb_hits * 3 + (10 if has_metrics else 0))
        resume_score = round(min(100, length_score + section_score + skill_score + impact_score), 1)
        ats_score = round(min(100, resume_score + (10 if extracted_skills else -5)), 1)

        suggestions = []
        if not sections["skills"]:
            suggestions.append("Add a dedicated skills section with role-specific keywords.")
        if not has_metrics:
            suggestions.append("Add measurable outcomes, such as percentages, scale, revenue, users, or time saved.")
        if not sections["projects"] and any(skill in extracted_skills for skill in ["machine learning", "llm", "rag"]):
            suggestions.append("Include project examples that show how you applied your AI/ML skills.")
        if len(words) < 300:
            suggestions.append("Add more detail to your experience bullets so recruiters can assess scope and impact.")
        if not suggestions:
            suggestions.append("Tune keywords and accomplishments for each target job description.")

        return ResumeIntelligenceOutput(
            ats_score=ats_score,
            resume_score=resume_score,
            extracted_skills=extracted_skills,
            work_history=[],
            education=[],
            suggestions=suggestions,
        )


resume_intelligence_engine = ResumeIntelligenceEngine()
