"""add resume_version_id and total_tokens to ai_response_logs

Revision ID: d4e1a7b93c20
Revises: c80ab1bbc979
Create Date: 2026-09-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4e1a7b93c20'
down_revision: Union[str, None] = 'c80ab1bbc979'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_response_logs', sa.Column('resume_version_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'ai_response_logs_resume_version_id_fkey', 'ai_response_logs', 'resume_versions',
        ['resume_version_id'], ['id'],
    )
    op.create_index(op.f('ix_ai_response_logs_resume_version_id'), 'ai_response_logs',
                    ['resume_version_id'], unique=False)
    op.add_column('ai_response_logs',
                  sa.Column('total_tokens', sa.Integer(), nullable=False, server_default='0'))
    op.execute("UPDATE ai_response_logs SET total_tokens = prompt_tokens + completion_tokens")


def downgrade() -> None:
    op.drop_column('ai_response_logs', 'total_tokens')
    op.drop_index(op.f('ix_ai_response_logs_resume_version_id'), table_name='ai_response_logs')
    op.drop_constraint('ai_response_logs_resume_version_id_fkey', 'ai_response_logs', type_='foreignkey')
    op.drop_column('ai_response_logs', 'resume_version_id')
