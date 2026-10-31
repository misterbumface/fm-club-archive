"""Small persistent application-wide settings.

These are curator choices for this private archive, not environment secrets.
API keys remain in the environment; safe preferences such as the selected AI
model live here so the web UI and background importer use the same value.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.mixins import TimestampMixin


class AppSetting(TimestampMixin, db.Model):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

