"""The tactics in a saved game: the tactic blocks of ``tactics_man.dat`` (the human manager's three slots and the saved
library) and of the match files (``.apm``/``.scm``: the tactic used that day).

A tactic block (the same in the save, in the match files and in a ``.fmf`` tactic file's ``.tac`` entry):
``22 3a 00 18 02 00 00 0x`` + u32 length + the name; 13 bytes; 6 bytes of which the second is the fluidity and the third
the mentality; 9 bytes of team-instruction flags (not decoded); 4 bytes; u32 length + the style's name ("Custom
Tiki-Taka", empty for a preset); the style tag, four letters reversed ("IKIT" = Tiki-Taka, "EGEG" = Gegenpress, "LLUN"
= none) + ``3a 00 02``. Then the 11 formation slots, each after ``ff 00 3a 00 02``: the position (u16, one bit of
POSITIONS), the side (u16: 0x20 right of centre, 0x10 left), an 8-byte mask whose byte 6 holds the duty in its top bits (0x20 Defend,
0x40 Support, 0x80 Attack) and whose one other bit is the role, ``01 01``, a slot byte, then the position's possible roles
(24-byte entries). After the 11 slots: a count and, per player with personalised instructions, the save's person
number (u32), the number of his records (u32), and that many records of the same layout, each the role and duty he has
in that slot (a record with ``01 00`` in place of ``01 01`` is switched off). Verified against the user's three tactic
screens (33 slots) and two tactics saved from the game on 24 Oct 2026.

A match file's header: ``02`` + fixture id (u32) + stadium uid (u32) + the stadium's name; then the home crowd, the away
fans, ``00 00 20 40`` and the attendance (which the archive's fixtures table holds); later ``02`` + team number + ``02``
+ FM uid + ``00 00 01`` + club index + 1 + FM uid again, for the home side and then the away side; and after the
attendance (twice) ``00 16 02 00 0d 02 01 18 02``, five bytes, two f32 and the season's first year (u16): the match's
date is not stored, but the season and the crowd find its fixture (23 of 23 played files of one save). The ``.scm`` is
the club's next match, not yet played: no season, no crowd.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

POSITIONS = ("GK", "SW", "D (R)", "D (L)", "D (C)", "WB (R)", "WB (L)", "DM", "M (R)", "M (L)", "M (C)", "AM (R)", "AM (L)",
             "AM (C)", "ST (C)")
SIDES = {0x20: "right", 0x10: "left"}
DUTIES = {0x20: "Defend", 0x40: "Support", 0x80: "Attack"}
DUTY_BYTE = 2  # of the 8-byte mask
DUTY_BITS = 0xE0  # its top three bits; the lower bits of the byte are role bits like the other bytes'
# The second and third of the six bytes after the name. 1, 2 and 4, 5 are proven by the user's screens; the rest follow
# the game's order of the choices.
FLUIDITY = {0: "Very Structured", 1: "Structured", 2: "Flexible", 3: "Fluid", 4: "Very Fluid"}
MENTALITY = {1: "Very Defensive", 2: "Defensive", 3: "Cautious", 4: "Balanced", 5: "Positive", 6: "Attacking", 7: "Very Attacking"}
STYLES = {"TIKI": "Tiki-Taka", "GEGE": "Gegenpress", "NULL": None}
# The role bit, as "mask byte.bit". Capitalised names are proven by the user's screens; the lower-case ones are only known
# by the position they appear in, and carry their key for the day a screen names them.
ROLES = {
    "0.01": ("Goalkeeper", "GK"), "1.10": ("Sweeper Keeper", "SK"),
    "0.02": ("Central Defender", "CD"), "3.01": ("Ball Playing Defender", "BPD"), "3.20": ("centre-back (role 3.20)", "DC"),
    "0.04": ("Full Back", "FB"), "0.08": ("Wing Back", "WB"), "6.10": ("Inverted Full Back", "IFB"),
    "5.10": ("full-back (role 5.10)", "FB"), "4.40": ("wing-back (role 4.40)", "WB"),
    "0.10": ("Defensive Midfielder", "DM"), "4.02": ("Anchor", "A"), "4.08": ("defensive midfielder (role 4.08)", "DM"),
    "4.80": ("deep midfielder (role 4.80)", "DM"), "6.04": ("Segundo Volante", "VOL"), "1.80": ("Deep Lying Playmaker", "DLP"),
    "0.20": ("Central Midfielder", "CM"), "3.10": ("central midfielder (role 3.10)", "MC"),
    "5.80": ("central midfielder (role 5.80)", "MC"), "5.40": ("central midfielder (role 5.40)", "MC"),
    "1.02": ("Attacking Midfielder", "AM"), "2.02": ("Advanced Playmaker", "AP"), "5.02": ("attacking midfielder (role 5.02)", "AMC"),
    "4.20": ("attacking midfielder (role 4.20)", "AMC"),
    "3.08": ("Inside Forward", "IF"), "0.80": ("Winger", "W"), "6.02": ("Inverted Winger", "IW"),
    "1.04": ("Deep Lying Forward", "DLF"), "1.08": ("Advanced Forward", "AF"), "3.80": ("striker (role 3.80)", "ST"),
    "4.01": ("forward (role 4.01)", "ST"), "5.01": ("striker (role 5.01)", "ST"),
}
PRESETS = {"4-4-2", "4-3-3 DM Wide", "4-4-2 Diamond Narrow", "4-3-2-1 DM AM Narrow", "4-2-4 DM Wide", "4-2-3-1 DM AM Wide",
           "5-3-2 DM WB", "5-2-3 DM Wide", "4-2-2-2 DM AM Narrow", "5-2-2-1 DM AM", "5-2-1-2 DM AM"}
SLOTS = 11
MAX_PLAYER_RECORDS = 9

_HEAD = re.compile(rb"\x22\x3a\x00\x18\x02\x00\x00[\x00\x01]")
_TAG = re.compile(rb"([A-Z]{4})\x3a\x00\x02")
_MARK = re.compile(rb"\x3a\x00\x02")
_SLOT_PREFIX = b"\xff\x00"
_MAX_NAME = 60
_HEAD_FIXED = 13 + 6 + 9 + 4  # bytes between the name and the style name
_MATCH_SIDES = re.compile(rb"\x02(.{4})\x02(.{4})\x00\x00\x01.{4}\2\x02\x02(.{4})\x02(.{4})\x00\x00\x01.{4}\4", re.DOTALL)
_MATCH_CROWD = re.compile(rb"\x02(.{4})(.{4})(.{4})(?P<name>.{0,60}?)[\x00\x01](.{4})(.{4})(.{4})\x00\x00\x20\x40(.{4})", re.DOTALL)
_MATCH_SEASON = re.compile(rb"\x16\x02\x00\x0d\x02\x01\x18\x02.{5}.{8}(..)", re.DOTALL)  # five bytes, two f32, the season's year
_MATCH_HEAD = 20_000
_FIRST_YEAR, _LAST_YEAR = 1990, 2200


@dataclass(frozen=True, slots=True)
class TacticSlot:
    position: str  # of POSITIONS
    side: str | None  # "right", "left" or None
    duty: str | None  # of DUTIES
    role_key: str  # the role bit as "byte.bit", or the whole mask in hex when it is not one bit
    role: str | None  # the role's name when known

    @property
    def short(self) -> str:
        """The game's short form: "AF-At"."""
        role = ROLES.get(self.role_key, (None, "?"))[1]
        return f"{role}-{(self.duty or '?')[:2]}"


@dataclass(frozen=True, slots=True)
class PlayerRole:
    person_number: int  # the save's person number (fm_save.read_person_numbers maps it to the FM uid)
    slot: TacticSlot


@dataclass(slots=True)
class Tactic:
    name: str
    style: str | None  # "Tiki-Taka", "Gegenpress", a custom style's name, or None
    style_tag: str
    mentality: str | None
    fluidity: str | None
    slots: list[TacticSlot] = field(default_factory=list)
    player_roles: list[PlayerRole] = field(default_factory=list)

    @property
    def is_preset(self) -> bool:
        return self.name in PRESETS and self.style_tag == "NULL"

    @property
    def formation(self) -> str | None:
        from app.services.tactics import formation

        return formation([s.position for s in self.slots]) if len(self.slots) == SLOTS else None


def _slot(record: bytes) -> TacticSlot | None:
    """A slot record's position, side, duty and role; None when the bytes are not one."""
    if len(record) < 16:
        return None
    bits, side = struct.unpack_from("<HH", record, 0)
    mask = record[4:12]
    if bits == 0 or bits & (bits - 1) or bits >= 1 << len(POSITIONS) or side not in (0, 0x10, 0x20) or record[12] != 1:
        return None
    duty = mask[DUTY_BYTE] & DUTY_BITS
    if duty not in DUTIES:
        return None
    role_bits = [(i, 1 << k) for i, v in enumerate(mask) for k in range(8) if v >> k & 1 and not (i == DUTY_BYTE and 1 << k & DUTY_BITS)]
    key = f"{role_bits[0][0]}.{role_bits[0][1]:02x}" if len(role_bits) == 1 else mask.hex()
    return TacticSlot(POSITIONS[bits.bit_length() - 1], SIDES.get(side), DUTIES[duty], key, ROLES.get(key, (None,))[0])


def _records(block: bytes, start: int) -> Iterator[tuple[bytes, bytes]]:
    """Each record after a ``3a 00 02`` mark from ``start``, with the bytes just before its mark (the prefix)."""
    marks = [m.start() for m in _MARK.finditer(block, start)]
    for i, at in enumerate(marks):
        end = marks[i + 1] - 8 if i + 1 < len(marks) else len(block)
        yield block[max(0, at - 8):at], block[at + 3:max(at + 3, end)]


def _read_block(block: bytes) -> Tactic | None:
    length = struct.unpack_from("<I", block, 8)[0]
    if not 0 < length <= _MAX_NAME:
        return None
    name = block[12:12 + length].decode("utf-8", "replace")
    after = 12 + length
    tag = _TAG.search(block, after, after + _HEAD_FIXED + _MAX_NAME + 12)
    if tag is None:
        return None
    style_length = struct.unpack_from("<I", block, tag.start() - 4)[0] if tag.start() - 4 >= after else 0
    custom = block[tag.start() - style_length:tag.start()].decode("utf-8", "replace") if 0 < style_length <= _MAX_NAME else ""
    style_tag = tag.group(1)[::-1].decode("ascii")
    style = STYLES.get(style_tag, custom or style_tag)
    fluidity, mentality = block[after + 14], block[after + 15]
    tactic = Tactic(name, style, style_tag, MENTALITY.get(mentality), FLUIDITY.get(fluidity))
    person = None
    for prefix, record in _records(block, tag.end() - 3):
        slot = _slot(record)
        if slot is None:
            break
        if record[13] != 1:  # a personalised record switched off
            continue
        if len(tactic.slots) < SLOTS:
            tactic.slots.append(slot)
            continue
        if not prefix.endswith(_SLOT_PREFIX) and len(prefix) == 8:
            number, count = struct.unpack("<II", prefix)
            if not 1 <= count <= MAX_PLAYER_RECORDS:
                break
            person = number
        if person is not None:
            tactic.player_roles.append(PlayerRole(person, slot))
    return tactic if len(tactic.slots) == SLOTS else None


def iter_tactics(data: bytes) -> Iterator[Tactic]:
    """Every tactic block in a save file, in file order."""
    heads = [m.start() for m in _HEAD.finditer(data)]
    for i, at in enumerate(heads):
        end = heads[i + 1] if i + 1 < len(heads) else len(data)
        tactic = _read_block(data[at:end])
        if tactic is not None:
            yield tactic


def read_tactic_slots(tactics_man: bytes) -> list[Tactic]:
    """The human manager's three tactic slots, first to third: the first three blocks of ``tactics_man.dat``."""
    found = []
    for tactic in iter_tactics(tactics_man):
        found.append(tactic)
        if len(found) == 3:
            break
    return found


def read_tactic_library(tactics_man: bytes) -> list[Tactic]:
    """The saved tactics of ``tactics_man.dat`` without the game's presets, each name once (the first copy)."""
    found: dict[str, Tactic] = {}
    for tactic in iter_tactics(tactics_man):
        if not tactic.is_preset and tactic.name not in found:
            found[tactic.name] = tactic
    return list(found.values())


@dataclass(slots=True)
class MatchTactics:
    home_number: int
    home_uid: int
    away_number: int
    away_uid: int
    attendance: int | None
    away_fans: int | None
    season: int | None  # the season's first year (2063 for 2063/64); None in a file of a match not yet played
    tactics: list[Tactic]  # the named, non-preset blocks: the human side's tactic that day


def read_match_tactics(match_file: bytes) -> MatchTactics | None:
    """The two teams, the crowd and the named tactics of a match file (``.apm`` or ``.scm``); None without the teams."""
    sides = _MATCH_SIDES.search(match_file, 0, _MATCH_HEAD)
    if sides is None:
        return None
    home_number, home_uid, away_number, away_uid = (struct.unpack("<I", g)[0] for g in sides.groups())
    crowd = _MATCH_CROWD.search(match_file, 0, _MATCH_HEAD)
    attendance = away_fans = None
    if crowd:
        away_fans, attendance = struct.unpack("<I", crowd.group(7))[0], struct.unpack("<I", crowd.group(8))[0]
    season = None
    for m in _MATCH_SEASON.finditer(match_file, 0, _MATCH_HEAD):
        year = struct.unpack("<H", m.group(1))[0]
        if _FIRST_YEAR <= year <= _LAST_YEAR:
            season = year
            break
    tactics = [t for t in iter_tactics(match_file) if not t.is_preset]
    return MatchTactics(home_number, home_uid, away_number, away_uid, attendance, away_fans, season, tactics)


def person_uids_in(match_file: bytes, sorted_known: np.ndarray) -> frozenset[int]:
    """The FM uids of people in a match file (both squads and staffs): every u32 at any alignment that is a known uid
    (``sorted_known``: the save's uids, sorted). A few hundred of them are chance matches of other bytes; they are
    spread over the whole world and do not weigh on one club's season."""
    data = np.frombuffer(match_file, dtype=np.uint8)
    found: set[int] = set()
    for offset in range(4):
        values = data[offset:offset + (len(data) - offset) // 4 * 4].view("<u4")
        at = np.minimum(np.searchsorted(sorted_known, values), len(sorted_known) - 1)
        found.update(int(v) for v in values[sorted_known[at] == values])
    return frozenset(found)
