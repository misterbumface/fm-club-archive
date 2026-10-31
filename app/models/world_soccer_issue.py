"""A dated, persistent edition of the archive's World Soccer newspaper."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.archive_image import ArchiveImage
    from app.models.outlet import Outlet
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_image_use import WorldSoccerImageUse


class WorldSoccerIssue(TimestampMixin, db.Model):
    __tablename__ = "world_soccer_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_date: Mapped[date_type] = mapped_column(
        Date, nullable=False, index=True
    )
    knowledge_cutoff: Mapped[date_type] = mapped_column(Date, nullable=False)
    edition_name: Mapped[str] = mapped_column(
        String(120), nullable=False, default="World Soccer"
    )
    headline: Mapped[str | None] = mapped_column(String(300))
    standfirst: Mapped[str | None] = mapped_column(Text)
    cover_image_url: Mapped[str | None] = mapped_column(String(1000))
    cover_image_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("archive_images.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The outlet that prints it; None for the World Soccer issues made before the outlets.
    outlet_id: Mapped[int | None] = mapped_column(ForeignKey("outlets.id", ondelete="SET NULL"), index=True)

    articles: Mapped[list["WorldSoccerArticle"]] = relationship(
        back_populates="issue",
        cascade="all, delete-orphan",
        order_by="WorldSoccerArticle.sort_order, WorldSoccerArticle.id",
    )
    cover_image_asset: Mapped["ArchiveImage | None"] = relationship()
    outlet: Mapped["Outlet | None"] = relationship()
    image_uses: Mapped[list["WorldSoccerImageUse"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan"
    )
