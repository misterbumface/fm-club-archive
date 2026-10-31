"""Add the squad-snapshot stamp and where-signed-from context.

A squad export is a photograph of one moment: the numbers in it are true
as of the day it was taken and nothing in the file says which day that
was. ``stats_as_of`` records the archive clock at import time so a later
snapshot is provably newer, and ``stats_source`` records which import
wrote the figures.

``signed_from_club``/``signed_from_fee`` carry the squad view's Last
Club and Last Trans. Fee columns. They describe where a player arrived
from at some point in the past -- not necessarily during the season
being imported -- so they live on the player rather than the season.

Revision ID: b7d3e5a1c904
Revises: e2f96b31c8a4
"""

from alembic import op
import sqlalchemy as sa

revision = "b7d3e5a1c904"
down_revision = "e2f96b31c8a4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("player_seasons") as batch_op:
        batch_op.add_column(sa.Column("stats_as_of", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("stats_source", sa.String(length=40), nullable=True))
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column("signed_from_club", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("signed_from_fee", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("signed_from_fee")
        batch_op.drop_column("signed_from_club")
    with op.batch_alter_table("player_seasons") as batch_op:
        batch_op.drop_column("stats_source")
        batch_op.drop_column("stats_as_of")
