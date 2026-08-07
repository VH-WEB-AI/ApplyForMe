"""create the ApplyForMe application schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02

The original migration enables pgvector, but does not create the tables
registered in ``Base.metadata``.  Keeping this as a follow-up migration makes
fresh Docker and local installs usable without rewriting migration history.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


USER_ROLE = postgresql.ENUM("candidate", "admin", name="userrole", create_type=False)
RESUME_STATUS = postgresql.ENUM("uploaded", "parsing", "parsed", "scored", "failed", name="resumestatus", create_type=False)
APPLICATION_STATUS = postgresql.ENUM(
    "saved",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
    name="applicationstatus",
    create_type=False,
)
MESSAGE_ROLE = postgresql.ENUM("user", "assistant", "system", name="messagerole", create_type=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    USER_ROLE.create(bind, checkfirst=True)
    RESUME_STATUS.create(bind, checkfirst=True)
    APPLICATION_STATUS.create(bind, checkfirst=True)
    MESSAGE_ROLE.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("role", USER_ROLE, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "candidate_profiles",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("headline", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("years_experience", sa.Integer(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "resumes",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("status", RESUME_STATUS, nullable=False),
        sa.Column("structured_data", sa.JSON(), nullable=False),
        sa.Column("extracted_skills", sa.JSON(), nullable=False),
        sa.Column("ats_score", sa.Float(), nullable=True),
        sa.Column("resume_score", sa.Float(), nullable=True),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("embedding", Vector(3072), nullable=True),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "job_descriptions",
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("company", sa.String(500), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("salary_range", sa.JSON(), nullable=False),
        sa.Column("visa_sponsorship", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("embedding", Vector(3072), nullable=True),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "applications",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.UUID(), sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_id", sa.UUID(), sa.ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", APPLICATION_STATUS, nullable=False),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("match_explanation", sa.JSON(), nullable=False),
        sa.Column("timeline", sa.JSON(), nullable=False),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "conversations",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("conversation_id", sa.UUID(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", MESSAGE_ROLE, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(3072), nullable=True),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "audit_logs",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("engine", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("conversation_messages")
    op.drop_table("conversations")
    op.drop_table("applications")
    op.drop_table("job_descriptions")
    op.drop_table("resumes")
    op.drop_table("candidate_profiles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    sa.Enum(name="messagerole").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="applicationstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="resumestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="userrole").drop(op.get_bind(), checkfirst=True)
