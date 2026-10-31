"""universe discovery: club-less league-table imports

Revision ID: d7a2e250a8e1
Revises: b6d2e91f4a70
Create Date: 2026-07-19 10:00:00

Allows a league-table upload to be committed without any club selected: it
describes one shared CompetitionEdition, not any one club's archive.
``import_records.club_id`` and ``league_standings.season_id`` become
nullable so that path has no club/season to attach to. No new tables are
introduced -- an unresolved participant is simply a LeagueStanding row with
``club_id IS NULL`` and its raw ``team`` label preserved, traceable back to
its SourceRecord/ImportRecord exactly like every other imported fact.
"""

from alembic import op
import sqlalchemy as sa


revision = "d7a2e250a8e1"
down_revision = "b6d2e91f4a70"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("import_records") as batch_op:
        batch_op.alter_column("club_id", existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table("league_standings") as batch_op:
        batch_op.alter_column("season_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    with op.batch_alter_table("league_standings") as batch_op:
        batch_op.alter_column("season_id", existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table("import_records") as batch_op:
        batch_op.alter_column("club_id", existing_type=sa.Integer(), nullable=False)
