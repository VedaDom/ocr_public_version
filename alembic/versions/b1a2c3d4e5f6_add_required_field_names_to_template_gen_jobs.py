"""add required_field_names to template_gen_jobs

Revision ID: b1a2c3d4e5f6
Revises: 0f0b2b0e8c1a
Create Date: 2025-11-21 09:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1a2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "0f0b2b0e8c1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "template_gen_jobs",
        sa.Column("required_field_names", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("template_gen_jobs", "required_field_names")
