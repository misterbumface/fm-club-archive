"""The social feed: accounts and their posts.

Revision ID: d1e3f5a7b9c0
Revises: c4e6a8b0d2f3
"""

import sqlalchemy as sa
from alembic import op


revision = "d1e3f5a7b9c0"
down_revision = "c4e6a8b0d2f3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("handle", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("club_uid", sa.Integer(), nullable=True),
        sa.Column("person_uid", sa.Integer(), nullable=True),
        sa.Column("persona", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("handle"),
    )
    with op.batch_alter_table("social_accounts") as batch:
        batch.create_index("ix_social_accounts_kind", ["kind"])
        batch.create_index("ix_social_accounts_club_uid", ["club_uid"])
        batch.create_index("ix_social_accounts_person_uid", ["person_uid"])
    op.create_table(
        "social_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("event_key", sa.String(length=80), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("links", sa.JSON(), nullable=True),
        sa.Column("reply_to_id", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=False),
        sa.Column("reposts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["social_accounts.id"]),
        sa.ForeignKeyConstraint(["reply_to_id"], ["social_posts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "event_key"),
    )
    with op.batch_alter_table("social_posts") as batch:
        batch.create_index("ix_social_posts_account_id", ["account_id"])
        batch.create_index("ix_social_posts_posted_on", ["posted_on"])
        batch.create_index("ix_social_posts_reply_to_id", ["reply_to_id"])


def downgrade():
    op.drop_table("social_posts")
    op.drop_table("social_accounts")
