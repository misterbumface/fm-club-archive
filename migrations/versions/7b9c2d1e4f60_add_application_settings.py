"""add application settings

Revision ID: 7b9c2d1e4f60
Revises: f21c88d00a74
Create Date: 2026-07-23 17:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "7b9c2d1e4f60"
down_revision = "f21c88d00a74"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_app_settings_key"), ["key"], unique=True
        )


def downgrade():
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_app_settings_key"))
    op.drop_table("app_settings")

