"""The app's own state: where the archives are, which one was open, the window's size.

Everything lives under ``~/Library/Application Support/FM Club Archive`` (``FM_APP_DIR`` overrides it):
``app.json`` and one folder per archive in ``archives/``. An archive folder is the one step 1 made
(docs/mac-app-plan.md): it can also live elsewhere, and the app remembers its path.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "FM Club Archive"
APP_DIR = Path(os.environ.get("FM_APP_DIR") or Path.home() / "Library" / "Application Support" / APP_NAME).expanduser()
ARCHIVES_DIR = APP_DIR / "archives"
LOG_DIR = Path(os.environ.get("FM_LOG_DIR") or Path.home() / "Library" / "Logs" / APP_NAME).expanduser()
STATE_FILE = APP_DIR / "app.json"


@dataclass
class AppState:
    archives: list[str] = field(default_factory=list)  # archive folders, as paths
    last: str | None = None  # the archive open when the app last closed
    width: int = 1280
    height: int = 860

    def remember(self, folder: Path) -> None:
        path = str(folder)
        if path not in self.archives:
            self.archives.append(path)
        self.last = path
        save(self)

    def forget(self, folder: Path) -> None:
        self.archives = [path for path in self.archives if path != str(folder)]
        if self.last == str(folder):
            self.last = None
        save(self)

    def existing(self) -> list[Path]:
        """The remembered archives whose folders still hold an archive."""
        return [Path(path) for path in self.archives if is_archive(Path(path))]


def load() -> AppState:
    try:
        stored = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppState()
    known = {key: value for key, value in stored.items() if key in AppState.__dataclass_fields__}
    return AppState(**known)


def save(state: AppState) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(asdict(state), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def is_archive(folder: Path) -> bool:
    """A folder that holds an archive: its settings file or its world."""
    return (folder / "archive.json").is_file() or (folder / "fm_world.sqlite3").is_file()


def folder_for(name: str) -> Path:
    """A new archive's folder in the app's archives, named after it (and numbered if taken)."""
    base = re.sub(r"[^\w\- .()]+", "", name).strip(" .") or "Archive"
    folder, number = ARCHIVES_DIR / base, 2
    while folder.exists():
        folder, number = ARCHIVES_DIR / f"{base} {number}", number + 1
    return folder
