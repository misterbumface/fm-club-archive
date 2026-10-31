"""add persistent competition winners

Revision ID: c7a91e5b204d
Revises: f4c8a2d90b31
Create Date: 2026-07-19 02:47:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c7a91e5b204d"
down_revision = "f4c8a2d90b31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "competition_winners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("competition_edition_id", sa.Integer()),
        sa.Column("club_id", sa.Integer()),
        sa.Column("win_year", sa.Integer(), nullable=False),
        sa.Column("team_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_team_name", sa.String(length=200), nullable=False),
        sa.Column("team_type", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(["competition_edition_id"], ["competition_editions.id"]),
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "competition_id", "win_year", name="uq_competition_winner_year"
        ),
    )
    op.create_index(
        "ix_competition_winners_normalized_team_name",
        "competition_winners",
        ["normalized_team_name"],
    )
    with op.batch_alter_table("player_honours") as batch_op:
        batch_op.add_column(sa.Column("competition_winner_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_player_honours_competition_winner_id",
            "competition_winners",
            ["competition_winner_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("player_honours") as batch_op:
        batch_op.drop_constraint(
            "fk_player_honours_competition_winner_id", type_="foreignkey"
        )
        batch_op.drop_column("competition_winner_id")
    op.drop_index(
        "ix_competition_winners_normalized_team_name",
        table_name="competition_winners",
    )
    op.drop_table("competition_winners")

