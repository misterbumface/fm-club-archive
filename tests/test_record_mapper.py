"""The record mapper (tools/record_mapper.py) labels positions from many records at once, finds lists of typed entries
and known values, and shows what changed between two saves. Made-up records only: no save is read."""

import importlib.util
import random
import struct
import sys
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location("record_mapper", Path(__file__).resolve().parents[1] / "tools" / "record_mapper.py")
mapper = importlib.util.module_from_spec(spec)
sys.modules["record_mapper"] = mapper  # dataclasses look their module up
spec.loader.exec_module(mapper)

PEOPLE = range(200_000, 260_000)
TEAMS = range(5_000, 6_000)


def _day(day: date, flags: int = 0) -> bytes:
    return struct.pack("<HH", day.timetuple().tm_yday | flags, day.year)


def _world(count: int = 400, seed: int = 3, happier: bool = False):
    """Records of the shape: a list (u32 count, 12-byte entries starting 07), a 00 marker, a date, a signed mood, then
    the person's own number, a team, ffffffff and a constant tail. The anchor is the person's number."""
    rng = random.Random(seed)
    data, recs = bytearray(), []
    for i in range(count):
        number = PEOPLE[i * 7]
        entries = rng.choice((0, 0, 1, 2, 3, 5))
        data += bytes(rng.randrange(256) for _ in range(rng.randrange(5, 40)))
        data += struct.pack("<I", entries) + b"".join(b"\x07" + bytes(rng.randrange(256) for _ in range(11)) for _ in range(entries))
        mood = rng.randrange(-100, 101) + (5 if happier and i % 4 == 0 else 0)
        data += b"\x00" + _day(date(2050 + rng.randrange(14), 1 + rng.randrange(12), 1 + rng.randrange(28))) + struct.pack("<b", max(-100, min(100, mood)))
        recs.append(mapper.Record(2_000_000_000 + i, number, len(data)))
        data += struct.pack("<III", number, rng.choice(TEAMS), mapper.NONE) + b"\x01\x00\x6c\x07"
    persons = {n: 2_000_000_000 + i for i, n in enumerate(PEOPLE)}
    return mapper.World(bytes(data), persons, set(TEAMS)), recs


def test_a_profile_labels_the_fields_from_many_records():
    w, recs = _world()
    labels = {label.offset: label for label in mapper.profile(w, recs, -6, 16)}
    assert labels[-6].kind == "const" and labels[-6].text == "00"
    assert labels[-5].kind == "date" and labels[-1].kind == "signed"
    assert (labels[0].kind, labels[4].kind, labels[8].kind) == ("self", "team", "none")
    assert labels[12].text == "01 00 6c 07"


def test_the_list_before_the_marker_is_found_with_its_entry_size():
    w, recs = _world()
    (shape,) = mapper.lists_ending(w, recs, -6)
    assert (shape.size, shape.direction) == (12, "ends")
    assert shape.first_bytes.most_common(1)[0][0] == 7 and shape.alike == 1.0


def test_a_known_value_is_found_where_it_stands():
    w, recs = _world()
    known = {r.uid: struct.unpack_from("<b", w.data, r.at - 1)[0] for r in recs}
    best = mapper.find(w, recs, known, -10, 10)[0]
    assert (best.offset, best.decoder, best.exact) == (-1, "i8", 1.0)
    dates = {r.uid: str(mapper.as_date(w.data, r.at - 5)) for r in recs}
    assert mapper.find(w, recs, dates, -10, 10)[0].offset == -5


def test_one_record_cannot_tell_a_number_from_noise_but_many_can():
    w, recs = _world()
    labels = mapper.profile(w, recs, -40, -6)
    assert not any(label.kind in ("person", "team", "date", "self") for label in labels)  # the random bytes and the list


def test_a_date_with_flags_in_the_day_is_still_a_date():
    assert mapper.as_date(_day(date(2055, 7, 10), 0x3200), 0) == date(2055, 7, 10)
    assert mapper.as_date(struct.pack("<HH", 400, 2055), 0) is None
