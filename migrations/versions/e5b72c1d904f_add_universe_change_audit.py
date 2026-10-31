"""add universe change audit

Revision ID: e5b72c1d904f
Revises: d4a91f0c2e63
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa


revision = "e5b72c1d904f"
down_revision = "d4a91f0c2e63"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "universe_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_universe_changes_entity_id"),
        "universe_changes",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_universe_changes_entity_type"),
        "universe_changes",
        ["entity_type"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_universe_changes_entity_type"), table_name="universe_changes"
    )
    op.drop_index(
        op.f("ix_universe_changes_entity_id"), table_name="universe_changes"
    )
    op.drop_table("universe_changes")
