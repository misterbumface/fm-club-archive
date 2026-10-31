"""Application factory for FM Club Archive.

The archive is built from Football Manager saves (docs/rewrite-plan.md): the world pages
are the site, with World Soccer, the settings page and the logo route beside them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask

from app.extensions import db, migrate


def create_app(config_name: str | None = None) -> Flask:
    """Build and configure the Flask application.

    Args:
        config_name: One of "development", "testing", "production", or
            "default". Falls back to the FLASK_CONFIG environment variable
            and then "default" when not supplied.
    """
    import os

    from config import ARCHIVE_DIR, BASE_DIR, CONFIG_BY_NAME

    # The archive folder is the instance folder: databases, backups, logs and job files live there.
    app = Flask(__name__, instance_path=str(ARCHIVE_DIR), instance_relative_config=True)

    resolved_name = config_name or os.environ.get("FLASK_CONFIG", "default")
    app.config.from_object(CONFIG_BY_NAME[resolved_name])
    if not app.config.get("SECRET_KEY"):
        from config import _load_secret_key

        app.config["SECRET_KEY"] = _load_secret_key()

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    _configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db, directory=str(BASE_DIR / "migrations"))

    from app import models  # noqa: F401  (ensure models are registered)

    _register_blueprints(app)
    _register_cli(app)
    _register_error_handlers(app)
    if not app.testing:
        _prepare_archive(app)

    return app


def _prepare_archive(app: Flask) -> None:
    """A new archive folder gets its library (all migrations) and the world's tables, so it opens
    empty, ready for its first save."""
    from sqlalchemy.engine import make_url

    def missing(uri: str) -> bool:
        url = make_url(uri)
        return url.get_backend_name() == "sqlite" and bool(url.database) and not Path(url.database).exists()

    library_new = missing(app.config["SQLALCHEMY_DATABASE_URI"])
    world_new = missing(app.config["SQLALCHEMY_BINDS"]["world"])
    if not (library_new or world_new):
        return
    with app.app_context():
        if library_new:
            from flask_migrate import upgrade

            app.logger.info("New archive: making the library in %s", app.instance_path)
            upgrade(directory=str(Path(app.extensions["migrate"].directory)))
        if world_new:
            from app.models import world
            from app.services.save_import import create_tables

            create_tables(db.engines[world.BIND])


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app.logger.setLevel(level)


def _register_blueprints(app: Flask) -> None:
    from flask import redirect, request

    from app.routes.assets import assets_bp
    from app.routes.news import news_bp
    from app.routes.settings import settings_bp
    from app.routes.social import social_bp
    from app.routes.world import world_bp
    from app.routes.world_soccer import world_soccer_bp

    app.register_blueprint(news_bp)
    app.register_blueprint(world_bp)
    app.register_blueprint(world_soccer_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(social_bp)
    app.register_blueprint(assets_bp)

    from flask import send_from_directory

    from config import GENERATED_URL

    # Pictures made or uploaded by this archive live in its folder, at their old addresses.
    @app.get(GENERATED_URL + "/<path:filename>")
    def generated(filename: str):
        return send_from_directory(app.config["GENERATED_DIR"], filename, max_age=86400)

    # The world pages lived under /world before the cut-over; saved links keep working.
    @app.get("/world/", defaults={"rest": ""})
    @app.get("/world/<path:rest>")
    def old_world_address(rest: str):
        query = request.query_string.decode()
        return redirect(f"/{rest}" + (f"?{query}" if query else ""), code=301)


def _register_cli(app: Flask) -> None:
    from app.cli import register_cli_commands

    register_cli_commands(app)


def _register_error_handlers(app: Flask) -> None:
    from flask import render_template

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("errors/500.html"), 500
