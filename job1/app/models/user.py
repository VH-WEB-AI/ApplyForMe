import uuid
import enum

from sqlalchemy import Boolean, Enum, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPK


class UserRole(str, enum.Enum):
    CANDIDATE = "candidate"
    ADMIN = "admin"


class User(Base, UUIDPK, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, values_callable=lambda e: [m.value for m in e]), default=UserRole.CANDIDATE, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    profile: Mapped["CandidateProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    resumes: Mapped[list["Resume"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    applications: Mapped[list["Application"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class CandidateProfile(Base, UUIDPK, TimestampMixin):
    __tablename__ = "candidate_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    headline: Mapped[str] = mapped_column(String(255), nullable=True)
    location: Mapped[str] = mapped_column(String(255), nullable=True)
    years_experience: Mapped[int] = mapped_column(nullable=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)  # visa, salary, location prefs

    user: Mapped["User"] = relationship(back_populates="profile")
