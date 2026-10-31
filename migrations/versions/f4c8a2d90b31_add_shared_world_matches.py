"""add canonical world matches shared by club perspectives

Revision ID: f4c8a2d90b31
Revises: a4e8c2d91f60
Create Date: 2026-07-18 21:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f4c8a2d90b31"
down_revision = "a4e8c2d91f60"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "world_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_key", sa.String(length=300), nullable=False),
        sa.Column("competition_edition_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("club_a_id", sa.Integer(), nullable=False),
        sa.Column("club_b_id", sa.Integer(), nullable=False),
        sa.Column("club_a_score", sa.Integer(), nullable=False),
        sa.Column("club_b_score", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(length=100), nullable=False),
        sa.Column("leg_key", sa.Integer(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(["club_a_id"], ["clubs.id"]),
        sa.ForeignKeyConstraint(["club_b_id"], ["clubs.id"]),
        sa.ForeignKeyConstraint(
            ["competition_edition_id"], ["competition_editions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_key"),
    )
    with op.batch_alter_table("matches") as batch_op:
        batch_op.add_column(sa.Column("world_match_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_matches_world_match_id",
            "world_matches",
            ["world_match_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_constraint("fk_matches_world_match_id", type_="foreignkey")
        batch_op.drop_column("world_match_id")
    op.drop_table("world_matches")
