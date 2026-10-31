"""An explicit navigable universe entity mentioned by a World Soccer story."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.world_soccer_article import WorldSoccerArticle


class WorldSoccerEntityLink(CreatedAtMixin, db.Model):
    __tablename__ = "world_soccer_entity_links"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "entity_type",
            "entity_id",
            name="uq_world_soccer_article_entity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("world_soccer_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(240))
    # The same entity in the world database: person:<uid>, club:<uid>, competition:<uid>,
    # edition:<uid>:<year>, match:<date>:<home uid>:<away uid>, club_season:<uid>:<season>, nation:<uid>.
    world_ref: Mapped[str | None] = mapped_column(String(80))

    article: Mapped["WorldSoccerArticle"] = relationship(
        back_populates="entity_links"
    )

