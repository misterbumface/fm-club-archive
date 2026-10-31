"""Curated image assets and their archive-entity context."""

from __future__ import annotations

from datetime import date as date_type
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.world_soccer_image_use import WorldSoccerImageUse


class ArchiveImage(TimestampMixin, db.Model):
    """One internally-served image with editorial context.

    Links are deliberately polymorphic: one matchday photograph can belong
    to both clubs, the competition and the match without adding a forest of
    nullable foreign keys.  The stored URL is a snapshot-safe internal URL;
    articles retain their own copy of it even when the asset is later retired.
    """

    __tablename__ = "archive_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default="photograph", index=True
    )
    caption: Mapped[str | None] = mapped_column(String(500))
    alt_text: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="upload")
    captured_on: Mapped[date_type | None] = mapped_column(Date)
    era_start_year: Mapped[int | None] = mapped_column(Integer)
    era_end_year: Mapped[int | None] = mapped_column(Integer)
    tags_json: Mapped[list | None] = mapped_column(JSON)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repeat_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="avoid_recent"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    links: Mapped[list["ArchiveImageLink"]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )
    usages: Mapped[list["WorldSoccerImageUse"]] = relationship(
        back_populates="image"
    )


class ArchiveImageLink(TimestampMixin, db.Model):
    """A many-to-many contextual link from an image to an archive entity."""

    __tablename__ = "archive_image_links"
    __table_args__ = (
        UniqueConstraint(
            "image_id",
            "entity_type",
            "entity_id",
            "relation",
            name="uq_archive_image_entity_link",
        ),
        Index("ix_archive_image_links_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("archive_images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    relation: Mapped[str] = mapped_column(String(40), nullable=False, default="subject")
    label: Mapped[str | None] = mapped_column(String(240))
    # The subject in the world database (docs/images-plan.md): person:<uid>, club:<uid>,
    # competition:<uid>, edition:<uid>:<year>, match:<date>:<home uid>:<away uid>, nation:<uid>.
    world_ref: Mapped[str | None] = mapped_column(String(80), index=True)

    image: Mapped[ArchiveImage] = relationship(back_populates="links")
