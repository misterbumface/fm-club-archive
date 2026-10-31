"""add national team archive

Revision ID: d3b7a91c4e20
Revises: c7a91e5b204d
Create Date: 2026-07-19 23:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d3b7a91c4e20"
down_revision = "c7a91e5b204d"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )


def upgrade():
    op.create_table(
        "national_teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("fm_uid", sa.String(length=64)),
        sa.Column("team_scope", sa.String(length=20), nullable=False),
        sa.Column("is_archive_team", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fm_uid"),
        sa.UniqueConstraint("normalized_name"),
    )
    op.create_index("ix_national_teams_normalized_name", "national_teams", ["normalized_name"])
    op.create_table(
        "national_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("national_team_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["national_team_id"], ["national_teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("national_team_id", "year", name="uq_national_campaign_team_year"),
    )
    op.create_table(
        "national_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("opponent_team_id", sa.Integer(), nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("competition_edition_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("time", sa.Time()),
        sa.Column("team_score", sa.Integer()),
        sa.Column("opponent_score", sa.Integer()),
        sa.Column("result_type", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("stage", sa.String(length=100)),
        sa.Column("group_name", sa.String(length=100)),
        sa.Column("leg", sa.Integer()),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("went_to_extra_time", sa.Boolean(), nullable=False),
        sa.Column("went_to_penalties", sa.Boolean(), nullable=False),
        sa.Column("penalty_winner", sa.String(length=10)),
        sa.Column("raw_result", sa.String(length=100)),
        sa.Column("raw_competition", sa.String(length=300)),
        sa.Column("source_filename", sa.String(length=300)),
        sa.Column("source_row_index", sa.Integer()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["campaign_id"], ["national_campaigns.id"]),
        sa.ForeignKeyConstraint(["competition_edition_id"], ["competition_editions.id"]),
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"]),
        sa.ForeignKeyConstraint(["opponent_team_id"], ["national_teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id", "date", "opponent_team_id", "competition_id",
            "team_score", "opponent_score", name="uq_national_match_duplicate_key",
        ),
    )
    op.create_table(
        "national_goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("national_match_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer()),
        sa.Column("scorer_name_raw", sa.String(length=200), nullable=False),
        sa.Column("minute", sa.Integer()),
        sa.Column("stoppage_time", sa.Integer(), nullable=False),
        sa.Column("is_penalty", sa.Boolean(), nullable=False),
        sa.Column("is_own_goal", sa.Boolean(), nullable=False),
        sa.Column("goal_order", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.String(length=300)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["national_match_id"], ["national_matches.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "national_selections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.String(length=30)),
        sa.Column("squad_role", sa.String(length=20), nullable=False),
        sa.Column("appearances", sa.Integer(), nullable=False),
        sa.Column("goals", sa.Integer(), nullable=False),
        sa.Column("average_rating", sa.Float()),
        sa.Column("source_filename", sa.String(length=300)),
        sa.Column("source_row_index", sa.Integer()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["campaign_id"], ["national_campaigns.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "player_id", name="uq_national_selection_player"),
    )
    with op.batch_alter_table("competition_winners") as batch_op:
        batch_op.add_column(sa.Column("national_team_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_competition_winners_national_team_id", "national_teams",
            ["national_team_id"], ["id"],
        )
    with op.batch_alter_table("import_records") as batch_op:
        batch_op.add_column(sa.Column("national_team_id", sa.Integer()))
        batch_op.add_column(sa.Column("national_campaign_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_import_records_national_team_id", "national_teams",
            ["national_team_id"], ["id"],
        )
        batch_op.create_foreign_key(
            "fk_import_records_national_campaign_id", "national_campaigns",
            ["national_campaign_id"], ["id"],
        )


def downgrade():
    with op.batch_alter_table("import_records") as batch_op:
        batch_op.drop_constraint("fk_import_records_national_campaign_id", type_="foreignkey")
        batch_op.drop_constraint("fk_import_records_national_team_id", type_="foreignkey")
        batch_op.drop_column("national_campaign_id")
        batch_op.drop_column("national_team_id")
    with op.batch_alter_table("competition_winners") as batch_op:
        batch_op.drop_constraint("fk_competition_winners_national_team_id", type_="foreignkey")
        batch_op.drop_column("national_team_id")
    op.drop_table("national_selections")
    op.drop_table("national_goals")
    op.drop_table("national_matches")
    op.drop_table("national_campaigns")
    op.drop_index("ix_national_teams_normalized_name", table_name="national_teams")
    op.drop_table("national_teams")
