"""Prompt Version Manager: fetches the active prompt template for an engine, so prompt
copy can evolve (new PromptVersion rows) without redeploying code."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.ai_ops import PromptVersion

DEFAULT_VERSION = "v1"


def get_active_template(db: Session, engine: str, default_template: str) -> tuple[str, str]:
    """Returns (template, version). Falls back to `default_template`/DEFAULT_VERSION
    when no active PromptVersion row exists for this engine yet."""
    row = db.scalar(
        select(PromptVersion)
        .where(PromptVersion.engine == engine, PromptVersion.is_active.is_(True))
        .order_by(PromptVersion.id.desc())
    )
    if row:
        return row.template, row.version
    return default_template, DEFAULT_VERSION


def get_active_prompt_version_id(db: Session, engine: str) -> int | None:
    row = db.scalar(
        select(PromptVersion)
        .where(PromptVersion.engine == engine, PromptVersion.is_active.is_(True))
        .order_by(PromptVersion.id.desc())
    )
    return row.id if row else None
