"""add career narrative source

Revision ID: d5e82a0c47f1
Revises: c9a41f7be205
Create Date: 2026-07-24 19:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e82a0c47f1"
down_revision = "c9a41f7be205"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("career_narrative_source", sa.String(length=40), nullable=True)
        )
    # Every narrative that exists today came from the AI writer; leaving them
    # null would make them indistinguishable from a verbatim FM paste.
    op.execute(
        "UPDATE players SET career_narrative_source = 'ai_universe_rewrite' "
        "WHERE career_narrative IS NOT NULL"
    )


def downgrade():
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.drop_column("career_narrative_source")
