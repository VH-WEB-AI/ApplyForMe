import uuid
import enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import Enum, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin, UUIDPK

settings = get_settings()


class ResumeStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    SCORED = "scored"
    FAILED = "failed"


class Resume(Base, UUIDPK, TimestampMixin):
    __tablename__ = "resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    file_name: Mapped[str] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(1000))
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)

    status: Mapped[ResumeStatus] = mapped_column(Enum(ResumeStatus, values_callable=lambda e: [m.value for m in e]), default=ResumeStatus.UPLOADED)

    # Structured extraction output (Engine 1: Resume Intelligence)
    structured_data: Mapped[dict] = mapped_column(JSON, default=dict)  # sections, work history, education, etc.
    extracted_skills: Mapped[list] = mapped_column(JSON, default=list)
    ats_score: Mapped[float] = mapped_column(Float, nullable=True)
    resume_score: Mapped[float] = mapped_column(Float, nullable=True)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)

    # Embedding for semantic job matching / RAG (pgvector)
    embedding: Mapped[list] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)

    user: Mapped["User"] = relationship(back_populates="resumes")


class JobDescription(Base, UUIDPK, TimestampMixin):
    __tablename__ = "job_descriptions"

    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(500), nullable=True)
    location: Mapped[str] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[list] = mapped_column(JSON, default=list)
    salary_range: Mapped[dict] = mapped_column(JSON, default=dict)
    visa_sponsorship: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String(255), nullable=True)  # e.g. "linkedin", "manual"
    embedding: Mapped[list] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)
