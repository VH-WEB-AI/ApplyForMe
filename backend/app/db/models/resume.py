from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin


class ResumeVersion(TimestampMixin, Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)

    file_name: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    sections: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    scores: Mapped[list["ResumeScore"]] = relationship(back_populates="resume_version")


class ResumeScore(TimestampMixin, Base):
    __tablename__ = "resume_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    resume_version_id: Mapped[int] = mapped_column(ForeignKey("resume_versions.id"), index=True)

    resume_score: Mapped[int] = mapped_column(Integer)
    ats_score: Mapped[int] = mapped_column(Integer)
    section_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    missing_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    weak_sections: Mapped[list[str]] = mapped_column(JSONB, default=list)
    recommendations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    rewrite_suggestions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    resume_version: Mapped["ResumeVersion"] = relationship(back_populates="scores")
