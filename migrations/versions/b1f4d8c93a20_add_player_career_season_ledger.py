"""add player career season ledger

Revision ID: b1f4d8c93a20
Revises: e8c2b47a901d
Create Date: 2026-07-20 02:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b1f4d8c93a20"
down_revision = "e8c2b47a901d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "player_career_seasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("club_id", sa.Integer()),
        sa.Column("season_label", sa.String(length=20), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=False),
        sa.Column("end_year", sa.Integer(), nullable=False),
        sa.Column("season_format", sa.String(length=20), nullable=False),
        sa.Column("team_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_team_name", sa.String(length=200), nullable=False),
        sa.Column("country", sa.String(length=100)),
        sa.Column("division_name", sa.String(length=200)),
        sa.Column("info_raw", sa.String(length=200)),
        sa.Column("is_loan", sa.Boolean(), nullable=False),
        sa.Column("transfer_fee", sa.Integer()),
        sa.Column("loan_fee", sa.Integer()),
        sa.Column("appearances", sa.Integer()),
        sa.Column("goals", sa.Integer()),
        sa.Column("assists", sa.Integer()),
        sa.Column("player_of_match", sa.Integer()),
        sa.Column("average_rating", sa.Float()),
        sa.Column("is_goalkeeper", sa.Boolean(), nullable=False),
        sa.Column("goals_conceded", sa.Integer()),
        sa.Column("clean_sheets", sa.Integer()),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_filename", sa.String(length=300)),
        sa.Column("source_row_index", sa.Integer()),
        sa.Column("evidence_text", sa.Text()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("player_career_seasons") as batch_op:
        batch_op.create_index(
            "ix_player_career_seasons_normalized_team_name",
            ["normalized_team_name"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("player_career_seasons") as batch_op:
        batch_op.drop_index("ix_player_career_seasons_normalized_team_name")
    op.drop_table("player_career_seasons")
