from pydantic import BaseModel, Field


class ResumeLLMOutput(BaseModel):
    missing_skills: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    rewrite_suggestions: dict[str, str] = Field(default_factory=dict)


RESUME_LLM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "missing_skills": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "rewrite_suggestions": {
            "type": "object",
            "description": "Map of weak section name -> a rewritten example for that section",
        },
    },
    "required": ["missing_skills", "recommendations", "rewrite_suggestions"],
}
