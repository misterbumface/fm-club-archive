"""The highlight shot events of a save's detailed match reports, for labelling (docs/goal-labels.md).

    .venv/bin/python tools/shot_events.py "<save.fm>" [world.sqlite3] [--goals] [--name <part of a player's name>]

Each line: the event's place in the match, the player (by the archive's name when a world database is given and his goal
shot is in it), the event's value, type byte and the bytes after it. Read-only.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import fm_save as fs  # noqa: E402

EVENT = re.compile(rb"(?=\x02([\x00\x01])(.)(.)(..)\x00\x00(.)\x00)", re.S)
TICKS: dict[tuple[int, int, int], int] = {}  # (person, minute, second) -> the shot record's tick (its bytes 11-12), the events' clock


def _shots_with_ticks(buffer: bytes, at: int):
    person, end = struct.unpack_from("<I", buffer, at)[0], at + fs._MP_SHOT_COUNT + 1
    for _ in range(buffer[at + fs._MP_SHOT_COUNT]):
        parts = buffer[end + fs._MP_SHOT_HEAD - 1]
        for k in range(parts):
            part = end + fs._MP_SHOT_HEAD + fs._MP_SHOT_PART * k
            if part + fs._MP_SHOT_PART <= len(buffer):
                TICKS[(person, buffer[part + 9], buffer[part + 10])] = struct.unpack_from("<H", buffer, part + 11)[0]
        end += fs._MP_SHOT_HEAD + fs._MP_SHOT_PART * parts
    return _read_shots(buffer, at)


_read_shots, fs._match_shots = fs._match_shots, _shots_with_ticks


def events(stream: bytes, start: int, end: int):
    for m in EVENT.finditer(stream, start, end):
        tick, minute = struct.unpack("<H", m.group(4))[0], m.group(5)[0]
        if 1 <= minute <= 125 and abs(tick / 248 - minute) <= 2.5:
            yield m.start(), m.group(1)[0], m.group(2)[0], m.group(3)[0], tick, minute


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("save")
    parser.add_argument("world", nargs="?")
    parser.add_argument("--goals", action="store_true", help="only shot events that match a goal")
    parser.add_argument("--name", help="only this player (needs the world database)")
    args = parser.parse_args()
    with fs.FmSave(args.save) as save:
        raw = save.read("match_stats_cfg.dat")
    stream = b"".join(fs.iter_inner_blocks(raw))
    reports = sorted(fs.read_match_reports(raw), key=lambda r: r.position)
    world = sqlite3.connect(args.world) if args.world else None
    for i, report in enumerate(reports):
        end = reports[i + 1].position if i + 1 < len(reports) else len(stream)
        for at, side_no, slot, code, tick, minute in events(stream, report.position, end):
            if code != 1:
                continue
            side = (report.home, report.away)[side_no]
            if slot >= len(side.players):
                continue
            player = side.players[slot]
            goal = next((s for s in player.shot_list if s.outcome == fs.SHOT_GOAL
                         and abs(TICKS.get((player.person, s.minute, s.second), -99) - tick) <= 4), None)
            if args.goals and goal is None:
                continue
            who, game = str(player.person), f"report {report.fixture_id}"
            if world is not None and goal is not None:
                row = world.execute("select fixture_id, person_uid from match_shots where minute = ? and second = ? and round(across, 2) = ? "
                                    "and round(height, 2) = ?", (goal.minute, goal.second, goal.across, goal.height)).fetchone()
                if row:
                    name = world.execute("select name from people where fm_uid = ?", (row[1],)).fetchone()
                    f = world.execute("select f.date, h.name, f.home_goals, f.away_goals, a.name from fixtures f join teams h on h.fm_uid = f.home_uid "
                                      "join teams a on a.fm_uid = f.away_uid where f.id = ?", (row[0],)).fetchone()
                    who = name[0] if name else who
                    game = f"{str(f[0])[:10]} {f[1]} {f[2]}-{f[3]} {f[4]}" if f else game
            if args.name and args.name.lower() not in who.lower():
                continue
            value = struct.unpack_from("<I", stream, at + 10)[0]
            where = f" goal h={goal.height} a={goal.across}" if goal else ""
            print(f"{game} | {who} {minute}' | value {value} type {stream[at + 14]:02x} | {stream[at + 15:at + 24].hex(' ')}{where}")


if __name__ == "__main__":
    main()
