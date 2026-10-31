"""add season story chapters

Revision ID: e2f96b31c8a4
Revises: d5e82a0c47f1
Create Date: 2026-07-24 20:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f96b31c8a4"
down_revision = "d5e82a0c47f1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "season_story_chapters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("covers_from_date", sa.Date(), nullable=True),
        sa.Column("covers_to_date", sa.Date(), nullable=True),
        sa.Column("match_ids_json", sa.JSON(), nullable=False),
        sa.Column(
            "adopted_from_full_story", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("model", sa.String(length=50), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("season_story_chapters", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_season_story_chapters_season_id"), ["season_id"], unique=False
        )


def downgrade():
    with op.batch_alter_table("season_story_chapters", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_season_story_chapters_season_id"))
    op.drop_table("season_story_chapters")
