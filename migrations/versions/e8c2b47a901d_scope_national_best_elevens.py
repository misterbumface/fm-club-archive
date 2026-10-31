"""scope national Best Elevens

Revision ID: e8c2b47a901d
Revises: d3b7a91c4e20
Create Date: 2026-07-19 22:43:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e8c2b47a901d"
down_revision = "d3b7a91c4e20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("national_selections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "selection_scope", sa.String(length=20),
                nullable=False, server_default="annual",
            )
        )


def downgrade():
    with op.batch_alter_table("national_selections") as batch_op:
        batch_op.drop_column("selection_scope")
