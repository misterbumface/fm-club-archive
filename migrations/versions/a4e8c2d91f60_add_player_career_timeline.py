"""add player career timeline and lifecycle state

Revision ID: a4e8c2d91f60
Revises: d7a2e250a8e1
Create Date: 2026-07-18 19:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "a4e8c2d91f60"
down_revision = "d7a2e250a8e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(
            sa.Column(
                "playing_status",
                sa.String(length=20),
                server_default="unknown",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "employment_status",
                sa.String(length=20),
                server_default="unknown",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("current_role", sa.String(length=80)))
        batch_op.add_column(
            sa.Column("current_organization_name", sa.String(length=200))
        )
        batch_op.add_column(sa.Column("retired_date", sa.Date()))

    op.create_table(
        "player_career_spells",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("club_id", sa.Integer()),
        sa.Column("parent_spell_id", sa.Integer()),
        sa.Column("track", sa.String(length=20), nullable=False),
        sa.Column("spell_type", sa.String(length=20), nullable=False),
        sa.Column("role", sa.String(length=80)),
        sa.Column("team_type", sa.String(length=20), nullable=False),
        sa.Column("team_scope", sa.String(length=20), nullable=False),
        sa.Column("organization_name", sa.String(length=200)),
        sa.Column("normalized_organization_name", sa.String(length=200)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("start_date_precision", sa.String(length=10)),
        sa.Column("end_date_precision", sa.String(length=10)),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("appearances", sa.Integer()),
        sa.Column("goals", sa.Integer()),
        sa.Column("evidence_text", sa.Text()),
        sa.Column("confidence", sa.Float(), nullable=False),
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
        sa.ForeignKeyConstraint(["parent_spell_id"], ["player_career_spells.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("player_career_spells") as batch_op:
        batch_op.create_index(
            "ix_player_career_spells_normalized_organization_name",
            ["normalized_organization_name"],
            unique=False,
        )

    with op.batch_alter_table("player_honours") as batch_op:
        batch_op.add_column(sa.Column("career_spell_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_player_honours_career_spell_id",
            "player_career_spells",
            ["career_spell_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("player_honours") as batch_op:
        batch_op.drop_constraint(
            "fk_player_honours_career_spell_id", type_="foreignkey"
        )
        batch_op.drop_column("career_spell_id")
    with op.batch_alter_table("player_career_spells") as batch_op:
        batch_op.drop_index(
            "ix_player_career_spells_normalized_organization_name"
        )
    op.drop_table("player_career_spells")
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("retired_date")
        batch_op.drop_column("current_organization_name")
        batch_op.drop_column("current_role")
        batch_op.drop_column("employment_status")
        batch_op.drop_column("playing_status")
