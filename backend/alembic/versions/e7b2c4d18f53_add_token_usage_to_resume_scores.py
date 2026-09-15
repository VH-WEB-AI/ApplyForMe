"""add token_usage to resume_scores

Revision ID: e7b2c4d18f53
Revises: d4e1a7b93c20
Create Date: 2026-09-15 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e7b2c4d18f53'
down_revision: Union[str, None] = 'd4e1a7b93c20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('resume_scores', sa.Column(
        'token_usage', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ))


def downgrade() -> None:
    op.drop_column('resume_scores', 'token_usage')
