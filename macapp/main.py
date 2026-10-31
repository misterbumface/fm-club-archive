"""FM Club Archive as a Mac app: a native window on one archive's local server (docs/mac-app-plan.md).

    python -m macapp.main        from the source code
    FM Club Archive.app          the packaged app (scripts/build_mac_app.sh)

The window shows the archive's pages; the menu and the welcome page open, make and switch archives.
Each archive runs in its own server process (macapp/server.py).
"""

from __future__ import annotations

import atexit
import fcntl
import json
import logging
import multiprocessing
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from macapp import server, state
from macapp.state import APP_NAME

JOB_CHECKS, JOB_CHECK_PAUSE = 5, 0.12  # a job holds its lock for minutes; a check holds it for an instant
log = logging.getLogger(__name__)

LAUNCHER = Path(__file__).with_name("launcher.html")
_SI_FOLDER = Path.home() / "Library/Application Support/Sports Interactive/Football Manager 2024"
_STEAM_GAME = Path.home() / "Library/Application Support/Steam/steamapps/common/Football Manager 2024"
BACKGROUND = "#151515"


def _page(title: str, text: str) -> str:
    return (f"<html><body style='background:{BACKGROUND};color:#ddd;font:15px -apple-system,sans-serif;"
            f"display:grid;place-items:center;height:96vh'><div style='text-align:center;max-width:560px'>"
            f"<h2 style='font-weight:600'>{title}</h2><p style='color:#999;line-height:1.5'>{text}</p></div></body></html>")


class ArchiveApp:
    """The window, the menu and the server of the open archive."""

    def __init__(self) -> None:
        self.state = state.load()
        self.window = None
        self.process = None
        self.port: int | None = None
        self.folder: Path | None = None

    # -- the window --------------------------------------------------------------------------------
    def run(self) -> None:
        import webview

        state.LOG_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, filename=state.LOG_DIR / "app.log",
                            format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
        self.window = webview.create_window(
            APP_NAME, html=_page(APP_NAME, "Opening…"), js_api=Bridge(self), width=self.state.width, height=self.state.height,
            min_size=(900, 620), background_color=BACKGROUND, text_select=True, zoomable=True)
        self.window.events.closing += self._closing
        self.window.events.resized += self._resized
        atexit.register(self.stop)
        state.APP_DIR.mkdir(parents=True, exist_ok=True)
        webview.start(self._first, menu=self._menu(), private_mode=False, storage_path=str(state.APP_DIR / "webview"))
        self.stop()

    def _first(self) -> None:
        last = Path(self.state.last) if self.state.last else None
        if last and state.is_archive(last):
            self.open(last)
        else:
            self.show_launcher()

    def _closing(self) -> bool:
        if self.job_running() and not self.confirm(
                "An import or a news cycle is running", "Quit anyway? The job stops, and the archive keeps what it wrote before."):
            return False
        return True

    def confirm(self, title: str, message: str) -> bool:
        """A Yes/No dialog. pywebview runs the window's closing handlers on the main thread and waits for them, and its
        own dialog call waits for the main thread: from there it froze the app (18 Oct 2026), so on the main thread the
        alert is shown directly, as pywebview shows its own close dialog."""
        if threading.current_thread() is threading.main_thread():
            from webview.platforms.cocoa import BrowserView

            return bool(BrowserView.display_confirmation_dialog("OK", "Cancel", f"{title}\n\n{message}"))
        return self.window.create_confirmation_dialog(title, message)

    def _resized(self, width: int, height: int) -> None:
        self.state.width, self.state.height = width, height
        state.save(self.state)

    def show_launcher(self) -> None:
        self.window.set_title(APP_NAME)
        self.window.load_html(LAUNCHER.read_text(encoding="utf-8"))

    def go(self, path: str) -> None:
        """Show a page of the archive; a load the window drops (at the first start, while WebKit makes
        its storage) is tried again."""
        if not self.port:
            return
        url = f"http://127.0.0.1:{self.port}{path}"
        for _ in range(3):
            log.info("Showing %s", path)
            self.window.load_url(url)
            for _ in range(20):
                time.sleep(0.25)
                if (self.window.get_current_url() or "").startswith(f"http://127.0.0.1:{self.port}"):
                    return
        log.warning("The window did not show %s", url)

    # -- the archive's server ------------------------------------------------------------------------
    def open(self, folder: Path) -> bool:
        """Show an archive: stop the open one's server and start this one's."""
        folder = folder.expanduser().resolve()
        if self.folder != folder and self.job_running() and not self.confirm(
                "An import or a news cycle is running", "Switch anyway? The job stops."):
            return False
        self.stop()
        name = _archive_name(folder)
        self.window.set_title(f"{APP_NAME} — {name}")
        self.window.load_html(_page(name, "Opening the archive…"))
        self.port = server.free_port()
        self.process = multiprocessing.get_context("spawn").Process(
            target=server.serve, args=(str(folder), self.port, str(state.LOG_DIR)), name=f"archive {name}")
        self.process.start()
        self.folder = folder
        log.info("Starting %s on port %d", folder, self.port)
        if not server.wait_until_up(self.port, timeout=180):
            self.window.load_html(_page("The archive did not start", f"The log is in {state.LOG_DIR / (folder.name + '.log')}."))
            return False
        self.state.remember(folder)
        self.go("/")
        return True

    def stop(self) -> None:
        if self.process is not None and self.process.is_alive():
            self.process.terminate()
            self.process.join(10)
            if self.process.is_alive():
                self.process.kill()
        self.process = None

    def restart(self) -> None:
        if self.folder:
            self.open(self.folder)

    def job_running(self) -> bool:
        """Whether the open archive's import or news cycle holds its job lock. Others take the lock for an instant only
        to ask the same (the fans' slow writer, the Desk): a job holds it for minutes, so the lock counts only when it
        stays taken over half a second."""
        if not self.folder:
            return False
        lock = self.folder / "run" / "archive-job.lock"
        if not lock.exists():
            return False
        for attempt in range(JOB_CHECKS):
            with open(lock, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    if attempt + 1 < JOB_CHECKS:
                        time.sleep(JOB_CHECK_PAUSE)
                    continue
                fcntl.flock(handle, fcntl.LOCK_UN)
                return False
        return True

    def post(self, path: str, fields: dict) -> None:
        """Send a form to the archive's server (the menu's actions), then show the desk's progress."""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=urllib.parse.urlencode(fields).encode(), method="POST")
        try:
            urllib.request.urlopen(request, timeout=30).close()
        except OSError as exc:
            log.warning("%s failed: %s", path, exc)

    # -- the menu -------------------------------------------------------------------------------------
    def _menu(self):
        from webview.menu import Menu, MenuAction, MenuSeparator

        def switch_menu():
            return [MenuAction(_archive_name(folder), lambda folder=folder: self.open(folder)) for folder in self.state.existing()]

        return [
            Menu("File", [
                MenuAction("New Archive…", self.show_launcher),
                MenuAction("Open Archive Folder…", self._open_folder),
                Menu("Switch To", switch_menu() or [MenuAction("(no other archive)", lambda: None)]),
                MenuSeparator(),
                MenuAction("Show Archive in Finder", self._reveal),
                MenuAction("Show Logs in Finder", lambda: _reveal(state.LOG_DIR)),
            ]),
            Menu("Archive", [
                MenuAction("Home", lambda: self.go("/")),
                MenuAction("Import the Newest Save", self._import_newest),
                MenuAction("The Desk (News Cycle)", lambda: self.go("/desk")),
                MenuSeparator(),
                MenuAction("Back Up Now", lambda: (self.post("/settings/backup", {}), self.go("/settings/"))),
                MenuAction("Archive Settings…", lambda: self.go("/settings/")),
            ]),
            Menu("Go", [
                MenuAction("Back", lambda: self.window.evaluate_js("history.back()")),
                MenuAction("Forward", lambda: self.window.evaluate_js("history.forward()")),
                MenuAction("Reload", lambda: self.window.evaluate_js("location.reload()")),
            ]),
        ]

    def _open_folder(self) -> None:
        folder = Bridge(self).choose_folder(str(state.ARCHIVES_DIR))
        if not folder:
            return
        if not state.is_archive(Path(folder)):
            self.confirm("Not an archive", f"{folder} holds no archive (no archive.json or fm_world.sqlite3).")
            return
        self.open(Path(folder))

    def _reveal(self) -> None:
        if self.folder:
            _reveal(self.folder)

    def _import_newest(self) -> None:
        if not self.port:
            return
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/app/newest-save", timeout=10) as response:
                newest = json.load(response).get("save")
        except OSError:
            newest = None
        if newest:
            self.post("/desk/import", {"save": newest, "next": "/desk"})
        self.go("/desk")


class Bridge:
    """What the pages can ask of the app (``window.pywebview.api``): folders, archives, restarts."""

    def __init__(self, app: ArchiveApp) -> None:
        self._app = app

    def choose_folder(self, start: str = "") -> str | None:
        import webview

        start_at = start if start and Path(start).expanduser().is_dir() else str(Path.home())
        chosen = self._app.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start_at)
        return chosen[0] if chosen else None

    def archives(self) -> list[dict]:
        return [{"name": _archive_name(folder), "path": str(folder), "open": folder == self._app.folder}
                for folder in self._app.state.existing()]

    def suggestions(self) -> dict:
        """Where the game's folders usually are."""
        return {"fm_user_dir": str(_SI_FOLDER) if _SI_FOLDER.is_dir() else "", "game_dir": str(_STEAM_GAME) if _STEAM_GAME.is_dir() else ""}

    def inspect_game_folder(self, folder: str) -> dict:
        """What a game folder holds: its saves (newest first), and its faces, logos and stadiums."""
        root = Path(folder).expanduser()
        games = root / "games"
        saves = sorted(games.glob("*.fm"), key=lambda path: path.stat().st_mtime, reverse=True) if games.is_dir() else []
        graphics = root / "graphics"
        return {"ok": games.is_dir(), "saves": [path.name for path in saves],
                "faces": (graphics / "Faces").is_dir(), "logos": (graphics / "Logos").is_dir(), "stadiums": (graphics / "stadium").is_dir()}

    def inspect_game(self, folder: str) -> bool:
        return (Path(folder).expanduser() / "data" / "database" / "db").is_dir()

    def open_archive(self, path: str) -> bool:
        return self._app.open(Path(path))

    def create_archive(self, name: str, fm_user_dir: str, game_dir: str, save_file: str) -> dict:
        """Make a new archive in the app's folder, open it, and start its first import."""
        from config import ArchiveSettings, write_archive_settings

        name = name.strip()
        if not name or not fm_user_dir or not save_file:
            return {"error": "The archive needs a name, a game folder and a save."}
        folder = state.folder_for(name)
        settings = ArchiveSettings(name=name, fm_user_dir=fm_user_dir, game_dir=game_dir or ArchiveSettings().game_dir,
                                   face_folder=f"Archive generated - {name}", save_file=save_file)
        write_archive_settings(settings, folder)
        if not self._app.open(folder):
            return {"error": "The new archive did not start."}
        self._app.post("/desk/import", {"save": save_file, "editions": "no", "next": "/desk"})
        self._app.go("/desk")
        return {"folder": str(folder)}

    def version(self) -> str:
        from macapp.version import APP_VERSION

        return APP_VERSION

    def restart_archive(self) -> None:
        self._app.restart()

    def show_launcher(self) -> None:
        self._app.show_launcher()


def _archive_name(folder: Path) -> str:
    try:
        return json.loads((folder / "archive.json").read_text(encoding="utf-8")).get("name") or folder.name
    except (OSError, ValueError):
        return folder.name


def _reveal(folder: Path) -> None:
    import subprocess

    subprocess.run(["open", str(folder)], check=False)


def main() -> None:
    multiprocessing.freeze_support()  # the packaged app: the reader and server processes start here too
    ArchiveApp().run()


if __name__ == "__main__":
    main()
