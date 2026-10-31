"""persist player honour attribution decisions

Revision ID: e9c4b7a21d60
Revises: d6f2a9c4e813
Create Date: 2026-07-28 12:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e9c4b7a21d60"
down_revision = "d6f2a9c4e813"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("competition_winners") as batch_op:
        batch_op.add_column(sa.Column("deciding_match_id", sa.Integer()))
        batch_op.add_column(sa.Column("won_on", sa.Date()))
        batch_op.create_foreign_key(
            "fk_competition_winners_deciding_match_id",
            "matches",
            ["deciding_match_id"],
            ["id"],
        )

    with op.batch_alter_table("player_honours") as batch_op:
        batch_op.add_column(sa.Column("claimed_year", sa.Integer()))
        batch_op.add_column(
            sa.Column(
                "attribution_basis",
                sa.String(length=40),
                nullable=False,
                server_default="imported",
            )
        )
        batch_op.add_column(
            sa.Column(
                "review_status",
                sa.String(length=20),
                nullable=False,
                server_default="accepted",
            )
        )
        batch_op.add_column(sa.Column("decision_reason", sa.Text()))
        batch_op.add_column(sa.Column("decided_at", sa.DateTime()))

    op.execute("UPDATE player_honours SET claimed_year = win_year")
    op.execute(
        """
        UPDATE player_honours
        SET attribution_basis = CASE
            WHEN source IN ('manual_editor', 'mcp_curator') THEN 'manual'
            WHEN source = 'player_history' THEN 'biography_explicit'
            WHEN source IN ('national_result', 'national_import') THEN 'national_selection'
            ELSE 'imported'
        END
        """
    )
    op.execute(
        """
        UPDATE player_honours
        SET review_status = CASE
            WHEN evidence_status = 'conflict' THEN 'conflict'
            ELSE 'accepted'
        END
        """
    )

    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(
            sa.Column(
                "honour_ledger_status",
                sa.String(length=20),
                nullable=False,
                server_default="unknown",
            )
        )
        batch_op.add_column(sa.Column("honour_ledger_claimed_total", sa.Integer()))
        batch_op.add_column(sa.Column("honour_ledger_source", sa.String(length=50)))
        batch_op.add_column(sa.Column("honour_ledger_updated_at", sa.DateTime()))

    op.execute(
        """
        UPDATE players
        SET honour_ledger_status = 'partial',
            honour_ledger_source = 'legacy_player_history'
        WHERE id IN (
            SELECT DISTINCT player_id
            FROM player_events
            WHERE event_type IN (
                'career_club_honour',
                'international_honour',
                'manager_honour'
            )
        )
        """
    )


def downgrade():
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("honour_ledger_updated_at")
        batch_op.drop_column("honour_ledger_source")
        batch_op.drop_column("honour_ledger_claimed_total")
        batch_op.drop_column("honour_ledger_status")

    with op.batch_alter_table("player_honours") as batch_op:
        batch_op.drop_column("decided_at")
        batch_op.drop_column("decision_reason")
        batch_op.drop_column("review_status")
        batch_op.drop_column("attribution_basis")
        batch_op.drop_column("claimed_year")

    with op.batch_alter_table("competition_winners") as batch_op:
        batch_op.drop_constraint(
            "fk_competition_winners_deciding_match_id", type_="foreignkey"
        )
        batch_op.drop_column("won_on")
        batch_op.drop_column("deciding_match_id")
