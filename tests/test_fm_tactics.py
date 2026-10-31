"""The tactic blocks of a save (fm_tactics): a tactic the game saved on 24 Oct 2026 ("Check 2", tests/data/tactic_check2.tac)
read against the user's tactics screen, the game's presets, and a match file's head (tests/data/match_file_head.bin: Aston
Villa v Chelsea, 27 Oct 2063)."""

import struct
from pathlib import Path

import numpy as np

from app.services import fm_tactics as t

DATA = Path(__file__).parent / "data"
MESSI, BAGGIO, PARDAL = 830949, 847079, 573604  # person numbers in the save


def _check2() -> bytes:
    return (DATA / "tactic_check2.tac").read_bytes()


def test_a_saved_tactic_reads_as_the_screen_shows_it():
    (tactic,) = list(t.iter_tactics(_check2()))
    assert (tactic.name, tactic.style, tactic.style_tag, tactic.mentality, tactic.fluidity) == ("Check 2", "Tiki-Taka", "TIKI", "Positive", "Flexible")
    assert tactic.formation == "4-2-3-1 DM AM Wide" and not tactic.is_preset
    assert [f"{s.position}{'/' + s.side if s.side else ''}:{s.short}" for s in tactic.slots] == [
        "GK:SK-Su", "D (R):FB-Su", "D (C)/right:BPD-De", "D (C)/left:CD-De", "D (L):WB-Su", "DM/right:VOL-Su", "DM/left:A-De",
        "AM (R):IF-Su", "AM (C):AM-At", "AM (L):IF-Su", "ST (C):AF-At"]
    assert tactic.slots[0].role == "Sweeper Keeper" and tactic.slots[5].role_key == "6.04"
    personal = {(p.person_number, p.slot.position): p.slot.short for p in tactic.player_roles}
    assert personal[(MESSI, "AM (L)")] == "IW-At" and personal[(MESSI, "AM (C)")].endswith("-At")  # Messi: an inverted winger on attack
    assert personal[(BAGGIO, "AM (R)")] == "IF-Su"
    assert sum(p.person_number == PARDAL for p in tactic.player_roles) == 3


def _block(name: str, style_tag: str, mentality: int, fluidity: int, slots: list[tuple[int, int, int, bytes]]) -> bytes:
    """A tactic block as the game writes it: the header, then slot records (position bit, side, duty byte, 8-byte role mask)."""
    custom = b"" if style_tag == "NULL" else b"Custom " + style_tag.encode()
    head = b"\x22\x3a\x00\x18\x02\x00\x00\x00" + struct.pack("<I", len(name)) + name.encode()
    head += bytes([1] + [0] * 12) + bytes([4, fluidity, mentality, 2, 2, 3]) + bytes(9) + b"\x01\x00\x00\xff"
    head += struct.pack("<I", len(custom)) + custom + style_tag.encode()[::-1] + b"\x3a\x00\x02"
    records = []
    for bit, side, duty, mask in slots:
        mask = bytearray(mask)
        mask[2] |= duty
        records.append(struct.pack("<HH", 1 << bit, side) + bytes(mask) + b"\x01\x01\x05" + bytes(40))
    return head + b"\xff\x00\x3a\x00\x02".join(records)


ELEVEN = [(0, 0, 0x40, b"\x00\x10\x00\x00\x00\x00\x00\x00"), (2, 0, 0x40, b"\x08\x00\x00\x00\x00\x00\x00\x00"),
          (4, 0x20, 0x20, b"\x02\x00\x00\x00\x00\x00\x00\x00"), (4, 0x10, 0x20, b"\x00\x00\x00\x01\x00\x00\x00\x00"),
          (3, 0, 0x80, b"\x04\x00\x00\x00\x00\x00\x00\x00"), (7, 0, 0x20, b"\x00\x00\x00\x00\x02\x00\x00\x00"),
          (10, 0x20, 0x40, b"\x20\x00\x00\x00\x00\x00\x00\x00"), (10, 0x10, 0x80, b"\x20\x00\x00\x00\x00\x00\x00\x00"),
          (11, 0, 0x40, b"\x00\x00\x00\x08\x00\x00\x00\x00"), (12, 0, 0x80, b"\x00\x00\x00\x00\x00\x00\x02\x00"),
          (14, 0, 0x80, b"\x00\x08\x00\x00\x00\x00\x00\x00")]


def test_the_slots_library_and_presets():
    data = _block("GPT 5.6 x", "TIKI", 5, 2, ELEVEN) + _block("4-4-2", "NULL", 4, 2, ELEVEN) + _block("Pressing", "GEGE", 6, 1, ELEVEN) \
        + _block("GPT 5.6 x", "TIKI", 5, 2, ELEVEN)
    found = list(t.iter_tactics(data))
    assert [x.name for x in found] == ["GPT 5.6 x", "4-4-2", "Pressing", "GPT 5.6 x"]
    assert found[1].is_preset and found[1].style is None and found[1].mentality == "Balanced"
    assert (found[2].style, found[2].mentality, found[2].fluidity) == ("Gegenpress", "Attacking", "Structured")
    assert [x.name for x in t.read_tactic_slots(data)] == ["GPT 5.6 x", "4-4-2", "Pressing"]  # the three slots are the first three blocks
    assert [x.name for x in t.read_tactic_library(data)] == ["GPT 5.6 x", "Pressing"]  # no presets, each name once
    assert found[0].formation == "4-3-3 DM Wide"
    assert [s.short for s in found[0].slots] == ["SK-Su", "WB-Su", "CD-De", "BPD-De", "FB-At", "A-De", "CM-Su", "CM-At", "IF-Su", "IW-At", "AF-At"]


def test_a_role_bit_in_the_duty_byte_and_an_unknown_role():
    slots = ELEVEN[:-1] + [(14, 0, 0x80, b"\x00\x00\x10\x00\x00\x00\x00\x00")]  # a striker whose role bit sits in the duty byte
    (tactic,) = list(t.iter_tactics(_block("Odd", "TIKI", 5, 2, slots)))
    striker = tactic.slots[-1]
    assert striker.duty == "Attack" and striker.role_key == "2.10" and striker.role is None and striker.short == "?-At"


def test_a_match_file_names_its_teams_crowd_and_tactic():
    data = (DATA / "match_file_head.bin").read_bytes() + _block("GPT 5.6 x", "TIKI", 5, 2, ELEVEN) + _block("4-4-2", "NULL", 4, 2, ELEVEN)
    found = t.read_match_tactics(data)
    assert (found.home_number, found.home_uid, found.away_number, found.away_uid) == (514, 603, 541, 630)  # Aston Villa v Chelsea
    assert (found.attendance, found.away_fans, found.season) == (49631, 3000, 2063)
    assert [x.name for x in found.tactics] == ["GPT 5.6 x"]
    assert t.read_match_tactics(b"nothing here") is None


def test_people_in_a_file_at_any_alignment():
    known = np.sort(np.array([2002811483, 2002827613, 7], dtype=np.uint32))
    data = b"\x01" + struct.pack("<I", 2002811483) + b"\x00\x00\x00" + struct.pack("<I", 2002827613) + struct.pack("<I", 99)
    assert t.person_uids_in(data, known) == {2002811483, 2002827613}
