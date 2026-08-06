from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")

    profile: Mapped["CandidateProfile"] = relationship(back_populates="user", uselist=False)


class CandidateProfile(TimestampMixin, Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    target_role: Mapped[str | None] = mapped_column(String(255), default=None)
    target_industry: Mapped[str | None] = mapped_column(String(255), default=None)
    experience_level: Mapped[str | None] = mapped_column(String(50), default=None)
    visa_status: Mapped[str | None] = mapped_column(String(100), default=None)
    location: Mapped[str | None] = mapped_column(String(255), default=None)
    desired_salary_min: Mapped[int | None] = mapped_column(default=None)
    desired_salary_max: Mapped[int | None] = mapped_column(default=None)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), default=None)
    github_url: Mapped[str | None] = mapped_column(String(500), default=None)
    portfolio_url: Mapped[str | None] = mapped_column(String(500), default=None)

    user: Mapped["User"] = relationship(back_populates="profile")
