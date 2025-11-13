"""credits counters and ledger idempotency

Revision ID: 9c1f0d3a8b7e
Revises: 676faa57469d
Create Date: 2025-11-13 00:18:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9c1f0d3a8b7e'
down_revision: Union[str, Sequence[str], None] = '676faa57469d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # org_credits table
    op.create_table(
        'org_credits',
        sa.Column('org_id', sa.UUID(), sa.ForeignKey('organizations.id'), primary_key=True, nullable=False),
        sa.Column('balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # credits_ledger idempotency
    op.add_column('credits_ledger', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
    op.create_unique_constraint('uq_credits_ledger_idempotency', 'credits_ledger', ['org_id', 'idempotency_key'])

    # Backfill org_credits balances from existing ledger
    op.execute(
        """
        INSERT INTO org_credits (org_id, balance, created_at, updated_at)
        SELECT org_id, COALESCE(SUM(delta), 0) AS balance, now(), now()
        FROM credits_ledger
        GROUP BY org_id
        ON CONFLICT (org_id) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_credits_ledger_idempotency', 'credits_ledger', type_='unique')
    op.drop_column('credits_ledger', 'idempotency_key')
    op.drop_table('org_credits')
