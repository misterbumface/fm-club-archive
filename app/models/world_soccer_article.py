"""One story printed in a persistent World Soccer issue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.archive_image import ArchiveImage
    from app.models.outlet import Writer
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink
    from app.models.world_soccer_image_use import WorldSoccerImageUse
    from app.models.world_soccer_issue import WorldSoccerIssue
    from app.models.world_soccer_thread import WorldSoccerStoryThread


class WorldSoccerArticle(TimestampMixin, db.Model):
    __tablename__ = "world_soccer_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("world_soccer_issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    story_thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("world_soccer_story_threads.id"), index=True
    )
    section: Mapped[str] = mapped_column(
        String(100), nullable=False, default="World"
    )
    article_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="report"
    )
    headline: Mapped[str] = mapped_column(String(300), nullable=False)
    standfirst: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    byline: Mapped[str] = mapped_column(
        String(160), nullable=False, default="World Soccer"
    )
    image_url: Mapped[str | None] = mapped_column(String(1000))
    image_caption: Mapped[str | None] = mapped_column(String(500))
    image_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("archive_images.id", ondelete="SET NULL"), index=True
    )
    prominence: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The story lead it was written from (story_leads.Lead, as article_writer.lead_to_dict), for a rewrite,
    # and the lead's key, so an outlet does not cover the same story twice.
    lead_key: Mapped[str | None] = mapped_column(String(300), index=True)
    lead: Mapped[dict | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(80))  # the AI model that wrote it
    writer_id: Mapped[int | None] = mapped_column(ForeignKey("writers.id", ondelete="SET NULL"), index=True)

    issue: Mapped["WorldSoccerIssue"] = relationship(back_populates="articles")
    image_asset: Mapped["ArchiveImage | None"] = relationship()
    image_uses: Mapped[list["WorldSoccerImageUse"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    writer: Mapped["Writer | None"] = relationship()
    story_thread: Mapped["WorldSoccerStoryThread | None"] = relationship(
        back_populates="articles"
    )
    entity_links: Mapped[list["WorldSoccerEntityLink"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        order_by="WorldSoccerEntityLink.id",
    )
