"""Add indexes for the archive's high-volume read paths.

Revision ID: e0f4a7b8c9d1
Revises: d9e2f3a4b5c6
"""

from alembic import op


revision = "e0f4a7b8c9d1"
down_revision = "d9e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    # Foreign-key columns are not automatically indexed by SQLite.  These
    # are the columns used by season pages, scorer lookups, world-match
    # linking and squad imports; the existing unique constraints already
    # cover the corresponding leading-key lookups.
    op.create_index("ix_matches_world_match_id", "matches", ["world_match_id"])
    op.create_index("ix_goals_match_id", "goals", ["match_id"])
    op.create_index("ix_goals_player_id", "goals", ["player_id"])
    op.create_index(
        "ix_player_events_season_type_source",
        "player_events",
        ["season_id", "event_type", "source"],
    )
    op.create_index(
        "ix_player_seasons_season_source",
        "player_seasons",
        ["season_id", "stats_source"],
    )
    op.create_index("ix_honours_season_id", "honours", ["season_id"])
    op.create_index(
        "ix_league_standings_edition_id",
        "league_standings",
        ["competition_edition_id"],
    )


def downgrade():
    op.drop_index("ix_league_standings_edition_id", table_name="league_standings")
    op.drop_index("ix_honours_season_id", table_name="honours")
    op.drop_index("ix_player_seasons_season_source", table_name="player_seasons")
    op.drop_index("ix_player_events_season_type_source", table_name="player_events")
    op.drop_index("ix_goals_player_id", table_name="goals")
    op.drop_index("ix_goals_match_id", table_name="goals")
    op.drop_index("ix_matches_world_match_id", table_name="matches")
