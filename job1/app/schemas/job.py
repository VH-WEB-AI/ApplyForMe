import uuid

from pydantic import BaseModel, Field


class JobMatchRequest(BaseModel):
    job_description: str
    job_metadata: dict = Field(default_factory=dict)
    resume_id: uuid.UUID | None = None


class JobMatchResponse(BaseModel):
    match_score: float
    matched_skills: list[str]
    missing_skills: list[str]
    explanation: str
    recommendation: str
    hard_constraints_satisfied: bool
