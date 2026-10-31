"""Add player magazine photo galleries.

Revision ID: d4a91f0c2e63
Revises: c3e78b4a9012
"""

from alembic import op
import sqlalchemy as sa

revision = "d4a91f0c2e63"
down_revision = "c3e78b4a9012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "player_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("caption", sa.String(length=300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_player_photos_player_id",
        "player_photos",
        ["player_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_player_photos_player_id", table_name="player_photos")
    op.drop_table("player_photos")
