from pydantic import BaseModel, Field


class CareerHealthLLMOutput(BaseModel):
    advice: str = Field(description="Personalized narrative advice on how to improve career health")
    todays_priorities: list[str] = Field(default_factory=list)


CAREER_HEALTH_LLM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "advice": {"type": "string"},
        "todays_priorities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["advice", "todays_priorities"],
}
