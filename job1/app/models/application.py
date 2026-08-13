import uuid
import enum

from sqlalchemy import Enum, Float, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPK


class ApplicationStatus(str, enum.Enum):
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class Application(Base, UUIDPK, TimestampMixin):
    __tablename__ = "applications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"))
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True)

    status: Mapped[ApplicationStatus] = mapped_column(Enum(ApplicationStatus, values_callable=lambda e: [m.value for m in e]), default=ApplicationStatus.SAVED)
    match_score: Mapped[float] = mapped_column(Float, nullable=True)
    match_explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    timeline: Mapped[list] = mapped_column(JSON, default=list)  # status change history

    user: Mapped["User"] = relationship(back_populates="applications")
