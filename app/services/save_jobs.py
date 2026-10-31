"""Importing saves from the browser (the desk): the saves in the game's folder, which of them the
archive has, and one background job at a time — an import followed by the news cycle, or a news
cycle alone. The status text is what the desk shows while it runs.

The web app runs in several processes, so the job's state lives in files that all of them see:
a lock file (held for the whole job: one job at a time across processes) and a status file.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from flask import Flask, current_app
from sqlalchemy import select

from app.extensions import db
from app.models import world as world_models
from config import ARCHIVE

log = logging.getLogger(__name__)

GAMES_DIR = ARCHIVE.games_dir

HIDDEN_SETTING = "hidden_saves"  # a JSON list of save file names the desk does not show (test saves, backups)

def _run_dir(app: Flask | None = None) -> Path:
    folder = Path((app or current_app).instance_path) / "run"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@dataclass
class SaveFile:
    name: str
    modified: datetime
    size_mb: float
    imported_at: datetime | None  # when the archive last read this file
    game_date: date | None  # the game date it had then

    @property
    def changed_since_import(self) -> bool:
        return self.imported_at is not None and self.modified > self.imported_at

    @property
    def new(self) -> bool:
        return self.imported_at is None or self.changed_since_import


def hidden_saves() -> set[str]:
    from app.models import AppSetting

    setting = AppSetting.query.filter_by(key=HIDDEN_SETTING).first()
    try:
        return set(json.loads(setting.value)) if setting else set()
    except ValueError:
        return set()


def hide_save(name: str) -> None:
    from app.models import AppSetting

    names = sorted(hidden_saves() | {name})
    setting = AppSetting.query.filter_by(key=HIDDEN_SETTING).first()
    if setting is None:
        db.session.add(AppSetting(key=HIDDEN_SETTING, value=json.dumps(names)))
    else:
        setting.value = json.dumps(names)
    db.session.commit()


def list_saves() -> list[SaveFile]:
    """The game's saves that are not hidden, newest first, with what the archive knows about each."""
    if not GAMES_DIR.is_dir():
        return []
    hidden = hidden_saves()
    table = world_models.WorldSave.__table__
    with db.engines[world_models.BIND].connect() as connection:
        try:
            known = {row.file_name: row for row in connection.execute(select(table.c.file_name, table.c.imported_at, table.c.game_date))}
        except Exception:  # noqa: BLE001 - an empty world database has no saves table yet
            known = {}
    saves = []
    for path in GAMES_DIR.glob("*.fm"):
        if path.name in hidden:
            continue
        stat = path.stat()
        row = known.get(path.name)
        saves.append(SaveFile(path.name, datetime.fromtimestamp(stat.st_mtime), round(stat.st_size / 1_048_576, 1),
                              row.imported_at if row else None, row.game_date if row else None))
    return sorted(saves, key=lambda save: save.modified, reverse=True)


def belongs(name: str) -> bool:
    """Whether a save is this archive's own: its save, or a copy saved under a longer name
    ("... (v02)", "... September"). Without a chosen save, every save belongs."""
    if not ARCHIVE.save_file:
        return True
    return Path(name).stem.startswith(Path(ARCHIVE.save_file).stem)


def newest_waiting() -> SaveFile | None:
    """This archive's newest save, when the archive does not have it yet (or it changed since it was read)."""
    saves = [save for save in list_saves() if belongs(save.name)]
    return saves[0] if saves and saves[0].new else None


def status(app: Flask | None = None) -> str | None:
    """The running or last job's status text."""
    try:
        return json.loads((_run_dir(app) / "archive-job.json").read_text())["status"]
    except (OSError, ValueError, KeyError):
        return None


def job(app: Flask | None = None) -> dict[str, str | bool | None]:
    """What the pages show: the status text, whether a job runs, and its state
    (working, done or failed; none before the first job)."""
    text, running = status(app), busy(app)
    if running:
        state = "working"
    elif text is None:
        state = None
    elif text.startswith("Failed"):
        state = "failed"
    elif text.startswith("Done"):
        state = "done"
    else:  # the process stopped in the middle of a job (a restart)
        state, text = "failed", f"Stopped before it finished (the web app restarted). The last step was: {text}"
    return {"status": text, "busy": running, "state": state}


def busy(app: Flask | None = None) -> bool:
    """True while some process holds the job lock."""
    with open(_run_dir(app) / "archive-job.lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def _setter(app: Flask):
    target = _run_dir(app) / "archive-job.json"

    def set_status(text: str) -> None:
        target.write_text(json.dumps({"status": text, "pid": os.getpid(), "at": datetime.now().isoformat(timespec="seconds")}))
        log.info("job: %s", text)

    return set_status


def start_import(app: Flask, name: str, make_editions: bool = True) -> None:
    """Import one save from the games folder, then make the news cycle, in the background."""
    path = GAMES_DIR / name
    if path.parent != GAMES_DIR or path.suffix != ".fm" or not path.is_file():
        raise ValueError("That save is not in the game's folder.")
    _start(app, lambda set_status: _import(path, make_editions, set_status))


def start_editions(app: Flask, since: date | None = None, until: date | None = None) -> None:
    """Make the news cycle's editions in the background."""
    from app.services import editions

    _start(app, lambda set_status: _finish(editions.make_editions(since, until, progress=set_status), set_status))


def start_pictures(app: Flask, issue_ids: list[int], again: bool = False) -> None:
    """Draw Things illustrations for the lead stories of the given editions, in the background."""
    from app.services import article_pictures

    def work(set_status) -> None:
        made = article_pictures.illustrate_editions(issue_ids, again, progress=lambda text: set_status(f"Pictures: {text}"))
        set_status(f"Done: {made} front-page picture{'s' if made != 1 else ''}.")
    _start(app, work)


def start_competition_pictures(app: Flask, count: int) -> None:
    """Draw Things pictures for the pools of the competitions in the news, in the background."""
    from app.services import article_pictures

    def work(set_status) -> None:
        made = article_pictures.draw_competition_pools(count, progress=lambda text: set_status(f"Pictures: {text}"))
        set_status(f"Done: {made} competition picture{'s' if made != 1 else ''}.")
    _start(app, work)


def start_club_pictures(app: Flask, count: int) -> None:
    """Pictures for the pools of the followed clubs and the clubs in the news, in the background."""
    from app.services import article_pictures

    def work(set_status) -> None:
        made = article_pictures.draw_club_pools(count, progress=lambda text: set_status(f"Pictures: {text}"))
        set_status(f"Done: {made} club picture{'s' if made != 1 else ''}.")
    _start(app, work)


def cloud_configured() -> bool:
    """Whether a publish can run: a Cloudflare token is kept (needs an app context)."""
    from app.services import site_publish
    from app.services.ai_settings import stored_key

    return bool(stored_key(site_publish.TOKEN_KEY))


def start_publish(app: Flask, reuse: bool = False) -> None:
    """The paper to Cloudflare, in the background: the export (unless the folder is fresh, or ``reuse``), then the upload,
    the result kept in the settings."""
    _start(app, lambda set_status: _publish(app, set_status, reuse))


def _publish(app: Flask, set_status, reuse: bool = False, prefix: str = "", workers: int | None = None) -> None:
    from config import ARCHIVE_DIR, read_archive_settings

    from app.public import create_public_app
    from app.services import site_publish
    from app.services.ai_settings import stored_key
    from app.services.story_leads import followed_clubs

    with app.app_context():
        token = stored_key(site_publish.TOKEN_KEY)
        hostname = site_publish.setting(site_publish.HOSTNAME_SETTING)
        account = site_publish.setting(site_publish.ACCOUNT_SETTING)
        followed = followed_clubs()
        name = read_archive_settings(ARCHIVE_DIR).name
    site = create_public_app()
    try:
        result = site_publish.publish(site, ARCHIVE_DIR / "published", name, followed, token, hostname, account,
                                      progress=lambda text: set_status(f"{prefix}Publish: {text}"), reuse=reuse,
                                      workers=workers or site_publish.EXPORT_WORKERS)
    except Exception as exc:  # noqa: BLE001 - the error goes to the Desk and the settings
        with app.app_context():
            site_publish.set_setting(site_publish.LAST_SETTING, json.dumps({"when": datetime.now().isoformat(timespec="minutes"), "error": str(exc)}))
        set_status(f"Failed: {prefix}the publish failed: {exc}")  # "Failed:" first, or the panel takes it for a crash
        return
    with app.app_context():
        site_publish.set_setting(site_publish.LAST_SETTING, json.dumps(result))
    set_status(f"Done: {prefix}the paper is at {result['address']} ({result['files']} files).")


def start_auto(app: Flask, name: str, on_failure=None) -> None:
    """The automatic chain (auto_pilot): import the save, write the papers for every save with AUTO_MIN_DAYS of new game
    time (the Import button waits a whole CYCLE_DAYS), then publish when the cloud is set up."""
    path = GAMES_DIR / name
    if path.parent != GAMES_DIR or path.suffix != ".fm" or not path.is_file():
        raise ValueError("That save is not in the game's folder.")

    def work(set_status) -> None:
        try:
            _import(path, True, set_status, min_days=AUTO_MIN_DAYS)
        except Exception:
            if on_failure:
                on_failure()  # the watcher leaves this save alone until the game writes it again
            raise
        done = status(app) or ""
        with app.app_context():
            publish = cloud_configured()
        if not publish:
            return
        _publish(app, set_status, prefix=f"{done.removeprefix('Done: ').rstrip('.')}; ", workers=AUTO_EXPORT_WORKERS)

    _start(app, work)


def _start(app: Flask, work) -> None:
    handle = open(_run_dir(app) / "archive-job.lock", "a")  # noqa: SIM115 - held until the job ends
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("An import or news cycle is already running: wait for it to finish.") from None
    set_status = _setter(app)
    set_status("Starting…")

    def run() -> None:
        with app.app_context():
            try:
                work(set_status)
            except Exception as exc:  # noqa: BLE001 - the desk shows what went wrong
                app.logger.exception("background job failed")
                set_status(f"Failed: {exc}")
            finally:
                db.session.remove()
                fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()

    threading.Thread(target=run, name="archive-job", daemon=True).start()


AUTO_MIN_DAYS = 2  # the automatic chain writes the papers for every save with this much new game time (the paper's size follows the days)
AUTO_EXPORT_WORKERS = 5  # the automatic chain renders the site with fewer processes than the Publish button (eight): the Mac stays usable


def _import(path: Path, make_editions: bool, set_status, min_days: int | None = None) -> None:
    from app.services import editions
    from app.services.save_import import import_save

    result = import_save(path, progress=lambda text: set_status(f"Importing {path.name}: {text}"))
    from app.services import social_feed

    social_feed.make_posts(progress=set_status)  # templates only: a second or two (nothing when the feed is off)
    if not make_editions:
        set_status(f"Done: imported {path.name} (game date {result.game_date:%-d %B %Y}).")
        return
    cycle = editions.make_editions(progress=set_status, min_days=editions.CYCLE_DAYS if min_days is None else min_days)
    _finish(cycle, set_status, f"imported {path.name} (game date {result.game_date:%-d %B %Y}); ", after_import=True)


def _finish(cycle, set_status, prefix: str = "", after_import: bool = False) -> None:
    from app.services import editions

    if cycle is None:
        due = editions.next_cycle_due() if after_import else None
        later = f"the next papers come out from {due:%-d %B %Y} (game date)" if due else "nothing new for the papers"
        set_status(f"Done: {prefix}{later}.")
    else:
        from app.services import social_feed

        social_feed.post_articles(cycle.editions)  # the writers post their lead stories on the Feed
        set_status(f"Done: {prefix}{len(cycle.editions)} editions, {cycle.written} articles"
                   + (f", {cycle.illustrated} pictures" if cycle.illustrated else "")
                   + (f", {len(cycle.refused)} articles refused" if cycle.refused else "") + ".")
