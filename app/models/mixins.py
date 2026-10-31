"""Timestamp mixins shared by every model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class CreatedAtMixin:
    """For append-only, immutable records (e.g. Goal, PlayerAlias, SourceRecord)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    """For records that can be edited after creation."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
