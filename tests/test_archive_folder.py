"""An archive is one folder: its settings file, and its pictures at their /static/generated addresses."""

from __future__ import annotations

from pathlib import Path

import config
from app import create_app


def test_settings_are_written_and_read_back_with_defaults_for_the_rest(tmp_path):
    settings = config.ArchiveSettings(name="Barcelona 2024", fm_user_dir=str(tmp_path / "fm"), save_file="Barcelona.fm")
    config.write_archive_settings(settings, tmp_path)
    read = config.read_archive_settings(tmp_path)
    assert read == settings and read.face_folder == "Archive generated"
    assert read.games_dir == tmp_path / "fm" / "games" and read.faces_dir == tmp_path / "fm" / "graphics" / "Faces"
    assert config.read_archive_settings(tmp_path / "none") == config.ArchiveSettings()  # no file: the defaults


def test_a_generated_address_maps_into_the_archive_folder_and_never_out_of_it():
    assert config.generated_path("/static/generated/faces/1.png") == config.GENERATED_DIR / "faces" / "1.png"
    assert config.generated_path("/static/generated/../archive.json") is None
    assert config.generated_path("/static/css/world.css") is None and config.generated_path(None) is None


def test_the_archive_serves_its_own_pictures(tmp_path):
    app = create_app("testing")
    app.config["GENERATED_DIR"] = tmp_path
    (tmp_path / "illustrations").mkdir()
    (tmp_path / "illustrations" / "a.jpg").write_bytes(b"jpeg")
    client = app.test_client()
    assert client.get("/static/generated/illustrations/a.jpg").data == b"jpeg"
    assert client.get("/static/generated/../secret_key").status_code == 404
    assert Path(app.instance_path) == config.ARCHIVE_DIR


def test_a_save_from_another_game_is_refused():
    from datetime import date

    import pytest
    from sqlalchemy import create_engine, text

    from app.services import fm_save
    from app.services.save_import import SaveImportError, check_same_game

    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("create table people (fm_uid integer primary key, name text, birth_date date)"))
        c.execute(text("insert into people values (:u, :n, :b)"),
                  [{"u": 2_000_000_000 + i, "n": f"Player {i}", "b": "2040-01-01"} for i in range(1000)])
    same = {2_000_000_000 + i: fm_save.PersonName(f"Player {i}", "Player", str(i), date(2040, 1, 1)) for i in range(1000)}
    other = dict(same) | {2_000_000_000 + i: fm_save.PersonName(f"Someone {i}", "Someone", str(i), date(2045, 5, 5)) for i in range(600)}
    with engine.connect() as c:
        check_same_game(c, same)
        with pytest.raises(SaveImportError, match="another game"):
            check_same_game(c, other)


def test_only_the_archive_s_own_saves_are_offered(monkeypatch):
    from dataclasses import replace

    from app.services import save_jobs

    monkeypatch.setattr(save_jobs, "ARCHIVE", replace(config.ArchiveSettings(), save_file="Test database save.fm"))
    assert save_jobs.belongs("Test database save September.fm") and save_jobs.belongs("Test database save.fm")
    assert not save_jobs.belongs("Sam Archer - Chelsea v2 - loxal.fm")
