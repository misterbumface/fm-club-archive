"""A recurring editorial thread followed across World Soccer editions."""

from __future__ import annotations

from datetime import date as date_type
from typing import TYPE_CHECKING

from sqlalchemy import Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.world_soccer_article import WorldSoccerArticle


class WorldSoccerStoryThread(TimestampMixin, db.Model):
    __tablename__ = "world_soccer_story_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ongoing", index=True
    )
    started_on: Mapped[date_type | None] = mapped_column(Date)
    concluded_on: Mapped[date_type | None] = mapped_column(Date)
    # The subject that ties the articles together across outlets and editions: "person:<uid>" or
    # "club:<uid>" (app.services.editions.thread_key); None for threads the curator made by hand.
    key: Mapped[str | None] = mapped_column(String(80), index=True)

    articles: Mapped[list["WorldSoccerArticle"]] = relationship(
        back_populates="story_thread",
        order_by="WorldSoccerArticle.id",
    )

