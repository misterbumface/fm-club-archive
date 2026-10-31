"""Consistent, rotating backups of the local SQLite archive.

A plain file copy of a database that a server is writing to can capture a
half-finished write. SQLite's own backup API copies a consistent snapshot even
while other connections read and write, so that is what this module uses.

Only files this module made (``fm_archive.auto-*``) are rotated. Backups that
were made by hand, with any other name, are never touched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

AUTO_PREFIX = "fm_archive.auto-"
AUTO_SUFFIX = ".sqlite3"
DEFAULT_KEEP = 14


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size_bytes: int
    pruned: tuple[Path, ...]


def sqlite_path_from_uri(uri: str) -> Path:
    """Return the database file named by a ``sqlite:///`` URI."""
    prefix = "sqlite:///"
    if not uri.startswith(prefix) or uri == "sqlite:///:memory:":
        raise ValueError("Backups work only for a SQLite database file.")
    return Path(uri[len(prefix):])


def create_backup(
    database: Path,
    backup_dir: Path,
    *,
    keep: int = DEFAULT_KEEP,
    now: datetime | None = None,
) -> BackupResult:
    """Copy ``database`` into ``backup_dir`` and prune old automatic backups."""
    if keep < 1:
        raise ValueError("keep must be at least 1.")
    if not database.is_file():
        raise FileNotFoundError(f"Database file not found: {database}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{AUTO_PREFIX}{stamp}{AUTO_SUFFIX}"
    partial = target.with_name(target.name + ".partial")

    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=60)
    try:
        destination = sqlite3.connect(partial)
        try:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("The new backup failed its integrity check.")
        finally:
            destination.close()
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    partial.replace(target)
    return BackupResult(
        path=target,
        size_bytes=target.stat().st_size,
        pruned=_prune(backup_dir, keep),
    )


def _prune(backup_dir: Path, keep: int) -> tuple[Path, ...]:
    automatic = sorted(
        path
        for path in backup_dir.iterdir()
        if path.name.startswith(AUTO_PREFIX) and path.name.endswith(AUTO_SUFFIX)
    )
    stale = automatic[:-keep]
    for path in stale:
        path.unlink()
    return tuple(stale)
