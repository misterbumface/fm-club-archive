"""add scheduled_fixtures for not-yet-played games in a fixture export

Revision ID: a7d3f8b1c942
Revises: b3e6f2a1d754
Create Date: 2026-07-20 23:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "a7d3f8b1c942"
down_revision = "b3e6f2a1d754"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduled_fixtures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("competition_edition_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("opponent_name", sa.String(length=200), nullable=False),
        sa.Column("opponent_club_id", sa.Integer(), nullable=True),
        sa.Column("stadium", sa.String(length=200), nullable=True),
        sa.Column("is_home", sa.Boolean(), nullable=True),
        sa.Column("stage", sa.String(length=100), nullable=True),
        sa.Column("group_name", sa.String(length=50), nullable=True),
        sa.Column("leg", sa.Integer(), nullable=True),
        sa.Column("raw_competition", sa.String(length=200), nullable=False),
        sa.Column("source_filename", sa.String(length=300), nullable=True),
        sa.Column("source_row_index", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"]),
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"]),
        sa.ForeignKeyConstraint(
            ["competition_edition_id"], ["competition_editions.id"]
        ),
        sa.ForeignKeyConstraint(["opponent_club_id"], ["clubs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season_id",
            "date",
            "opponent_name",
            "competition_id",
            name="uq_scheduled_fixture_duplicate_key",
        ),
    )


def downgrade():
    op.drop_table("scheduled_fixtures")
