"""Application configuration objects."""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# An archive is one folder: one game's world, its library of texts, its pictures and its settings.
# FM_ARCHIVE_DIR chooses it; the first archive lives in instance/.
ARCHIVE_DIR = Path(os.environ.get("FM_ARCHIVE_DIR") or BASE_DIR / "instance").expanduser().resolve()
INSTANCE_DIR = ARCHIVE_DIR
GENERATED_DIR = ARCHIVE_DIR / "generated"  # pictures made or uploaded here, served at GENERATED_URL
GENERATED_URL = "/static/generated"
SETTINGS_FILE = "archive.json"

_SI = Path.home() / "Library/Application Support/Sports Interactive/Football Manager 2024"
_STEAM = Path.home() / "Library/Application Support/Steam/steamapps/common/Football Manager 2024"


@dataclass(frozen=True)
class ArchiveSettings:
    """Where one archive finds its game: FM's own folder (games/, graphics/) and the installed game."""

    name: str = "FM Club Archive"
    fm_user_dir: str = str(_SI)  # FM's user folder, as set in the game's preferences
    game_dir: str = str(_STEAM)  # the installed game, for its language file (competition and award names)
    face_folder: str = "Archive generated"  # this archive's own folder in the face pack
    save_file: str | None = None  # the save this archive follows, in games_dir ("Sam Archer - Chelsea v2 - loxal.fm")

    @property
    def games_dir(self) -> Path:
        return Path(os.environ.get("FM_GAMES_DIR") or Path(self.fm_user_dir) / "games").expanduser()

    @property
    def faces_dir(self) -> Path:
        return Path(os.environ.get("FM_FACES_DIR") or Path(self.fm_user_dir) / "graphics" / "Faces").expanduser()

    @property
    def stadiums_dir(self) -> Path:
        return Path(os.environ.get("FM_STADIUMS_DIR") or Path(self.fm_user_dir) / "graphics" / "stadium").expanduser()

    @property
    def logos_dir(self) -> Path:
        return Path(os.environ.get("FM_LOGO_DIR") or Path(self.fm_user_dir) / "graphics" / "Logos").expanduser()

    @property
    def flags_dir(self) -> Path:
        return Path(os.environ.get("FM_FLAGS_DIR") or Path(self.fm_user_dir) / "graphics" / "Flags").expanduser()

    @property
    def game_database_dir(self) -> Path:
        return Path(self.game_dir).expanduser() / "data" / "database" / "db"


def read_archive_settings(folder: Path = ARCHIVE_DIR) -> ArchiveSettings:
    """The archive's settings file, with the defaults for anything it leaves out."""
    path = folder / SETTINGS_FILE
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    known = {key: value for key, value in stored.items() if key in ArchiveSettings.__dataclass_fields__}
    return ArchiveSettings(**known)


def write_archive_settings(settings: ArchiveSettings, folder: Path = ARCHIVE_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SETTINGS_FILE
    path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


ARCHIVE = read_archive_settings()


def generated_path(url: str | None) -> Path | None:
    """The file behind a /static/generated/... address, inside this archive's folder."""
    if not url or not url.startswith(GENERATED_URL + "/"):
        return None
    path = (GENERATED_DIR / url[len(GENERATED_URL) + 1:]).resolve()
    return path if path.is_relative_to(GENERATED_DIR) else None


def _load_secret_key() -> str:
    """Return SECRET_KEY from the environment, or from a key file made once.

    The file lives in the archive folder (not in git) so that every Gunicorn
    worker, and every restart, signs flash-message cookies with the same
    random key. ``O_EXCL`` makes the first worker to start create it; any
    other worker reads that file instead.
    """
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured
    key_file = INSTANCE_DIR / "secret_key"
    try:
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(secrets.token_hex(32))
    # A second worker can arrive between the other worker's create and write.
    for _ in range(50):
        key = key_file.read_text().strip()
        if key:
            return key
        time.sleep(0.1)
    raise RuntimeError(f"{key_file} is empty. Delete it, or set SECRET_KEY.")


class Config:
    """Base configuration shared by all environments."""

    SECRET_KEY = None  # made or read when the app starts (create_app), not when this module is imported
    ARCHIVE_DIR = ARCHIVE_DIR
    GENERATED_DIR = GENERATED_DIR
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{ARCHIVE_DIR / 'fm_archive.sqlite3'}"
    )
    # The archive rebuilt from saved games (app/models/world.py), kept apart from
    # the old archive until the rewrite's cut-over (docs/rewrite-plan.md, step 7).
    SQLALCHEMY_BINDS = {
        "world": os.environ.get("WORLD_DATABASE_URL", f"sqlite:///{ARCHIVE_DIR / 'fm_world.sqlite3'}")
    }
    # A few Gunicorn workers can serve read requests concurrently while an
    # import is writing the local SQLite archive.  Give SQLite enough time to
    # wait for that single writer instead of surfacing a transient
    # ``database is locked`` error after its short default timeout.  Server
    # databases (MariaDB/PostgreSQL) use their own pooling defaults.
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"connect_args": {"timeout": 60}}
        if SQLALCHEMY_DATABASE_URI.startswith("sqlite")
        else {}
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024  # 32 MB upload limit
    # The AI model chosen in Settings writes bios, stories and match reports. Never commit
    # a real key -- set it in the environment or .env.
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
    # Used when GPT-5.6 Luna is the model chosen in Settings.
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "testing"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_BINDS = {"world": "sqlite:///:memory:"}


class ProductionConfig(Config):
    DEBUG = False


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
