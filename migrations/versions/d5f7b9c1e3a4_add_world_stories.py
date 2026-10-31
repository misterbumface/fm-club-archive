"""Add stories keyed by FM ids: club seasons, editions, old chapters, club texts; match text
sources; world references on World Soccer links.

Revision ID: d5f7b9c1e3a4
Revises: c3e5a7b9d1f2
"""

import sqlalchemy as sa
from alembic import op


revision = "d5f7b9c1e3a4"
down_revision = "c3e5a7b9d1f2"
branch_labels = None
depends_on = None


def _story(name: str, first: str, second: str):
    op.create_table(
        name,
        sa.Column(first, sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(second, sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("game_date", sa.Date(), nullable=True),
        sa.Column("written_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint(first, second),
    )


def _chapters(name: str, owner: str, period: str):
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(owner, sa.Integer(), nullable=False),
        sa.Column(period, sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("covers_from", sa.Date(), nullable=True),
        sa.Column("covers_to", sa.Date(), nullable=True),
        sa.Column("old_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("old_id"),
    )
    op.create_index(f"ix_{name}_{owner}", name, [owner])


def upgrade():
    _story("club_season_stories", "club_uid", "season")
    _story("edition_stories", "competition_uid", "year")
    _chapters("club_season_chapters", "club_uid", "season")
    _chapters("edition_chapters", "competition_uid", "year")
    op.create_table(
        "club_texts",
        sa.Column("club_uid", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("profile_summary", sa.Text(), nullable=True),
        sa.Column("historical_context", sa.Text(), nullable=True),
        sa.Column("written_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("club_uid"),
    )
    with op.batch_alter_table("match_texts") as batch:
        batch.add_column(sa.Column("source", sa.String(length=20), nullable=False, server_default="curator"))
    with op.batch_alter_table("world_soccer_entity_links") as batch:
        batch.add_column(sa.Column("world_ref", sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table("world_soccer_entity_links") as batch:
        batch.drop_column("world_ref")
    with op.batch_alter_table("match_texts") as batch:
        batch.drop_column("source")
    op.drop_table("club_texts")
    for name, owner in (("edition_chapters", "competition_uid"), ("club_season_chapters", "club_uid")):
        op.drop_index(f"ix_{name}_{owner}", table_name=name)
        op.drop_table(name)
    op.drop_table("edition_stories")
    op.drop_table("club_season_stories")
