"""One archive's web server, in its own process.

The archive's settings are read when ``config`` is first imported (``FM_ARCHIVE_DIR``), so each archive
gets a fresh process: switching archives stops one server and starts another. waitress serves the
Flask app on 127.0.0.1; the import jobs it starts spawn their own reader processes as before.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

BACKUP_EVERY = timedelta(hours=24)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def serve(archive_dir: str, port: int, log_dir: str) -> None:
    """Run one archive's server until the process is stopped (the target of a spawned process)."""
    os.environ["FM_ARCHIVE_DIR"] = archive_dir
    os.environ.setdefault("FLASK_CONFIG", "production")
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.FileHandler(Path(log_dir) / f"{Path(archive_dir).name}.log", encoding="utf-8")],
    )
    from waitress import serve as waitress_serve

    from app import create_app

    app = create_app()
    _upgrade(app)
    threading.Thread(target=_backups, args=(app,), daemon=True, name="backups").start()
    threading.Thread(target=_slow_fans, args=(app,), daemon=True, name="fans").start()
    threading.Thread(target=_follow_parent, args=(os.getppid(),), daemon=True, name="parent").start()
    threading.Thread(target=_public_site, daemon=True, name="public").start()
    threading.Thread(target=_automatic, args=(app,), daemon=True, name="automatic").start()
    logging.getLogger(__name__).info("Archive %s on port %d", archive_dir, port)
    waitress_serve(app, host="127.0.0.1", port=port, threads=8, _quiet=True)


def _upgrade(app) -> None:
    """Bring the archive up to the app's version: the library's migrations, and the world's new
    tables and columns. A new app version needs nothing else: there is no terminal to run them in."""
    from flask_migrate import upgrade

    from app.extensions import db
    from app.models import world
    from app.services.save_import import create_tables

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    with app.app_context():
        upgrade(directory=str(app.extensions["migrate"].directory))
        create_tables(db.engines[world.BIND])
    # The migrations' env.py sets up logging from alembic.ini, which drops this process's log file
    # and silences the loggers made before it: put them back.
    root.handlers, root.level = handlers, level
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False


def _automatic(app) -> None:
    """The automatic switch (app/services/auto_pilot): a look at the game's folder every two minutes while the app runs."""
    from app.services import auto_pilot

    auto_pilot.watch(app)


def _public_site() -> None:
    """World Sport (app/public): the published stories for the whole network, on PUBLIC_PORT (FM_PUBLIC_PORT, 0 for none).
    Only the first archive to start gets the port; the others log it and go on."""
    from waitress import serve as waitress_serve

    from app.public import PUBLIC_PORT, create_public_app

    port = int(os.environ.get("FM_PUBLIC_PORT", PUBLIC_PORT))
    if not port:
        return
    log = logging.getLogger(__name__)
    site = create_public_app()
    threading.Thread(target=_warm_history, args=(site,), daemon=True, name="public history").start()
    try:
        log.info("The public site on port %d", port)
        waitress_serve(site, host="0.0.0.0", port=port, threads=4, _quiet=True)
    except OSError as error:
        log.warning("The public site could not open port %d: %s", port, error)


WARM_EVERY = 300  # seconds between checks for a new game date (after an import)


def _warm_history(site) -> None:
    """The public site's history lists (records, legends), worked out after start and after each import, so no visitor waits."""
    from app.public import football, history

    while True:
        try:
            with site.test_request_context(), football.connect() as c:  # a request context: the lists build picture addresses
                history.warm(c)
        except Exception:  # noqa: BLE001 - a busy or empty archive: try again later
            logging.getLogger(__name__).exception("The public site's history could not be prepared")
        time.sleep(WARM_EVERY)


def _follow_parent(parent: int) -> None:
    """Stop with the app: when the app's process is gone (quit, crashed or killed), this one ends too."""
    while os.getppid() == parent:
        time.sleep(2)
    logging.getLogger(__name__).info("The app is gone: the server stops")
    os._exit(0)


def _slow_fans(app) -> None:
    """The social feed's slow writer: fans' threads with a local model while the app runs, written a little ahead of
    their replies, which show one by one (social_fans.trickle_once says when it may write)."""
    from app.extensions import db
    from app.services import social_fans

    log = logging.getLogger("fans")
    time.sleep(social_fans.SLOW_PAUSE)  # let the app open first
    while True:
        wrote = False
        try:
            with app.app_context():
                wrote = social_fans.trickle_once(log.info)
                db.session.remove()
        except Exception:  # noqa: BLE001 - the writer must not stop the app; try again later
            log.exception("the slow writer failed")
        try:
            with app.app_context():
                pause = social_fans.slow_pause(wrote)
                db.session.remove()
        except Exception:  # noqa: BLE001
            pause = social_fans.SLOW_IDLE
        time.sleep(pause)


def _backups(app) -> None:
    """The library's daily backup, as the launchd service made it: at start when the newest automatic
    backup is a day old or more, then every day while the app runs."""
    from app.services.backup_service import AUTO_PREFIX, create_backup, sqlite_path_from_uri

    log = logging.getLogger("backups")
    folder = Path(app.instance_path) / "backups"
    while True:
        newest = max((path.stat().st_mtime for path in folder.glob(f"{AUTO_PREFIX}*")), default=0)
        due = datetime.fromtimestamp(newest) + BACKUP_EVERY
        if datetime.now() >= due:
            try:
                result = create_backup(sqlite_path_from_uri(app.config["SQLALCHEMY_DATABASE_URI"]), folder)
                log.info("Backup saved: %s", result.path.name)
            except Exception:  # noqa: BLE001 - a failed backup is logged; the next day tries again
                log.exception("The backup failed")
            due = datetime.now() + BACKUP_EVERY
        time.sleep(max(60.0, (due - datetime.now()).total_seconds()))


def wait_until_up(port: int, timeout: float = 60.0) -> bool:
    """Whether the server answers on the port within the time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False
