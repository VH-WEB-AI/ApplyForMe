"""
Engine 2: Job Match Engine
Job description analysis, semantic matching (embeddings), skill &
experience matching, location/visa/salary match, match score + explanation.
"""
import re
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.engines.base_engine import BaseEngine
from app.orchestrator.context_manager import CandidateContext
from app.shared_services.embedding_service import embedding_service
from app.shared_services.json_formatter import json_formatter
from app.shared_services.llm_client import llm_client
from app.shared_services.prompt_builder import JOB_MATCH_EXPLANATION_PROMPT, prompt_builder
from app.shared_services.response_validator import response_validator

settings = get_settings()
logger = get_logger(__name__)


class JobMatchOutput(BaseModel):
    match_score: float = Field(ge=0, le=1)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    explanation: str = ""
    recommendation: str = "possible_fit"


class JobMatchEngine(BaseEngine):
    name = "job_match"

    async def run(self, payload: dict[str, Any], context: CandidateContext | None = None) -> dict[str, Any]:
        candidate_text = payload["candidate_text"]      # resume text or profile summary
        job_description = payload["job_description"]

        if not settings.AI_ENABLED:
            result = self._build_local_match(candidate_text, job_description, payload, context)
            return {
                "result": result.model_dump() | {"hard_constraints_satisfied": self._check_hard_constraints(payload, context)},
                "usage": self._local_usage(),
            }

        # 1. Semantic matching via embeddings (cosine similarity)
        try:
            candidate_emb, job_emb = await embedding_service.get_embeddings_batch([candidate_text, job_description])
            semantic_score = embedding_service.cosine_similarity(candidate_emb, job_emb)
            semantic_score = max(0.0, min(1.0, (semantic_score + 1) / 2))  # normalize [-1,1] -> [0,1]
        except Exception as exc:
            logger.warning("job_match_embedding_fallback", error=str(exc))
            semantic_score = self._lexical_similarity(candidate_text, job_description)

        # 2. Structured / visa / salary / location constraints (deterministic, no LLM)
        constraints_ok = self._check_hard_constraints(payload, context)

        # 3. LLM explanation layer grounded in the computed score
        try:
            candidate_summary = context.as_prompt_summary() if context else candidate_text[:500]
            messages = prompt_builder.build(
                JOB_MATCH_EXPLANATION_PROMPT,
                candidate_summary=candidate_summary,
                job_description=job_description,
                match_score=f"{semantic_score:.2f}",
            )
            response = await llm_client.chat_completion(
                messages, temperature=0.2, response_format={"type": "json_object"}
            )
            parsed = json_formatter.parse(response.content)
            parsed["match_score"] = semantic_score
            validated = response_validator.validate(parsed, JobMatchOutput)
            usage = {
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_ms": response.latency_ms,
            }
        except Exception as exc:
            logger.warning("job_match_explanation_fallback", error=str(exc))
            validated = self._build_local_match(candidate_text, job_description, payload, context, semantic_score)
            usage = self._local_usage()

        result = validated.model_dump()
        result["hard_constraints_satisfied"] = constraints_ok

        return {
            "result": result,
            "usage": usage,
        }

    def _check_hard_constraints(
        self, payload: dict[str, Any], context: CandidateContext | None = None
    ) -> bool:
        # Preferences belong to the candidate profile loaded by ContextManager.
        # An explicit payload value is still accepted for trusted internal callers.
        prefs = payload.get("candidate_preferences") or (context.profile.get("preferences", {}) if context else {})
        job_meta = payload.get("job_metadata", {})

        if prefs.get("requires_visa_sponsorship") and not job_meta.get("visa_sponsorship", False):
            return False

        min_salary = prefs.get("min_salary")
        job_salary_max = (job_meta.get("salary_range") or {}).get("max")
        if min_salary and job_salary_max and job_salary_max < min_salary:
            return False

        return True

    def _build_local_match(
        self,
        candidate_text: str,
        job_description: str,
        payload: dict[str, Any],
        context: CandidateContext | None = None,
        score: float | None = None,
    ) -> JobMatchOutput:
        matched_skills, missing_skills = self._skill_overlap(candidate_text, job_description)
        match_score = score if score is not None else self._lexical_similarity(candidate_text, job_description)
        constraints_ok = self._check_hard_constraints(payload, context)
        if not constraints_ok:
            match_score = min(match_score, 0.65)

        if match_score >= 0.75:
            recommendation = "strong_fit"
            explanation = "Strong overlap between your resume and the job description."
        elif match_score >= 0.5:
            recommendation = "possible_fit"
            explanation = "Some relevant overlap exists, but a few gaps should be addressed before applying."
        else:
            recommendation = "stretch"
            explanation = "This looks like a stretch match based on the current resume and job description."

        if matched_skills:
            explanation += f" Matched skills include {', '.join(matched_skills[:5])}."
        if missing_skills:
            explanation += f" Consider highlighting or learning {', '.join(missing_skills[:5])}."
        if not constraints_ok:
            explanation += " One or more hard preferences such as visa or salary may not be satisfied."

        return JobMatchOutput(
            match_score=round(max(0.0, min(1.0, match_score)), 3),
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            explanation=explanation,
            recommendation=recommendation,
        )

    def _lexical_similarity(self, candidate_text: str, job_description: str) -> float:
        candidate_tokens = self._tokens(candidate_text)
        job_tokens = self._tokens(job_description)
        if not candidate_tokens or not job_tokens:
            return 0.0
        overlap = candidate_tokens & job_tokens
        return min(1.0, (len(overlap) / len(job_tokens)) * 1.8)

    def _skill_overlap(self, candidate_text: str, job_description: str) -> tuple[list[str], list[str]]:
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
        candidate_lower = candidate_text.lower()
        job_lower = job_description.lower()
        job_skills = [skill for skill in known_skills if self._contains_skill(job_lower, skill)]
        matched = [skill for skill in job_skills if self._contains_skill(candidate_lower, skill)]
        missing = [skill for skill in job_skills if skill not in matched]
        return matched, missing

    @staticmethod
    def _contains_skill(text: str, skill: str) -> bool:
        pattern = r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])"
        return bool(re.search(pattern, text))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stopwords = {
            "and", "the", "with", "for", "you", "your", "our", "are", "will", "this",
            "that", "from", "have", "has", "into", "using", "role", "work", "team",
        }
        return {
            token
            for token in re.findall(r"[a-zA-Z][a-zA-Z+#.-]{2,}", text.lower())
            if token not in stopwords
        }

    @staticmethod
    def _local_usage() -> dict[str, Any]:
        return {
            "model": "local-job-match",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
        }


job_match_engine = JobMatchEngine()
