"""add confidence to extracted_fields

Revision ID: 0f0b2b0e8c1a
Revises: 8d7a1e2c3b45
Create Date: 2025-11-15 17:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0f0b2b0e8c1a"
down_revision: Union[str, Sequence[str], None] = "8d7a1e2c3b45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("extracted_fields", sa.Column("confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("extracted_fields", "confidence")
