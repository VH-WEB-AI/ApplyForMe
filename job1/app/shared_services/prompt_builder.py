"""
Shared Prompt Builder service. Centralizes prompt templates + versions so
every engine constructs prompts the same way, and prompt changes are
tracked (feeds "Prompt & Model Versioning" in Observability).
"""
from dataclasses import dataclass
from string import Template


@dataclass
class PromptTemplate:
    name: str
    version: str
    system: str
    user_template: str

    def render(self, **kwargs) -> list[dict[str, str]]:
        user_content = Template(self.user_template).safe_substitute(**kwargs)
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user_content},
        ]


RESUME_SCORING_PROMPT = PromptTemplate(
    name="resume_scoring",
    version="v1",
    system=(
        "You are an expert ATS (Applicant Tracking System) analyst and resume "
        "reviewer. You always respond with strict, valid JSON matching the "
        "requested schema and nothing else."
    ),
    user_template=(
        "Analyze the following resume text and return JSON with keys: "
        "ats_score (0-100), resume_score (0-100), extracted_skills (array of "
        "strings), work_history (array of {company, title, start, end, summary}), "
        "education (array of {institution, degree, field, year}), "
        "suggestions (array of short actionable strings).\n\n"
        "Resume text:\n$resume_text"
    ),
)

JOB_MATCH_EXPLANATION_PROMPT = PromptTemplate(
    name="job_match_explanation",
    version="v1",
    system=(
        "You are a career matching assistant. You always respond with strict, "
        "valid JSON matching the requested schema and nothing else."
    ),
    user_template=(
        "Given this candidate profile summary:\n$candidate_summary\n\n"
        "And this job description:\n$job_description\n\n"
        "The computed semantic match score is $match_score (0-1). "
        "Return JSON with keys: match_score (echo the given score), "
        "matched_skills (array), missing_skills (array), "
        "explanation (2-3 sentence string), recommendation "
        "('strong_fit'|'possible_fit'|'weak_fit')."
    ),
)

CAREER_HEALTH_PROMPT = PromptTemplate(
    name="career_health_analysis",
    version="v1",
    system=(
        "You are a career analytics engine. You always respond with strict, "
        "valid JSON matching the requested schema and nothing else."
    ),
    user_template=(
        "Given this aggregate candidate activity data (applications, scores, "
        "trends over time):\n$aggregate_data\n\n"
        "Return JSON with keys: career_health_score (0-100), "
        "trend ('improving'|'stable'|'declining'), "
        "weak_areas (array of strings), "
        "benchmarks (object comparing candidate to peer averages), "
        "recommendations (array of short actionable strings)."
    ),
)

COPILOT_SYSTEM_PROMPT = PromptTemplate(
    name="career_copilot_chat",
    version="v1",
    system=(
        "You are an AI Career Copilot. Answer resume questions only using "
        "the retrieved resume context. Never invent achievements, percentages, "
        "experience, companies, tools, or technologies. If information is not "
        "available in the retrieved resume context, say exactly: "
        "'Information not found in the uploaded resume.' Do not ask the user "
        "to upload a resume when resume context is available. Keep answers "
        "precise, professional, and actionable."
    ),
    user_template=(
        "Candidate context:\n$candidate_context\n\n"
        "Relevant retrieved context:\n$retrieved_context\n\n"
        "Conversation so far:\n$conversation_history\n\n"
        "Response instructions:\n$response_instructions\n\n"
        "User message: $user_message"
    ),
)


class PromptBuilder:
    """Facade so engines don't import templates directly — keeps a single
    seam for future features like A/B prompt versions or per-tenant prompts."""

    def build(self, template: PromptTemplate, **kwargs) -> list[dict[str, str]]:
        return template.render(**kwargs)


prompt_builder = PromptBuilder()
