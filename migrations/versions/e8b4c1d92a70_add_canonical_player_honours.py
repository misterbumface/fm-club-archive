"""add canonical player honours

Revision ID: e8b4c1d92a70
Revises: c2a9e7d4f611
Create Date: 2026-07-18 11:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e8b4c1d92a70"
down_revision = "c2a9e7d4f611"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("clubs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_archive_club",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )

    with op.batch_alter_table("competitions") as batch_op:
        batch_op.add_column(sa.Column("fm_uid", sa.String(length=64)))
        batch_op.create_unique_constraint("uq_competitions_fm_uid", ["fm_uid"])

    op.create_table(
        "player_honours",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("competition_edition_id", sa.Integer()),
        sa.Column("honour_id", sa.Integer()),
        sa.Column("club_id", sa.Integer()),
        sa.Column("source_event_id", sa.Integer()),
        sa.Column("team_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_team_name", sa.String(length=200), nullable=False),
        sa.Column("team_type", sa.String(length=20), nullable=False),
        sa.Column("career_role", sa.String(length=20), nullable=False),
        sa.Column("win_year", sa.Integer()),
        sa.Column("period_start_year", sa.Integer()),
        sa.Column("period_end_year", sa.Integer()),
        sa.Column("win_count", sa.Integer(), nullable=False),
        sa.Column("evidence_status", sa.String(length=20), nullable=False),
        sa.Column("resolution_status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("evidence_text", sa.Text()),
        sa.Column("notes", sa.Text()),
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
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"]),
        sa.ForeignKeyConstraint(
            ["competition_edition_id"], ["competition_editions.id"]
        ),
        sa.ForeignKeyConstraint(["honour_id"], ["honours.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["source_event_id"], ["player_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_id",
            "competition_id",
            "normalized_team_name",
            "career_role",
            "win_year",
            name="uq_player_honour_exact_win",
        ),
    )


def downgrade():
    op.drop_table("player_honours")
    with op.batch_alter_table("competitions") as batch_op:
        batch_op.drop_constraint("uq_competitions_fm_uid", type_="unique")
        batch_op.drop_column("fm_uid")
    with op.batch_alter_table("clubs") as batch_op:
        batch_op.drop_column("is_archive_club")
