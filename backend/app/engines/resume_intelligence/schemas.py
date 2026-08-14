import re

from pydantic import BaseModel, Field, field_validator

SECTION_SCORE_KEYS = ("summary", "experience", "education", "skills")


class ResumeLLMOutput(BaseModel):
    tags: list[str] = Field(default_factory=list)
    resume_score: int = 0
    ats_score: int = 0
    section_scores: dict[str, int] = Field(default_factory=dict)
    weak_sections: list[str] = Field(default_factory=list)
    total_experience_years: float = 0.0
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    rewrite_suggestions: dict[str, str] = Field(default_factory=dict)

    # The model is the sole author of every number here now -- these validators
    # are a safety net against out-of-range or malformed values, not a second
    # scoring pass. They clamp/coerce; they never recompute a verdict.
    @field_validator("resume_score", "ats_score")
    @classmethod
    def _clamp_score(cls, v: int) -> int:
        return max(0, min(100, int(v)))

    @field_validator("section_scores")
    @classmethod
    def _clamp_section_scores(cls, v: dict[str, int]) -> dict[str, int]:
        return {str(k): max(0, min(100, int(val))) for k, val in v.items()}

    @field_validator("total_experience_years")
    @classmethod
    def _clamp_experience(cls, v: float) -> float:
        return max(0.0, round(float(v), 1))

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        # Safety net, not a second extraction pass: recovers a copy-pasted
        # "X, Y, Z" line as separate tags instead of one malformed tag,
        # strips PDF-extraction artifacts (stray tabs/control chars), and
        # deduplicates case-insensitively while capping the list -- the LLM
        # still decides *which* tags exist, this just cleans the shape.
        cleaned: list[str] = []
        for raw in v:
            for piece in str(raw).split(","):
                piece = re.sub(r"\s+", " ", piece).strip(" \t\n\r-")
                if piece:
                    cleaned.append(piece)

        seen: set[str] = set()
        deduped: list[str] = []
        for tag in cleaned:
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(tag)
        return deduped[:50]


RESUME_LLM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Up to 50 recruiter/job-matching tags -- be exhaustive, do not "
                "trim genuine items early. Pass (a) exhaustive: every "
                "specific skill/technology/tool/framework/language/platform "
                "literally named in the resume, treating any Skills section as "
                "ground truth. Pass (b): every distinct job title/role/position "
                "the candidate actually held, taken from the resume's own "
                "role/position labels (e.g. 'technical lead', 'lead full stack "
                "engineer') -- literal titles held, never invented ones. Pass (c) "
                "(max 5, lowest priority): broader domain keywords describing "
                "demonstrated hands-on work, never a rephrased job title."
            ),
        },
        "resume_score": {
            "type": "integer",
            "description": "Overall resume quality/impact as a document, 0-100.",
        },
        "ats_score": {
            "type": "integer",
            "description": (
                "How well an Applicant Tracking System would parse and rank this "
                "resume: contact parseability, standard section structure, "
                "keyword/skill match against the target role, formatting/bullet "
                "clarity and explicit date ranges, and an explicit skills section."
            ),
        },
        "section_scores": {
            "type": "object",
            "description": (
                "0-100 quality score for exactly these 4 keys: summary, "
                "experience, education, skills. 0 only if that section is "
                "genuinely absent -- search the whole resume for it under any "
                "heading/wording before concluding it's missing."
            ),
        },
        "weak_sections": {
            "type": "array",
            "items": {"type": "string"},
            "description": "section_scores keys whose value is below 60.",
        },
        "total_experience_years": {
            "type": "number",
            "description": (
                "Total years of professional experience computed from the "
                "actual employment date ranges stated in the resume (use the "
                "given current date for 'Present'/'Current'). 0 if no dates "
                "are stated anywhere."
            ),
        },
        "education": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Each distinct degree/qualification literally stated in the resume.",
        },
        "certifications": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Each distinct named credential literally stated in the resume "
                "-- never a sentence that merely uses the word 'certification' "
                "or 'certified' in passing. Empty list if none."
            ),
        },
        "missing_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Skills expected for the target role/industry that are absent from the resume.",
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 specific, actionable improvements, each referencing an exact weak section.",
        },
        "rewrite_suggestions": {
            "type": "object",
            "description": "Map of weak section name -> one rewritten example for that section.",
        },
    },
    "required": [
        "tags", "resume_score", "ats_score", "section_scores", "weak_sections",
        "total_experience_years", "education", "certifications",
        "missing_skills", "recommendations", "rewrite_suggestions",
    ],
}
