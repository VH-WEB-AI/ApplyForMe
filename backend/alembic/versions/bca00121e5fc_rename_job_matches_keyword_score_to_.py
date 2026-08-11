"""rename job_matches keyword_score to tags_score

Revision ID: bca00121e5fc
Revises: a60c15bdb213
Create Date: 2026-08-11 16:40:47.297801

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bca00121e5fc'
down_revision: Union[str, None] = 'a60c15bdb213'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # True rename, not drop+add -- autogenerate can't detect renames and would
    # otherwise silently discard every existing row's recorded score.
    op.alter_column('job_matches', 'keyword_score', new_column_name='tags_score')


def downgrade() -> None:
    op.alter_column('job_matches', 'tags_score', new_column_name='keyword_score')
