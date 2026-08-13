import uuid

from sqlalchemy import Float, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPK


class AuditLog(Base, UUIDPK, TimestampMixin):
    """
    Records every AI Orchestrator invocation for observability, cost tracking,
    and prompt/model versioning (feeds section 11 - Observability & Ops).
    """

    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    engine: Mapped[str] = mapped_column(String(100))          # e.g. "resume_intelligence"
    action: Mapped[str] = mapped_column(String(100))          # e.g. "score_resume"
    request_id: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(nullable=True)
    completion_tokens: Mapped[int] = mapped_column(nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="success")  # success | error
    error_message: Mapped[str] = mapped_column(String(2000), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
