"""Put the national squads of older saves of the same game in the archive, so the first comparison has something to compare
with (8 Nov 2026). Adds only rows of the national_squads table. Never run it on a test save.

    .venv/bin/python tools/backfill_national_squads.py <fm_world.sqlite3> <save.fm> [<save.fm> ...]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import fm_save  # noqa: E402
from app.services.save_import import national_squad_rows  # noqa: E402

TABLE = ("create table if not exists national_squads (team_uid integer not null, as_of date not null, person_uid integer not null, "
         "role varchar(12), primary key (team_uid, as_of, person_uid))")


def read(path: Path) -> list[dict]:
    save = fm_save.FmSave(path)
    game_db = save.read("game_db.dat")
    teams = fm_save.read_teams(game_db, fm_save.read_clubs(game_db))
    squads = fm_save.read_national_squads(game_db, teams, fm_save._person_rows(game_db))
    as_of = max(f.date for f in fm_save.iter_all_fixtures(save.read("fix_man.dat")) if f.home_goals is not None)
    return national_squad_rows(squads, as_of)


def main(database: str, saves: list[str]) -> None:
    connection = sqlite3.connect(database)
    with connection:
        connection.execute(TABLE)
        connection.execute("create index if not exists ix_national_squads_person_uid on national_squads (person_uid)")
        for path in saves:
            rows = read(Path(path))
            connection.execute("delete from national_squads where as_of = ?", (str(rows[0]["as_of"]),))
            connection.executemany("insert into national_squads (team_uid, as_of, person_uid, role) values (?, ?, ?, ?)",
                                   [(r["team_uid"], str(r["as_of"]), r["person_uid"], r["role"]) for r in rows])
            print(f"{rows[0]['as_of']}: {len(rows)} players, {len({r['team_uid'] for r in rows})} nations")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
