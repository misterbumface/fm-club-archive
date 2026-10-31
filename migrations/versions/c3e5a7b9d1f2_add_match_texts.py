"""Add match_texts: reports and previews for world fixtures, keyed by date and FM team ids.

Revision ID: c3e5a7b9d1f2
Revises: b7d2e4f6a8c1
"""

import sqlalchemy as sa
from alembic import op


revision = "c3e5a7b9d1f2"
down_revision = "b7d2e4f6a8c1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_texts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("home_uid", sa.Integer(), nullable=False),
        sa.Column("away_uid", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("written_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", "home_uid", "away_uid", "kind"),
    )


def downgrade():
    op.drop_table("match_texts")
