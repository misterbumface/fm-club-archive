"""Earlier versions of season and edition stories, kept when a new story replaces one.

Revision ID: b7e2c4d6f8a1
Revises: 96f4f3587c88
"""

import sqlalchemy as sa
from alembic import op


revision = "b7e2c4d6f8a1"
down_revision = "96f4f3587c88"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "story_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("subject_uid", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("game_date", sa.Date(), nullable=True),
        sa.Column("written_at", sa.DateTime(), nullable=False),
        sa.Column("replaced_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_story_versions_subject", "story_versions", ["kind", "subject_uid", "year"])


def downgrade():
    op.drop_index("ix_story_versions_subject", table_name="story_versions")
    op.drop_table("story_versions")
