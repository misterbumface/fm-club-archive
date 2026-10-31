"""Map the records of a Football Manager 24 save without guessing from one hex dump. Read-only.

Many records of one kind are lined up on an anchor (a place each record has: the person row, the contract, the
reputation row) and every position near it is labelled from what it holds in thousands of records at once: a constant,
a date, a link to a person or a team, the person himself, "none" (ffffffff), a small number, a signed byte. One record
cannot tell a person number from 4 random bytes (person numbers fill 0 to about a million); thousands can.

    python tools/record_mapper.py profile SAVE --anchor contract --window -60:24
    python tools/record_mapper.py lists SAVE --anchor contract --window -40:0
    python tools/record_mapper.py find SAVE --anchor reputation --window 0:200 --sql "select person_uid, morale from person_moods"
    python tools/record_mapper.py diff SAVE OLDER_SAVE --anchor contract --window -60:24

``find`` and the names in the examples read the archive's world database (``--db``, read-only); by default the live
archive's. Anchors: ``row`` (the person row: number, uid, uid), ``contract`` (the person number in his contract),
``reputation`` (the reputation row), each moved by ``--shift``. A window is in bytes from the anchor.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import struct
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import fm_save  # noqa: E402

NONE = 0xFFFFFFFF
SURE = 0.9  # the share of records that makes a label
CONSTANT = 0.97
MAX_LIST = 60
LIVE_DB = Path(os.environ.get("FM_WORLD_DB", Path.home() / "Library/Application Support/FM Club Archive/archives/My Archive/fm_world.sqlite3"))


@dataclass
class World:
    """One save's game_db and what the labels need: the person numbers (number -> uid) and the team numbers."""
    data: bytes
    persons: dict[int, int]
    teams: set[int]
    rows: dict[int, tuple[int, int]] = field(default_factory=dict)
    team_uid: dict[int, int] = field(default_factory=dict)


@dataclass
class Record:
    uid: int
    number: int  # his person number
    at: int  # the anchor's position


def load(save: Path) -> World:
    with fm_save.FmSave(save) as s:
        data = s.read("game_db.dat")
    rows = fm_save._person_rows(data)
    teams = fm_save.read_teams(data, fm_save.read_clubs(data))
    return World(data, {n: uid for n, (uid, _) in rows.items()}, set(teams), rows, {n: t.fm_uid for n, t in teams.items()})


# -- anchors ---------------------------------------------------------------------------------------
def anchor_row(w: World) -> list[Record]:
    return [Record(uid, number, row) for number, (uid, row) in w.rows.items() if row >= 0]


def anchor_contract(w: World) -> list[Record]:
    """The person number in each person's contract (fm_save.read_staff_contracts' rule)."""
    found: dict[int, Record] = {}
    for match in fm_save._CONTRACT.finditer(w.data):
        number, team = struct.unpack("<II", match.group(1) + match.group(2))
        if team not in w.teams or number not in w.rows:
            continue
        uid, row = w.rows[number]
        if uid not in found and 0 < row - match.start() < fm_save._CONTRACT_REACH:
            found[uid] = Record(uid, number, match.start())
    return list(found.values())


def anchor_reputation(w: World) -> list[Record]:
    number_of = {uid: n for n, uid in w.persons.items()}
    found: dict[int, Record] = {}
    for match in fm_save._REPUTATION_ROW.finditer(w.data):
        uid = struct.unpack("<I", match.group(1))[0] + 1
        if uid in number_of and uid not in found:
            found[uid] = Record(uid, number_of[uid], match.start())
    return list(found.values())


ANCHORS: dict[str, Callable[[World], list[Record]]] = {"row": anchor_row, "contract": anchor_contract, "reputation": anchor_reputation}


def records(w: World, anchor: str, shift: int = 0, sample: int | None = None, seed: int = 1) -> list[Record]:
    found = [Record(r.uid, r.number, r.at + shift) for r in ANCHORS[anchor](w)]
    if sample and len(found) > sample:
        found = random.Random(seed).sample(found, sample)
    return found


# -- reading values ----------------------------------------------------------------------------------
def as_date(data: bytes, at: int) -> date | None:
    """u16 day of the year (low 9 bits) and u16 year, as the save writes dates; year 1900 is the game's "none"."""
    if at < 0 or at + 4 > len(data):
        return None
    day, year = struct.unpack_from("<HH", data, at)
    day &= 0x1FF
    if not 1900 <= year <= 2150 or not 1 <= day <= 366:
        return None
    result = date(year, 1, 1) + timedelta(days=day - 1)
    return result if result.year == year else None


def u32(data: bytes, at: int) -> int | None:
    return struct.unpack_from("<I", data, at)[0] if 0 <= at and at + 4 <= len(data) else None


DECODERS: dict[str, Callable[[bytes, int], object]] = {
    "u8": lambda d, a: d[a] if 0 <= a < len(d) else None,
    "i8": lambda d, a: struct.unpack_from("<b", d, a)[0] if 0 <= a < len(d) else None,
    "u16": lambda d, a: struct.unpack_from("<H", d, a)[0] if 0 <= a and a + 2 <= len(d) else None,
    "i16": lambda d, a: struct.unpack_from("<h", d, a)[0] if 0 <= a and a + 2 <= len(d) else None,
    "u32": u32,
    "date": as_date,
}


# -- profile ---------------------------------------------------------------------------------------------
@dataclass
class Label:
    offset: int
    width: int
    kind: str  # const, self, date, team, person, none, number, signed, ?
    text: str
    share: float


def _four_byte_label(w: World, recs: list[Record], offset: int) -> Label | None:
    values = [(r, u32(w.data, r.at + offset)) for r in recs]
    values = [(r, v) for r, v in values if v is not None]
    if not values:
        return None
    total = len(values)
    real = [v for _, v in values if v != NONE]
    if real and Counter(v & 0xFF for v in real).most_common(1)[0][1] >= CONSTANT * len(real):
        return None  # a number's low byte varies: a constant first byte belongs to a field of its own
    if sum(v == r.number for r, v in values) >= SURE * total:
        return Label(offset, 4, "self", "the person himself (his number)", sum(v == r.number for r, v in values) / total)
    real = [(r, v) for r, v in values if v != NONE]
    none_share = 1 - len(real) / total
    if not real:
        return Label(offset, 4, "none", "always ffffffff", 1.0) if none_share >= CONSTANT else None
    suffix = f", none in {none_share:.0%}" if none_share >= 0.01 else ""
    dates = [as_date(w.data, r.at + offset) for r, _ in real]
    good = [d for d in dates if d]
    if len(good) >= SURE * len(real) and len(real) >= 0.2 * total and len({d.toordinal() for d in good}) > 2:
        return Label(offset, 4, "date", f"date {min(good)} .. {max(good)}{suffix}", len(good) / len(real))
    if len(real) >= 0.2 * total and len({v for _, v in real}) > 2:
        teams = sum(v in w.teams for _, v in real) / len(real)
        persons = sum(v in w.persons for _, v in real) / len(real)
        big = sum(v >= 1000 for _, v in real) / len(real)
        if teams >= SURE and big >= 0.5:
            return Label(offset, 4, "team", f"team ({teams:.0%} are team numbers){suffix}", teams)
        if persons >= SURE and big >= SURE:
            return Label(offset, 4, "person", f"person ({persons:.0%} are person numbers){suffix}", persons)
    return None


def _byte_label(w: World, recs: list[Record], offset: int) -> Label | None:
    values = [w.data[r.at + offset] for r in recs if 0 <= r.at + offset < len(w.data)]
    if not values:
        return None
    (mode, count), = Counter(values).most_common(1)
    if count >= CONSTANT * len(values):
        return Label(offset, 1, "const", f"{mode:02x}", count / len(values))
    distinct = len(set(values))
    low, high = min(values), max(values)
    signed = [v - 256 if v >= 128 else v for v in values]
    if distinct >= 3 and high <= 100:
        return Label(offset, 1, "number", f"number {low}..{high} ({distinct} values, most {mode})", count / len(values))
    if distinct >= 3 and all(-100 <= v <= 100 for v in signed) and min(signed) < 0:
        return Label(offset, 1, "signed", f"signed {min(signed)}..{max(signed)} ({distinct} values)", 1.0)
    return Label(offset, 1, "?", f"varies ({distinct} values, most {mode:02x} in {count / len(values):.0%})", count / len(values))


def profile(w: World, recs: list[Record], start: int, end: int) -> list[Label]:
    """Labels for the positions start..end (bytes from the anchor): 4-byte fields first, single bytes otherwise; runs
    of constant bytes are joined."""
    labels: list[Label] = []
    offset = start
    while offset < end:
        label = _four_byte_label(w, recs, offset) if offset + 4 <= end else None
        label = label or _byte_label(w, recs, offset)
        if label is None:
            offset += 1
            continue
        last = labels[-1] if labels else None
        if label.kind == "const" and last and last.kind == "const" and last.offset + last.width == offset:
            labels[-1] = Label(last.offset, last.width + 1, "const", f"{last.text} {label.text}", min(last.share, label.share))
        else:
            labels.append(label)
        offset += label.width
    return labels


def aligned_until(labels: list[Label]) -> int | None:
    """The first offset from which most positions vary without a label: the layout stops lining up there (a list or a
    text of its own length comes first)."""
    unknown = 0
    for label in labels:
        unknown = unknown + 1 if label.kind == "?" else 0
        if unknown >= 6:
            return label.offset - 5
    return None


# -- lists -------------------------------------------------------------------------------------------------
@dataclass
class ListShape:
    offset: int
    size: int
    direction: str  # "ends" (count, then entries ending at the offset) or "starts" (count at the offset, entries after)
    share: float
    with_entries: int
    lengths: Counter
    first_bytes: Counter
    alike: float = 0.0  # of the records with two entries or more, the share whose entries all start with the same byte


def _list_ending(w: World, recs: list[Record], offset: int, size: int) -> tuple[list[int | None], int]:
    counts = []
    for r in recs:
        end = r.at + offset
        k = next((k for k in range(MAX_LIST, 0, -1) if u32(w.data, end - size * k - 4) == k), None)
        counts.append(k if k is not None else (0 if u32(w.data, end - 4) == 0 else None))
    return counts, sum(1 for k in counts if k)


def _alike(w: World, starts: list[tuple[int, int]], size: int) -> float:
    """Of the lists with two entries or more (start, count), the share whose entries all start with the same byte: by
    chance a few do, in a real list of typed entries nearly all do."""
    several = [(at, k) for at, k in starts if k >= 2]
    if not several:
        return 0.0
    return sum(len({w.data[at + size * i] for i in range(k)}) == 1 for at, k in several) / len(several)


ALIKE = 0.8
MIN_WITH_ENTRIES = 20
ONE_LENGTH = 0.8  # a chance fit gives nearly always the same count; a real list has lists of many lengths


def lists_ending(w: World, recs: list[Record], offset: int, sizes: Iterable[int] = range(2, 65)) -> list[ListShape]:
    """Sizes of entries for which most records have a u32 count k and then k entries ending exactly at ``offset``, the
    entries of a record alike (the same first byte)."""
    found = []
    for size in sizes:
        counts, with_entries = _list_ending(w, recs, offset, size)
        share = sum(k is not None for k in counts) / len(counts)
        if share < SURE or with_entries < max(MIN_WITH_ENTRIES, 0.01 * len(recs)):
            continue
        starts = [(r.at + offset - size * k, k) for r, k in zip(recs, counts) if k]
        alike = _alike(w, starts, size)
        lengths = Counter(k for k in counts if k)
        if alike >= ALIKE and lengths.most_common(1)[0][1] < ONE_LENGTH * with_entries:
            firsts = Counter(w.data[at] for at, _ in starts)
            found.append(ListShape(offset, size, "ends", share, with_entries, Counter(k for k in counts if k is not None), firsts, alike))
    return found


def lists_starting(w: World, recs: list[Record], offset: int, sizes: Iterable[int] = range(2, 65), after: int = 8) -> list[ListShape]:
    """Sizes for which a u32 count k at ``offset`` and k entries after it bring the records back in line: the ``after``
    bytes that follow the list are the same in most records."""
    counts = [u32(w.data, r.at + offset) for r in recs]
    if sum(c is not None and c <= MAX_LIST for c in counts) < SURE * len(recs) or sum(1 for c in counts if c) < 5:
        return []
    found = []
    for size in sizes:
        tails = Counter(w.data[r.at + offset + 4 + size * c: r.at + offset + 4 + size * c + after] for r, c in zip(recs, counts) if c is not None)
        top = tails.most_common(1)[0][1] / sum(tails.values())
        if top >= 0.8:
            starts = [(r.at + offset + 4, c) for r, c in zip(recs, counts) if c]
            alike = _alike(w, starts, size)
            if alike >= ALIKE:
                firsts = Counter(w.data[at] for at, _ in starts)
                found.append(ListShape(offset, size, "starts", top, len(starts), Counter(counts), firsts, alike))
    return sorted(found, key=lambda s: -s.share)[:3]


# -- find a known value -------------------------------------------------------------------------------
@dataclass
class Hit:
    offset: int
    decoder: str
    exact: float  # share of records whose value is exactly the known one
    known: int


def _same(decoded, known) -> bool:
    if isinstance(decoded, date):
        return str(decoded) == str(known)[:10]
    try:
        return decoded == int(known)
    except (TypeError, ValueError):
        return False


def find(w: World, recs: list[Record], known: dict[int, object], start: int, end: int, top: int = 10) -> list[Hit]:
    """The positions and readings that give the known values (uid -> value) in the most records."""
    pairs = [(r, known[r.uid]) for r in recs if known.get(r.uid) is not None]
    hits = []
    for offset in range(start, end):
        for name, decode in DECODERS.items():
            same = sum(_same(decode(w.data, r.at + offset), value) for r, value in pairs)
            if same:
                hits.append(Hit(offset, name, same / len(pairs), len(pairs)))
    return sorted(hits, key=lambda h: -h.exact)[:top]


# -- compare two saves -----------------------------------------------------------------------------
@dataclass
class Change:
    offset: int
    share: float  # of the records whose byte differs
    examples: list[tuple[int, int, int]]  # uid, old byte, new byte


def diff(new: World, old: World, anchor: str, start: int, end: int, shift: int = 0, sample: int = 4000) -> list[Change]:
    """The positions that changed between two saves, in the same people's records."""
    before = {r.uid: r for r in records(old, anchor, shift)}
    pairs = [(r, before[r.uid]) for r in records(new, anchor, shift, sample) if r.uid in before]
    changes = []
    for offset in range(start, end):
        moved = [(r.uid, o_.at, r.at) for r, o_ in pairs if 0 <= r.at + offset < len(new.data) and 0 <= o_.at + offset < len(old.data)
                 and new.data[r.at + offset] != old.data[o_.at + offset]]
        if moved:
            changes.append(Change(offset, len(moved) / len(pairs),
                                  [(uid, old.data[a + offset], new.data[b + offset]) for uid, a, b in moved[:3]]))
    return changes


# -- the command line ----------------------------------------------------------------------------------
def _names(db: Path | None) -> dict[int, str]:
    if not db or not db.exists():
        return {}
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        return dict(c.execute("select fm_uid, name from people"))


def _window(text: str) -> tuple[int, int]:
    start, end = text.split(":")
    return int(start), int(end)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("profile", "lists", "find", "diff"))
    parser.add_argument("save", type=Path)
    parser.add_argument("older", type=Path, nargs="?", help="diff: the earlier save")
    parser.add_argument("--anchor", choices=tuple(ANCHORS), default="contract")
    parser.add_argument("--shift", type=int, default=0)
    parser.add_argument("--window", type=_window, default=(-64, 32))
    parser.add_argument("--sample", type=int, default=4000)
    parser.add_argument("--sql", help="find: a query giving (uid, value)")
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    args = parser.parse_args(argv)
    (start, end), w = args.window, load(args.save)
    recs = records(w, args.anchor, args.shift, args.sample)
    print(f"{len(recs)} records on the {args.anchor} anchor{f' {args.shift:+d}' if args.shift else ''}")
    if args.command == "profile":
        labels = profile(w, recs, start, end)
        for label in labels:
            print(f"{label.offset:+6d}  {label.width}  {label.kind:7s} {label.text}" + (f"  [{label.share:.0%}]" if label.kind not in ("const", "?") else ""))
        stop = aligned_until(labels)
        if stop is not None:
            print(f"the records stop lining up at {stop:+d}: look for a list there (lists command)")
    elif args.command == "lists":
        for offset in range(start, end + 1):
            for shape in lists_ending(w, recs, offset) + lists_starting(w, recs, offset):
                lengths = ", ".join(f"{k}x{n}" for k, n in sorted(shape.lengths.items())[:8])
                firsts = ", ".join(f"{b:02x} {n}" for b, n in shape.first_bytes.most_common(3))
                print(f"{shape.offset:+6d}  {shape.direction}  entries of {shape.size} bytes  [{shape.share:.0%}]  "
                      f"{shape.with_entries} records with entries, alike in {shape.alike:.0%}; counts {lengths}; entries start {firsts}")
    elif args.command == "find":
        if not args.sql:
            parser.error("find needs --sql")
        with sqlite3.connect(f"file:{args.db}?mode=ro", uri=True) as c:
            known = dict(c.execute(args.sql))
        for hit in find(w, recs, known, start, end):
            print(f"{hit.offset:+6d}  {hit.decoder:4s}  {hit.exact:.1%} of {hit.known} records")
    else:
        if not args.older:
            parser.error("diff needs the earlier save")
        names = _names(args.db)
        for change in diff(w, load(args.older), args.anchor, start, end, args.shift, args.sample):
            examples = "; ".join(f"{names.get(uid, uid)} {a:02x}->{b:02x}" for uid, a, b in change.examples)
            print(f"{change.offset:+6d}  changed in {change.share:.1%}  {examples}")


if __name__ == "__main__":
    main()
