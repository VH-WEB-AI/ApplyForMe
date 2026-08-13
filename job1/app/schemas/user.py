import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CandidateProfileUpdate(BaseModel):
    headline: str | None = None
    location: str | None = None
    years_experience: int | None = None
    skills: list[str] | None = None
    preferences: dict | None = None


class CandidateProfileOut(BaseModel):
    headline: str | None
    location: str | None
    years_experience: int | None
    skills: list[str]
    preferences: dict

    class Config:
        from_attributes = True
