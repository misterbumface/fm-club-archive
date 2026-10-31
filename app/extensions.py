"""Flask extension instances, created here to avoid circular imports."""

from __future__ import annotations

import sqlite3

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
migrate = Migrate()


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Apply the archive's SQLite settings to every new connection.

    ``foreign_keys`` is a per-connection switch that SQLite leaves off, so
    without this the declared foreign keys are never checked. WAL lets the
    Gunicorn workers read while one connection writes. ``synchronous=NORMAL``
    is the setting WAL is designed for: a crash cannot corrupt the file.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")  # stays "memory" for :memory:
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()
