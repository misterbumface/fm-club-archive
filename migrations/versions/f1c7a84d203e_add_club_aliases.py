"""add persistent club aliases

Revision ID: f1c7a84d203e
Revises: e8b4c1d92a70
Create Date: 2026-07-18 11:45:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f1c7a84d203e"
down_revision = "e8b4c1d92a70"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "club_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("club_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("normalized_alias", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias"),
    )
    op.create_index(
        "ix_club_aliases_normalized_alias",
        "club_aliases",
        ["normalized_alias"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_club_aliases_normalized_alias", table_name="club_aliases")
    op.drop_table("club_aliases")
