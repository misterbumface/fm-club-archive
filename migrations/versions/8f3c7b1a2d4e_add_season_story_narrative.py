"""Add generated season story narrative fields

Revision ID: 8f3c7b1a2d4e
Revises: 4c9a59d50563
Create Date: 2026-07-18 01:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8f3c7b1a2d4e"
down_revision = "4c9a59d50563"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("seasons", schema=None) as batch_op:
        batch_op.add_column(sa.Column("story_narrative", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("story_narrative_generated_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(sa.Column("story_narrative_model", sa.String(50), nullable=True))


def downgrade():
    with op.batch_alter_table("seasons", schema=None) as batch_op:
        batch_op.drop_column("story_narrative_model")
        batch_op.drop_column("story_narrative_generated_at")
        batch_op.drop_column("story_narrative")
