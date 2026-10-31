"""add world_snapshot_json/updated_at to clubs and players

Revision ID: b3e6f2a1d754
Revises: c3d9e1a5b208
Create Date: 2026-07-21 00:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b3e6f2a1d754"
down_revision = "c3d9e1a5b208"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("clubs") as batch_op:
        batch_op.add_column(sa.Column("world_snapshot_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("world_snapshot_updated_at", sa.DateTime(), nullable=True)
        )
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column("world_snapshot_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("world_snapshot_updated_at", sa.DateTime(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("world_snapshot_updated_at")
        batch_op.drop_column("world_snapshot_json")
    with op.batch_alter_table("clubs") as batch_op:
        batch_op.drop_column("world_snapshot_updated_at")
        batch_op.drop_column("world_snapshot_json")
