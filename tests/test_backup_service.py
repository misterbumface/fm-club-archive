import sqlite3
from datetime import datetime, timedelta

import pytest

from app.services.backup_service import (
    create_backup,
    sqlite_path_from_uri,
)


@pytest.fixture()
def database(tmp_path):
    path = tmp_path / "archive.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE club (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO club (name) VALUES ('Fulham')")
    connection.commit()
    connection.close()
    return path


def test_backup_holds_the_same_rows(database, tmp_path):
    result = create_backup(database, tmp_path / "backups")

    copy = sqlite3.connect(result.path)
    assert copy.execute("SELECT name FROM club").fetchall() == [("Fulham",)]
    copy.close()
    assert result.size_bytes > 0
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_prune_removes_only_old_automatic_backups(database, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    by_hand = backups / "fm_archive.before-repair.sqlite3"
    by_hand.write_bytes(b"kept")

    start = datetime(2026, 1, 1, 3, 0, 0)
    for day in range(4):
        create_backup(database, backups, keep=2, now=start + timedelta(days=day))

    names = sorted(path.name for path in backups.iterdir())
    assert names == [
        "fm_archive.auto-20260103-030000.sqlite3",
        "fm_archive.auto-20260104-030000.sqlite3",
        "fm_archive.before-repair.sqlite3",
    ]


def test_missing_database_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_backup(tmp_path / "none.sqlite3", tmp_path / "backups")


def test_memory_database_is_refused():
    with pytest.raises(ValueError):
        sqlite_path_from_uri("sqlite:///:memory:")
