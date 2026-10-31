"""add club curator context

Revision ID: d8f5a2c94e31
Revises: c6e4a91d7b20
Create Date: 2026-07-23 01:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d8f5a2c94e31"
down_revision = "c6e4a91d7b20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("clubs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("profile_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("historical_context", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("current_manager_name", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("stadium_photo_path", sa.String(length=500), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("clubs", schema=None) as batch_op:
        batch_op.drop_column("stadium_photo_path")
        batch_op.drop_column("current_manager_name")
        batch_op.drop_column("historical_context")
        batch_op.drop_column("profile_summary")
