"""add persistent pre-match previews

Revision ID: f8c4a2d91e70
Revises: b7d31f9a2c44
Create Date: 2026-07-28 20:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f8c4a2d91e70"
down_revision = "b7d31f9a2c44"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("scheduled_fixtures") as batch_op:
        batch_op.add_column(sa.Column("preview_text", sa.Text()))
        batch_op.add_column(sa.Column("preview_source", sa.String(length=30)))
        batch_op.add_column(
            sa.Column("preview_source_filename", sa.String(length=300))
        )

    with op.batch_alter_table("matches") as batch_op:
        batch_op.add_column(sa.Column("preview_text", sa.Text()))
        batch_op.add_column(sa.Column("preview_source", sa.String(length=30)))
        batch_op.add_column(
            sa.Column("preview_source_filename", sa.String(length=300))
        )


def downgrade():
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_column("preview_source_filename")
        batch_op.drop_column("preview_source")
        batch_op.drop_column("preview_text")

    with op.batch_alter_table("scheduled_fixtures") as batch_op:
        batch_op.drop_column("preview_source_filename")
        batch_op.drop_column("preview_source")
        batch_op.drop_column("preview_text")
