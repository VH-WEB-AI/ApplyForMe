"""add resume_uploads table and link ai_response_logs to it

Revision ID: f3a9d2e6b714
Revises: e7b2c4d18f53
Create Date: 2026-09-15 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3a9d2e6b714'
down_revision: Union[str, None] = 'e7b2c4d18f53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('resume_uploads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('endpoint', sa.String(length=50), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=True),
    sa.Column('resume_version_id', sa.Integer(), nullable=True),
    sa.Column('file_name', sa.String(length=500), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('target_role', sa.String(length=255), nullable=False),
    sa.Column('target_industry', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('cached', sa.Boolean(), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('embedding_tokens', sa.Integer(), nullable=False),
    sa.Column('total_tokens', sa.Integer(), nullable=False),
    sa.Column('api_calls', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidate_profiles.id'], ),
    sa.ForeignKeyConstraint(['resume_version_id'], ['resume_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resume_uploads_endpoint'), 'resume_uploads', ['endpoint'], unique=False)
    op.create_index(op.f('ix_resume_uploads_candidate_id'), 'resume_uploads', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_resume_uploads_resume_version_id'), 'resume_uploads', ['resume_version_id'], unique=False)
    op.create_index(op.f('ix_resume_uploads_content_hash'), 'resume_uploads', ['content_hash'], unique=False)

    op.add_column('ai_response_logs', sa.Column('resume_upload_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'ai_response_logs_resume_upload_id_fkey', 'ai_response_logs', 'resume_uploads',
        ['resume_upload_id'], ['id'],
    )
    op.create_index(op.f('ix_ai_response_logs_resume_upload_id'), 'ai_response_logs',
                    ['resume_upload_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ai_response_logs_resume_upload_id'), table_name='ai_response_logs')
    op.drop_constraint('ai_response_logs_resume_upload_id_fkey', 'ai_response_logs', type_='foreignkey')
    op.drop_column('ai_response_logs', 'resume_upload_id')
    op.drop_index(op.f('ix_resume_uploads_content_hash'), table_name='resume_uploads')
    op.drop_index(op.f('ix_resume_uploads_resume_version_id'), table_name='resume_uploads')
    op.drop_index(op.f('ix_resume_uploads_candidate_id'), table_name='resume_uploads')
    op.drop_index(op.f('ix_resume_uploads_endpoint'), table_name='resume_uploads')
    op.drop_table('resume_uploads')
