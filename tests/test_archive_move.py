"""Moving an archive to another Mac: one .fmarchive file out, a new archive folder in, and the game folders found again."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import config
from macapp import move, state


def _archive(folder: Path) -> None:
    folder.mkdir(parents=True)
    config.write_archive_settings(config.ArchiveSettings(name="Chelsea v2", fm_user_dir="/Users/someone/Dropbox/FM", save_file="a.fm"), folder)
    for name in move.DATABASES:
        with sqlite3.connect(folder / name) as db:
            db.execute("pragma journal_mode=wal")
            db.execute("create table t (x)")
            db.execute("insert into t values (1)")
    (folder / "generated" / "faces").mkdir(parents=True)
    (folder / "generated" / "faces" / "1.png").write_bytes(b"png")
    (folder / "secret_key").write_text("s")
    for left_out in ("backups/fm_archive.auto-1.sqlite3", "run/archive-job.lock", "fm_archive.before-remake.sqlite3"):
        (folder / left_out).parent.mkdir(exist_ok=True)
        (folder / left_out).write_bytes(b"x")


def test_an_archive_goes_out_as_one_file_and_comes_back_as_a_new_folder_with_its_keys(tmp_path, monkeypatch):
    here, there = tmp_path / "here", tmp_path / "there"
    _archive(here / "archives" / "Chelsea v2")
    here.joinpath("keys.json").write_text(json.dumps({"DEEPSEEK_API_KEY": "a", "OPENAI_API_KEY": "b"}))
    monkeypatch.setattr(state, "APP_DIR", here)
    game = tmp_path / "game" / "data" / "database" / "db" / "2400" / "2400_fm"
    game.mkdir(parents=True)
    (game / "lang_db.dat").write_bytes(b"names")
    settings = config.read_archive_settings(here / "archives" / "Chelsea v2")
    config.write_archive_settings(config.ArchiveSettings(**{**settings.__dict__, "game_dir": str(tmp_path / "game")}), here / "archives" / "Chelsea v2")
    packed = move.export_archive(here / "archives" / "Chelsea v2", tmp_path / "Chelsea v2")
    assert packed.name == "Chelsea v2.fmarchive" and not list(tmp_path.glob("*.partial"))

    there.mkdir()
    there.joinpath("keys.json").write_text(json.dumps({"OPENAI_API_KEY": "mine"}))
    monkeypatch.setattr(state, "APP_DIR", there)
    monkeypatch.setattr(state, "ARCHIVES_DIR", there / "archives")
    folder = move.import_archive(packed)
    assert folder == there / "archives" / "Chelsea v2"
    files = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
    assert files == {"archive.json", "fm_archive.sqlite3", "fm_world.sqlite3", "generated/faces/1.png", "secret_key", "game/lang_db.dat"}
    assert (folder / "game" / "lang_db.dat").read_bytes() == b"names"  # the game's names come along
    with sqlite3.connect(folder / "fm_world.sqlite3") as db:
        assert db.execute("select x from t").fetchall() == [(1,)]  # the -wal's rows are in the copy
    assert json.loads((there / "keys.json").read_text()) == {"DEEPSEEK_API_KEY": "a", "OPENAI_API_KEY": "mine"}
    assert move.import_archive(packed) == there / "archives" / "Chelsea v2 2"  # a second copy never overwrites the first


def test_a_file_that_is_not_an_archive_is_refused_and_leaves_nothing(tmp_path, monkeypatch):
    import zipfile

    monkeypatch.setattr(state, "ARCHIVES_DIR", tmp_path / "archives")
    bad = tmp_path / "bad.fmarchive"
    with zipfile.ZipFile(bad, "w") as packed:
        packed.writestr(move.MANIFEST, "{}")
        packed.writestr("archive/../../evil.txt", "x")
    try:
        move.import_archive(bad)
    except ValueError as error:
        assert "out of its folder" in str(error)
    else:
        raise AssertionError("the bad file was opened")
    assert not (tmp_path / "evil.txt").exists() and not list((tmp_path / "archives").glob("*")) if (tmp_path / "archives").exists() else True


def test_game_folders_from_another_mac_are_found_again_here(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    dropbox = tmp_path / "Library" / "CloudStorage" / "Dropbox" / "Football Manager 2024"
    dropbox.mkdir(parents=True)
    assert config.find_here(str(dropbox), []) == str(dropbox)  # here already
    assert config.find_here("/Users/someone/Library/CloudStorage/Dropbox/Football Manager 2024", []) == str(dropbox)  # other user name
    assert config.find_here("/Volumes/Old/FM", [tmp_path / "none", dropbox]) == str(dropbox)  # a usual place
    assert config.find_here("/Volumes/Old/FM", []) == "/Volumes/Old/FM"  # nothing found: kept
    (tmp_path / "a").mkdir()
    config.write_archive_settings(config.ArchiveSettings(fm_user_dir="/Volumes/Old/FM"), tmp_path / "a")
    assert config.read_archive_settings(tmp_path / "a").fm_user_dir == str(dropbox)


def test_an_import_without_the_game_uses_the_archive_s_own_names(tmp_path, monkeypatch):
    from app.services import fm_save, save_import

    own = tmp_path / "game" / "lang_db.dat"
    monkeypatch.setattr(save_import, "OWN_LANG_DB", own)
    monkeypatch.setattr(fm_save, "find_language_database", lambda root: None)  # no game on this Mac
    assert save_import.language_database() is None
    own.parent.mkdir()
    own.write_bytes(b"names")
    assert save_import.language_database() == own
