import uuid

from pydantic import BaseModel, Field


class CopilotMessageRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str


class CopilotMessageResponse(BaseModel):
    conversation_id: uuid.UUID
    reply: str
    suggested_actions: list[str] = Field(default_factory=list)
