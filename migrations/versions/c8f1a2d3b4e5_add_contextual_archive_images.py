"""Add contextual archive images and World Soccer usage history.

Revision ID: c8f1a2d3b4e5
Revises: b4d6e8f0a2c3
"""

from alembic import op
import sqlalchemy as sa


revision = "c8f1a2d3b4e5"
down_revision = "b4d6e8f0a2c3"
branch_labels = None
depends_on = None


def _internal_url(path: str | None, *, prefix: str) -> str | None:
    if not path:
        return None
    value = path.strip()
    if not value:
        return None
    if value.startswith("/"):
        return value
    if value.startswith("static/"):
        return f"/{value}"
    clean_prefix = prefix.rstrip("/")
    if not clean_prefix.startswith("/"):
        clean_prefix = f"/{clean_prefix}"
    return f"{clean_prefix}/{value.lstrip('/')}"


def _ensure_asset(
    bind,
    *,
    url: str,
    kind: str,
    caption: str,
    source: str,
    entity_type: str,
    entity_id: int,
    relation: str = "subject",
) -> int:
    row = bind.execute(
        sa.text("SELECT id FROM archive_images WHERE url = :url"),
        {"url": url},
    ).first()
    if row is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO archive_images
                    (url, kind, caption, alt_text, source, priority,
                     repeat_policy, is_active, created_at, updated_at)
                VALUES
                    (:url, :kind, :caption, :alt_text, :source, :priority,
                     :repeat_policy, :is_active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {
                "url": url,
                "kind": kind,
                "caption": caption,
                "alt_text": caption,
                "source": source,
                "priority": 0,
                "repeat_policy": "unrestricted" if kind == "crest" else "avoid_recent",
                "is_active": True,
            },
        )
        row = bind.execute(
            sa.text("SELECT id FROM archive_images WHERE url = :url"),
            {"url": url},
        ).first()
    asset_id = int(row[0])
    bind.execute(
        sa.text(
            """
            INSERT INTO archive_image_links
                (image_id, entity_type, entity_id, relation, label,
                 created_at, updated_at)
            VALUES
                (:image_id, :entity_type, :entity_id, :relation, :label,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(image_id, entity_type, entity_id, relation) DO NOTHING
            """
        ),
        {
            "image_id": asset_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "relation": relation,
            "label": caption,
        },
    )
    return asset_id


def upgrade():
    op.create_table(
        "archive_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("caption", sa.String(length=500)),
        sa.Column("alt_text", sa.String(length=500)),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("captured_on", sa.Date()),
        sa.Column("era_start_year", sa.Integer()),
        sa.Column("era_end_year", sa.Integer()),
        sa.Column("tags_json", sa.JSON()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repeat_policy", sa.String(length=20), nullable=False, server_default="avoid_recent"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", name="uq_archive_image_url"),
    )
    op.create_index("ix_archive_images_kind", "archive_images", ["kind"])
    op.create_index("ix_archive_images_is_active", "archive_images", ["is_active"])

    op.create_table(
        "archive_image_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=240)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.ForeignKeyConstraint(["image_id"], ["archive_images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "image_id", "entity_type", "entity_id", "relation",
            name="uq_archive_image_entity_link",
        ),
    )
    op.create_index(
        "ix_archive_image_links_entity",
        "archive_image_links",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_archive_image_links_image_id",
        "archive_image_links",
        ["image_id"],
    )

    op.create_table(
        "world_soccer_image_uses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer()),
        sa.Column("article_id", sa.Integer()),
        sa.Column("issue_id", sa.Integer()),
        sa.Column("image_url", sa.String(length=1000), nullable=False),
        sa.Column("story_type", sa.String(length=40)),
        sa.Column("selection_reason", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.ForeignKeyConstraint(["image_id"], ["archive_images.id"]),
        sa.ForeignKeyConstraint(["article_id"], ["world_soccer_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_id"], ["world_soccer_issues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_world_soccer_image_uses_image_id", "world_soccer_image_uses", ["image_id"])
    op.create_index("ix_world_soccer_image_uses_article_id", "world_soccer_image_uses", ["article_id"])
    op.create_index("ix_world_soccer_image_uses_issue_id", "world_soccer_image_uses", ["issue_id"])

    op.add_column(
        "world_soccer_articles",
        sa.Column("image_asset_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_world_soccer_articles_image_asset_id",
        "world_soccer_articles",
        ["image_asset_id"],
    )
    op.add_column(
        "world_soccer_issues",
        sa.Column("cover_image_asset_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_world_soccer_issues_cover_image_asset_id",
        "world_soccer_issues",
        ["cover_image_asset_id"],
    )

    bind = op.get_bind()
    for row in bind.execute(
        sa.text("SELECT id, full_name, portrait_path FROM players WHERE portrait_path IS NOT NULL")
    ):
        url = _internal_url(row.portrait_path, prefix="/static/generated/portraits")
        if url:
            _ensure_asset(
                bind,
                url=url,
                kind="portrait",
                caption=f"{row.full_name} portrait",
                source="generated",
                entity_type="player",
                entity_id=row.id,
            )
    for row in bind.execute(
        sa.text("SELECT id, player_id, filename, caption FROM player_photos")
    ):
        url = _internal_url(row.filename, prefix="/static/generated/portraits")
        if url:
            _ensure_asset(
                bind,
                url=url,
                kind="photograph",
                caption=row.caption or "Archive photograph",
                source="upload",
                entity_type="player",
                entity_id=row.player_id,
            )
    for row in bind.execute(
        sa.text("SELECT id, name, stadium_photo_path FROM clubs WHERE stadium_photo_path IS NOT NULL")
    ):
        url = _internal_url(row.stadium_photo_path, prefix="/static/generated/stadiums")
        if url:
            _ensure_asset(
                bind,
                url=url,
                kind="stadium",
                caption=f"{row.name} stadium",
                source="upload",
                entity_type="club",
                entity_id=row.id,
            )
    for row in bind.execute(
        sa.text(
            "SELECT id, name, trophy_image_path, badge_image_path, banner_image_path "
            "FROM competitions"
        )
    ):
        for field, kind in (
            ("trophy_image_path", "trophy"),
            ("badge_image_path", "competition_badge"),
            ("banner_image_path", "competition_banner"),
        ):
            url = _internal_url(getattr(row, field), prefix="/static")
            if url:
                _ensure_asset(
                    bind,
                    url=url,
                    kind=kind,
                    caption=f"{row.name} {kind.replace('_', ' ')}",
                    source="upload",
                    entity_type="competition",
                    entity_id=row.id,
                )

    bind.execute(
        sa.text(
            "UPDATE world_soccer_articles SET image_asset_id = "
            "(SELECT id FROM archive_images WHERE archive_images.url = world_soccer_articles.image_url) "
            "WHERE image_url IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE world_soccer_issues SET cover_image_asset_id = "
            "(SELECT id FROM archive_images WHERE archive_images.url = world_soccer_issues.cover_image_url) "
            "WHERE cover_image_url IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO world_soccer_image_uses
                (image_id, article_id, issue_id, image_url, story_type,
                 selection_reason, created_at)
            SELECT a.image_asset_id, a.id, a.issue_id, a.image_url,
                   a.article_type, 'Imported from existing article', CURRENT_TIMESTAMP
            FROM world_soccer_articles a
            WHERE a.image_url IS NOT NULL
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO world_soccer_image_uses
                (image_id, issue_id, image_url, story_type,
                 selection_reason, created_at)
            SELECT i.cover_image_asset_id, i.id, i.cover_image_url,
                   'cover', 'Imported from existing issue cover', CURRENT_TIMESTAMP
            FROM world_soccer_issues i
            WHERE i.cover_image_url IS NOT NULL
            """
        )
    )


def downgrade():
    op.drop_index(
        "ix_world_soccer_issues_cover_image_asset_id",
        table_name="world_soccer_issues",
    )
    op.drop_column("world_soccer_issues", "cover_image_asset_id")
    op.drop_index(
        "ix_world_soccer_articles_image_asset_id",
        table_name="world_soccer_articles",
    )
    op.drop_column("world_soccer_articles", "image_asset_id")
    op.drop_index("ix_world_soccer_image_uses_issue_id", table_name="world_soccer_image_uses")
    op.drop_index("ix_world_soccer_image_uses_article_id", table_name="world_soccer_image_uses")
    op.drop_index("ix_world_soccer_image_uses_image_id", table_name="world_soccer_image_uses")
    op.drop_table("world_soccer_image_uses")
    op.drop_index("ix_archive_image_links_image_id", table_name="archive_image_links")
    op.drop_index("ix_archive_image_links_entity", table_name="archive_image_links")
    op.drop_table("archive_image_links")
    op.drop_index("ix_archive_images_is_active", table_name="archive_images")
    op.drop_index("ix_archive_images_kind", table_name="archive_images")
    op.drop_table("archive_images")
