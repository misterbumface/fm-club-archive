"""Move an archive to another Mac as one file (``.fmarchive``): File → Export Archive… and File → Open Archive File….

The file is a zip: the archive's folder (its two databases, its pictures, the published site, its settings) and the
app's keys. The databases go in as SQLite backups, so a running server's unwritten changes (the -wal file) are in them.
The installed game's lang_db.dat goes in too
(the only file of the game an import reads), so the new Mac does not need the game. It leaves out the backups, the run folder and old hand-made database copies: they stay on the old Mac.

On the new Mac the archive gets its own folder in the app's archives; its game folders are found again there
(config.find_here), so nothing needs a path change.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from macapp import state

SUFFIX = ".fmarchive"
MANIFEST = "fmarchive.json"
KEYS = "keys.json"  # the app's keys, at the top of the file next to the manifest
FOLDER = "archive"  # the archive's folder, in the file
LANG_DB = "game/lang_db.dat"  # the game's names, in the archive folder (app/services/save_import.OWN_LANG_DB)
DATABASES = ("fm_archive.sqlite3", "fm_world.sqlite3")
LEAVE_OUT_FOLDERS = {"backups", "run"}
PACKED = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".mp4"}  # already packed: stored as they are, which is faster


def _left_out(relative: PurePosixPath) -> bool:
    name = relative.name
    return (relative.parts[0] in LEAVE_OUT_FOLDERS or name.endswith(("-wal", "-shm", ".partial"))
            or (name.endswith(".sqlite3") and name not in DATABASES) or name == ".DS_Store")


def export_archive(folder: Path, target: Path, progress: Callable[[str], None] = lambda text: None) -> Path:
    """Write the archive in ``folder`` to the file ``target`` (a .fmarchive); the file, when it is complete."""
    folder = folder.expanduser().resolve()
    target = target.with_suffix(SUFFIX) if target.suffix != SUFFIX else target
    partial = target.with_name(target.name + ".partial")
    name = _name(folder)
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True) as packed, tempfile.TemporaryDirectory() as scratch:
            packed.writestr(MANIFEST, json.dumps({"name": name, "format": 1}, indent=2))
            if (state.APP_DIR / KEYS).is_file():
                packed.write(state.APP_DIR / KEYS, KEYS)
            for database in DATABASES:
                if (folder / database).is_file():
                    progress(f"Copying {database}…")
                    copy = Path(scratch) / database
                    _snapshot(folder / database, copy)
                    packed.write(copy, f"{FOLDER}/{database}", zipfile.ZIP_DEFLATED)
                    copy.unlink()
            lang_db = _game_names(folder)
            if lang_db:  # the game's names, so the archive can import on a Mac without the game
                progress("Copying the game's names (lang_db.dat)…")
                packed.write(lang_db, f"{FOLDER}/{LANG_DB}", zipfile.ZIP_DEFLATED)
            files = sorted(path for path in folder.rglob("*") if path.is_file())
            for number, path in enumerate(files, 1):
                relative = PurePosixPath(path.relative_to(folder).as_posix())
                if _left_out(relative) or relative.name in DATABASES and len(relative.parts) == 1:
                    continue
                if number % 500 == 0:
                    progress(f"Packing the pictures and pages: {number:,} of {len(files):,}…")
                kind = zipfile.ZIP_STORED if path.suffix.lower() in PACKED else zipfile.ZIP_DEFLATED
                packed.write(path, f"{FOLDER}/{relative}", kind)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


def import_archive(source: Path, progress: Callable[[str], None] = lambda text: None) -> Path:
    """Unpack a .fmarchive into a new folder in the app's archives; the folder. Keys this Mac does not have yet are
    added; a key this Mac has already is kept."""
    with zipfile.ZipFile(source) as packed:
        names = set(packed.namelist())
        if MANIFEST not in names:
            raise ValueError(f"{source.name} is not an archive file.")
        name = json.loads(packed.read(MANIFEST)).get("name") or source.stem
        folder = state.folder_for(name)
        partial = folder.with_name(folder.name + ".partial")
        members = [member for member in packed.infolist() if member.filename.startswith(f"{FOLDER}/") and not member.is_dir()]
        try:
            for number, member in enumerate(members, 1):
                relative = PurePosixPath(member.filename).relative_to(FOLDER)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"{source.name} holds a path out of its folder: {member.filename}")
                if number % 500 == 0:
                    progress(f"Unpacking: {number:,} of {len(members):,}…")
                destination = partial.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with packed.open(member) as reader, open(destination, "wb") as writer:
                    while chunk := reader.read(1 << 20):
                        writer.write(chunk)
            if not state.is_archive(partial):
                raise ValueError(f"{source.name} holds no archive.")
            partial.rename(folder)
        except BaseException:
            import shutil

            shutil.rmtree(partial, ignore_errors=True)
            raise
        if KEYS in names:
            _add_keys(json.loads(packed.read(KEYS)))
    return folder


def _game_names(folder: Path) -> Path | None:
    """The installed game's lang_db.dat for this archive, when the archive has no copy of its own yet."""
    if (folder / LANG_DB).is_file():
        return None  # its own copy goes in with the other files
    import config
    from app.services import fm_save

    return fm_save.find_language_database(config.read_archive_settings(folder).game_database_dir)


def _snapshot(database: Path, copy: Path) -> None:
    """A consistent copy of a database that may be open (backup_service does the same for the daily backup)."""
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=60)
    try:
        destination = sqlite3.connect(copy)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def _add_keys(keys: dict) -> None:
    path = state.APP_DIR / KEYS
    try:
        here = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        here = {}
    merged = {**{key: value for key, value in keys.items() if value}, **{key: value for key, value in here.items() if value}}
    if merged == here:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


def _name(folder: Path) -> str:
    try:
        return json.loads((folder / "archive.json").read_text(encoding="utf-8")).get("name") or folder.name
    except (OSError, ValueError):
        return folder.name
