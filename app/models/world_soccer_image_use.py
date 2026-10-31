"""Editorial use history for World Soccer image selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.archive_image import ArchiveImage
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue


class WorldSoccerImageUse(CreatedAtMixin, db.Model):
    """Records an image choice without rewriting historical article URLs."""

    __tablename__ = "world_soccer_image_uses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int | None] = mapped_column(
        ForeignKey("archive_images.id"), index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("world_soccer_articles.id", ondelete="CASCADE"), index=True
    )
    issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("world_soccer_issues.id", ondelete="CASCADE"), index=True
    )
    image_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    story_type: Mapped[str | None] = mapped_column(String(40))
    selection_reason: Mapped[str | None] = mapped_column(String(500))

    image: Mapped["ArchiveImage | None"] = relationship(back_populates="usages")
    article: Mapped["WorldSoccerArticle | None"] = relationship(
        back_populates="image_uses"
    )
    issue: Mapped["WorldSoccerIssue | None"] = relationship(
        back_populates="image_uses"
    )

