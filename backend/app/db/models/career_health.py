from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin


class CareerHealthSnapshot(TimestampMixin, Base):
    __tablename__ = "career_health_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)

    overall_score: Mapped[int] = mapped_column(Integer)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    weak_areas: Mapped[list[str]] = mapped_column(JSONB, default=list)
    todays_priorities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    advice: Mapped[str] = mapped_column(Text, default="")


class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)

    engine: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="open")


class Feedback(TimestampMixin, Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)
    engine: Mapped[str] = mapped_column(String(100))
    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id"), default=None
    )

    rating: Mapped[int | None] = mapped_column(Integer, default=None)
    comment: Mapped[str | None] = mapped_column(Text, default=None)
