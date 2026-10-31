"""The automatic switch (25 Oct 2026): while it is on and the app runs, the archive watches the game's folder, and when a
newer save of its own appears it does by itself what the desk does by hand: import the save, make the news cycle, and
put the paper in the cloud when a Cloudflare token is set. The game writes a save in pieces, so a save counts only once it
has been still for SETTLE_SECONDS."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from app.extensions import db
from app.models.app_setting import AppSetting

log = logging.getLogger(__name__)

SETTING = "auto_pilot"
LAST_SETTING = "auto_pilot_last"  # what the last automatic run did, for the settings card
FAILED_SETTING = "auto_pilot_failed"  # the save (name and file time) whose automatic run failed: not tried again until the game writes it again
CHECK_SECONDS = 120  # between looks at the game's folder
SETTLE_SECONDS = 90  # a save must be this old before it is read (the game is still writing a younger one)
last_look: datetime | None = None  # when the watcher last looked (this process; the settings card shows it)


def enabled() -> bool:
    try:
        setting = AppSetting.query.filter_by(key=SETTING).first()
    except Exception:  # noqa: BLE001 - no app context or no table: off
        return False
    return bool(setting and json.loads(setting.value))


def set_enabled(value: bool) -> None:
    _set(SETTING, json.dumps(bool(value)))


def last() -> dict | None:
    setting = AppSetting.query.filter_by(key=LAST_SETTING).first()
    try:
        return json.loads(setting.value) if setting else None
    except ValueError:
        return None


def failed(save) -> None:
    """Remember that the automatic run of this save failed, so the watcher does not start it again every look."""
    _set(FAILED_SETTING, json.dumps({"save": save.name, "modified": save.modified.isoformat()}))


def _failed_before(save) -> bool:
    setting = AppSetting.query.filter_by(key=FAILED_SETTING).first()
    try:
        mark = json.loads(setting.value) if setting else None
    except ValueError:
        return False
    return bool(mark) and mark.get("save") == save.name and mark.get("modified") == save.modified.isoformat()


def _set(key: str, value: str) -> None:
    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value
    db.session.commit()


def due(now: datetime | None = None):
    """The save to read now: this archive's newest, not read yet or changed since, and still for SETTLE_SECONDS."""
    from app.services import save_jobs

    save = save_jobs.newest_waiting()
    if save is None:
        return None
    age = ((now or datetime.now()) - save.modified).total_seconds()
    return save if age >= SETTLE_SECONDS else None


def watching() -> dict:
    """What the settings card says about the watcher: its last look and the newest save's state."""
    from app.services import save_jobs

    saves = [save for save in save_jobs.list_saves() if save_jobs.belongs(save.name)]
    newest = saves[0] if saves else None
    if newest is None:
        state = "no save of this archive in the game's folder"
    elif not newest.new:
        state = f"{newest.name} is read (game date {newest.game_date:%-d %B %Y})" if newest.game_date else f"{newest.name} is read"
    elif (datetime.now() - newest.modified).total_seconds() < SETTLE_SECONDS:
        state = f"{newest.name} is being written; it is read once it has been still for {SETTLE_SECONDS} seconds"
    else:
        state = f"{newest.name} is new and waits for the next look"
    return {"last_look": last_look, "newest": state}


def run_once(app) -> str | None:
    """One look: start the chain for a due save unless a job runs. Returns what was started, for the log and the tests."""
    global last_look
    from app.services import save_jobs

    last_look = datetime.now()
    with app.app_context():
        if not enabled() or save_jobs.busy(app):
            return None
        save = due()
        if save is None or _failed_before(save):
            return None
        try:
            save_jobs.start_auto(app, save.name, on_failure=lambda: failed(save))
        except RuntimeError as exc:  # a job started between the check and now
            log.info("automatic: %s", exc)
            return None
        _set(LAST_SETTING, json.dumps({"when": datetime.now().isoformat(timespec="minutes"), "save": save.name}))
        return save.name


def watch(app) -> None:
    """The thread the Mac app runs: a look every CHECK_SECONDS, for as long as the app runs."""
    log.info("automatic: watching the game's folder every %d seconds", CHECK_SECONDS)
    while True:
        try:
            started = run_once(app)
            if started:
                log.info("automatic: reading %s", started)
        except Exception:  # noqa: BLE001 - the watcher survives a bad look
            log.exception("automatic: the look failed")
        time.sleep(CHECK_SECONDS)
