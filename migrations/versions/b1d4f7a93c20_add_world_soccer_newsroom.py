"""add World Soccer newsroom

Revision ID: b1d4f7a93c20
Revises: a9c3e7f12b40
Create Date: 2026-07-26 22:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b1d4f7a93c20"
down_revision = "a9c3e7f12b40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "world_soccer_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=False),
        sa.Column("knowledge_cutoff", sa.Date(), nullable=False),
        sa.Column("edition_name", sa.String(length=120), nullable=False),
        sa.Column("headline", sa.String(length=300)),
        sa.Column("standfirst", sa.Text()),
        sa.Column("cover_image_url", sa.String(length=1000)),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("published_at", sa.DateTime()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_world_soccer_issues_publication_date",
        "world_soccer_issues",
        ["publication_date"],
    )
    op.create_index(
        "ix_world_soccer_issues_status", "world_soccer_issues", ["status"]
    )

    op.create_table(
        "world_soccer_story_threads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_on", sa.Date()),
        sa.Column("concluded_on", sa.Date()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_world_soccer_story_threads_status",
        "world_soccer_story_threads",
        ["status"],
    )

    op.create_table(
        "world_soccer_articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("story_thread_id", sa.Integer()),
        sa.Column("section", sa.String(length=100), nullable=False),
        sa.Column("article_type", sa.String(length=40), nullable=False),
        sa.Column("headline", sa.String(length=300), nullable=False),
        sa.Column("standfirst", sa.Text()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("byline", sa.String(length=160), nullable=False),
        sa.Column("image_url", sa.String(length=1000)),
        sa.Column("image_caption", sa.String(length=500)),
        sa.Column("prominence", sa.String(length=20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"], ["world_soccer_issues.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["story_thread_id"], ["world_soccer_story_threads.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_world_soccer_articles_issue_id",
        "world_soccer_articles",
        ["issue_id"],
    )
    op.create_index(
        "ix_world_soccer_articles_story_thread_id",
        "world_soccer_articles",
        ["story_thread_id"],
    )

    op.create_table(
        "world_soccer_entity_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=240)),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(
            ["article_id"], ["world_soccer_articles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "article_id",
            "entity_type",
            "entity_id",
            name="uq_world_soccer_article_entity",
        ),
    )
    op.create_index(
        "ix_world_soccer_entity_links_article_id",
        "world_soccer_entity_links",
        ["article_id"],
    )


def downgrade():
    op.drop_index(
        "ix_world_soccer_entity_links_article_id",
        table_name="world_soccer_entity_links",
    )
    op.drop_table("world_soccer_entity_links")
    op.drop_index(
        "ix_world_soccer_articles_story_thread_id",
        table_name="world_soccer_articles",
    )
    op.drop_index(
        "ix_world_soccer_articles_issue_id",
        table_name="world_soccer_articles",
    )
    op.drop_table("world_soccer_articles")
    op.drop_index(
        "ix_world_soccer_story_threads_status",
        table_name="world_soccer_story_threads",
    )
    op.drop_table("world_soccer_story_threads")
    op.drop_index(
        "ix_world_soccer_issues_status", table_name="world_soccer_issues"
    )
    op.drop_index(
        "ix_world_soccer_issues_publication_date",
        table_name="world_soccer_issues",
    )
    op.drop_table("world_soccer_issues")
