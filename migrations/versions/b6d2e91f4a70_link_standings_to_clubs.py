"""link league standings to canonical clubs

Revision ID: b6d2e91f4a70
Revises: f1c7a84d203e
Create Date: 2026-07-18 12:35:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b6d2e91f4a70"
down_revision = "f1c7a84d203e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("league_standings") as batch_op:
        batch_op.add_column(sa.Column("club_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_league_standings_club",
            "clubs",
            ["club_id"],
            ["id"],
        )
        batch_op.create_index("ix_league_standings_club_id", ["club_id"])

    # Link exact canonical names, short names, and stored aliases. More
    # sophisticated normalization is performed by the application whenever a
    # club is imported; this conservative migration never guesses identities.
    op.execute(
        """
        UPDATE league_standings
        SET club_id = (
            SELECT c.id
            FROM clubs c
            LEFT JOIN club_aliases ca ON ca.club_id = c.id
            WHERE lower(trim(league_standings.team)) = lower(trim(c.name))
               OR lower(trim(league_standings.team)) = lower(trim(c.short_name))
               OR lower(trim(league_standings.team)) = lower(trim(ca.alias))
            GROUP BY c.id
            LIMIT 1
        )
        WHERE club_id IS NULL
          AND (
            SELECT count(DISTINCT c.id)
            FROM clubs c
            LEFT JOIN club_aliases ca ON ca.club_id = c.id
            WHERE lower(trim(league_standings.team)) = lower(trim(c.name))
               OR lower(trim(league_standings.team)) = lower(trim(c.short_name))
               OR lower(trim(league_standings.team)) = lower(trim(ca.alias))
          ) = 1
        """
    )


def downgrade():
    with op.batch_alter_table("league_standings") as batch_op:
        batch_op.drop_index("ix_league_standings_club_id")
        batch_op.drop_constraint("fk_league_standings_club", type_="foreignkey")
        batch_op.drop_column("club_id")
