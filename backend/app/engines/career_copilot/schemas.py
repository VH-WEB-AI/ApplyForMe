from pydantic import BaseModel, Field


class CopilotLLMOutput(BaseModel):
    answer: str
    follow_up_suggestions: list[str] = Field(default_factory=list)


COPILOT_LLM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "follow_up_suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "follow_up_suggestions"],
}
