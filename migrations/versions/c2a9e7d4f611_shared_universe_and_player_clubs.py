"""shared competition editions and global player club associations

Revision ID: c2a9e7d4f611
Revises: 8f3c7b1a2d4e
Create Date: 2026-07-18 10:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c2a9e7d4f611"
down_revision = "8f3c7b1a2d4e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "competition_editions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=False),
        sa.Column("end_year", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
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
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "competition_id",
            "start_year",
            "end_year",
            name="uq_competition_edition_years",
        ),
    )

    with op.batch_alter_table("seasons") as batch_op:
        batch_op.add_column(sa.Column("primary_league_edition_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_seasons_primary_league_edition",
            "competition_editions",
            ["primary_league_edition_id"],
            ["id"],
        )
    with op.batch_alter_table("league_standings") as batch_op:
        batch_op.add_column(sa.Column("competition_edition_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_standings_competition_edition",
            "competition_editions",
            ["competition_edition_id"],
            ["id"],
        )
    with op.batch_alter_table("matches") as batch_op:
        batch_op.add_column(sa.Column("competition_edition_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_matches_competition_edition",
            "competition_editions",
            ["competition_edition_id"],
            ["id"],
        )
    with op.batch_alter_table("honours") as batch_op:
        batch_op.add_column(sa.Column("competition_edition_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_honours_competition_edition",
            "competition_editions",
            ["competition_edition_id"],
            ["id"],
        )

    op.create_table(
        "player_club_associations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("club_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_season_id", sa.Integer()),
        sa.Column("last_seen_season_id", sa.Integer()),
        sa.Column("relationship_type", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
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
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["first_seen_season_id"], ["seasons.id"]),
        sa.ForeignKeyConstraint(["last_seen_season_id"], ["seasons.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "club_id", name="uq_player_club_association"),
    )

    # Every competition used by a club season gets one shared edition.
    op.execute(
        """
        INSERT OR IGNORE INTO competition_editions
            (competition_id, start_year, end_year, name)
        SELECT DISTINCT x.competition_id, s.start_year, s.end_year, s.name
        FROM (
            SELECT season_id, competition_id FROM matches
            UNION SELECT season_id, competition_id FROM league_standings
            UNION SELECT season_id, competition_id FROM honours WHERE competition_id IS NOT NULL
            UNION SELECT id AS season_id, primary_league_id AS competition_id
                FROM seasons WHERE primary_league_id IS NOT NULL
        ) AS x
        JOIN seasons s ON s.id = x.season_id
        WHERE x.competition_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE seasons
        SET primary_league_edition_id = (
            SELECT ce.id FROM competition_editions ce
            WHERE ce.competition_id = seasons.primary_league_id
              AND ce.start_year = seasons.start_year
              AND ce.end_year = seasons.end_year
        )
        WHERE primary_league_id IS NOT NULL
        """
    )
    for table in ("league_standings", "matches", "honours"):
        op.execute(
            f"""
            UPDATE {table}
            SET competition_edition_id = (
                SELECT ce.id
                FROM seasons s
                JOIN competition_editions ce
                  ON ce.competition_id = {table}.competition_id
                 AND ce.start_year = s.start_year
                 AND ce.end_year = s.end_year
                WHERE s.id = {table}.season_id
            )
            WHERE competition_id IS NOT NULL
            """
        )

    # A club season with no direct table import can attach to the one
    # same-year edition whose standings contain that club's exact name.
    op.execute(
        """
        UPDATE seasons
        SET primary_league_edition_id = (
                SELECT ce.id
                FROM competition_editions ce
                JOIN league_standings ls ON ls.competition_edition_id = ce.id
                JOIN clubs c ON c.id = seasons.club_id
                WHERE ce.start_year = seasons.start_year
                  AND ce.end_year = seasons.end_year
                  AND (
                    lower(trim(ls.team)) = lower(trim(c.name))
                    OR (c.short_name IS NOT NULL AND lower(trim(ls.team)) = lower(trim(c.short_name)))
                  )
                GROUP BY ce.id
                LIMIT 1
            ),
            primary_league_id = (
                SELECT ce.competition_id
                FROM competition_editions ce
                JOIN league_standings ls ON ls.competition_edition_id = ce.id
                JOIN clubs c ON c.id = seasons.club_id
                WHERE ce.start_year = seasons.start_year
                  AND ce.end_year = seasons.end_year
                  AND (
                    lower(trim(ls.team)) = lower(trim(c.name))
                    OR (c.short_name IS NOT NULL AND lower(trim(ls.team)) = lower(trim(c.short_name)))
                  )
                GROUP BY ce.id
                LIMIT 1
            )
        WHERE primary_league_edition_id IS NULL
          AND (
            SELECT count(DISTINCT ce.id)
            FROM competition_editions ce
            JOIN league_standings ls ON ls.competition_edition_id = ce.id
            JOIN clubs c ON c.id = seasons.club_id
            WHERE ce.start_year = seasons.start_year
              AND ce.end_year = seasons.end_year
              AND (
                lower(trim(ls.team)) = lower(trim(c.name))
                OR (c.short_name IS NOT NULL AND lower(trim(ls.team)) = lower(trim(c.short_name)))
              )
          ) = 1
        """
    )

    # Existing club ownership becomes the first explicit archive association.
    op.execute(
        """
        INSERT OR IGNORE INTO player_club_associations
            (player_id, club_id, relationship_type, source)
        SELECT id, club_id, 'archive', 'legacy_backfill'
        FROM players
        WHERE club_id IS NOT NULL
        """
    )


def downgrade():
    op.drop_table("player_club_associations")
    with op.batch_alter_table("honours") as batch_op:
        batch_op.drop_constraint("fk_honours_competition_edition", type_="foreignkey")
        batch_op.drop_column("competition_edition_id")
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_constraint("fk_matches_competition_edition", type_="foreignkey")
        batch_op.drop_column("competition_edition_id")
    with op.batch_alter_table("league_standings") as batch_op:
        batch_op.drop_constraint("fk_standings_competition_edition", type_="foreignkey")
        batch_op.drop_column("competition_edition_id")
    with op.batch_alter_table("seasons") as batch_op:
        batch_op.drop_constraint("fk_seasons_primary_league_edition", type_="foreignkey")
        batch_op.drop_column("primary_league_edition_id")
    op.drop_table("competition_editions")
