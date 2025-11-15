"""template generation jobs

Revision ID: a581c27ddc9b
Revises: 4bb760a34147
Create Date: 2025-11-14 18:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a581c27ddc9b'
down_revision: Union[str, Sequence[str], None] = '4bb760a34147'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'template_gen_jobs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_by_id', sa.UUID(), nullable=False),
        sa.Column('pdf_url', sa.String(length=1024), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='queued'),
        sa.Column('error_message', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('template_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['template_id'], ['document_templates.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_template_gen_jobs_org_id', 'template_gen_jobs', ['org_id'], unique=False)
    op.create_index('ix_template_gen_jobs_status', 'template_gen_jobs', ['status'], unique=False)
    op.create_index('ix_template_gen_jobs_idempotency', 'template_gen_jobs', ['org_id', 'idempotency_key'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_template_gen_jobs_idempotency', table_name='template_gen_jobs')
    op.drop_index('ix_template_gen_jobs_status', table_name='template_gen_jobs')
    op.drop_index('ix_template_gen_jobs_org_id', table_name='template_gen_jobs')
    op.drop_table('template_gen_jobs')
