"""Drop the old archive's tables (the cut-over, docs/cutover-plan.md).

Every fact in them comes from the Football Manager saves again (the world database), and
every written text has moved to tables keyed by FM ids. A copy of the database from before
the drop is in instance/backups/fm_archive.pre-cutover-20261015.sqlite3; the old code is in
git under the tag pre-cutover.

Revision ID: a8c0e2f4b6d7
Revises: f7b9d1e3a5c6
"""

from alembic import op


revision = "a8c0e2f4b6d7"
down_revision = "f7b9d1e3a5c6"
branch_labels = None
depends_on = None

OLD_TABLES = [
    "appearances", "club_aliases", "club_events", "clubs", "competition_editions", "competition_format_revisions",
    "competition_story_chapters", "competition_winners", "competitions", "goal_attributions", "goals", "honours",
    "import_records", "league_standings", "matches", "national_campaigns", "national_goals", "national_matches",
    "national_selections", "national_teams", "player_aliases", "player_career_seasons", "player_career_spells",
    "player_club_associations", "player_events", "player_honours", "player_identity_splits", "player_photos",
    "player_seasons", "players", "scheduled_fixtures", "scorer_stubs", "season_story_chapters", "seasons",
    "source_records", "tactical_snapshots", "transfers", "universe_changes", "world_matches",
]


def upgrade():
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")  # the old tables point at each other
    for table in OLD_TABLES:
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}"')
    bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade():
    raise NotImplementedError(
        "The old archive tables cannot be rebuilt by a migration. Restore "
        "instance/backups/fm_archive.pre-cutover-20261015.sqlite3 and check out the git tag pre-cutover."
    )
