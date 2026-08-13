import uuid
from datetime import datetime

from pydantic import BaseModel


class ResumeOut(BaseModel):
    id: uuid.UUID
    file_name: str
    status: str
    ats_score: float | None
    resume_score: float | None
    structured_data: dict
    extracted_skills: list[str]
    suggestions: list[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ResumeScoreResponse(BaseModel):
    resume_id: uuid.UUID
    ats_score: float
    resume_score: float
    extracted_skills: list[str]
    suggestions: list[str]
