from pydantic import BaseModel, Field


class JobMatchLLMOutput(BaseModel):
    explanation: str = Field(description="Plain-language explanation of why this job matches (or doesn't)")
    resume_changes: list[str] = Field(default_factory=list)


JOB_MATCH_LLM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "resume_changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["explanation", "resume_changes"],
}
