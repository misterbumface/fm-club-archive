"""The migrations and the models must describe the same database."""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from app import create_app
from app.extensions import db
from config import TestingConfig

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_migrated_schema_matches_models(tmp_path, monkeypatch):
    monkeypatch.setattr(
        TestingConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path / 'migrated.sqlite3'}"
    )
    app = create_app("testing")

    with app.app_context():
        upgrade(directory=str(MIGRATIONS))
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection)
            differences = compare_metadata(context, db.metadata)
        db.session.remove()
        db.engine.dispose()

    assert differences == []
