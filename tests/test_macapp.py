"""The Mac app's own logic, without a window: its state, and making a new archive (docs/mac-app-plan.md)."""

from __future__ import annotations

import json
from pathlib import Path

import config
from macapp import main, state


class FakeApp:
    """The parts of ArchiveApp the bridge uses, recording what it was asked."""

    def __init__(self) -> None:
        self.state = state.AppState()
        self.folder = None
        self.opened: list[Path] = []
        self.posted: list[tuple[str, dict]] = []
        self.shown: list[str] = []

    def open(self, folder: Path) -> bool:
        self.opened.append(folder)
        self.folder = folder
        return True

    def post(self, path: str, fields: dict) -> None:
        self.posted.append((path, fields))

    def go(self, path: str) -> None:
        self.shown.append(path)


def test_a_new_archive_gets_its_folder_settings_and_first_import(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ARCHIVES_DIR", tmp_path / "archives")
    app = FakeApp()
    bridge = main.Bridge(app)
    games = tmp_path / "fm" / "games"
    games.mkdir(parents=True)
    (games / "Barcelona.fm").write_bytes(b"save")
    found = bridge.inspect_game_folder(str(tmp_path / "fm"))
    assert found["ok"] and found["saves"] == ["Barcelona.fm"] and not found["faces"]

    result = bridge.create_archive("Barcelona 2024", str(tmp_path / "fm"), "", "Barcelona.fm")
    folder = Path(result["folder"])
    assert folder == tmp_path / "archives" / "Barcelona 2024" and app.opened == [folder]
    settings = config.read_archive_settings(folder)
    assert (settings.name, settings.save_file, settings.face_folder) == ("Barcelona 2024", "Barcelona.fm", "Archive generated - Barcelona 2024")
    assert app.posted == [("/desk/import", {"save": "Barcelona.fm", "editions": "no", "next": "/desk"})]
    assert state.folder_for("Barcelona 2024") == tmp_path / "archives" / "Barcelona 2024 2"  # the name is taken now
    assert "error" in bridge.create_archive("", str(tmp_path / "fm"), "", "Barcelona.fm")


def test_the_app_remembers_its_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "APP_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "app.json")
    archive = tmp_path / "one"
    archive.mkdir()
    (archive / "archive.json").write_text(json.dumps({"name": "One"}))
    remembered = state.load()
    remembered.remember(archive)
    remembered.remember(tmp_path / "gone")
    again = state.load()
    assert again.last == str(tmp_path / "gone") and again.existing() == [archive]
    assert main._archive_name(archive) == "One"


def test_a_lock_taken_for_an_instant_is_no_job_but_one_held_is(tmp_path, monkeypatch):
    import fcntl
    import threading
    import time

    monkeypatch.setattr(main, "JOB_CHECK_PAUSE", 0.05)
    app = object.__new__(main.ArchiveApp)
    app.folder = tmp_path
    (tmp_path / "run").mkdir()
    lock = tmp_path / "run" / "archive-job.lock"
    lock.touch()
    assert not app.job_running()

    def hold(seconds: float) -> None:
        with open(lock, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            time.sleep(seconds)
            fcntl.flock(handle, fcntl.LOCK_UN)

    brief = threading.Thread(target=hold, args=(0.06,))  # the slow writer asking whether a job runs
    brief.start()
    time.sleep(0.01)
    assert not app.job_running()
    brief.join()
    held = threading.Thread(target=hold, args=(1.0,))  # a job
    held.start()
    time.sleep(0.01)
    assert app.job_running()
    held.join()


def test_the_close_dialog_runs_directly_on_the_main_thread(monkeypatch):
    import sys
    import types

    shown = []
    cocoa = types.ModuleType("webview.platforms.cocoa")
    cocoa.BrowserView = type("BrowserView", (), {"display_confirmation_dialog": staticmethod(
        lambda first, second, message: shown.append(message) or True)})
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", cocoa)
    app = object.__new__(main.ArchiveApp)
    app.window = None  # the window's own dialog call must not be used: from the main thread it waits on itself
    assert app.confirm("A job is running", "Quit anyway?") and "Quit anyway?" in shown[0]
