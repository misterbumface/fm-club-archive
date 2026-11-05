"""Read a Football Manager 24 saved game (``.fm``) without FM's HTML exports.

What is known, and how it was learned
-------------------------------------
The format was worked out by editing one player in the in-game editor, saving,
and diffing the saves. Everything marked *verified* below was checked that way
or against screenshots of the game. Nothing here writes to a save.

Container (verified)
    A ``.fm`` file is a header, then zstd frames laid end to end, then a second
    small section (its own ``02 01 'fmf.'`` header) holding a zstd-compressed
    *directory*. The directory lists 2 500+ named files (``game_db.dat``,
    ``fix_man.dat``, ``transfer_man.dat``...). Each entry gives the file's
    offset (counted from the first frame), its compressed size and its
    decompressed size. A file may span several frames.

Strings (verified)
    ``u32`` little-endian length, then the UTF-8 bytes. No terminator.

Dates (verified on edits and on 36 of 40 players)
    Birth date: ``u16`` day of the year (1-based, leap-aware) then ``u16`` year.
    Contract end: ``u16`` day of the year then one byte holding ``year - 2048``.

Player attributes (verified: 36 of 36 values against a screenshot)
    One byte each, on a 0-100 scale. The number the game shows is the byte
    divided by 5 and rounded; the fraction is hidden. The bytes sit in one
    block at fixed distances from the ``Finishing`` byte (see ``ATTRIBUTE_OFFSETS``).
    Height (cm) and weight (kg) are single bytes in the same block, at +82 and +80.

Player records (verified against the archive: 16 842 of 16 861 birthdays agree)
    In ``game_db.dat`` a player's record runs, in this order: the attribute block,
    a variable-length history, four zero bytes, the birth date, a ``0xFF`` byte,
    more fields, and finally the player's FM unique id written twice in a row.
    ``iter_players`` uses that shape. Players who have retired are not in the
    table (a save keeps only the living database), so an archive can hold more
    players than a save does.

Names (verified: 90% identical to the archive; most of the rest differ only in accents)
    Names are not in the player record. Three tables hold ``[u32 id][u32 length][utf-8]``
    entries, each split into several runs with a few bytes between them: first
    names, surnames, common names, in that order in the file. A record stores the
    first-name id 19 bytes before its birth date and the surname id 14 bytes before.
    The save spells names correctly (``Bursac`` in an HTML export is ``Bursa\u0107`` here).
    Edit-verified only for one player (so not decoded here): the common-name id
    9 bytes before the date, a shirt name at +252 from the attribute block, and a
    preferred squad number.

Fixtures and results (verified: 498 of 498 Premier Division results agree with the archive;
archived matches from 2023-2025 agree with the game's own screens, goal by goal)
    Learned by replaying one match three times from the same save (0-2, 0-1, 1-0)
    and diffing. ``fix_man.dat`` holds every fixture of every competition: the
    current ones in the file itself, and every archived one since the save began in
    compressed blocks inside the file (``iter_all_fixtures``). A fixture header is
    ``0x18``, competition (``u32``), fixture id (``u32``), 3 flag bytes, home team
    (``u32``), 3 flag bytes, away team (``u32``), 2 flag bytes, the date (``u16``
    whose low 9 bits are the day of the year, ``u16`` year), a second date, five
    ``ff`` bytes and the season year. The flags are usually ``02 ?? 00``,
    ``ff 00 00`` and ``ff 00``; knockout ties and second legs use other values, so
    headers are found by two patterns (see ``_fixture_headers``).
    A played fixture has its result directly *before* the header: ``01 18``, an
    id (``u32``), 4 bytes per side (goals, goals after extra time, shoot-out
    penalties, one more byte; ``ff`` when not used), attendance (``u32``), away
    fans (``u32``), 4 bytes not decoded, the number of goals (``u32``), then
    8 bytes per goal: person number (``u32``), side (0 home, 1 away), kind, minute,
    added time. Kinds: 0 goal, 1 counted (probably an own goal), 2 penalty, 3 not
    counted (probably a missed penalty), 4 and 5 shoot-out kick scored and missed.
    Checked against the game's match list for a whole Chelsea season (70 of 70
    matches, including penalty shoot-outs and second legs).
    Many results keep only the score and no goal list (8 350 of 38 552 in one
    save); when there is a list, it always agrees with the score.
    Team, competition and person numbers are the save's own numbers, not FM unique
    ids; ``read_teams``, ``read_stages`` and ``read_person_numbers`` translate
    them. The Premier Division is competition 162.

League tables (verified: Premier Division against the archive on two saves, all clubs)
    ``comp_<fm uid>.dat`` (``comp_11.dat`` is the English Premier Division) holds
    one row per club. A row starts with a zero byte, the club number (``u32``), a
    zero byte, six ``ff`` bytes and 4 bytes that are the same in every row of the
    file. The row data follows: +5 position (0-based),
    +21 played. Counted back from the end of the row: -67 points (``u16``),
    -65 clean sheets, -64 goals for, -63 goals against, -48 won, -47 lost. Drawn
    is not stored; it is played - won - lost. Rows have different lengths because
    of a recent-results list in the middle, so the end is the reliable anchor.
    The table starts with a ``u32`` row count just before the first row.
    Cross-checked on five saves: of the tables with games, about 70% equal (row
    for row) a table built from the results in ``fix_man.dat``, or exceed it only
    by play-off and second-stage games, which have their own competition number.
    The rest are lower or regional leagues for which the save keeps fewer results
    than the table counts. Most competition files have no table or another layout;
    ``read_league_table`` returns ``[]`` or refuses them instead of guessing.

Clubs and teams (verified: 1 471 of 1 512 first-team FM uids name the same club as the archive)
    ``game_db.dat`` holds 56 000+ club records: club index (``u32``), a number
    written twice (not the FM uid), a zero byte, 26 more bytes, the full name and the short
    name. Fixtures and tables do not use the club index: they use *team* numbers.
    A team record is ``ff ff ff 00``, 3 bytes, the age limit (100 or 255 for none,
    otherwise 18, 19, 21...), ``ff``, a type byte, club index + 1 (``u32``), the
    team's own name and short name (each followed by one byte; usually empty, but
    set for teams such as "Borussia Dortmund U19"), the team number, and the team's
    FM uid. A first team's FM uid is the club's FM uid (Arsenal 602, Chelsea 630).
    Learned from the match files (``.apm``), which store team number, FM uid and
    club index + 1 side by side: 2 292 of 2 293 agree.
    National teams have team records too: the second of the 3 bytes is 1, and the
    index is a *nation* index, not a club index (the two overlap, so reading it as a
    club put England under a Belgian club). A senior national team's FM uid is its
    nation's FM uid (England 765), and ``read_nation_names`` names it from the game's
    ``lang_db.dat``. In one save, 99.3% of the team numbers in fixtures have a record;
    the rest (78 African and Brazilian clubs) are not found yet.

Competitions (verified: 4 629 of 4 636 finals since 2022 lie in a stage of the right
competition; the other 7 are regional U19 winners meeting again in the national final)
    Competition names are not in the save; they come with the game's database.
    ``read_competition_names`` reads them from the installed game's ``lang_db.dat``
    (7 035 competitions; all 50 FM uids in the archive are there, 37 with the same
    name, the rest under the database's unlicensed name, such as "Spanish First
    Division" for La Liga).
    Fixtures name a *stage*, not a competition: a league has one stage (162 =
    Premier Division), a cup one per round (FA Cup 3016 third round ... 3807 final).
    ``game_db.dat`` has a stage table with one 33-byte row per stage: an index
    (``u32``), the stage number (index + 1) written twice, a zero byte, the
    competition's history key (``u32``) and 16 more bytes (probably the parent stage
    and the round). ``read_stages`` maps every stage to a competition FM uid; every
    fixture since 2022 has its stage there, because stage numbers stay the same from
    season to season (the FA Cup final is stage 3807 in every year).

People (verified: the ids of goal scorers since 2022 agree with the game's match list)
    Goals name their scorer by the save's own person number. ``game_db.dat`` maps
    person numbers to FM unique ids in rows of consecutive numbers: number (``u32``),
    FM uid twice, a type byte (0x10 or 0x13) and more bytes (``read_person_numbers``).
    This covers retired players too (664 469 people in one 2063 save). Their names
    are not decoded yet: the save's name tables hold each name text under many ids.

Competition winners (verified: 810 of 824 archive winners agree, and the German
Supercup against the game's own history screen; most of the rest are archive errors)
    ``comp_history_dt.cmt`` holds one 55-byte row per competition and season, from
    1872 on (see ``read_competition_history``). The row's key is the save's own
    competition number (key 7 = Premier Division, 664 = FA Cup, 5380 = German
    Supercup). ``game_db.dat`` has a record per competition with key and FM uid
    (``read_competition_keys``); the key is *almost* the position in the game
    database's list, which fills the few records not found. Slot 0 is the winner and slot 1
    the runner-up; leagues also fill slot 2 (third). The game writes a season's row
    late: saves from June and August 2063 hold rows up to 2061/62 only. Fixture
    competition numbers are *stages* (one per cup round) and are reused for other
    competitions in other seasons, so ``tc_cup_history_dt.cmt`` (team, stage,
    season, status 3 = won) cannot name a cup on its own.

Holders (verified: Premier Division, FA Cup, Conference League, German Supercup and
Club World Cup against the game; in a save from Sept 2062 the list agrees with the
2061/62 row written later in 114 of 147 competitions, the others are national teams)
    The latest finished edition is not in ``comp_history_dt.cmt``. Each competition's
    record in ``game_db.dat`` holds it as one more history row after ``01 03``
    (``read_latest_editions``; 3,313 competitions in an August 2063 save; checked against
    the game's history screens of the 2. Bundesliga and the Jupiler Pro League).
    ``comp_<uid>.dat`` also keeps the full final order as ``count (u32)`` + count x
    ``00 team (u32)``, followed by ``01 0a 00 00 00 01`` (``read_holders``), but only
    for the about 220 competitions the game plays in detail. Where a final stage follows
    a league stage (Rio State Championship) this list is the league-stage table, so take
    the winner from ``read_latest_editions``.

Player season statistics (verified: 2 427 of 2 680 club seasons in the archive's player
career tables agree in appearances, goals, assists, player of the match and rating; most
of the rest were exported in mid-season. Clean sheets and goals conceded agree for 163 of
164 goalkeeper seasons; yellow cards for most squad snapshots)
    ``player_stats_hist_dt.cmt`` holds one 152-byte record per player, club, season and
    kind of competition (``read_season_stats``): friendly, league, cup, continental or
    national team (counted at the player's club). All cups of a club share one record,
    so a single cup's total is not stored. ``player_stats_hist_ls.dat`` groups the
    records by player. A player record in ``game_db.dat`` holds its group number 40
    bytes before the marked birth date (``read_stat_groups``; 84 000 players in an
    August 2063 save; 23 groups claimed by two players are left out). Players who
    retired keep no player record; ``retirement_manager.dat`` holds their group number
    (``read_retired_players``; Paul Malcolm, Chelsea 2036-2051, 505 league goals).

Awards (verified: Ken Myers' awards and best elevens against the game's milestones
screen; the World and English Footballer of the Year look right)
    ``award_year_hist_dt.cmt`` holds one 82-byte row per award and year: year, award
    key, then three places of 26 bytes (person number, club or national team FM uid,
    age, rating total, appearances, goals, assists, votes; ``read_award_history``).
    Award names come from section 6 of the game's ``lang_db.dat`` (``read_award_names``);
    award records in ``game_db.dat`` map keys to award uids (``read_award_keys``), and
    like competition keys they leave the database order after about key 2139.
    ``tc_best_eleven_history_dt.cmt`` holds each team's seasonal best eleven
    (``read_best_elevens``): 18 players (the eleven, then seven substitutes) with
    appearances, goals, rating and position. Which team owns a list is not in the rows;
    ``guess_best_eleven_clubs`` infers clubs from the players' statistics (574 of 989
    lists; Chelsea's 2022/23 list equals the archive's). Monthly awards and the club
    awards in ``award_club_hist_dt.cmt`` are not decoded.

Transfers (verified: 1 240 of 1 447 archive transfers have a matching record within
200 days; the archive dates most moves by their season, not their day)
    ``transfer_man.dat`` holds every move since the save began, in plain bytes for the
    current window and in compressed blocks for older ones (``read_transfers``): person,
    new team, old team, the squad joined, fee, fee with add-ons, kind (transfer, loan,
    free transfer, release, youth intake ...) and date. Fees are in pounds; the game
    shows euros at about 1.166 times. Statistics records also hold the fee (bytes 7-10)
    and a loan flag (byte 30) of each club spell.

Match reports (verified: every value on the game's match screens for Chelsea 2-0
A. Madrid and Vitória 0-1 Sport, 7 July 2063: ratings, distances, xG, shots, passes,
crosses, tackles, headers, cards, substitutions, Player of the Match, referee)
    ``match_stats_cfg.dat`` keeps detailed reports in compressed blocks inside the file
    (one stream, like ``fix_man.dat``). It holds the user's club's matches of about the
    last four months and the last one to three rounds of the other competitions the game
    plays in detail, from all over the world: 300 to 600 matches. ``read_match_reports``
    reads them. A report starts with the competition's names, the fixture id (``u32``),
    4 bytes, the stadium name, then ``02 02`` home team (``u32``) ... ``02 02`` away
    team. The referee follows (person, 4 bytes, ``02 ff ff ff ff``, first and last
    name), then the goals (as in ``fix_man.dat``), attendance and away fans, then
    ``02`` Player of the Match (person ``u32``), a byte, the side and the match length
    (``u16``, 90). Five zero bytes later each team's part starts: ``02`` team (``u32``),
    squad size, a team block, the manager, and one record per squad member. A
    player record is 200 bytes when it has no shots and no items; ``_match_record_size``
    walks its shot list and item list. The offsets are in ``_MP``. Team totals are the sums of the players. The team block holds the team's own
    passes (``u16`` attempted, completed; in some matches not the players' sum), tackles and
    headers, corners and woodwork; it is found by the players' passes, or by their tackles
    and headers.
    Possession is not stored: the game's figure equals each side's share of the
    completed passes. Each match also has a second, shorter record (no players).
    A player's injury minute is at +71 (+16 is probably the kind of injury, ``ff`` when none).
    The game shows no rating for a player on the pitch for only a few minutes (3 minutes: none;
    19 minutes: rated); the record still holds a default rating. Red cards are at +63 with
    the minute at +73 (the added time at +68), and in the team block next to the yellow cards.

Not yet known
    * International caps and goals, contract end and wage. In one record they sit
      272, 168 and 84 bytes before the birth date, but that distance changes from
      player to player (it matched only 19% of the time), so it is not used here.
    * A direct link from every fixture
      competition number to its FM uid, and the link from the save's player
      numbers to FM unique ids.
    * Winners of the season just finished (not yet in the history rows), the
      names a user gave to renamed competitions (an edit log near the end of
      ``game_db.dat``), transfers and statistics.
"""

from __future__ import annotations

import bisect
import math
import mmap
import re
import struct
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator

ZSTD_MAGIC = bytes.fromhex("28b52ffd")
SECTION_HEADER = b"\x02\x01fmf."

# Distance, in bytes, of each attribute from the ``Finishing`` byte.
ATTRIBUTE_OFFSETS: dict[str, int] = {
    # technical
    "Crossing": -2, "Dribbling": -1, "Finishing": 0, "Heading": 1, "Long Shots": 2,
    "Marking": 3, "Passing": 5, "Penalty Taking": 6, "Tackling": 7, "First Touch": 20,
    "Technique": 21, "Corners": 25, "Long Throws": 28, "Free Kick Taking": 33,
    # mental
    "Off The Ball": 4, "Vision": 8, "Anticipation": 15, "Decisions": 16, "Positioning": 18,
    "Flair": 24, "Teamwork": 26, "Work Rate": 27, "Leadership": 38, "Bravery": 41,
    "Aggression": 43, "Determination": 49, "Composure": 50, "Concentration": 51,
    # physical
    "Acceleration": 32, "Strength": 34, "Stamina": 35, "Pace": 36, "Jumping Reach": 37,
    "Balance": 40, "Agility": 44, "Natural Fitness": 48,
}
WEIGHT_OFFSET = 80
HEIGHT_OFFSET = 82
# The game's position ratings (1-20; 20 natural) stand just before the attribute block, in the game's
# order; the feet (stored like attributes, 0-100) sit inside it. Found with the in-game editor
# (Roberto Ştefănescu: D (L) 5, D (C) 20, D (R) 18, AM (C) 2; feet 13 and 20).
POSITION_CODES = ("GK", "SW", "D (L)", "D (C)", "D (R)", "DM", "M (L)", "M (C)", "M (R)",
                  "AM (L)", "AM (C)", "AM (R)", "ST (C)", "WB (L)", "WB (R)")
POSITION_OFFSET = -17
LEFT_FOOT_OFFSET = 22
RIGHT_FOOT_OFFSET = 23


class FmSaveError(Exception):
    """The file is not a save this reader understands."""


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    name: str
    offset: int  # from the first frame
    compressed: int
    size: int  # decompressed


def _zstd():
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise FmSaveError(
            "Reading saves needs the 'zstandard' package: pip install -r requirements.txt"
        ) from exc
    return zstandard


class FmSave:
    """An open ``.fm`` file. Use as a context manager, or call ``close()``."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._file = open(self.path, "rb")
        try:
            self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError) as exc:
            self._file.close()
            raise FmSaveError(f"Cannot read {self.path}: {exc}") from exc
        try:
            if self._map[2:6] != b"fmf.":
                raise FmSaveError(f"{self.path.name} does not start with the FM save header.")
            self._first = self._map.find(ZSTD_MAGIC, 0, 64)
            if self._first < 0:
                raise FmSaveError("No data block follows the header.")
            self.directory: dict[str, DirectoryEntry] = self._read_directory()
        except Exception:
            self.close()
            raise

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        for resource in (getattr(self, "_map", None), getattr(self, "_file", None)):
            if resource is not None:
                try:
                    resource.close()
                except Exception:  # pragma: no cover
                    pass

    def __enter__(self) -> "FmSave":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- directory -----------------------------------------------------------
    def _read_directory(self) -> dict[str, DirectoryEntry]:
        zstd = _zstd()
        start = self._map.rfind(SECTION_HEADER)
        if start <= 0:
            raise FmSaveError("The save has no directory section.")
        # Header is 9 bytes: 02 01 'fmf.' 08 00 00
        body = self._map[start + 9 :]
        try:
            data = zstd.ZstdDecompressor().decompressobj().decompress(body)
        except zstd.ZstdError as exc:
            raise FmSaveError(f"The directory section is damaged: {exc}") from exc
        entries: dict[str, DirectoryEntry] = {}
        for match in re.finditer(rb"([A-Za-z0-9_]{3,60})\x04\x00\x00\x00(\.[a-z]{3})", data):
            name = match.group(1).decode()
            if struct.unpack_from("<I", data, match.start(1) - 4)[0] != len(name):
                continue
            if match.end() + 24 > len(data):
                continue
            offset, compressed, size = struct.unpack_from("<QQQ", data, match.end())
            full = name + match.group(2).decode()
            entries[full] = DirectoryEntry(full, offset, compressed, size)
        if not entries:
            raise FmSaveError("The directory lists no files.")
        return entries

    def names(self) -> list[str]:
        return sorted(self.directory)

    # -- file contents ---------------------------------------------------------
    def read(self, name: str) -> bytes:
        """Return the decompressed contents of one named file."""
        try:
            entry = self.directory[name]
        except KeyError:
            raise FmSaveError(f"No file named {name!r} in this save.") from None
        zstd = _zstd()
        pos = self._first + entry.offset
        end = pos + entry.compressed
        parts: list[bytes] = []
        while pos < end:
            decompressor = zstd.ZstdDecompressor().decompressobj()
            chunk = self._map[pos:end]
            parts.append(decompressor.decompress(chunk))
            pos = end - len(decompressor.unused_data)
        data = b"".join(parts)
        if len(data) != entry.size:
            raise FmSaveError(f"{name}: expected {entry.size} bytes, got {len(data)}.")
        return data


# -- decoders --------------------------------------------------------------------
def _date_from_day_of_year(day_of_year: int, year: int) -> date | None:
    if not 1900 <= year <= 2300 or not 1 <= day_of_year <= 366:
        return None
    result = date(year, 1, 1) + timedelta(days=day_of_year - 1)
    return result if result.year == year else None  # day 366 of a normal year is invalid


def decode_birth_date(buffer: bytes, position: int) -> date | None:
    """``u16`` day of year + ``u16`` year, or ``None`` if the bytes are not a date."""
    if position < 0 or position + 4 > len(buffer):
        return None
    day_of_year, year = struct.unpack_from("<HH", buffer, position)
    return _date_from_day_of_year(day_of_year, year)


def decode_contract_date(buffer: bytes, position: int) -> date | None:
    """``u16`` day of year + one byte holding ``year - 2048``."""
    if position < 0 or position + 3 > len(buffer):
        return None
    day_of_year, year_offset = struct.unpack_from("<HB", buffer, position)
    return _date_from_day_of_year(day_of_year, 2048 + year_offset)


def display_value(raw: int) -> int:
    """The number the game shows for a stored attribute byte (0-100 scale)."""
    return int(raw / 5 + 0.5)


def read_attributes(buffer: bytes, finishing_position: int) -> dict[str, int]:
    """The 36 attributes of the block whose ``Finishing`` byte is at ``finishing_position``."""
    return {
        name: display_value(buffer[finishing_position + offset])
        for name, offset in ATTRIBUTE_OFFSETS.items()
    }


def read_raw_attributes(buffer: bytes, finishing_position: int) -> dict[str, int]:
    """The stored bytes (0-100 scale), which keep the fraction the game hides."""
    return {name: buffer[finishing_position + offset] for name, offset in ATTRIBUTE_OFFSETS.items()}


def read_positions(buffer: bytes, finishing_position: int) -> dict[str, int] | None:
    """The 15 position ratings before the block, or None when the bytes there are not ratings."""
    start = finishing_position + POSITION_OFFSET
    if start < 0:
        return None
    ratings = buffer[start:start + len(POSITION_CODES)]
    if not all(1 <= rating <= 20 for rating in ratings) or max(ratings) < 15:
        return None
    return dict(zip(POSITION_CODES, ratings))


def read_feet(buffer: bytes, finishing_position: int) -> tuple[int, int]:
    """Left and right foot, 1-20 as the game shows them."""
    return (display_value(buffer[finishing_position + LEFT_FOOT_OFFSET]),
            display_value(buffer[finishing_position + RIGHT_FOOT_OFFSET]))


def read_body(buffer: bytes, finishing_position: int) -> dict[str, int]:
    return {
        "height_cm": buffer[finishing_position + HEIGHT_OFFSET],
        "weight_kg": buffer[finishing_position + WEIGHT_OFFSET],
    }


# -- searching ---------------------------------------------------------------------
def find_uid(buffer: bytes, uid: int, limit: int = 20) -> list[int]:
    """Positions of a player's FM unique id (``u32``) in ``buffer``."""
    needle = struct.pack("<I", uid)
    found: list[int] = []
    start = 0
    while len(found) < limit:
        start = buffer.find(needle, start)
        if start < 0:
            break
        found.append(start)
        start += 1
    return found


def candidate_birth_dates(buffer: bytes, uid_position: int, window: int = 1200) -> list[tuple[int, date]]:
    """Every place before ``uid_position`` whose bytes decode as a plausible birth date.

    A heuristic: other fields can look like dates. Returned nearest-first as
    ``(distance_before_uid, date)``. The caller must confirm the right one.
    """
    found = []
    for back in range(0, window):
        position = uid_position - back
        if position < 0:
            break
        decoded = decode_birth_date(buffer, position)
        if decoded is not None and 1950 <= decoded.year <= 2200:
            found.append((back, decoded))
    return found


def find_attribute_blocks(buffer: bytes, chunk: int = 32_000_000) -> Iterator[int]:
    """Yield the position of the ``Finishing`` byte of every attribute block.

    A block is recognised by shape: all 36 attribute bytes in 3-100, a weight of
    45-140 kg and a height of 140-215 cm at the known distances. On the sample
    save this finds 49 112 blocks. Shape alone can, rarely, match other data.
    Needs numpy.
    """
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise FmSaveError("find_attribute_blocks needs numpy: pip install -r requirements.txt") from exc

    data = np.frombuffer(buffer, dtype=np.uint8)
    low = -min(ATTRIBUTE_OFFSETS.values())
    high = max(max(ATTRIBUTE_OFFSETS.values()), HEIGHT_OFFSET, WEIGHT_OFFSET)
    total = len(data)
    start = low
    while start < total - high:
        stop = min(start + chunk, total - high)
        mask = np.ones(stop - start, dtype=bool)
        for offset in ATTRIBUTE_OFFSETS.values():
            values = data[start + offset : stop + offset]
            mask &= (values >= 3) & (values <= 100)
        weight = data[start + WEIGHT_OFFSET : stop + WEIGHT_OFFSET]
        height = data[start + HEIGHT_OFFSET : stop + HEIGHT_OFFSET]
        mask &= (weight >= 45) & (weight <= 140) & (height >= 140) & (height <= 215)
        yield from (int(i) + start for i in np.nonzero(mask)[0])
        start = stop


def diff_files(first: bytes, second: bytes, limit: int = 40) -> tuple[int, list[tuple[int, int, int]]]:
    """Compare two same-sized byte strings. Returns (count, first ``limit`` ``(position, a, b)``)."""
    if len(first) != len(second):
        raise ValueError("Files differ in size; compare them by hand.")
    if first == second:
        return 0, []
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        positions = [i for i in range(len(first)) if first[i] != second[i]]
    else:
        positions = np.nonzero(np.frombuffer(first, np.uint8) != np.frombuffer(second, np.uint8))[0].tolist()
    return len(positions), [(p, first[p], second[p]) for p in positions[:limit]]


# -- players --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PlayerRecord:
    uid: int
    block_position: int  # position of the Finishing byte
    birth_date: date
    attributes: dict[str, int]
    height_cm: int
    weight_kg: int
    first_name: str | None = None
    last_name: str | None = None
    positions: dict[str, int] | None = None  # POSITION_CODES -> 1-20
    left_foot: int | None = None
    right_foot: int | None = None
    traits: dict[str, int] | None = None  # TRAIT_NAMES (1-20) and HIDDEN_ATTRIBUTE_OFFSETS' names
    preferred_moves: tuple[str, ...] | None = None  # PREFERRED_MOVES, the editor's "Player Traits"


# The hidden traits (verified: Howard Parkins edited in the in-game editor to 1, 2, 3, 20, 19, 18, 17, 16 and the save
# diffed, 26 Oct 2026; the shape holds for 48 557 of 48 559 players, the two others have no birth date): eight bytes on
# the 1-20 scale, in the editor's order, 17 bytes after the birth date (date, 0xFF, five bytes, seven zero bytes, traits).
# Five more hidden ones live in the attribute block on the 0-100 scale (Pardal and Parkins agree): HIDDEN_ATTRIBUTE_OFFSETS.
TRAIT_NAMES = ("Adaptability", "Ambition", "Loyalty", "Pressure", "Professionalism", "Sportsmanship", "Temperament", "Controversy")
TRAITS_AFTER_BIRTH_DATE = 17
HIDDEN_ATTRIBUTE_OFFSETS = {"Dirtiness": 39, "Consistency": 42, "Important Matches": 45, "Injury Proneness": 46, "Versatility": 47}


# The preferred moves, the editor's "Player Traits" (2-3 Nov 2026, docs/player-traits.md): a u64, one bit per move, 27 bytes
# before the birth date. Found with Exhibits 10-14: Geyer's "Cuts Inside From Left Wing" untouched in the editor changed only
# bit 57; Byorn Eind given 3, then 12, then 12 other moves changed exactly those bits. The order is the editor's list read
# row by row (left column even, right column odd); five bits have no move in the editor. About 1% of records read with many
# bits (not aligned): more than MAX_PREFERRED_MOVES is no reading.
PREFERRED_MOVES = (
    "Runs With Ball Down Left", "Runs With Ball Down Right", "Runs With Ball Through Centre", "Gets Into Opposition Area",
    "Moves Into Channels", "Gets Forward Whenever Possible", "Plays Short Simple Passes", "Tries Killer Balls Often",
    "Shoots From Distance", "Shoots With Power", "Places Shots", "Curls Ball", "Likes To Round Keeper",
    "Likes To Try To Beat Offside Trap", "Uses Outside Of Foot", "Marks Opponent Tightly", "Winds Up Opponents",
    "Argues With Officials", "Plays With Back To Goal", "Comes Deep To Get Ball", "Plays One-Twos", "Likes To Lob Keeper",
    "Dictates Tempo", "Attempts Overhead Kicks", "Looks For Pass Rather Than Attempting To Score", "Plays No Through Balls",
    "Stops Play", "Knocks Ball Past Opponent", "Moves Ball To Right Foot Before Dribble Attempt",
    "Moves Ball To Left Foot Before Dribble Attempt", "Dwells On Ball", "Arrives Late In Opponents' Area",
    "Tries To Play Way Out Of Trouble", "Stays Back At All Times", "Avoids Using Weaker Foot", "Tries Tricks",
    "Tries Long Range Free Kicks", "Dives Into Tackles", "Does Not Dive Into Tackles", "Cuts Inside From Both Wings", "Hugs Line",
    "Gets Crowd Going", "Tries First Time Shots", "Tries Long Range Passes", "Likes Ball Played Into Feet",
    "Hits Free Kicks With Power", "Likes To Beat Opponent Repeatedly", "Likes To Switch Ball To Wide Areas", None, None,
    "Possesses Long Flat Throw", "Runs With Ball Often", "Runs With Ball Rarely", None, "Does Not Move Into Channels",
    "Uses Long Throw To Start Counter Attacks", "Refrains From Taking Long Shots", "Cuts Inside From Left Wing",
    "Cuts Inside From Right Wing", "Crosses Early", "Brings Ball Out Of Defence", None, None, "Plays Ball With Feet",
)
PREFERRED_MOVES_BEFORE_BIRTH_DATE = 27
MAX_PREFERRED_MOVES = 12


def read_preferred_moves(buffer: bytes, dob_position: int) -> tuple[str, ...] | None:
    """A player's preferred moves in the editor's order; None when the bytes there are no such field."""
    at = dob_position - PREFERRED_MOVES_BEFORE_BIRTH_DATE
    if at < 0:
        return None
    bits = struct.unpack_from("<Q", buffer, at)[0]
    if bin(bits).count("1") > MAX_PREFERRED_MOVES:
        return None
    return tuple(name for number, name in enumerate(PREFERRED_MOVES) if name and bits >> number & 1)


def read_traits(buffer: bytes, dob_position: int, finishing_position: int) -> dict[str, int] | None:
    """The thirteen hidden attributes of a record: the eight person traits after the birth date (1-20 as stored) and the
    five in the attribute block (shown like the others), or None when the bytes after the date have not the traits' shape."""
    start = dob_position + TRAITS_AFTER_BIRTH_DATE
    between = buffer[dob_position + 4:start]
    traits = buffer[start:start + len(TRAIT_NAMES)]
    if len(traits) < len(TRAIT_NAMES) or between[:1] != b"\xff" or between[6:13] != bytes(7) or not all(1 <= t <= 20 for t in traits):
        return None
    found = dict(zip(TRAIT_NAMES, traits))
    found.update({name: display_value(buffer[finishing_position + offset]) for name, offset in HIDDEN_ATTRIBUTE_OFFSETS.items()})
    return found


_MAX_WRITTEN_NAME = 120  # bytes of a full name written out before a birth date


def _written_name_length(buffer: bytes, position: int) -> int | None:
    """The full name written out just before a birth date: its u32 length, then the text. A new
    player has none (length 0: four zero bytes); one from the game's database has his whole name
    ("Issey Morgan Nakajima-Farran"). None when the bytes there are neither."""
    if position < 4:
        return None
    if buffer[position - 4:position] == b"\x00\x00\x00\x00":
        return 0
    for length in range(1, min(_MAX_WRITTEN_NAME, position - 4) + 1):
        if struct.unpack_from("<I", buffer, position - length - 4)[0] == length:
            try:
                text = buffer[position - length:position].decode("utf-8")
            except UnicodeDecodeError:
                return None
            return length if text.isprintable() and any(c.isalpha() for c in text) else None
    return None


def _written_name_shift(buffer: bytes, birth: int) -> int:
    """How far the name ids and the other fields before a birth date stand back: the written name's length."""
    return _written_name_length(buffer, birth) or 0


# A name block: [first id] 00 [last id] 00 [common id or ff ff ff ff] 00 [u32 length][written name][birth date].
_NAME_BLOCK = re.compile(rb"(?=.{4}\x00.{4}\x00.{4}\x00)", re.DOTALL)


def _name_block_dates(buffer: bytes, start: int, stop: int) -> Iterator[tuple[int, bool]]:
    """Birth dates in [start, stop) that end a name block, with whether each is a strong one.

    A new player has no written name (length 0); one from the game's database has his whole
    name ("Issey Morgan Nakajima-Farran"). A strong date has a written name or 0xFF after it
    (as older records have); a weak one, neither: in older records that is often someone else's
    block, in a new game's records it is the player's own."""
    for match in _NAME_BLOCK.finditer(buffer, max(0, start + FIRST_NAME_ID_OFFSET), stop):
        block = match.start()
        if not (struct.unpack_from("<I", buffer, block)[0] and struct.unpack_from("<I", buffer, block + 5)[0]):
            continue
        length = struct.unpack_from("<I", buffer, block + 15)[0] if block + 19 <= len(buffer) else None
        if length is None or length > _MAX_WRITTEN_NAME:
            continue
        position = block + 19 + length
        if not start <= position < stop - 8:
            continue
        decoded = decode_birth_date(buffer, position)
        if decoded is None or not 1960 <= decoded.year <= 2060 or _written_name_length(buffer, position) != length:
            continue
        yield position, length > 0 or buffer[position + 4] == 0xFF


def _find_marked_birth_date(buffer: bytes, start: int, stop: int) -> int | None:
    """The record's own birth date: the first strong one in [start, stop), else the first weak one."""
    weak = None
    for position, strong in _name_block_dates(buffer, start, stop):
        if strong:
            return position
        weak = position if weak is None else weak
    return weak


_NO_DATE_YEAR = 1900


def _find_person_row(buffer: bytes, start: int, stop: int) -> tuple[int, int, int] | None:
    """The first person row in [start, stop): ``[person number][uid][uid]`` as (number, uid,
    position), any uid (a new
    player's or one from the game's database). A run of the game's 'no date' (year 1900:
    ``01 01 6c 07``, read as 124518401 or, two bytes on, 67436) is not a uid."""
    for position in range(start, stop - 12):
        uid = struct.unpack_from("<I", buffer, position + 4)[0]
        if 1000 < uid < _MAX_UID and _NO_DATE_YEAR not in (uid >> 16, uid & 0xFFFF) \
                and struct.unpack_from("<I", buffer, position + 8)[0] == uid:
            number = struct.unpack_from("<I", buffer, position)[0]
            if 0 < number < _MAX_PERSON and number != uid:
                return number, uid, position
    return None


_NAME_HEADER = re.compile(rb"[\x00-\xff]{2}[\x00-\x0f]\x00[\x01-\x3c]\x00\x00\x00")
FIRST_NAME_ID_OFFSET = -19  # from the birth date
LAST_NAME_ID_OFFSET = -14


@dataclass(frozen=True, slots=True)
class NameTables:
    first: dict[int, str]
    last: dict[int, str]
    common: dict[int, str]


def _name_entry(buffer: bytes, position: int) -> tuple[int, str, int] | None:
    if position < 0 or position + 8 > len(buffer):
        return None
    ident, length = struct.unpack_from("<II", buffer, position)
    if not 1 <= length <= 60 or position + 8 + length > len(buffer) or ident > 5_000_000:
        return None
    try:
        text = buffer[position + 8 : position + 8 + length].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return (ident, text, position + 8 + length) if text.isprintable() else None


def read_name_tables(buffer: bytes, min_entries: int = 100) -> NameTables:
    """Read the first-name, surname and common-name tables from ``game_db.dat``.

    Finds every long run of ``[id][length][text]`` entries, then joins runs into
    tables: a table ends where the ids start again from a lower number.
    """
    runs: list[list[tuple[int, str]]] = []
    end_of_last = 0
    for match in _NAME_HEADER.finditer(buffer):
        position = match.start()
        if position < end_of_last:
            continue
        items: list[tuple[int, str]] = []
        cursor = position
        while (entry := _name_entry(buffer, cursor)) is not None:
            items.append((entry[0], entry[1]))
            cursor = entry[2]
        if len(items) >= min_entries:
            runs.append(items)
            end_of_last = cursor
    tables: list[list[tuple[int, str]]] = []
    for items in runs:
        if not tables or items[0][0] < tables[-1][-1][0]:
            tables.append([])
        tables[-1].extend(items)
    if len(tables) != 3:
        raise FmSaveError(f"Expected 3 name tables, found {len(tables)}.")
    return NameTables(*(dict(table) for table in tables))


_BLOCK_SLIP = 14  # bytes: how far past the true block a shape match can lie


def player_blocks(buffer: bytes) -> list[int]:
    """The attribute blocks of players: each stands just after its position ratings.

    A block's shape can also match a few bytes inside the true block (148 of 48 781 players
    in one 2063 save), which then ended the true one early or stood alone when the true one
    failed the shape test (an attribute stored below 3). Such a match moves back to the
    ratings before it."""
    found = set()
    for block in find_attribute_blocks(buffer):
        if read_positions(buffer, block) is None:
            block = next((block - back for back in range(1, _BLOCK_SLIP + 1) if read_positions(buffer, block - back)), None)
        if block is not None:
            found.add(block)
    return sorted(found)


def player_rows(buffer: bytes, blocks: list[int] | None = None) -> Iterator[tuple[int, int, int]]:
    """Each player's own person row (number, uid, position): the first row after his birth date."""
    blocks = player_blocks(buffer) if blocks is None else blocks
    for index, block in enumerate(blocks):
        next_block = blocks[index + 1] if index + 1 < len(blocks) else len(buffer) - 8
        stop = min(block + 60_000, next_block)
        birth = _find_marked_birth_date(buffer, block, stop)
        row = _find_person_row(buffer, birth + 4, min(birth + 1500, stop)) if birth is not None else None
        if row is not None:
            yield row


def iter_players(
    buffer: bytes,
    blocks: list[int] | None = None,
    max_record: int = 60_000,
    names: NameTables | None = None,
) -> Iterator[PlayerRecord]:
    """Yield every player whose record can be read from ``game_db.dat``.

    Walks the attribute blocks in file order. Blocks whose date or id cannot be
    found are skipped (169 of 49 112 on the sample save). A player from the game's database
    has his full name written before his birth date and a small uid; a new player has neither.
    """
    if blocks is None:
        blocks = player_blocks(buffer)
    for index, block in enumerate(blocks):
        next_block = blocks[index + 1] if index + 1 < len(blocks) else len(buffer) - 8
        stop = min(block + max_record, next_block)
        dob_position = _find_marked_birth_date(buffer, block, stop)
        if dob_position is None:
            continue
        row = _find_person_row(buffer, dob_position + 4, min(dob_position + 1500, stop))
        if row is None:
            continue
        uid = row[1]
        body = read_body(buffer, block)
        feet = read_feet(buffer, block)
        first_name = last_name = None
        if names is not None:
            shift = _written_name_shift(buffer, dob_position)
            first_name = names.first.get(struct.unpack_from("<I", buffer, dob_position - shift + FIRST_NAME_ID_OFFSET)[0])
            last_name = names.last.get(struct.unpack_from("<I", buffer, dob_position - shift + LAST_NAME_ID_OFFSET)[0])
        yield PlayerRecord(
            uid=uid,
            block_position=block,
            birth_date=decode_birth_date(buffer, dob_position),
            attributes=read_attributes(buffer, block),
            height_cm=body["height_cm"],
            weight_kg=body["weight_kg"],
            first_name=first_name,
            last_name=last_name,
            positions=read_positions(buffer, block),
            left_foot=feet[0],
            right_foot=feet[1],
            traits=read_traits(buffer, dob_position, block),
            preferred_moves=read_preferred_moves(buffer, dob_position),
        )


# -- fixtures and results ----------------------------------------------------------------
# The 3 bytes after the fixture id and the bytes after each team are flags (neutral
# ground, leg, knockout tie...); usually 02 ?? 00, ff 00 00 and ff 00, but knockout and
# replayed fixtures use other values, so they are not part of the pattern.
_FIXTURE_HEADER = re.compile(rb"\x18(.{4})(.{4}).{3}(.{4})\xff.{2}(.{4})\xff.(.{2})(.{2})", re.DOTALL)
# Second legs and some knockout fixtures have other bytes in place of the two ff bytes;
# they are found by the end of the header instead: a second date, five ff bytes and the
# season year.
_FIXTURE_HEADER_BY_SEASON = re.compile(
    rb"\x18(.{4})(.{4}).{3}(.{4}).{3}(.{4}).{2}(.{2})(.{2}).{4}\xff{5}(.{2})", re.DOTALL
)
_RESULT_START = b"\x01\x18"
_RESULT_FIXED = 30  # bytes before the goal list
_GOAL_SIZE = 8
_MAX_GOALS = 80  # goals plus penalty shoot-out kicks
_MAX_CLUB = 500_000  # team numbers (second XIs reach about 144 000)
_NO_VALUE = 0xFF
_MAX_SCORE = 100
_FIRST_YEAR, _LAST_YEAR = 1990, 2200
GOAL_NOT_COUNTED = 3
SHOOTOUT_SCORED = 4
SHOOTOUT_MISSED = 5


@dataclass(frozen=True, slots=True)
class Goal:
    player_id: int  # the save's own person number (see read_person_numbers)
    side: int  # 0 home, 1 away
    kind: int  # 0 normal, 1 own goal?, 2 penalty, 3 not counted, 4/5 shoot-out kick scored/missed
    minute: int
    added_time: int

    @property
    def counts(self) -> bool:
        """Whether the goal is part of the score (not a missed penalty or a shoot-out kick)."""
        return self.kind not in (GOAL_NOT_COUNTED, SHOOTOUT_SCORED, SHOOTOUT_MISSED)


@dataclass(frozen=True, slots=True)
class Fixture:
    position: int  # of the header in fix_man.dat
    competition_id: int  # the save's own number, not the FM uid
    fixture_id: int
    home_id: int
    away_id: int
    date: date
    home_goals: int | None = None
    away_goals: int | None = None
    attendance: int | None = None
    goals: tuple[Goal, ...] = ()
    away_fans: int | None = None
    home_goals_after_extra_time: int | None = None  # only when extra time was played
    away_goals_after_extra_time: int | None = None
    home_penalties: int | None = None  # only after a penalty shoot-out
    away_penalties: int | None = None

    @property
    def played(self) -> bool:
        return self.home_goals is not None


def decode_match_date(raw: bytes) -> date | None:
    """``u16`` whose low 9 bits are the day of the year, then ``u16`` year."""
    packed, year = struct.unpack("<HH", raw)
    return _date_from_day_of_year(packed & 0x1FF, year)


def _score_bytes(raw: bytes) -> tuple[int, int | None, int | None] | None:
    """Goals, goals after extra time and shoot-out penalties of one side, or None."""
    if any(value != _NO_VALUE and value >= _MAX_SCORE for value in raw) or raw[0] == _NO_VALUE:
        return None
    goals, extra_time, penalties = raw[0], raw[1], raw[2]
    return goals, None if extra_time == _NO_VALUE else extra_time, None if penalties == _NO_VALUE else penalties


def _result_before(buffer: bytes, header: int) -> dict | None:
    """The result block that ends exactly at ``header``, if there is one."""
    for count in range(_MAX_GOALS + 1):
        start = header - _RESULT_FIXED - _GOAL_SIZE * count
        if start < 0:
            return None
        if buffer[start : start + 2] != _RESULT_START or struct.unpack_from("<I", buffer, start + 26)[0] != count:
            continue
        home, away = _score_bytes(buffer[start + 6 : start + 10]), _score_bytes(buffer[start + 10 : start + 14])
        if home is None or away is None:
            continue
        goals = tuple(
            Goal(*struct.unpack_from("<IBBBB", buffer, start + _RESULT_FIXED + _GOAL_SIZE * i))
            for i in range(count)
        )
        attendance, away_fans = struct.unpack_from("<II", buffer, start + 14)
        return {
            "home_goals": home[0], "away_goals": away[0], "attendance": attendance, "goals": goals,
            "away_fans": away_fans, "home_goals_after_extra_time": home[1], "away_goals_after_extra_time": away[1],
            "home_penalties": home[2], "away_penalties": away[2],
        }
    return None


def _fixture_headers(buffer: bytes) -> Iterator[re.Match]:
    """Header matches from both patterns, in file order, each position once."""
    seen: set[int] = set()
    matches = list(_FIXTURE_HEADER.finditer(buffer))
    for match in _FIXTURE_HEADER_BY_SEASON.finditer(buffer):
        season = struct.unpack("<H", match.group(7))[0]
        when = decode_match_date(match.group(5) + match.group(6))
        if when is not None and abs(season - when.year) <= 1:
            matches.append(match)
    for match in sorted(matches, key=lambda m: m.start()):
        if match.start() not in seen:
            seen.add(match.start())
            yield match


def iter_fixtures(buffer: bytes) -> Iterator[Fixture]:
    """Yield every fixture in ``fix_man.dat``, with its result when it was played."""
    for match in _fixture_headers(buffer):
        competition, fixture_id, home, away = (struct.unpack("<I", g)[0] for g in match.groups()[:4])
        when = decode_match_date(match.group(5) + match.group(6))
        if when is None or not _FIRST_YEAR <= when.year <= _LAST_YEAR:
            continue
        if not (0 <= home < _MAX_CLUB and 0 <= away < _MAX_CLUB):  # team 0 is a real team (Algeria)
            continue
        result = _result_before(buffer, match.start()) or {}
        yield Fixture(match.start(), competition, fixture_id, home, away, when, **result)


def iter_inner_blocks(data: bytes) -> Iterator[bytes]:
    """Decompress the zstd frames stored *inside* a file's data.

    Some files keep their older data as compressed blocks within the file:
    ``fix_man.dat`` (every archived fixture), ``comp_archive.dat``, ``award_man.dat``,
    ``transfer_man.dat`` and others. ``FmSave.read`` returns the file with those blocks
    still compressed. Bytes that only look like a frame start are skipped.
    """
    zstd = _zstd()
    position = data.find(ZSTD_MAGIC)
    while position >= 0:
        decompressor = zstd.ZstdDecompressor().decompressobj()
        try:
            block = decompressor.decompress(data[position:])
        except zstd.ZstdError:
            position = data.find(ZSTD_MAGIC, position + 4)
            continue
        used = len(data) - position - len(decompressor.unused_data)
        yield block
        position = data.find(ZSTD_MAGIC, position + max(used, 4))


def iter_all_fixtures(fix_man: bytes) -> Iterator[Fixture]:
    """Every fixture in ``fix_man.dat``: the current ones, then the archived ones.

    The archived fixtures (the whole world, from the first season of the save) are
    in compressed blocks inside the file and use the same layout. The blocks are
    pieces of one stream (most are 131 072 bytes), so a fixture can start in one
    block and end in the next: they are joined before parsing. In one 2063 save:
    60 704 current and 994 981 archived fixtures, with no fixture in both.
    ``Fixture.position`` is a position in the joined archive for archived fixtures.
    """
    yield from iter_fixtures(fix_man)
    yield from iter_fixtures(b"".join(iter_inner_blocks(fix_man)))


# -- league tables -------------------------------------------------------------------------
# A row starts: zero byte, club number (u32), zero byte, six 0xff bytes, 4 bytes
# that are the same in every row of one file (``a0 26 cb 04`` in the Premier Division).
_TABLE_ROW = re.compile(rb"\x00(.{4})\x00\xff{6}(.{4})", re.DOTALL)
_ROW_POSITION = 5
_ROW_PLAYED = 21
_ROW_POINTS = -67  # u16, from the end of the row
_ROW_CLEAN_SHEETS = -65
_ROW_GOALS_FOR = -64
_ROW_GOALS_AGAINST = -63
_ROW_WON = -48
_ROW_LOST = -47
_ROW_MIN, _ROW_MAX = 380, 460
_MAX_TABLE_ROWS = 60


@dataclass(frozen=True, slots=True)
class TableRow:
    club_id: int  # the save's own number, not the FM uid
    position: int  # 1-based
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int
    clean_sheets: int

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def _last_row_end(buffer: bytes, start: int) -> int | None:
    """The table ends with ``ff`` and a ``u32`` count; every row ends with a zero byte."""
    for end in range(start + _ROW_MIN, min(start + _ROW_MAX, len(buffer) - 5)):
        if buffer[end] == 0xFF and buffer[end - 1] == 0 and 0 < struct.unpack_from("<I", buffer, end + 1)[0] < _MAX_CLUB:
            return end
    return None


def _table_row(club_id: int, row: bytes) -> TableRow:
    played, won, lost = row[_ROW_PLAYED], row[len(row) + _ROW_WON], row[len(row) + _ROW_LOST]
    return TableRow(
        club_id=club_id,
        position=row[_ROW_POSITION] + 1,
        played=played,
        won=won,
        drawn=played - won - lost,
        lost=lost,
        goals_for=row[len(row) + _ROW_GOALS_FOR],
        goals_against=row[len(row) + _ROW_GOALS_AGAINST],
        points=struct.unpack_from("<H", row, len(row) + _ROW_POINTS)[0],
        clean_sheets=row[len(row) + _ROW_CLEAN_SHEETS],
    )


def _find_table_rows(buffer: bytes) -> list[re.Match]:
    """The first run of rows that follows a ``u32`` row count, each row of a plausible length.

    The count anchors the table: the same row pattern also matches other data
    (runs of ``ff`` bytes), sometimes more often than the real rows.
    """
    for first in _TABLE_ROW.finditer(buffer):
        if first.start() < 4:
            continue
        count = struct.unpack_from("<I", buffer, first.start() - 4)[0]
        if not 2 <= count <= _MAX_TABLE_ROWS:
            continue
        rows = [first]
        while len(rows) < count:
            following = _TABLE_ROW.search(buffer, rows[-1].end() + _ROW_MIN - 1, rows[-1].end() + _ROW_MAX + 16)
            if following is None or following.group(2) != first.group(2):
                break
            rows.append(following)
        if len(rows) == count:
            return rows
    return []


def read_league_table(buffer: bytes) -> list[TableRow]:
    """The current table of a league competition file (``comp_<fm uid>.dat``), top first.

    Returns ``[]`` when the file has no table (no row count followed by that many
    rows). Raises ``FmSaveError`` when the rows do not form a table (positions not
    exactly 1..n, more wins and losses than games, more points than 3 per game):
    then the file uses a layout that is not decoded yet. Points are not checked against 3 x won + drawn, because the
    game can deduct points.
    """
    found = _find_table_rows(buffer)
    if not found:
        return []
    rows = []
    for index, match in enumerate(found):
        start = match.end()
        if index + 1 < len(found):
            end = found[index + 1].start() + 1  # the zero byte that ends this row
        else:
            end = _last_row_end(buffer, start)
            if end is None:
                raise FmSaveError("Cannot find the end of the last table row.")
        if not _ROW_MIN <= end - start <= _ROW_MAX:
            raise FmSaveError(f"A table row of {end - start} bytes is outside the known sizes.")
        row = _table_row(struct.unpack("<I", match.group(1))[0], buffer[start:end])
        if row.drawn < 0 or row.points > 3 * row.played:
            raise FmSaveError(f"The table row for club {row.club_id} does not add up.")
        rows.append(row)
    rows.sort(key=lambda r: r.position)
    if [r.position for r in rows] != list(range(1, len(rows) + 1)):
        raise FmSaveError("The rows do not form a table; this competition's layout is not decoded.")
    return rows


# -- clubs and teams ------------------------------------------------------------------------
# A club record starts: index (u32), a number (u32) written twice, a zero byte, 26 more
# bytes, then the full name and the short name. The second number is not the FM uid.
# Nation index + 1 (u32): at 13 the country whose leagues the club plays in, at 21 the club's
# own country (Cardiff City: 13 England, 21 Wales; Toronto FC: 13 United States, 21 Canada).
_CLUB_LEAGUE_NATION, _CLUB_NATION = 13, 21
_CLUB_HEADER = re.compile(rb"(.{4})(.{4})\2\x00.{4}\xff{4}.{18}(?=[\x01-\x50]\x00\x00\x00)", re.DOTALL)
# A team record starts: ff ff ff 00, 3 bytes, the age limit, ff, a type byte, club index + 1.
# Then the team's own name and short name (usually empty), the team number and its FM uid.
_TEAM_HEADER = re.compile(rb"(?=\xff\xff\xff\x00.{3}(.)\xff.(.{4}))", re.DOTALL)
_TEAM_HEADER_SIZE = 14
_NATIONAL_FLAG = 5  # 1 for a national team, in the 3 bytes after ff ff ff 00
NO_AGE_LIMIT = (100, 255)


@dataclass(frozen=True, slots=True)
class Club:
    index: int  # the save's own number
    name: str
    short_name: str
    nation_index: int | None = None  # the club's own country (Cardiff: Wales), a nation index
    league_nation_index: int | None = None  # the country whose leagues it plays in (Cardiff: England)


@dataclass(frozen=True, slots=True)
class Team:
    number: int  # the number fixtures and tables use
    club_index: int  # the nation index for a national team
    fm_uid: int  # for a first team, the club's FM unique id; for a senior national team, the nation's
    age_limit: int | None  # 21 for an under-21 team; None for senior teams
    own_name: str  # empty unless the team has a name of its own ("Borussia Dortmund U19")
    own_short_name: str
    national: bool = False
    reputation: int | None = None  # 0-10 000, as the editor shows it (Chelsea 9983)
    stadium: int | None = None  # the stadium's number in the save (read_stadiums gives its FM uid)


def _read_string(buffer: bytes, position: int, limit: int = 100) -> tuple[str, int] | None:
    """A ``u32`` length and UTF-8 text; returns the text and the position after it."""
    if position + 4 > len(buffer):
        return None
    length = struct.unpack_from("<I", buffer, position)[0]
    if length > limit or position + 4 + length > len(buffer):
        return None
    try:
        text = buffer[position + 4 : position + 4 + length].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return (text, position + 4 + length) if text.isprintable() else None


def read_clubs(buffer: bytes) -> dict[int, Club]:
    """Every club in ``game_db.dat``, by the save's own club index."""
    clubs: dict[int, Club] = {}
    for match in _CLUB_HEADER.finditer(buffer):
        name = _read_string(buffer, match.end(), limit=0x50)
        if name is None or not name[0]:
            continue
        short = _read_string(buffer, name[1], limit=0x50)
        if short is None:
            continue
        index = struct.unpack("<I", match.group(1))[0]
        start = match.start()
        league_nation, nation = (struct.unpack_from("<I", buffer, start + offset)[0] - 1 for offset in (_CLUB_LEAGUE_NATION, _CLUB_NATION))
        clubs.setdefault(index, Club(index, name[0], short[0], nation if 0 <= nation < 1000 else None,
                                     league_nation if 0 <= league_nation < 1000 else None))
    return clubs


# -- a club's relationships -------------------------------------------------------------------------
# Each club record holds one list (a count byte, then 16-byte entries): its rival clubs and its
# favourite and disliked people. An entry: the subject (u32: a person number, or a club's index + 1),
# 4 zero bytes, the reason (u16), the kind (u8) and the relation (u8), the level (0-100), 4f when
# permanent, 01 when the club may name its stadium after the person, ff. Found with the editor:
# Chelsea's Lampard 100 permanent and Billy Laing 100 not, disliked Poyet 25, rivals Millwall 100
# ("Local", reason 1) to Brentford 40; Arsenal's and Liverpool's lists agree too. Other kinds (4,29;
# 1,55; 3,68) are not decoded.
RIVAL, FAVOURITE, DISLIKED = "rival", "favourite", "disliked"
_RELATION_KINDS = {(1, 5): RIVAL, (3, 1): FAVOURITE, (3, 2): DISLIKED}
# The reason codes, in the order of the editor's lists (3 is probably "Historic": a long rivalry that
# is neither local nor between rivals for the same honours).
RIVAL_REASONS = {0: "", 1: "Local", 2: "Competitive", 3: "Historic"}
PERSON_REASONS = {0: "", 1: "Player", 2: "Manager", 3: "Backroom Staff", 4: "Chairperson",
                  5: "Player and Manager", 6: "Owner", 7: "Club Staff", 8: "Founder"}
_RELATION_ENTRY = re.compile(rb"(?=[\s\S]{4}\x00\x00\x00\x00[\s\S]{2}[\x01\x03\x04][\s\S][\x00-\x64][\s\S]{2}\xff)")
_RELATION_SIZE = 16
_PERMANENT = 0x4F
_CLUB_BLOCK_GAP = 2_000_000  # the club records are one block; a header further on is a chance match


@dataclass(frozen=True, slots=True)
class ClubRelation:
    relation: str  # RIVAL, FAVOURITE or DISLIKED
    subject: int  # a person number; for a rival, the club's index
    reason: int  # the game's reason code (a person's: 1 player, 2 manager; a rival's: 1 local)
    level: int  # 0-100
    permanent: bool
    names_stadium: bool  # the club may name its stadium after the person


def _club_records(buffer: bytes) -> list[tuple[int, int]]:
    """Each club record's start and the club's index, in file order, within the clubs' block."""
    found = []
    for match in _CLUB_HEADER.finditer(buffer):
        name = _read_string(buffer, match.end(), limit=0x50)
        if name is not None and name[0]:
            found.append((match.start(), struct.unpack("<I", match.group(1))[0]))
    found.sort()
    block = found[:1]
    for record in found[1:]:
        if record[0] - block[-1][0] > _CLUB_BLOCK_GAP:
            break
        block.append(record)
    return block


def read_club_relations(buffer: bytes, records: list[tuple[int, int]] | None = None) -> dict[int, list[ClubRelation]]:
    """Every club's rivals and favourite and disliked people, by club index (``records``: _club_records, when read already)."""
    records = _club_records(buffer) if records is None else records
    if not records:
        return {}
    starts = [start for start, _ in records]
    block_end = records[-1][0] + 200_000
    entries = {m.start() for m in _RELATION_ENTRY.finditer(buffer, records[0][0], block_end)}
    found: dict[int, list[ClubRelation]] = {}
    for first in sorted(entries):
        if first - _RELATION_SIZE in entries:
            continue
        count = 0
        while first + _RELATION_SIZE * count in entries:
            count += 1
        if buffer[first - 1] != count:  # a list is its count byte and that many entries
            continue
        record = bisect.bisect_right(starts, first) - 1
        if record < 0:
            continue
        index = records[record][1]
        for position in range(first, first + _RELATION_SIZE * count, _RELATION_SIZE):
            kind = _RELATION_KINDS.get((buffer[position + 10], buffer[position + 11]))
            if kind is None:
                continue
            subject = struct.unpack_from("<I", buffer, position)[0]
            found.setdefault(index, []).append(ClubRelation(
                kind, subject - 1 if kind == RIVAL else subject, struct.unpack_from("<H", buffer, position + 8)[0],
                buffer[position + 12], buffer[position + 13] == _PERMANENT, buffer[position + 14] == 1))
    return found


# -- club finances ----------------------------------------------------------------------------------
# All sums are in pounds, as the game keeps them (it shows euros at about 1.16 to the pound).
INCOME_TYPES = {1: "Kit Sponsor", 2: "Government/Council Grant", 3: "Stadium Sponsor", 4: "General Sponsor", 6: "Other Income",
                7: "Club Membership", 8: "Kit Sponsor (Secondary)", 9: "Kit Sponsor (Other)", 10: "Parachute Payment",
                13: "Training Kit", 14: "Youth Team", 16: "Continental Competition", 17: "Equity Injection"}
# The editor's list of debt sources, from 1; 16 shows as an empty source in the editor.
DEBT_SOURCES = {1: "Bank", 2: "Chairperson", 3: "CVA", 4: "PFA", 5: "State", 6: "Supporters Trust",
                7: "Gift Repayable Once Profitable", 8: "Gift Repayable On Departure", 9: "Gift Non-Repayable",
                10: "Loan Repayable On Promotion To Top Division", 11: "Loan Repayable When Established In Top Division",
                12: "Director", 13: "PIK Loan", 14: "Lump Sum Paid On Promotion To Top Division", 15: "Miscellaneous"}


@dataclass(frozen=True, slots=True)
class Income:
    kind: int  # INCOME_TYPES
    start: date
    end: date
    total: int  # pounds over the whole deal
    yearly: int  # paid at the end of each season
    renews: bool


@dataclass(frozen=True, slots=True)
class Debt:
    source: int  # DEBT_SOURCES
    amount: int  # the original debt
    monthly_repayment: int
    monthly_interest: int
    conditional_repayment: int
    start: date
    end: date
    in_fpp: bool  # counted in Financial Fair Play


@dataclass(frozen=True, slots=True)
class ClubFinances:
    bank: int | None
    transfer_budget: int | None
    wage_budget: int | None
    incomes: tuple[Income, ...] = ()
    debts: tuple[Debt, ...] = ()


# In a club record, after its names: 01|02, u16, u32, a flag, a count n and n u32s, then 01, a u32 and the transfer
# budget; the wage budget is 62 bytes on. The bank balance (i32) stands before four bytes of flags and three
# placeholder dates (1 January 1900), and the income list's count byte 45 bytes after it.
_BUDGETS = re.compile(rb"[\x01\x02][\s\S]{6}[\x00-\xff]([\x00-\x40])", re.DOTALL)
_WAGE_AFTER_TRANSFER = 62
_BANK = re.compile(rb"\xff\x00([\s\S]{4})[\s\S]{4}(?:\x01\x00\x6c\x07){3}")  # 4 bytes of flags after it (Blackburn: 00 00 01 00)
_INCOME_AFTER_BANK = 45
_INCOME_SIZE = 24
_DEBT_SIZE = 28
_DEBT_ENTRY = re.compile(rb"(?=\x01[\s\S]{19}[\x07\x08][\x01-\x20][\x00\x01][\x00\x01][\s\S]{3}[\x07\x08])")
# A club's block in the area of debt lists starts with the club's index + 1, two small bytes, 00 and a u16 count
# of u32s; its debt list (u32 count, then the debts) follows within a few thousand bytes.
_DEBT_OWNER_REACH = 8000


def _game_date(buffer: bytes, position: int) -> date | None:
    """u16 whose low 9 bits are the day of the year, u16 year (as in fixtures)."""
    day, year = struct.unpack_from("<HH", buffer, position)
    return _date_from_day_of_year(day & 0x1FF, year)


def _budgets(buffer: bytes, start: int, names_end: int) -> tuple[int | None, int | None]:
    """The transfer and wage budgets: the first form after the club's names that fits."""
    for at in range(names_end, names_end + 24):  # every start: a wrong early match must not hide the right one
        match = _BUDGETS.match(buffer, at)
        if match is None:
            continue
        count = match.group(1)[0]
        after = match.end() + 4 * count
        if buffer[after] == 1 and after + 9 + _WAGE_AFTER_TRANSFER <= len(buffer):
            transfer = struct.unpack_from("<i", buffer, after + 5)[0]
            wage = struct.unpack_from("<i", buffer, after + 5 + _WAGE_AFTER_TRANSFER)[0]
            return transfer, wage
    return None, None


def _incomes(buffer: bytes, bank_at: int) -> tuple[Income, ...]:
    count_at = bank_at + _INCOME_AFTER_BANK
    found = []
    for position in range(count_at + 1, count_at + 1 + _INCOME_SIZE * buffer[count_at], _INCOME_SIZE):
        start, end = _game_date(buffer, position + 2), _game_date(buffer, position + 6)
        if buffer[position] != 1 or start is None or end is None:
            return ()  # not an income list after all
        total, yearly = struct.unpack_from("<I", buffer, position + 11)[0], struct.unpack_from("<I", buffer, position + 20)[0]
        found.append(Income(buffer[position + 1], start, end, total, yearly, buffer[position + 10] == 1))
    return tuple(found)


def read_club_finances(buffer: bytes, clubs: dict[int, Club], records: list[tuple[int, int]] | None = None) -> dict[int, ClubFinances]:
    """Every club's bank balance, budgets, income streams and debts, by club index (``records``: _club_records, when read already)."""
    records = _club_records(buffer) if records is None else records
    debts = _read_debts(buffer)
    found: dict[int, ClubFinances] = {}
    ends = [start for start, _ in records[1:]] + [records[-1][0] + 200_000] if records else []
    for (start, index), end in zip(records, ends):
        club = clubs.get(index)
        if club is None:
            continue
        short = club.short_name.encode()
        names_end = buffer.find(struct.pack("<I", len(short)) + short, start + 20, start + 400)  # with its length: not inside the full name
        transfer, wage = _budgets(buffer, start, names_end + 4 + len(short)) if names_end >= 0 else (None, None)
        bank_match = _BANK.search(buffer, start, end)
        bank = struct.unpack("<i", bank_match.group(1))[0] if bank_match else None
        incomes = _incomes(buffer, bank_match.start() + 2) if bank_match else ()
        found[index] = ClubFinances(bank, transfer, wage, incomes, debts.get(index, ()))
    return found


def _read_debts(buffer: bytes) -> dict[int, tuple[Debt, ...]]:
    """Every club's debts. A debt list is a u32 count and its 28-byte debts; its club is the nearest club
    block header before it and after the previous list, the clubs coming in rising order."""
    entries = {m.start() for m in _DEBT_ENTRY.finditer(buffer)}
    lists = []
    for first in sorted(entries):
        if first - _DEBT_SIZE in entries:
            continue
        length = 1
        while first + _DEBT_SIZE * length in entries:
            length += 1
        if struct.unpack_from("<I", buffer, first - 4)[0] == length:  # a list is its count and that many debts
            lists.append((first, length))
    found: dict[int, tuple[Debt, ...]] = {}
    previous_end, previous_club = 0, -1
    for first, count in lists:
        candidates = []
        for header in range(max(previous_end, first - _DEBT_OWNER_REACH), first - 13):
            club = struct.unpack_from("<I", buffer, header)[0] - 1
            if 0 <= club < 100_000 and buffer[header + 6] == 0 and 1 <= buffer[header + 4] <= 0x20 and 1 <= buffer[header + 5] <= 0x20:
                n = struct.unpack_from("<H", buffer, header + 7)[0]
                if 0 < n < 300 and header + 9 + 4 * n <= first - 4:
                    candidates.append(club)
        if not candidates:
            continue
        rising = [club for club in candidates if club > previous_club]
        club = (rising or candidates)[-1]
        debts = []
        for position in range(first, first + _DEBT_SIZE * count, _DEBT_SIZE):
            amount, repayment, interest, conditional = struct.unpack_from("<IIII", buffer, position + 1)
            debts.append(Debt(buffer[position + 21], amount, repayment, interest, conditional,
                              _game_date(buffer, position + 24), _game_date(buffer, position + 17), buffer[position + 22] == 1))
        found[club] = tuple(debts)
        previous_end, previous_club = first + _DEBT_SIZE * count, club
    return found


def read_teams(buffer: bytes, clubs: dict[int, Club]) -> dict[int, Team]:
    """Every national team, and every team that belongs to a known club, by team number."""
    teams: dict[int, Team] = {}
    for match in _TEAM_HEADER.finditer(buffer):
        club_index = struct.unpack("<I", match.group(2))[0] - 1
        national = buffer[match.start() + _NATIONAL_FLAG] == 1
        if not national and club_index not in clubs:
            continue
        own = _read_string(buffer, match.start() + _TEAM_HEADER_SIZE)
        if own is None:
            continue
        own_short = _read_string(buffer, own[1] + 1)  # each name is followed by one byte
        if own_short is None or own_short[1] + 9 > len(buffer):
            continue
        number, fm_uid = struct.unpack_from("<II", buffer, own_short[1] + 1)
        if not 0 <= number < 1_000_000 or number in teams:  # the first team, number 0, is Algeria
            continue
        age = match.group(1)[0]
        reputation, stadium = _team_reputation_and_stadium(buffer, match.start())
        teams[number] = Team(
            number, club_index, fm_uid, None if age in NO_AGE_LIMIT else age, own[0], own_short[0], national,
            reputation, stadium,
        )
    return teams


# A team's data come before its header and end: reputation (u16), five u32, a byte,
# ff ff ff ff, the squad size (u16), the squad (u32 each), the captain and the vice-captain
# (u32), then 6 bytes. Found with the editor (Boreham Wood 4865 -> 8776, Gabon 3391 -> 5819).
_TEAM_SQUAD_COUNT = 16  # from the header back to the squad size, for an empty squad
_TEAM_REPUTATION = 31  # from the squad size back to the reputation
_TEAM_STADIUM = 14  # from the reputation to the home stadium's number (u32); Arsenal 7727 (Emirates)
_MAX_SQUAD = 200
MAX_REPUTATION = 10_000


def _team_reputation_and_stadium(buffer: bytes, header: int) -> tuple[int | None, int | None]:
    for size in range(_MAX_SQUAD):
        count = header - _TEAM_SQUAD_COUNT - 4 * size
        if count < _TEAM_REPUTATION:
            return None, None
        if struct.unpack_from("<H", buffer, count)[0] == size and buffer[count - 4 : count] == b"\xff\xff\xff\xff":
            at = count - _TEAM_REPUTATION
            value = struct.unpack_from("<H", buffer, at)[0]
            stadium = struct.unpack_from("<I", buffer, at + _TEAM_STADIUM)[0]
            return (value if value <= MAX_REPUTATION else None), (stadium if stadium != _NONE_U32 else None)
    return None, None


_NONE_U32 = 0xFFFFFFFF
# A stadium record: 01 00 6c 07, latitude and longitude (float32), the stadium's number, its FM uid
# written twice (the uid the game's stadium packs use: Emirates Stadium 5114336 at 51.56, -0.11).
_STADIUM_RECORD = re.compile(rb"(?=\x01\x00\x6c\x07(.{4})(.{4})(.{4})(.{4})\4)", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Stadium:
    uid: int
    latitude: float
    longitude: float


def read_stadiums(game_db: bytes) -> dict[int, Stadium]:
    """Every stadium with a place on the map, by its number in the save."""
    found: dict[int, Stadium] = {}
    for match in _STADIUM_RECORD.finditer(game_db):
        latitude, longitude = struct.unpack("<ff", match.group(1) + match.group(2))
        number, uid = struct.unpack("<II", match.group(3) + match.group(4))
        if uid != _NONE_U32 and -90 <= latitude <= 90 and -180 <= longitude <= 180 and max(abs(latitude), abs(longitude)) > 0.01:
            found.setdefault(number, Stadium(uid, latitude, longitude))
    return found


def team_name(team: Team, clubs: dict[int, Club], nations: dict[int, str] | None = None) -> str:
    """The team's own name, or its club's (or nation's) name with the age group (``Arsenal U21``).

    ``nations`` maps nation indexes to names (``national_team_nations``); without it a
    national team is called ``nation <index>``.
    """
    if team.own_name:
        return team.own_name
    if team.national:
        name = (nations or {}).get(team.club_index, f"nation {team.club_index}")
    else:
        name = clubs[team.club_index].name
    return f"{name} U{team.age_limit}" if team.age_limit else name


@dataclass(frozen=True, slots=True)
class NationName:
    fm_uid: int
    name: str  # "England"
    short_name: str
    abbreviation: str  # "ENG"
    adjective: str  # "English"


def read_nation_names(lang_db: str | Path) -> dict[int, NationName]:
    """Nation names by FM uid, from section 0 of the English block of the game's ``lang_db.dat``.

    Per nation: the FM uid, the name and short name (each followed by one byte), the
    abbreviation, and the adjective (followed by one byte).
    """
    with open(lang_db, "rb") as handle:
        raw = handle.read()
    data = _decompress_prefix(raw, _DB_SECTION, occurrences=2)
    section = data.find(_DB_SECTION)
    if section < 0:
        raise FmSaveError(f"{lang_db} does not have the expected sections.")
    count = struct.unpack_from("<I", data, section + 8)[0]
    position = section + _DB_SECTION_HEADER
    nations: dict[int, NationName] = {}
    for _ in range(count):
        fm_uid = struct.unpack_from("<I", data, position)[0]
        position += 4
        texts = []
        for trailing in (1, 1, 0, 1):
            entry = _read_string(data, position, limit=200)
            if entry is None:
                raise FmSaveError(f"Unreadable nation name near byte {position} of {lang_db}.")
            texts.append(entry[0])
            position = entry[1] + trailing
        nations[fm_uid] = NationName(fm_uid, *texts)
    return nations


def national_team_nations(teams: dict[int, Team], nations: dict[int, NationName]) -> dict[int, NationName]:
    """Map nation indexes to nations: a senior national team's FM uid is its nation's uid."""
    found: dict[int, NationName] = {}
    for team in teams.values():
        if team.national and team.age_limit is None and team.fm_uid in nations:
            found.setdefault(team.club_index, nations[team.fm_uid])
    return found


# -- competitions -----------------------------------------------------------------------------
# A row of the stage table in game_db.dat: index (u32), the stage number (index + 1) written
# twice, a zero byte, the competition's history key (u32), then 16 more bytes.
_STAGE_ROW = re.compile(rb"(?=(.{4})(.{4})\2\x00(.{4}))", re.DOTALL)
_STAGE_NEIGHBOUR = 100  # rows are 33 bytes apart
_MAX_COMPETITION_KEY = 100_000


def read_stages(game_db: bytes, keys: dict[int, int]) -> dict[int, int]:
    """Map fixture competition numbers (stages) to competition FM uids.

    ``keys`` comes from ``read_competition_keys`` (give it the game database's order,
    or about 9% of the stages stay unnamed). Only rows that lie next to another row
    are kept, so that a lone match elsewhere in the file is not taken for one.
    """
    found = []
    for match in _STAGE_ROW.finditer(game_db):
        index, number, key = struct.unpack("<III", match.group(1) + match.group(2) + match.group(3))
        if number == index + 1 and key < _MAX_COMPETITION_KEY:
            found.append((match.start(), number, key))
    rows = []
    for position, (start, number, key) in enumerate(found):
        neighbours = found[max(0, position - 1) : position] + found[position + 1 : position + 2]
        if key in keys and any(abs(other - start) <= _STAGE_NEIGHBOUR for other, _, _ in neighbours):
            rows.append((start, number, key))
    # The save holds a few short lists shaped like stage rows before the stage table itself (33 rows numbered 1..33 at
    # a third of the file: '2. Division, League One, Premier Division...'). Their numbers repeat the table's, so the
    # first row found named the East Asian Cup (13) Sky Bet League One. Of the rows with one number, the one nearest
    # the table's middle (the median position of all rows) is the table's.
    if not rows:
        return {}
    middle = sorted(start for start, _, _ in rows)[len(rows) // 2]
    stages: dict[int, int] = {}
    nearest: dict[int, int] = {}
    for start, number, key in rows:
        if number not in stages or abs(start - middle) < nearest[number]:
            stages[number], nearest[number] = keys[key], abs(start - middle)
    return stages


# -- the game's own database (names that saves do not hold) ------------------------------------
GAME_DATABASE_ROOT = (
    Path.home() / "Library/Application Support/Steam/steamapps/common/Football Manager 2024/data/database/db"
)
_DB_SECTION = b"\x03\x01tad."
_DB_SECTION_HEADER = 12  # marker, 2 bytes, u32 count


@dataclass(frozen=True, slots=True)
class CompetitionName:
    fm_uid: int
    name: str  # "English Premier Division"
    short_name: str  # "Premier Division"
    abbreviation: str  # "PRM"


def find_language_database(root: Path = GAME_DATABASE_ROOT) -> Path | None:
    """The newest ``lang_db.dat`` under the game's database folder (macOS Steam path by default)."""
    found = sorted(root.glob("*/*_fm/lang_db.dat")) if root.is_dir() else []
    return found[-1] if found else None


def _decompress_prefix(raw: bytes, until: bytes, occurrences: int) -> bytes:
    """Decompress zstd frames from the start until ``until`` has appeared ``occurrences`` times."""
    zstd = _zstd()
    parts: list[bytes] = []
    seen = 0
    position = raw.find(ZSTD_MAGIC)
    while position >= 0:
        decompressor = zstd.ZstdDecompressor().decompressobj()
        try:
            chunk = decompressor.decompress(raw[position:])
        except zstd.ZstdError as exc:
            raise FmSaveError(f"The game database is damaged: {exc}") from exc
        parts.append(chunk)
        seen += chunk.count(until)
        if seen >= occurrences:
            break
        position = raw.find(ZSTD_MAGIC, len(raw) - len(decompressor.unused_data))
    return b"".join(parts)


def read_competition_names(lang_db: str | Path) -> dict[int, CompetitionName]:
    """Competition names by FM uid, from the game's ``lang_db.dat`` (English block).

    The file is sections that start with ``03 01 'tad.'``, two bytes and a ``u32``
    count, repeated once per language. In the first (English) block, section 1 is
    nations and section 2 is competitions: per competition the FM uid, then the
    name, short name and abbreviation, each followed by one byte.
    """
    with open(lang_db, "rb") as handle:
        raw = handle.read()
    data = _decompress_prefix(raw, _DB_SECTION, occurrences=3)
    first = data.find(_DB_SECTION)
    section = data.find(_DB_SECTION, first + 1)
    if first < 0 or section < 0:
        raise FmSaveError(f"{lang_db} does not have the expected sections.")
    count = struct.unpack_from("<I", data, section + 8)[0]
    position = section + _DB_SECTION_HEADER
    names: dict[int, CompetitionName] = {}
    for _ in range(count):
        fm_uid = struct.unpack_from("<I", data, position)[0]
        texts = []
        position += 4
        for _ in range(3):
            entry = _read_string(data, position, limit=200)
            if entry is None:
                raise FmSaveError(f"Unreadable competition name near byte {position} of {lang_db}.")
            texts.append(entry[0])
            position = entry[1] + 1
        names[fm_uid] = CompetitionName(fm_uid, *texts)
    return names


# -- competition history (winners) ----------------------------------------------------------------
_HISTORY_HEADER = 8
_HISTORY_ROW = 55
_HISTORY_SLOTS = 9
_FIRST_HISTORY_YEAR = 1850
NO_TEAM = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class CompetitionSeason:
    competition_key: int  # position of the competition in the game database's list
    year: int  # the year it was won: the end of a season, or the single year of a one-off final
    season_start: int | None  # the first year of a season, when the row records one
    slots: tuple[int | None, ...]  # team numbers; slot 0 is the winner, slot 1 the runner-up
    decided_on: date | None  # date of the final or of the last match
    season: tuple[int, int] = (0, 0)  # the season as the game shows it: (2061, 2062) or (2057, 2057)

    @property
    def season_label(self) -> str:
        """The season as the game shows it. Rows from before the save began keep a placeholder season
        (2000, 1999/00 or 2000/01 for a World Cup of 1930): then the label is built from the year."""
        first, last = self.season
        if not first:
            return str(self.year)
        if self.year not in (first, last):
            return str(self.year) if first == last else f"{self.year - 1}/{str(self.year)[-2:]}"
        return str(first) if first == last else f"{first}/{str(last)[-2:]}"

    @property
    def winner(self) -> int | None:
        return self.slots[0]

    @property
    def runner_up(self) -> int | None:
        return self.slots[1]


def read_competition_history(buffer: bytes) -> list[CompetitionSeason]:
    """Every season row of ``comp_history_dt.cmt``: who won each competition, by year.

    Rows are 55 bytes after an 8-byte header: competition key (``u32``), year
    (``u16``), season start (``u16``, 0 in old rows), nine team slots (``u32``,
    ``ffffffff`` when empty), one byte, a type (``u16``: 2 for a one-year edition, 4
    for a season), the season as the game shows it (two ``u16`` years) and the date
    of the final. ``read_competition_keys`` maps the key to the competition.
    """
    return [_history_row(buffer, position) for position in range(_HISTORY_HEADER, len(buffer) - _HISTORY_ROW + 1, _HISTORY_ROW)]


def _history_row(buffer: bytes, position: int) -> CompetitionSeason:
    key, year, start = struct.unpack_from("<IHH", buffer, position)
    slots = struct.unpack_from(f"<{_HISTORY_SLOTS}I", buffer, position + 8)
    return CompetitionSeason(
        competition_key=key,
        year=year,
        season_start=start or None,
        slots=tuple(None if team == NO_TEAM else team for team in slots),
        decided_on=decode_match_date(buffer[position + 51 : position + 55]),
        season=struct.unpack_from("<HH", buffer, position + 47),
    )


# Inside each competition record in game_db.dat: 01 03, then a history row (key, year, start,
# nine team slots, one byte, type 02 or 04, 00, season years, date of the final). The byte
# before the type is not always 00 (03 for the 2062 World Cup; 0-7 in comp_history_dt.cmt).
_LATEST_ROW = re.compile(rb"(?=\x01\x03(.{8}.{36}.[\x02\x04]\x00.{8}))", re.DOTALL)


def read_latest_editions(game_db: bytes, keys: dict[int, int]) -> dict[int, CompetitionSeason]:
    """The latest finished edition of every competition, by FM uid.

    ``comp_history_dt.cmt`` does not have it yet: the game keeps it as a history row
    inside the competition's record in ``game_db.dat``. ``keys`` comes from
    ``read_competition_keys``; rows with another key are not competition records.
    """
    latest: dict[int, CompetitionSeason] = {}
    for match in _LATEST_ROW.finditer(game_db):
        row = _history_row(game_db, match.start(1))
        uid = keys.get(row.competition_key)
        if uid is not None and uid not in latest and _FIRST_HISTORY_YEAR <= row.year <= _LAST_YEAR:
            latest[uid] = row
    return latest


# The holders list in comp_<uid>.dat: a count (u32), then count x (00, team u32), then this.
_HOLDERS_END = re.compile(rb"\x01\x0a\x00\x00\x00\x01")
_MAX_HOLDERS = 600


def read_holders(buffer: bytes) -> list[int]:
    """The final order of a competition's latest finished edition (``comp_<uid>.dat``).

    Team numbers, winner first (a league's whole final table; a cup's winner, runner-up,
    then the teams that went out earlier). When a final stage follows a league stage,
    the list is the league-stage table; ``read_latest_editions`` has the real winner. ``comp_history_dt.cmt`` has every edition
    except this one. Only the competitions that the game plays in detail keep this
    list; the others give ``[]``.
    """
    for match in _HOLDERS_END.finditer(buffer):
        end = match.start()
        for count in range(1, _MAX_HOLDERS):
            start = end - 4 - 5 * count
            if start < 0:
                break
            if struct.unpack_from("<I", buffer, start)[0] == count and not any(
                buffer[start + 4 + 5 * index] for index in range(count)
            ):
                return [struct.unpack_from("<I", buffer, start + 5 + 5 * index)[0] for index in range(count)]
    return []


def competition_uids_in_order(lang_db: str | Path) -> list[int]:
    """Competition FM uids in the game database's order.

    A save's history keys follow this order closely but not exactly (the save's
    list drops a few competitions and adds new ones), so use it only to fill gaps
    in ``read_competition_keys``.
    """
    return list(read_competition_names(lang_db))


# A competition record in game_db.dat: two dates (their year bytes end in 07 or 08), 00 ?? ff,
# the history key (u32), the FM uid written twice, then 00 00 00 01 (a long career's save) or
# 00 ff ff 01 (a new game's).
_COMPETITION_RECORD = re.compile(rb"(?=[\x07\x08]...[\x07\x08]\x00.\xff(.{4})(.{4})\2\x00(?:\x00\x00|\xff\xff)\x01)", re.DOTALL)


def read_competition_keys(game_db: bytes, uids_in_order: list[int] | None = None) -> dict[int, int]:
    """Map history keys to competition FM uids, from the competition records in ``game_db.dat``.

    Some records use another layout and are not found. When ``uids_in_order``
    (``competition_uids_in_order``) is given, a gap between two found keys is
    filled from the game database's order, but only where that order has exactly
    as many competitions between the two as the gap needs.
    """
    keys: dict[int, int] = {}
    for match in _COMPETITION_RECORD.finditer(game_db):
        key, uid = struct.unpack("<II", match.group(1) + match.group(2))
        keys.setdefault(key, uid)
    return _fill_key_gaps(keys, uids_in_order)


# A competition's data come after the record of the competition before it (keys n and n + 1):
# its reputation (u16, 0-200) is 84 bytes after that record's ff. Found with the editor
# (English Premier Division 184).
_COMPETITION_MARKER = re.compile(rb"(?=\xff(.{4})(.{4})\2\x00(?:\x00\x00|\xff\xff)\x01)", re.DOTALL)
_COMPETITION_REPUTATION = 84
MAX_COMPETITION_REPUTATION = 200


def read_competition_reputations(game_db: bytes) -> dict[int, int]:
    """Each competition's reputation (0-200: the World Cup and the Champions League 200), by FM uid."""
    records = []
    for match in _COMPETITION_MARKER.finditer(game_db):
        key, uid = struct.unpack("<II", match.group(1) + match.group(2))
        if key < _MAX_COMPETITION_KEY:
            records.append((match.start(), key, uid))
    found: dict[int, int] = {}
    for (position, key, _), (_, next_key, next_uid) in zip(records, records[1:]):
        if next_key == key + 1 and position + _COMPETITION_REPUTATION + 2 <= len(game_db):
            value = struct.unpack_from("<H", game_db, position + _COMPETITION_REPUTATION)[0]
            if value <= MAX_COMPETITION_REPUTATION:
                found.setdefault(next_uid, value)
    return found


def _fill_key_gaps(keys: dict[int, int], uids_in_order: list[int] | None) -> dict[int, int]:
    """Fill gaps between known keys from the game database's order where the spacing matches."""
    if not uids_in_order:
        return keys
    position = {uid: index for index, uid in enumerate(uids_in_order)}
    known = sorted((key, position[uid]) for key, uid in keys.items() if uid in position)
    if known and known[0][0] == known[0][1]:
        known.insert(0, (-1, -1))  # the list starts where the game database starts
    for (key1, index1), (key2, index2) in zip(known, known[1:]):
        if key2 - key1 > 1 and index2 - index1 == key2 - key1:
            for key in range(key1 + 1, key2):
                keys.setdefault(key, uids_in_order[index1 + key - key1])
    return keys


# -- people ---------------------------------------------------------------------------------------
# A person row: the save's person number (u32), the FM uid written twice, then a type byte
# (0x10 or 0x13) and 12 to 22 more bytes. Rows come in runs of consecutive person numbers
# (the people created together), spread over game_db.dat. Goals name their scorer by
# person number.
_PERSON_ROW = re.compile(rb"(?=(.{4})(.{4})\2[\x10\x13])", re.DOTALL)  # the shape; _doubled_values scans it
_PERSON_ROW_TAILS = (0x10, 0x13)


def _doubled_values(game_db: bytes, tails: tuple[int, ...] | None = None, first_below: int | None = None,
                    second_between: tuple[int, int] | None = None, distinct: bool = False) -> list[tuple[int, int, int]]:
    """Every ``[a][b][b]`` of three u32 in the file, as (position, a, b) in file order: the shape of a person row
    (number, uid, uid) and of _DOUBLED_ID. The filters (a below a limit, b strictly between two limits, a != b, one
    of ``tails`` after) are applied in numpy: a file holds tens of millions of doubled values (runs of zeros). The
    regexes gave the same, eight times slower (14 s against 1.7 s for a 320 MB save): numpy reads the file as u32 at
    each of the four alignments and compares neighbours."""
    import numpy as np

    bytes_view = np.frombuffer(game_db, dtype=np.uint8)
    positions, firsts, seconds = [], [], []
    for align in range(4):
        count = (len(game_db) - align) // 4
        if count < 3:
            continue
        words = np.frombuffer(game_db, dtype="<u4", count=count, offset=align)
        first, second = words[:-2], words[1:-1]
        keep = second == words[2:]
        if first_below is not None:
            keep &= first < first_below
        if second_between is not None:
            keep &= (second > second_between[0]) & (second < second_between[1])
        if distinct:
            keep &= first != second
        hits = np.flatnonzero(keep)
        at = align + 4 * hits
        if tails is not None:
            inside = at + 12 < len(game_db)
            hits, at = hits[inside], at[inside]
            tail_ok = np.isin(bytes_view[at + 12], tails)
            hits, at = hits[tail_ok], at[tail_ok]
        positions.append(at)
        firsts.append(first[hits])
        seconds.append(second[hits])
    if not positions:
        return []
    position = np.concatenate(positions)
    order = np.argsort(position, kind="stable")
    first, second = np.concatenate(firsts)[order], np.concatenate(seconds)[order]
    return list(zip(position[order].tolist(), first.tolist(), second.tolist()))
_PERSON_NEIGHBOUR = 60  # the next row in a run starts within this many bytes
_MAX_PERSON = 10_000_000


def read_person_numbers(game_db: bytes) -> dict[int, int]:
    """Map the save's person numbers (used by goals) to FM unique ids.

    A candidate row is kept only when the row before or after it is the person
    number one lower or higher; this removes chance matches. A number found with two
    different ids is left out. In one 2063 save: 664 469 people, including retired
    players such as Paul Malcolm (331 777 -> 2002312311).
    """
    rows = _doubled_values(game_db, tails=_PERSON_ROW_TAILS, first_below=_MAX_PERSON)
    found: dict[int, set[int]] = {}
    for index, (position, number, uid) in enumerate(rows):
        for other in (index - 1, index + 1):
            if 0 <= other < len(rows):
                other_position, other_number, _ = rows[other]
                if abs(other_position - position) <= _PERSON_NEIGHBOUR and abs(other_number - number) == 1:
                    found.setdefault(number, set()).add(uid)
                    break
    return {number: next(iter(uids)) for number, uids in found.items() if len(uids) == 1}


_DOUBLED_ID = re.compile(rb"(?=(.{4})(.{4})\2)", re.DOTALL)
_NEIGHBOUR_SPAN = 8 * 1500  # bytes between the rows of a person's neighbours when the person's row lies between them
_RECORD_REACH = 1500  # a full person record's birth date is this close before its person row
# Some staff records are longer (Aster Vranckx's name block is 1 558 bytes before his row): a person
# still without a name may take one from this far back, with a birth date that no one else has.
_FAR_RECORD_REACH = 2500
_MAX_FULL_NAME = 120  # bytes of a full legal name written out in a name block
_MAX_UID = 2_200_000_000


@dataclass(frozen=True, slots=True)
class PersonName:
    name: str  # as the game shows it: a common name, the first name only, or first + surname
    first_name: str | None = None
    last_name: str | None = None
    birth_date: date | None = None


def _person_rows(game_db: bytes, weak: set[int] | None = None) -> dict[int, tuple[int, int]]:
    """Person number -> (FM uid, position of the row), from both rules; ``weak`` collects the numbers
    found by the doubled-value rules alone (no run of neighbours, no player record).

    Rows inside runs of consecutive numbers are trusted. Other ``[number][uid][uid]``
    pairs are added when the uid follows this number only and is not taken by a trusted
    row, and it is the number's only such uid (Alan Kardec, 529760, also stands once
    before a doubled 60000).
    """
    trusted: dict[int, tuple[int, int]] = {}
    for number, uid in read_person_numbers(game_db).items():
        trusted[number] = (uid, -1)
    taken = {uid for uid, _ in trusted.values()}
    by_number: dict[int, set[int]] = {}
    by_uid: dict[int, set[int]] = {}
    positions: dict[tuple[int, int], int] = {}
    for position, number, uid in _doubled_values(game_db, first_below=_MAX_PERSON, second_between=(1000, _MAX_UID), distinct=True):
        if number == 0:
            continue
        positions.setdefault((number, uid), position)
        by_number.setdefault(number, set()).add(uid)
        by_uid.setdefault(uid, set()).add(number)
    rows: dict[int, tuple[int, int]] = {}
    for number, (uid, _) in trusted.items():
        rows[number] = (uid, positions.get((number, uid), -1))
    crowded: dict[int, list[int]] = {}
    for number, uids in by_number.items():
        if number in rows:
            continue
        # a number can also stand before some other doubled value (60000 60000): only a uid
        # that follows no other number and belongs to nobody else can be this person's
        own = [uid for uid in uids if len(by_uid[uid]) == 1 and uid not in taken]
        if len(own) == 1:
            rows[number] = (own[0], positions[(number, own[0])])
            if weak is not None:
                weak.add(number)
            taken.add(own[0])
        elif own:
            crowded[number] = own
    # A player's own row is the first after his birth date. Most people of a new game stand in full
    # records like his, not in the runs of short rows that hold a long career's retired people.
    for number, uid, position in player_rows(game_db):
        if number not in rows and uid not in taken:
            rows[number] = (uid, position)
            taken.add(uid)
            crowded.pop(number, None)
    # Several such uids (Mathias Stokkeland, 846657, also stands before a doubled 120591360 far
    # away): the rows are in number order, so the person's own row lies between its neighbours'.
    placed = sorted((number, position) for number, (_, position) in rows.items() if position >= 0)
    numbers = [number for number, _ in placed]
    for number, own in crowded.items():
        at = bisect.bisect_left(numbers, number)
        low = placed[at - 1][1] if at else 0
        high = placed[at][1] if at < len(placed) else len(game_db)
        if high - low > _NEIGHBOUR_SPAN:  # neighbours far apart: the gap holds other things too
            continue
        inside = [uid for uid in own if low < positions[(number, uid)] < high and uid not in taken]
        if len(inside) == 1:
            rows[number] = (inside[0], positions[(number, inside[0])])
            if weak is not None:
                weak.add(number)
            taken.add(inside[0])
    return rows


def _name_from_short_row(game_db: bytes, row: int, names: NameTables) -> PersonName | None:
    """``10 00`` / ``13 ff ff ff ff 00`` + first + last + 8 bytes, or ``10 01`` + one id + 8 bytes."""
    if row < 22:
        return None
    if game_db[row - 14] == 0x10 and game_db[row - 13] == 1:
        common = names.common.get(struct.unpack_from("<I", game_db, row - 12)[0])
        return PersonName(common) if common else None
    full_form = game_db[row - 18] == 0x10 and game_db[row - 17] == 0
    common_form = game_db[row - 22] == 0x13 and game_db[row - 21 : row - 17] == b"\xff\xff\xff\xff"
    if not (full_form or common_form):
        return None
    first_id, last_id = struct.unpack_from("<II", game_db, row - 16)
    first, last = names.first.get(first_id), names.last.get(last_id)
    if first is None or last is None:
        return None
    return PersonName(f"{first} {last}", first, last)


def _find_named_date(game_db: bytes, row: int, low: int, names: NameTables) -> int | None:
    """The nearest position in [low, row) holding a birth date with valid name ids before it."""
    for position in range(row - 4, max(23, low), -1):
        found = decode_birth_date(game_db, position)
        if found is None or not 1940 <= found.year <= 2060:
            continue
        shift = _written_name_shift(game_db, position)
        if position - shift + FIRST_NAME_ID_OFFSET < 0:
            continue
        first_id = struct.unpack_from("<I", game_db, position - shift + FIRST_NAME_ID_OFFSET)[0]
        last_id = struct.unpack_from("<I", game_db, position - shift + LAST_NAME_ID_OFFSET)[0]
        if first_id in names.first and last_id in names.last:
            return position
    return None


def _find_name_triple(game_db: bytes, row: int, low: int, names: NameTables) -> tuple[int, date | None] | None:
    """The nearest name block in [low, row): ``[first][00][last][00][common][00]`` (common may be
    ``ff ff ff ff``), the full name as a string (often empty) and the birth date.

    Staff and many players keep their names this way. Only the whole form counts: three
    small numbers are often valid name ids by chance (Alan Kardec's record has such a chance
    triple 157 bytes before his row; his real block is 707 bytes before it, with
    "Alan Kardec" written out). First-name id 0 is the game's empty placeholder.
    """
    for position in range(max(0, low), row - 15):
        if game_db[position + 4] or game_db[position + 9] or game_db[position + 14]:
            continue
        first_id, last_id, common_id = (struct.unpack_from("<I", game_db, position + o)[0] for o in (0, 5, 10))
        if not first_id or first_id not in names.first or last_id not in names.last or not (
            common_id in (first_id, 0xFFFFFFFF) or common_id in names.common
        ):
            continue
        length = struct.unpack_from("<I", game_db, position + 15)[0]
        after = position + 19 + length
        if length > _MAX_FULL_NAME or after + 4 > row:
            continue
        if length:
            try:
                game_db[position + 19:after].decode("utf-8")
            except UnicodeDecodeError:
                continue
        day, year = struct.unpack_from("<HH", game_db, after)
        birth = _date_from_day_of_year(day & _DAY_BITS, year) if 1900 <= year <= 2070 else None
        if birth is not None:
            return position, birth
    return None


def _person_name(first_id: int, last_id: int, common_id: int, names: NameTables, birth: date | None) -> PersonName:
    first, last = names.first[first_id], names.last[last_id]
    if common_id == first_id:
        shown = first  # known by the first name only ("Francisco")
    elif common_id != 0xFFFFFFFF and common_id in names.common:
        shown = names.common[common_id]
    else:
        shown = f"{first} {last}"
    return PersonName(shown, first, last, birth)


def _last_marked_birth_date(game_db: bytes, low: int, row: int) -> int | None:
    """The birth date nearest before the person row: the player record's own (the last strong
    one, else the last weak one)."""
    strong = weak = None
    for position, is_strong in _name_block_dates(game_db, low, row):
        if is_strong:
            strong = position
        else:
            weak = position
    return strong if strong is not None else weak


def _name_from_record(game_db: bytes, row: int, low: int, names: NameTables, reach: int = _RECORD_REACH) -> PersonName | None:
    """The record between the previous person row (``low``) and this one.

    Tried in order: a marked birth date with name ids 19/14/9 bytes before it (players),
    the record's first full name block (staff, and players without a marked date), a
    birth date without the marker. A record also holds other people's name blocks (a
    player's record names Harry Kane), so a person's own block is the first. A record
    never reaches back past the previous person's row. Order chosen on 245 people whose
    full name is written out (212 right, against 173 before) and 48 130 players read
    from their attribute records (unchanged).
    """
    low = max(low, row - reach)
    birth = _last_marked_birth_date(game_db, low, row)
    if birth is not None:
        found = _names_before_date(game_db, birth, names)
        if found is not None:
            return found
    triple = _find_name_triple(game_db, row, low, names)
    if triple is not None:
        ids = [struct.unpack_from("<I", game_db, triple[0] + o)[0] for o in (0, 5, 10)]
        return _person_name(*ids, names, triple[1])
    birth = _find_named_date(game_db, row, low, names)
    return _names_before_date(game_db, birth, names) if birth is not None else None


def _names_before_date(game_db: bytes, birth: int, names: NameTables) -> PersonName | None:
    shift = _written_name_shift(game_db, birth)
    ids = [struct.unpack_from("<I", game_db, birth - shift + o)[0] for o in (FIRST_NAME_ID_OFFSET, LAST_NAME_ID_OFFSET, -9)]
    if ids[0] in names.first and ids[1] in names.last:
        return _person_name(*ids, names, decode_birth_date(game_db, birth))
    return None


_DOUBTFUL_REPEATS = 3  # a name on this many possibly misread rows is junk, whatever the birth dates


def _misread_row(game_db: bytes, row: int, uid: int) -> bool:
    """Whether a row may be a doubled value read one byte early ("de d8 13 00 de d8 13 00" as uid 0x13d8de00): the
    uid ends in 00 and the same form fits one byte on. 275 such rows in tables of other things took the junk
    name "Valter Ganesan" from the bytes before them; a real person there (Jens Keller, 0x003a6900) has his
    name ids just before his row. Checked only for rows the weak rules found."""
    return uid & 0xFF == 0 and game_db[row + 5:row + 9] == game_db[row + 9:row + 13]


def read_person_names(game_db: bytes, names: NameTables | None = None) -> tuple[dict[int, int], dict[int, PersonName]]:
    """Person numbers to FM uids, and FM uids to names, for living and retired people.

    Returns ``(person_number -> uid, uid -> PersonName)``. A person is either a short
    row (the name ids sit just before the person number; used for people the game
    keeps only for history, such as retired players) or a full record ending in the
    person row (the names are read like a player record's).
    """
    names = names or read_name_tables(game_db)
    weak: set[int] = set()
    rows = _person_rows(game_db, weak)
    people = {number: uid for number, (uid, _) in rows.items()}
    weak_uids = {rows[number][0] for number in weak if number in rows}
    # a weak row numbered beyond every row the other rules found is as doubtful as a misread one
    highest = max((number for number in rows if number not in weak), default=0)
    beyond = {rows[number][0] for number in weak if number in rows and number > highest}
    doubtful: dict[int, PersonName | None] = {}
    result: dict[int, PersonName] = {}
    far: dict[int, tuple[int, int]] = {}
    ordered = sorted((row, uid) for uid, row in rows.values() if row >= 0)
    for index, (row, uid) in enumerate(ordered):
        if uid in result:
            continue
        previous_end = ordered[index - 1][0] + 12 if index else 0
        found = _written_name_before_row(game_db, row) or _name_from_short_row(game_db, row, names)
        if found is None and uid in weak_uids and (uid in beyond or _misread_row(game_db, row, uid)):
            doubtful[uid] = _name_from_record(game_db, row, previous_end, names)  # kept below only if no one else has it
            continue
        found = found or _name_from_record(game_db, row, previous_end, names)
        if found is not None:
            result[uid] = found
        elif row - previous_end > _RECORD_REACH:
            far[uid] = (row, previous_end)
    # A far name block can also be another person's: only a birth-dated name that no one else has is
    # taken (Aster Vranckx and Regan Slater, managers in a 2063 save, found this way).
    taken = Counter((p.name, p.birth_date) for p in result.values())
    found_far = {uid: _name_from_record(game_db, row, low, names, _FAR_RECORD_REACH) for uid, (row, low) in far.items()}
    taken.update((p.name, p.birth_date) for p in found_far.values() if p is not None)
    # A row that may be misread keeps its record's name only when the name and birth date are no one else's: the
    # junk repeats ("Valter Ganesan", born 15 September 2000, 271 times); a real player does not (James Henry).
    taken.update((p.name, p.birth_date) for p in doubtful.values() if p is not None)
    doubtful_names = Counter(p.name for p in doubtful.values() if p is not None)
    for uid, found in doubtful.items():
        if (found is not None and found.birth_date and taken[(found.name, found.birth_date)] == 1
                and doubtful_names[found.name] < _DOUBTFUL_REPEATS):
            result[uid] = found
    # id 0 of each name table is its empty placeholder ("Oldřich", "Nejedlý", "DJ Buffonge")
    placeholders = {names.first.get(0), names.last.get(0), names.common.get(0)} - {None}
    for uid, found in found_far.items():
        if (found is not None and found.birth_date and taken[(found.name, found.birth_date)] == 1
                and not placeholders & {found.name, found.first_name, found.last_name}):
            result[uid] = found
    return people, result


_NAMED_ROW = b"\x05\0\0\0\0"  # a person kept only by name (a club's historic legend or personnel)
_NAMED_ROW_TAIL = 8  # the bytes between the written name and the person row


def _written_name_before_row(game_db: bytes, row: int) -> PersonName | None:
    """A written name just before the person row: ``05 00 00 00 00`` + u32 length + the name +
    8 bytes, the form of a short row with the text in place of the name ids ("Peter Osgood",
    one of Chelsea's legends). The name is the row's own, as the ids before a short row are:
    the clubs' reasons agree (Arsène Wenger a Manager, Ronnie Moran Backroom Staff)."""
    end = row - _NAMED_ROW_TAIL
    # the marker, then the length, then the name up to ``end``: the nearest marker before ``end`` first (the shortest
    # name), as the search by length did; one C search instead of a Python loop over every length, for a million rows
    longest = min(_MAX_WRITTEN_NAME, end - 9)
    if longest < 1:
        return None
    low = max(0, end - longest - 9)  # rfind counts a negative start from the file's end
    marker = game_db.rfind(_NAMED_ROW, low, end - 9 + len(_NAMED_ROW) - 1)
    while marker >= 0:
        start = marker + 9
        length = end - start
        if length >= 1 and struct.unpack_from("<I", game_db, start - 4)[0] == length:
            try:
                text = game_db[start:end].decode("utf-8").strip()
            except UnicodeDecodeError:
                return None
            if not any(c.isalpha() for c in text):
                return None
            first, _, last = text.partition(" ")
            return PersonName(text, first, last or first)
        marker = game_db.rfind(_NAMED_ROW, low, marker + len(_NAMED_ROW) - 1)
    return None


# -- player season statistics --------------------------------------------------------------------
_STATS_HEADER = 8
_STATS_RECORD = 152
_STATS_GROUP_OFFSET = -40  # the group number sits 40 bytes before a player's marked birth date
_MIN_PLAYER_RECORD = 100  # a player's birth date lies further than this before the person row
_YOUNGEST, _OLDEST = 14, 45  # ages at which a player has season statistics
# The kind of competition a statistics record counts (byte 31). One record holds all
# competitions of its kind: the FA Cup and the League Cup share the CUP record.
FRIENDLY, LEAGUE, CUP, CONTINENTAL, INTERNATIONAL = range(5)
KIND_NAMES = {FRIENDLY: "friendly", LEAGUE: "league", CUP: "cup", CONTINENTAL: "continental", INTERNATIONAL: "national team"}


@dataclass(frozen=True, slots=True)
class SeasonStats:
    team: int  # team number
    league_key: int  # history key of the club's league that season, also on cup records
    season_start: int  # 2051 for 2051/52
    kind: int  # FRIENDLY, LEAGUE, CUP, CONTINENTAL or INTERNATIONAL (national team, counted at the club)
    starts: int
    substitute_appearances: int
    rated_appearances: int  # matches with a rating; the average rating uses this
    goals: int
    assists: int
    player_of_match: int
    goals_conceded: int
    clean_sheets: int
    yellow_cards: int
    red_cards: int
    minutes: int
    rating_total: int  # sum of match ratings, in tenths

    @property
    def league(self) -> bool:
        return self.kind == LEAGUE

    @property
    def appearances(self) -> int:
        return self.starts + self.substitute_appearances

    @property
    def average_rating(self) -> float | None:
        return round(self.rating_total / self.rated_appearances / 10, 2) if self.rated_appearances else None


def _season_stats(dt: bytes, offset: int) -> SeasonStats:
    position = _STATS_HEADER + offset
    team, key = struct.unpack_from("<IH", dt, position + 1)
    year, rating_total, minutes = struct.unpack_from("<HHH", dt, position + 11)
    r = dt[position : position + _STATS_RECORD]
    return SeasonStats(
        team, key, year, r[31], r[24], r[25], r[26], r[27], r[28], r[29], r[17], r[22], r[21], r[20], minutes, rating_total
    )


def _stat_groups(ls: bytes) -> list[list[int]]:
    """The record offsets of each group in ``player_stats_hist_ls.dat``.

    After the 8-byte header: a count and that many plain offsets (records that belong
    to no group), then the number of groups and, per group, a count and the
    offsets. In a group the first offset is plain and each next one is stored as
    offset minus the value stored before it.
    """
    values = struct.unpack_from(f"<{(len(ls) - _STATS_HEADER) // 4}i", ls, _STATS_HEADER)
    index = 1 + values[0]
    groups = []
    for _ in range(values[index]):
        count = values[index + 1]
        stored = values[index + 2 : index + 2 + count]
        groups.append([value if k == 0 else value + stored[k - 1] for k, value in enumerate(stored)])
        index += 1 + count
    return groups


def read_season_stats(dt: bytes, ls: bytes) -> list[list[SeasonStats]]:
    """Every player's season statistics, by group number, oldest first.

    ``dt`` is ``player_stats_hist_dt.cmt``: an 8-byte header, then 152-byte records,
    one per player, club, season and competition: team (``u32`` at 1), the club's
    league key (``u16`` at 5), the first year of the season (``u16`` at 11), rating
    total in tenths and minutes (``u16`` at 13 and 15), then single bytes: goals
    conceded (17), red cards (20), yellow cards (21), clean sheets (22), starts (24),
    substitute appearances (25), rated appearances (26), goals (27), assists (28),
    player of the match (29) and the kind of competition (31). A record names the
    kind, not the competition. ``ls`` is ``player_stats_hist_ls.dat``
    (``_stat_groups``); ``read_stat_groups`` gives each player his group.
    """
    limit = len(dt) - _STATS_HEADER - _STATS_RECORD
    return [
        [_season_stats(dt, offset) for offset in offsets if 0 <= offset <= limit and not offset % _STATS_RECORD]
        for offsets in _stat_groups(ls)
    ]


_RETIRED_GROUP, _RETIRED_BIRTH_YEAR = 13, 27  # in a retirement_manager.dat block
_MAX_RETIRED_BLOCK = 10_000


def read_retired_players(retirement_manager: bytes) -> dict[int, tuple[int, int]]:
    """Retired players' statistics groups: person number -> (group, birth year).

    ``retirement_manager.dat`` has an index of 12-byte entries (person number, offset
    from the first compressed block, size), preceded by their count, then one small
    compressed block per player (about 800 in an August 2063 save). In a block the
    group number is a ``u32`` at 13 and the birth year a ``u16`` at 27; ``ffffffff``
    means no statistics.
    """
    base = retirement_manager.find(ZSTD_MAGIC)
    if base < 0:
        return {}
    for start in range(0, base - 4):
        count = struct.unpack_from("<I", retirement_manager, start)[0]
        end = start + 4 + 12 * count
        if not 0 < count or not base - 64 <= end <= base:
            continue
        entries = [struct.unpack_from("<III", retirement_manager, start + 4 + 12 * k) for k in range(count)]
        if all(0 < size < _MAX_RETIRED_BLOCK for _, _, size in entries):
            break
    else:
        return {}
    zstd = _zstd()
    result = {}
    for number, offset, size in entries:
        frame = retirement_manager.find(ZSTD_MAGIC, base + offset - 8, base + offset + 16)
        if frame < 0:
            continue
        try:
            block = zstd.ZstdDecompressor().decompressobj().decompress(retirement_manager[frame : frame + size + 16])
        except zstd.ZstdError:
            continue
        if len(block) >= _RETIRED_BIRTH_YEAR + 2:
            group = struct.unpack_from("<I", block, _RETIRED_GROUP)[0]
            if group != 0xFFFFFFFF:
                result[number] = (group, struct.unpack_from("<H", block, _RETIRED_BIRTH_YEAR)[0])
    return result


def read_stat_groups(
    game_db: bytes,
    stats: list[list[SeasonStats]],
    names: NameTables | None = None,
    retirement_manager: bytes | None = None,
) -> dict[int, int]:
    """Each player's statistics group number, by FM uid.

    The number sits 40 bytes before the marked birth date in the player record, after
    the first and last name ids. Retired players who stay on as staff keep the number
    of a group the game has since given to a new player, so a claim counts only when
    the player was 14 to 45 years old in the group's seasons, and a group claimed by
    two such players is left out. ``stats`` comes from ``read_season_stats``. Players
    who retired keep no player record; give ``retirement_manager.dat`` to link them
    (``read_retired_players``).
    """
    names = names or read_name_tables(game_db)
    rows = _person_rows(game_db)
    ordered = sorted((row, uid) for uid, row in rows.values() if row >= 0)
    claims: dict[int, list[int]] = {}
    for index, (row, uid) in enumerate(ordered):
        low = max(ordered[index - 1][0] + 12 if index else 0, row - _RECORD_REACH)
        birth = _last_marked_birth_date(game_db, low, row)
        if birth is None:
            staff = _staff_stats_group(game_db, low, row, names)
            if staff and staff[0] < len(stats) and stats[staff[0]]:
                _claim(claims, stats, staff[0], staff[1], uid)
            continue
        shift = _written_name_shift(game_db, birth)
        if birth - shift < -_STATS_GROUP_OFFSET or row - birth < _MIN_PLAYER_RECORD:
            continue
        first_id, last_id = (struct.unpack_from("<I", game_db, birth - shift + o)[0] for o in (FIRST_NAME_ID_OFFSET, LAST_NAME_ID_OFFSET))
        group = struct.unpack_from("<I", game_db, birth - shift + _STATS_GROUP_OFFSET)[0]
        if first_id not in names.first or last_id not in names.last or group >= len(stats) or not stats[group]:
            continue
        _claim(claims, stats, group, decode_birth_date(game_db, birth).year, uid)
    if retirement_manager:
        for number, (group, born) in read_retired_players(retirement_manager).items():
            if number in rows and group < len(stats) and stats[group]:
                _claim(claims, stats, group, born, rows[number][0])
    return {uids[0]: group for group, uids in claims.items() if len(set(uids)) == 1}


# A former player of the game's own database who is now staff (Joe Haigh, Chelsea's head of youth
# development) has no marked birth date. His record has the group number, 17 bytes, the first and
# last name ids (one byte apart), 00 ff ff ff ff 00, his full name, and his birth day and year (u16).
_STAFF_NAMES = re.compile(rb"(.{4}).(.{4})\x00\xff\xff\xff\xff\x00(.{4})", re.DOTALL)
_STAFF_STATS_GROUP = -21  # from the first name id
_MAX_FULL_NAME = 80


def _staff_stats_group(game_db: bytes, low: int, row: int, names: NameTables) -> tuple[int, int] | None:
    """The statistics group and birth year in a staff record without a marked birth date."""
    found = None
    for match in _STAFF_NAMES.finditer(game_db, max(low, -_STAFF_STATS_GROUP), row):
        first, last, length = struct.unpack("<III", match.group(1) + match.group(2) + match.group(3))
        if first in names.first and last in names.last and 0 < length < _MAX_FULL_NAME and match.end() + length + 4 <= row:
            group = struct.unpack_from("<I", game_db, match.start() + _STAFF_STATS_GROUP)[0]
            found = (group, struct.unpack_from("<H", game_db, match.end() + length + 2)[0])
    return found


def _claim(claims: dict[int, list[int]], stats: list[list[SeasonStats]], group: int, born: int, uid: int) -> None:
    seasons = [record.season_start for record in stats[group]]
    if min(seasons) - born >= _YOUNGEST and max(seasons) - born <= _OLDEST:
        claims.setdefault(group, []).append(uid)


# -- awards ---------------------------------------------------------------------------------------
_AWARD_SECTION = 6  # in the English block of lang_db.dat
_AWARD_ROW = 82
_AWARD_PLACES = 3
_AWARD_PLACE = 26
# An award record in game_db.dat: the history key, the FM uid written twice, 00 00 00 01.
_AWARD_RECORD = re.compile(rb"(?=(.{4})(.{4})\2\x00(?:\x00\x00|\xff\xff)\x01)", re.DOTALL)
_MAX_AWARD_KEY = 100_000


@dataclass(frozen=True, slots=True)
class AwardName:
    fm_uid: int
    name: str  # "World Footballer of the Year"
    short_name: str  # "Footballer of the Year"


@dataclass(frozen=True, slots=True)
class AwardPlace:
    person: int  # the save's person number (read_person_names gives the FM uid and name)
    club: int | None  # the FM uid of the club or national team; None when not recorded
    age: int
    appearances: int  # in the period the award judges (a league season, a tournament, a year)
    goals: int
    assists: int
    rating_total: int  # tenths
    votes: int  # points in a vote (world awards); 0 otherwise

    @property
    def average_rating(self) -> float | None:
        return round(self.rating_total / self.appearances / 10, 2) if self.appearances else None


@dataclass(frozen=True, slots=True)
class AwardYear:
    award_key: int  # read_award_keys maps it to the award's FM uid
    year: int
    places: tuple[AwardPlace, ...]  # winner first; up to three


def read_award_names(lang_db: str | Path) -> dict[int, AwardName]:
    """Award names by FM uid, from section 6 of the English block of the game's ``lang_db.dat``.

    Per award: the FM uid, then the name and the short name, each followed by one
    byte, then one more byte.
    """
    with open(lang_db, "rb") as handle:
        raw = handle.read()
    data = _decompress_prefix(raw, _DB_SECTION, occurrences=_AWARD_SECTION + 2)
    section = -1
    for _ in range(_AWARD_SECTION + 1):
        section = data.find(_DB_SECTION, section + 1)
        if section < 0:
            raise FmSaveError(f"{lang_db} does not have the expected sections.")
    count = struct.unpack_from("<I", data, section + 8)[0]
    position = section + _DB_SECTION_HEADER
    names: dict[int, AwardName] = {}
    for _ in range(count):
        fm_uid = struct.unpack_from("<I", data, position)[0]
        position += 4
        texts = []
        for _ in range(2):
            entry = _read_string(data, position, limit=300)
            if entry is None:
                raise FmSaveError(f"Unreadable award name near byte {position} of {lang_db}.")
            texts.append(entry[0])
            position = entry[1] + 1
        position += 1
        names[fm_uid] = AwardName(fm_uid, *texts)
    return names


def read_award_keys(game_db: bytes, award_uids_in_order: list[int]) -> dict[int, int]:
    """Map award history keys to award FM uids, from the award records in ``game_db.dat``.

    ``award_uids_in_order`` is ``list(read_award_names(...))``: it tells award uids
    apart from other records and fills gaps where the spacing matches. The keys follow
    that order up to about key 2139 and then drift, as competition keys do.
    """
    known = set(award_uids_in_order)
    keys: dict[int, int] = {}
    for match in _AWARD_RECORD.finditer(game_db):
        key, uid = struct.unpack("<II", match.group(1) + match.group(2))
        if uid in known and key < _MAX_AWARD_KEY:
            keys.setdefault(key, uid)
    return _fill_key_gaps(keys, award_uids_in_order)


def read_award_history(buffer: bytes) -> list[AwardYear]:
    """Every row of ``award_year_hist_dt.cmt``: who won each award, by year.

    Rows are 82 bytes after an 8-byte header: year and award key (``u16`` each), then
    three places of 26 bytes: person number and club FM uid (``u32``), age, rating
    total in tenths (``u16``), appearances, goals and assists (one byte each, with a
    zero byte after each), votes (``u16``), then position and nation bytes. An empty
    place has person ``ffffffff``.
    """
    years = []
    for position in range(_HISTORY_HEADER, len(buffer) - _AWARD_ROW + 1, _AWARD_ROW):
        year, key = struct.unpack_from("<HH", buffer, position)
        places = []
        for index in range(_AWARD_PLACES):
            at = position + 4 + index * _AWARD_PLACE
            person, club = struct.unpack_from("<II", buffer, at)
            if person == NO_TEAM:
                continue
            rating_total, apps, goals, assists, votes = struct.unpack_from("<HBxBxBxH", buffer, at + 9)
            places.append(
                AwardPlace(person, None if club == NO_TEAM else club, buffer[at + 8], apps, goals, assists, rating_total, votes)
            )
        years.append(AwardYear(key, year, tuple(places)))
    return years



# -- seasonal best elevens ------------------------------------------------------------------------
_ELEVEN_ROW = 509
_ELEVEN_SQUAD = 18  # the starting eleven, then seven substitutes
_ELEVEN_ENTRY = 27
_ELEVEN_STARTERS = 11
# Position bits, as in the game's position lists.
POSITION_NAMES = (
    "GK", "SW", "D (R)", "D (L)", "D (C)", "WB (R)", "WB (L)", "DM",
    "M (R)", "M (L)", "M (C)", "AM (R)", "AM (L)", "AM (C)", "ST (C)",
)


def position_name(bits: int) -> str | None:
    """The first position named by a position bit field."""
    for index, name in enumerate(POSITION_NAMES):
        if bits & (1 << index):
            return name
    return None


@dataclass(frozen=True, slots=True)
class BestElevenPlayer:
    person: int  # the save's person number
    appearances: int
    goals: int
    rating_total: int  # tenths
    position: int  # the position bit he fills in the eleven
    starting: bool  # one of the first eleven; the others are the substitutes

    @property
    def average_rating(self) -> float | None:
        return round(self.rating_total / self.appearances / 10, 2) if self.appearances else None


@dataclass(frozen=True, slots=True)
class BestEleven:
    year: int  # first year of the season (2022 for 2022/23)
    competition_key: int  # the team's league (or tournament, for a national team) that season
    players: tuple[BestElevenPlayer, ...]



# -- goal awards (Goal of the Month, of the Season, of the Tournament) ---------------------------------------------------------
# award_man.dat (plain part and inner blocks) holds each goal award as: the award key (u16, read_award_keys), 03 (places),
# ff x4, the season's first and last days, a u16, 01 00 6c 07 (no date), ff x4, a date, then the latest month's goals (u16
# count, then the goals, best first); then the past months: a u16 count, and per month a u32 count, the goals and the month
# the award was given (u8, 0-based, counted on from January of the u16 year: 12 = January of the next year). A goal is 33
# bytes: 01, the shot event's value (u32), a u32, the minute (u16), the scorer's team (u32 team number), 00, the opponent
# (u32), the scorer (u32 person number), the day (u16 day of year, u16 year), the shot type and range bytes of the match
# event (shot_kind, shot_range) and three bytes. Found 4 Nov 2026 from the user's screenshot of the Premier Division's
# February 2064 award (Hilgers, Samuel, José Miguel; key 634) and checked against its six past months.
_GOAL_AWARD = re.compile(rb"(..)\x03\xff\xff\xff\xff(....)(....)..\x01\x00\x6c\x07\xff\xff\xff\xff....(..)", re.DOTALL)
_GOAL_ENTRY = struct.Struct("<BIIHIBIIHHBB3x")
_MAX_AWARD_MONTHS = 40


@dataclass(frozen=True, slots=True)
class AwardGoal:
    place: int  # 1 = the winner
    person: int  # person number
    team: int  # team number
    against: int  # team number
    scored_on: date
    minute: int
    kind: str | None  # shot_kind: header, volley, drive, finish
    range: str | None  # shot_range: close, outside


@dataclass(frozen=True, slots=True)
class GoalAwardMonth:
    award_key: int
    season_start: date | None
    given: date | None  # the first day of the month it was given; None for the latest, not yet in the history
    goals: tuple[AwardGoal, ...]


def _award_goals(buffer: bytes, position: int, count: int) -> tuple[AwardGoal, ...] | None:
    goals = []
    for place in range(1, count + 1):
        if position + _GOAL_ENTRY.size > len(buffer):
            return None
        one, _, _, minute, team, zero, against, person, day, year, kind, far = _GOAL_ENTRY.unpack_from(buffer, position)
        scored = _date_from_day_of_year(day & _DAY_BITS, year)
        if one != 1 or zero != 0 or scored is None or minute > 130:
            return None
        goals.append(AwardGoal(place, person, team, against, scored, minute, shot_kind(kind), shot_range(far)))
        position += _GOAL_ENTRY.size
    return tuple(goals)


def _goal_award_months(buffer: bytes) -> Iterator[GoalAwardMonth]:
    for match in _GOAL_AWARD.finditer(buffer):
        key = struct.unpack("<H", match.group(1))[0]
        first = struct.unpack("<HH", match.group(2))
        season_start = _date_from_day_of_year(first[0] & _DAY_BITS, first[1])
        count, position = struct.unpack("<H", match.group(4))[0], match.end()
        latest = _award_goals(buffer, position, count) if 0 < count <= 5 else None
        if latest is None or season_start is None:
            continue
        position += _GOAL_ENTRY.size * count
        months = struct.unpack_from("<H", buffer, position)[0] if position + 2 <= len(buffer) else 0
        position += 2
        for _ in range(months if months <= _MAX_AWARD_MONTHS else 0):
            count = struct.unpack_from("<I", buffer, position)[0] if position + 4 <= len(buffer) else 0
            goals = _award_goals(buffer, position + 4, count) if 0 < count <= 5 else None
            if goals is None:
                break
            position += 4 + _GOAL_ENTRY.size * count
            if position + 3 > len(buffer):
                break
            month, year = struct.unpack_from("<BH", buffer, position)
            position += 3
            yield GoalAwardMonth(key, season_start, date(year + month // 12, month % 12 + 1, 1), goals)
        yield GoalAwardMonth(key, season_start, None, latest)


def read_goal_awards(award_man: bytes) -> list[GoalAwardMonth]:
    """Every goal award month in ``award_man.dat``: the past months with the month they were given, and each award's latest."""
    found = list(_goal_award_months(award_man))
    for block in iter_inner_blocks(award_man):
        found.extend(_goal_award_months(block))
    return found


def read_best_elevens(dt: bytes, ls: bytes) -> list[list[BestEleven]]:
    """Seasonal best elevens, one list per team, oldest season first.

    ``tc_best_eleven_history_dt.cmt``: an 8-byte header, then 509-byte rows: the year
    (``u16``), 18 entries of 27 bytes (person number ``u32``, appearances and goals
    ``u16``, rating total ``u32``, then three ``02 u32`` fields: positions he can
    play, one not decoded, the position he fills), 12 bytes of formation, the
    competition key (``u16``) and 7 bytes not decoded. Empty places have person
    ``ffffffff``. ``tc_best_eleven_history_ls.dat`` groups the rows by team in the
    same coding as ``player_stats_hist_ls.dat``; which team owns a group is not
    stored in the rows (see ``guess_best_eleven_clubs``).
    """
    limit = len(dt) - _STATS_HEADER - _ELEVEN_ROW
    result = []
    for offsets in _stat_groups(ls):
        seasons = []
        for offset in offsets:
            if not 0 <= offset <= limit or offset % _ELEVEN_ROW:
                continue
            row = _STATS_HEADER + offset
            players = []
            for index in range(_ELEVEN_SQUAD):
                at = row + 2 + index * _ELEVEN_ENTRY
                person, apps, goals, rating = struct.unpack_from("<IHHI", dt, at)
                if person == NO_TEAM:
                    continue
                position = struct.unpack_from("<I", dt, at + 23)[0]
                players.append(BestElevenPlayer(person, apps, goals, rating, position, index < _ELEVEN_STARTERS))
            year = struct.unpack_from("<H", dt, row)[0]
            key = struct.unpack_from("<H", dt, row + 2 + _ELEVEN_SQUAD * _ELEVEN_ENTRY + 12)[0]
            seasons.append(BestEleven(year, key, tuple(players)))
        result.append(seasons)
    return result


def guess_best_eleven_clubs(
    elevens: list[list[BestEleven]],
    stats: list[list[SeasonStats]],
    stat_groups: dict[int, int],
    person_uids: dict[int, int],
) -> dict[int, int]:
    """Best-eleven group -> team number, inferred from the players' own club records.

    A season votes for the club most of its linked players played for that season in
    the row's competition (so a national team's eleven, full of one club's players,
    does not vote for that club); a group gets the club that wins at least 70% of its
    votes. National teams and clubs whose players have no statistics (some lower
    leagues) stay out. Verified on Chelsea and Everton; an inference, not a stored link.
    """
    owners: dict[int, int] = {}
    for group, seasons in enumerate(elevens):
        wins: dict[int, int] = {}
        voting = 0
        for season in seasons:
            votes: dict[int, int] = {}
            linked = 0
            for player in season.players:
                records = stats[stat_groups[person_uids[player.person]]] if person_uids.get(player.person) in stat_groups else None
                if records is None:
                    continue
                linked += 1
                for team in {
                    r.team for r in records if r.season_start == season.year and r.league_key == season.competition_key and r.appearances
                }:
                    votes[team] = votes.get(team, 0) + 1
            if votes:
                team, count = max(votes.items(), key=lambda item: item[1])
                if count * 2 > linked:
                    wins[team] = wins.get(team, 0) + 1
                    voting += 1
        if wins:
            team, count = max(wins.items(), key=lambda item: item[1])
            if count >= 0.7 * voting:
                owners[group] = team
    return owners


# -- transfers ------------------------------------------------------------------------------------
# A transfer record: 06, person, the new team, the old team, the squad joined (u32 each,
# the first three followed by a zero byte), the fee twice, the fee with add-ons, the kind
# and the date (day of year and year).
_TRANSFER = re.compile(rb"(?=\x06(.{4})\x00(.{4})\x00(.{4})\x00(.{4})(.{4}).{4}(.{4})([\x00-\x0f])(.{2}.[\x07\x08]))", re.DOTALL)
TRANSFER, EXCHANGE, LOAN, FREE_TRANSFER, COMPENSATION, FREE_AGENT, TRADE, RELEASE, YOUTH_INTAKE = 1, 2, 3, 4, 6, 7, 8, 9, 14
TRANSFER_KINDS = {
    TRANSFER: "transfer", EXCHANGE: "part-exchange", LOAN: "loan", FREE_TRANSFER: "free transfer",
    COMPENSATION: "compensation", FREE_AGENT: "free agent signed", TRADE: "trade", RELEASE: "released",
    YOUTH_INTAKE: "youth intake",
}


@dataclass(frozen=True, slots=True)
class Transfer:
    person: int  # the save's person number
    date: date
    kind: int  # TRANSFER, LOAN, FREE_TRANSFER, ... (TRANSFER_KINDS)
    from_team: int | None  # None for a youth intake or a free agent
    to_team: int | None  # None for a release
    squad: int | None  # the team he joined within the club, such as "Chelsea U18"
    fee: int  # in the game's own currency (pounds; the game shows euros at about 1.166 times)
    fee_with_add_ons: int


def _optional_team(number: int) -> int | None:
    return None if number == NO_TEAM else number


def read_transfers(transfer_man: bytes) -> list[Transfer]:
    """Every transfer, loan, release and youth intake since the save began.

    ``transfer_man.dat`` keeps the current window as plain bytes and older ones in
    compressed blocks inside the file; together they are one stream (records run
    across block borders), as in ``fix_man.dat``.
    """
    first_block = transfer_man.find(ZSTD_MAGIC)
    stream = transfer_man if first_block < 0 else transfer_man[:first_block] + b"".join(iter_inner_blocks(transfer_man))
    transfers = []
    for match in _TRANSFER.finditer(stream):
        person, to_team, from_team, squad, fee, fee_with_add_ons = struct.unpack("<6I", b"".join(match.group(i) for i in range(1, 7)))
        when = decode_match_date(match.group(8))
        teams = [team for team in (to_team, from_team, squad) if team != NO_TEAM]
        if when is None or not _FIRST_YEAR <= when.year <= _LAST_YEAR or not 0 < person < _MAX_PERSON:
            continue
        if any(team >= _MAX_CLUB for team in teams):
            continue
        kind = match.group(7)[0]
        transfers.append(
            Transfer(person, when, kind, _optional_team(from_team), _optional_team(to_team), _optional_team(squad), fee, fee_with_add_ons)
        )
    return transfers


# -- the transfer market: who wants whom, and the bids --------------------------------------------------------------------
# Also in transfer_man.dat (its plain, current part). Each club has a record ``09 00 01 00`` + team number (u32), then within
# 200 bytes a u32 count and that many 28-byte entries ``11 00`` + person number (u32) + ``04`` + a wage (u32), 4 bytes, the
# wage the club would pay (u32), 6 bytes, a flag, 2 bytes: the players it wants. Every record is stored three times.
# Verified on 24 Nov 2063 against four Chelsea players' Transfer tabs (Juventus for Formicola; Al-Hilal and Al-Nassr for
# Júnior and Mačkić, whose offered wage is double; Real Madrid for Medina, listed as "rumoured" with the two wages equal).
# A bid is ``02 0d 00`` + person (u32) + ``00`` + the bidding team (u32) + ``00`` + the squad he would join (u32: the club's
# first team, U21 or U18) + the fee in pounds (u32) + two u32 + a status byte, then dates in the next 80 bytes: the bid's, its
# response or expiry, and the completion. Verified on Chelsea's 2063/64 business: the eight signings' fees (Haas
# £10,821,460 shown as £10.75M, Di Loreto £23,536,160 as £23.5M), Tacita's open offer (£5,906,135 shown as £6M, 24 Nov 2063,
# expiry 24 Dec, completion 9 Jun 2064), Slagboom agreed for 1 Jan 2064, and Plaßhenrich's £43.2M that fell through.
_INTEREST_HEAD = re.compile(rb"\x09\x00\x01\x00(.{4})", re.DOTALL)
_INTEREST_TAG, _INTEREST_ENTRY, _INTEREST_LIST_WITHIN = b"\x11\x00", 28, 200
_MAX_INTEREST = 200
_BID = re.compile(rb"\x02\x0d\x00(.{4})\x00(.{4})\x00(.{4})(.{4})", re.DOTALL)
# A loan offer is the same record with 03 for 02 (26 Oct 2026: King's Lynn's offer for Chelsea's Spyrou, 3 Dec 2063, the
# loan to run to March 2064; the news item showed his value, £15.6M, not a fee). Its four bytes in the fee's place are a
# small sum (0, 580, 6 924, 20 244): most likely the weekly part of his wage the borrower pays; kept as loan_wage, unverified.
_LOAN = re.compile(rb"\x03\x0d\x00(.{4})\x00(.{4})\x00(.{4})(.{4})", re.DOTALL)
_BID_STATUS = 8  # after the fee: two u32, then the status
_BID_DATES_WITHIN = 80
# A bid's clauses (Man Utd for Cosmin Lupu, Exhibit 9, 1 Jan 2064: the game says 'an offer of £11,750,000 ... may rise to
# £14,250,000'; the record's fee is 9 189 937): at +118 from the record's start, FF*4 then entries of 8 bytes, two code
# bytes, `01 02`, the amount (u32). Code 01 01 is part of the offer (9 189 937 + 2 561 440 = £11.75M); the others are
# conditional add-ons (18 06: 2 494 411, to £14.25M). 322 of 3 364 bids have a list; codes 24 0c, 18 0c, 18 06, 0c 06,
# 14 00, 32 00, 0a 00 seen (their conditions are not named yet).
_BID_CLAUSES = re.compile(rb"\xff{4}((?:..\x01\x02.{4})+)", re.DOTALL)
_BID_CLAUSES_AT = 118
_GUARANTEED = b"\x01\x01"
_MAX_FEE = 1_000_000_000
BID_STATUS = {0x00: "made", 0x02: "rejected", 0x03: "withdrawn", 0x10: "collapsed", 0x13: "agreed", 0x14: "completed"}  # 0x03 seen once; 0x02 = the user rejected King's Lynn's loan offer for Spyrou (26 Oct 2026, one case); the rest verified


@dataclass(frozen=True, slots=True)
class TransferInterest:
    team: int  # the interested club's team number
    person: int  # the save's person number
    wage: int  # the player's wage as the club has it (not yet checked against the game's figure)
    offered_wage: int  # what the club would pay; equal to the wage when the interest is only rumoured
    flags: int

    @property
    def rumoured(self) -> bool:
        return self.offered_wage == self.wage


@dataclass(frozen=True, slots=True)
class TransferOffer:
    person: int
    team: int  # the bidding club's team number
    squad: int  # the team he would join (the club's first team, U21 or U18)
    fee: int  # pounds
    status: int  # BID_STATUS
    offered_on: date | None
    until: date | None  # the response or expiry date
    completes_on: date | None  # when the move goes through (the same as until when there are two dates)
    loan: bool = False  # a loan offer (fee 0; the record's sum is loan_wage)
    loan_wage: int | None = None
    fee_guaranteed: int | None = None  # the offer as the game states it: the fee and the clauses that are part of it
    fee_max: int | None = None  # with every conditional add-on
    counter: bool = False  # the selling club's counter-proposal to a bid, not a bid

    @property
    def status_word(self) -> str:
        return "counter" if self.counter else BID_STATUS.get(self.status, f"status {self.status}")


def _plain_part(transfer_man: bytes) -> bytes:
    first_block = transfer_man.find(ZSTD_MAGIC)
    return transfer_man if first_block < 0 else transfer_man[:first_block]


def read_transfer_interest(transfer_man: bytes) -> list[TransferInterest]:
    """Every club's wanted players, each pair once."""
    data = _plain_part(transfer_man)
    found: dict[tuple[int, int], TransferInterest] = {}
    for match in _INTEREST_HEAD.finditer(data):
        team = struct.unpack("<I", match.group(1))[0]
        if not 0 < team < _MAX_CLUB:
            continue
        at = data.find(_INTEREST_TAG, match.end(), match.end() + _INTEREST_LIST_WITHIN)
        if at < 4:
            continue
        count = struct.unpack_from("<I", data, at - 4)[0]
        if not 1 <= count <= _MAX_INTEREST:
            continue
        for _ in range(count):
            if data[at:at + 2] != _INTEREST_TAG or at + _INTEREST_ENTRY > len(data):
                break
            person, wage, offered = struct.unpack_from("<I", data, at + 2)[0], struct.unpack_from("<I", data, at + 7)[0], struct.unpack_from("<I", data, at + 15)[0]
            if 0 < person < _MAX_PERSON:
                found.setdefault((team, person), TransferInterest(team, person, wage, offered, data[at + 25]))
            at += _INTEREST_ENTRY
    return list(found.values())


def read_transfer_offers(transfer_man: bytes) -> list[TransferOffer]:
    """Every bid in the current part of the file: the player, the bidding club, the fee and its dates."""
    data = _plain_part(transfer_man)
    found: dict[tuple, TransferOffer] = {}
    for pattern, loan in ((_BID, False), (_LOAN, True)):
        for match in pattern.finditer(data):
            person, team, squad, fee = (struct.unpack("<I", g)[0] for g in match.groups())
            if not 0 < team < _MAX_CLUB or not 0 < squad < _MAX_CLUB or not 0 < person < _MAX_PERSON or fee >= _MAX_FEE:
                continue
            tail = data[match.end():match.end() + _BID_DATES_WITHIN]
            if len(tail) <= _BID_STATUS:
                continue
            dates = sorted({when for i in range(len(tail) - 3) if (when := decode_match_date(tail[i:i + 4])) and _FIRST_YEAR <= when.year <= _LAST_YEAR})
            guaranteed = extra = 0
            clauses = _BID_CLAUSES.match(data, match.start() + _BID_CLAUSES_AT) if not loan else None
            if clauses:
                body = clauses.group(1)
                for k in range(0, len(body), 8):
                    amount = struct.unpack_from("<I", body, k + 4)[0]
                    if body[k:k + 2] == _GUARANTEED:
                        guaranteed += amount
                    else:
                        extra += amount
            offer = TransferOffer(person, team, squad, 0 if loan else fee, tail[_BID_STATUS], dates[0] if dates else None,
                                  dates[1] if len(dates) > 1 else None, dates[-1] if dates else None, loan, fee if loan else None,
                                  None if loan else fee + guaranteed, None if loan else fee + guaranteed + extra)
            found.setdefault((person, team, offer.fee, offer.offered_on, loan), offer)
    return _mark_counters(list(found.values()))


def _mark_counters(offers: list[TransferOffer]) -> list[TransferOffer]:
    """A counter-proposal is a second record of a bid: the same bidder, player and day, another fee, status 00 and blank
    answer dates (the game's 1 Jan 1900, so one date is read). Chelsea asked Juventus for £165m for Valera on their £71.5m
    bid of 18 Jan 2064; Juventus withdrew (26 Jan 2064). A revised bid has dates of its own and stays a bid."""
    bids = {(o.person, o.team, o.offered_on) for o in offers if not o.loan and o.until is not None}
    return [replace(o, counter=True) if not o.loan and o.status == 0 and o.until is None and (o.person, o.team, o.offered_on) in bids
            else o for o in offers]


# -- appearance and nationality -------------------------------------------------------------------
# Single bytes after a player's marked birth date (found with a controlled edit: Ken Myers,
# hair Black 1 -> Red 1, Medium -> Long, English -> Brazilian; ethnicity by its spread over nations).
_ETHNICITY_OFFSET, _HAIR_COLOUR_OFFSET, _HAIR_LENGTH_OFFSET, _SKIN_TONE_OFFSET, _NATION_OFFSET = 5, 6, 7, 8, 9
ETHNICITIES = {
    0: "Northern European", 1: "Mediterranean/Hispanic", 2: "North African/Middle Eastern", 3: "African/Caribbean",
    4: "South Asian", 5: "South East Asian", 6: "Pacific Islander", 7: "Native American", 8: "Native Australian",
    9: "Mixed race", 10: "East Asian",
}
HAIR_LENGTHS = {0: "bald", 1: "short", 2: "medium", 3: "long"}
# The game's hair colour codes as the editor names them (Billy Laing, Kevin Stokes, Aidan Mellon, Marcelo
# Formicola, Robert Mocioacă and Roberto Ştefănescu): 3 and 6 are both "Dark Brown 1" there. The others by
# which people have them: 1, 7 and 8 only Northern Europeans (blond), 9 and 10 with Light Brown 1, 13 and
# 14 rare like Red 1, 16 with the blacks, 12, 17 and 18 half Northern and half Mediterranean like the
# dark browns.
HAIR_COLOURS = {2: "Light Brown 1", 3: "Dark Brown 1", 4: "Red 1", 5: "Black 1", 6: "Dark Brown 1", 11: "Dark Brown 2",
                15: "Black 2"}
HAIR_COLOUR_WORDS = {1: "blond", 7: "blond", 8: "blond", 2: "light brown", 9: "light brown", 10: "light brown",
                     3: "dark brown", 6: "dark brown", 11: "dark brown", 12: "dark brown", 17: "dark brown", 18: "dark brown",
                     4: "red", 13: "red", 14: "red", 5: "black", 15: "black", 16: "black"}
NOT_SET = 255


@dataclass(frozen=True, slots=True)
class Appearance:
    nation_index: int | None  # the nation index (national_team_nations maps it to a nation)
    ethnicity: int | None  # ETHNICITIES
    skin_tone: int | None  # 1-20 when the database sets it (about 1 player in 10); else the game derives it
    hair_colour: int | None  # the game's own code: HAIR_COLOURS, HAIR_COLOUR_WORDS
    hair_length: int | None  # HAIR_LENGTHS


_TEAM_HISTORY_END = -18  # from the person row: the newest entry of the player's team history
_TEAM_HISTORY_ENTRY = 8  # a team (u32) and four more bytes
_TEAM_HISTORY_LOOK = 3  # entries to look back for the newest club


def read_team_histories(game_db: bytes) -> dict[int, tuple[int, ...]]:
    """The newest entries of every player's team history, newest first, by FM uid.

    Before the person row is the player's team history: entries of a team (u32) and four
    more bytes, clubs alternating with the country of the club's league. The newest club is
    the team the player is in now: the loan club during a loan, the parent club again when
    the loan has ended. Use ``current_team`` to pick it. Players without a club have values
    that are not team numbers.
    """
    rows = _person_rows(game_db)
    ordered = sorted((row, uid) for uid, row in rows.values() if row >= 0)
    found: dict[int, tuple[int, ...]] = {}
    for index, (row, uid) in enumerate(ordered):
        low = max(ordered[index - 1][0] + 12 if index else 0, row - _RECORD_REACH)
        birth = _last_marked_birth_date(game_db, low, row)
        if birth is None or row - birth < _MIN_PLAYER_RECORD:
            continue
        found.setdefault(uid, tuple(struct.unpack_from("<I", game_db, row + _TEAM_HISTORY_END - _TEAM_HISTORY_ENTRY * k)[0]
                                    for k in range(_TEAM_HISTORY_LOOK)))
    return found


def current_team(history: tuple[int, ...], teams: dict[int, Team]) -> int | None:
    """The newest club team in a team history (``read_team_histories``), or None."""
    return next((number for number in history if number in teams and not teams[number].national), None)


def read_appearances(game_db: bytes) -> dict[int, Appearance]:
    """Nationality, ethnicity, skin tone and hair of every player with a full record, by FM uid.

    The bytes follow the marked birth date: +5 ethnicity, +6 hair colour, +7 hair length,
    +8 skin tone (255 when not set) and +9 nationality (nation index + 1).
    """
    rows = _person_rows(game_db)
    ordered = sorted((row, uid) for uid, row in rows.values() if row >= 0)
    found: dict[int, Appearance] = {}

    def byte(position: int, limit: int | None = None) -> int | None:
        value = game_db[position]
        return None if value == NOT_SET or (limit is not None and value > limit) else value

    for index, (row, uid) in enumerate(ordered):
        low = max(ordered[index - 1][0] + 12 if index else 0, row - _RECORD_REACH)
        birth = _last_marked_birth_date(game_db, low, row)
        if birth is None or row - birth < _MIN_PLAYER_RECORD:
            continue
        nation = game_db[birth + _NATION_OFFSET]
        found.setdefault(
            uid,
            Appearance(
                nation - 1 if nation else None,
                byte(birth + _ETHNICITY_OFFSET, max(ETHNICITIES)),
                byte(birth + _SKIN_TONE_OFFSET, 20),
                byte(birth + _HAIR_COLOUR_OFFSET),
                byte(birth + _HAIR_LENGTH_OFFSET, max(HAIR_LENGTHS)),
            ),
        )
    return found


# A playing player's reputations follow a row of their own: a number (u32), the player's FM uid
# minus one, written twice, 02, then home, current and world reputation (u16 each, 0-10 000),
# then the current and the potential ability (u16, 1-200; a negative potential is a range).
# Found with the editor (Ken Myers 8531/9005/6946 -> 2222/1111/3333) and checked against the
# game's scouting screen (Fabio Musumeci 110/191, Anthony Obinna 99/190). Retired players have none.
_REPUTATION_ROW = re.compile(rb"(?=.{4}(.{4})\1\x02)", re.DOTALL)
# A player's morale (1-20) in his reputation row; the game's words for it (the Dynamics table and the player's overview,
# 18 of 18 Chelsea players on 19 October 2063). The words for the other values were not on any screen yet.
_MORALE = 115
# Match sharpness (u16), fatigue (i16) and condition (u16), 0-10 000, in the same row: found with tools/record_mapper.py
# (what changes between two saves) and checked in the game's editor (Maurício, 28 October 2063: 8600, -151, 9050).
_FITNESS = 124
_MAX_FITNESS = 10_000
_MAX_FATIGUE = 500  # -500 fresh, +500 jaded; 49 of 80 140 rows are outside it (another layout): no fitness is read there
# 20-14 and 8 from the user's screens; 13-11 from the FM-24-Match-Harness fixture notes (11 "fairly good", 12 "quite good",
# 13 "good"), which read the same byte in the running game.
MORALE_WORDS = {20: "Perfect", 19: "Exceptional", 18: "Superb", 17: "Excellent", 16: "Extremely Good", 14: "Really Good", 13: "Good",
                12: "Quite Good", 11: "Fairly Good", 8: "Fair"}
_REPUTATION_VALUES = 13
_ABILITY_VALUES = 19
MAX_ABILITY = 200


@dataclass(frozen=True, slots=True)
class Reputation:
    home: int
    current: int
    world: int
    ability: int | None = None  # current ability, 1-200
    potential: int | None = None  # potential ability, 1-200, or -1 to -10 for a range the game has not fixed
    morale: int | None = None  # 1-20 (MORALE_WORDS)
    sharpness: int | None = None  # match sharpness, 0-10 000 (the game shows 8600 as 86%)
    fatigue: int | None = None  # -500 completely fresh .. +500 completely jaded (-151 shows as "Fresh")
    condition: int | None = None  # overall physical condition, 0-10 000


def read_person_reputations(game_db: bytes, uids: Iterable[int]) -> dict[int, Reputation]:
    """Home, current and world reputation, current and potential ability, morale, match sharpness, fatigue and
    condition, of every playing player among ``uids``, by FM uid."""
    known = set(uids)
    found: dict[int, Reputation] = {}
    for match in _REPUTATION_ROW.finditer(game_db):
        uid = struct.unpack("<I", match.group(1))[0] + 1
        if uid not in known or uid in found or match.start() + _REPUTATION_VALUES + 6 > len(game_db):
            continue
        values = struct.unpack_from("<HHH", game_db, match.start() + _REPUTATION_VALUES)
        if max(values) <= MAX_REPUTATION:
            ability, potential = struct.unpack_from("<Hh", game_db, match.start() + _ABILITY_VALUES)
            valid = 1 <= ability <= MAX_ABILITY and (ability <= potential <= MAX_ABILITY or -10 <= potential <= -1)
            morale = game_db[match.start() + _MORALE] if match.start() + _MORALE < len(game_db) else 0
            fitness = (struct.unpack_from("<HhH", game_db, match.start() + _FITNESS) if match.start() + _FITNESS + 6 <= len(game_db)
                       else (None, None, None))
            usable = fitness[0] is not None and fitness[0] <= _MAX_FITNESS and fitness[2] <= _MAX_FITNESS and abs(fitness[1]) <= _MAX_FATIGUE
            sharpness, fatigue, condition = fitness if usable else (None, None, None)
            found[uid] = Reputation(*values, ability if valid else None, potential if valid else None, morale if 1 <= morale <= 20 else None,
                                    sharpness, fatigue, condition)
    return found


# -- staff contracts ------------------------------------------------------------------------------
# A contract in a person's record (before the person row) ends: the person number (u32), the team
# number (u32), 4 zero bytes, a u32, the job code (u8), 00 00. In the usual layout it starts with
# 01 00 6c 07 00 and has, 39 bytes before the person number, the start date (day of year u16, low
# 9 bits, and year u16). Found with the editor (Rosetta J0/J1: four staff hired at Chelsea, Joe
# Haigh dismissed) and named from the game's staff list.
PLAYER_JOB = 1
JOB_NAMES = {
    PLAYER_JOB: "Player", 2: "Coach", 10: "Director of Football", 12: "Physio", 14: "Scout", 16: "Manager",
    20: "Assistant Manager", 22: "Set Piece Coach", 26: "Fitness Coach", 34: "Goalkeeping Coach",
    38: "Chief Doctor", 40: "Head of Sports Science", 44: "Chief Scout", 48: "Sports Scientist", 50: "Head Physio",
    58: "Recruitment Analyst", 60: "Performance Analyst", 62: "Head Performance Analyst",
    64: "Head of Youth Development", 86: "Loan Manager", 88: "Technical Director",
}
_CONTRACT = re.compile(rb"(?=(.{4})(.{4})\x00\x00\x00\x00.{4}(.)\x00\x00)", re.DOTALL)
_CONTRACT_USUAL = b"\x01\x00\x6c\x07\x00"
_CONTRACT_START = -39  # from the person number
_CONTRACT_EXPIRES = -43  # the end of the contract, just before the day he joined
_CONTRACT_SIGNED = -24  # the current deal's start; not moved by the concerns (24 Oct 2026: 83 702 of 84 947 players have a past date here)
_CONTRACT_WAGE = 12  # the weekly wage, in the contract after the person number, the team and four zero bytes
_CONTRACT_REACH = 3000  # a contract lies this close before the person row
_DAY_BITS = 0x1FF


# Before a person's contract (the person number at P): a block B = P - 25 that starts 00, then the date of the contract
# (u16 day of year, u16 year), the overall happiness (i8, -100..100) at B+5, and a date at B+20 (usually none). Just before
# B, a u32 count and the person's dated concerns (29-byte entries); between B and the person row, his feelings about
# people, clubs and nations (16-byte entries: FF, the subject u32, 4 zero bytes, the reason u16, the kind, 1 likes or
# 2 dislikes, the level 0-100). Found with the game's player panels and Dynamics table (19 October 2063):
# happiness 17 of 17, the concerns named below, the favoured personnel 30 of 30.
_MOOD_BLOCK = -25  # from the person number
_HAPPINESS = 5
_CONCERN_SIZE, _RELATION_SIZE = 29, 16
_RELATION = re.compile(rb"(?:\xff.{4}\x00{4}.\x00.{5})+", re.DOTALL)
HAPPINESS_WORDS = ((80, "Delighted"), (60, "Very Happy"), (40, "Happy"))  # the lower words not seen yet
# A concern's (kind, code) and the game's words; ref is a person, a competition key or a nation index + 1. Its stage
# byte: 2 when he is complaining to team-mates, 3 and 4 seen when the game says "(In public domain)" (not exact).
LEFT_OUT, TREATED_UNFAIRLY, MOVE_ON, MORE_GAMES, RETURN_HOME, NEW_DEAL = (32, 596), (19, 808), (10, 666), (12, 652), (6, 622), (8, 634)
CONCERNS = {LEFT_OUT: "Left out of {competition} squad", TREATED_UNFAIRLY: "Thinks {person} has been treated unfairly",
            MOVE_ON: "Feels it is time to move on for a new challenge", MORE_GAMES: "Wants to start more games",
            RETURN_HOME: "Wants to return to {nation}", NEW_DEAL: "Wants a new deal to reflect his ability"}
CONCERN_COMPETITION = {LEFT_OUT: "ref", TREATED_UNFAIRLY: "ref2"}  # where the competition key stands
COMPLAINING = 2  # the stage of a concern he has taken to his team-mates
RELATION_REASONS = {2: "family", 5: "team-mate", 6: "manager", 7: "a player he managed", 8: "backroom staff", 9: "idol", 10: "friend",
                    11: "a coach who brought him through"}  # 2, 7 and 11 read from the people they join (26 Oct 2026)
RELATION_PERSON = 3
FAVOURED_LEVEL = 50  # the game lists a liked person from this level on


CONCERN_ABOUT_PERSON = (19, 20)  # the kinds whose ref is a person (a team-mate, the manager)


@dataclass(frozen=True, slots=True)
class Concern:
    kind: int
    code: int
    stage: int
    ref: int | None  # a person number, a competition key or a nation index + 1 (CONCERNS); None for none
    ref2: int | None
    started: date | None
    ended: date | None  # None while it lasts
    person: int | None = None  # the FM uid of the person it is about (CONCERN_ABOUT_PERSON)


@dataclass(frozen=True, slots=True)
class Feeling:
    """How a person feels about another person (the game also keeps feelings about clubs and nations: not read)."""
    person: int  # FM uid
    reason: int  # RELATION_REASONS
    likes: bool  # False for a dislike
    level: int  # 0-100


def _packed_date(game_db: bytes, at: int) -> date | None:
    day, year = struct.unpack_from("<HH", game_db, at)
    return _date_from_day_of_year(day & _DAY_BITS, year)


def _null(value: int) -> int | None:
    return None if value == 0xFFFFFFFF else value


def _concerns(game_db: bytes, block: int, uid_of: dict[int, tuple[int, int]]) -> list[Concern]:
    """The dated concerns that end at the mood block (the longest run whose count stands before it)."""
    for count in range(60, 0, -1):
        start = block - _CONCERN_SIZE * count
        if start < 4 or struct.unpack_from("<I", game_db, start - 4)[0] != count:
            continue
        if all(game_db[start + _CONCERN_SIZE * i] == 7 for i in range(count)):
            found = []
            for i in range(count):
                at = start + _CONCERN_SIZE * i
                code, = struct.unpack_from("<H", game_db, at + 2)
                ref, ref2 = struct.unpack_from("<II", game_db, at + 5)
                ended, kind = _packed_date(game_db, at + 17), game_db[at + 1]
                person = uid_of[ref][0] if kind in CONCERN_ABOUT_PERSON and ref in uid_of else None
                found.append(Concern(kind, code, game_db[at + 4], _null(ref), _null(ref2), _packed_date(game_db, at + 13),
                                     ended if ended and ended.year > 1900 else None, person))
            return found
    return []


def _feelings(game_db: bytes, block: int, row: int, uid_of: dict[int, tuple[int, int]]) -> list[Feeling]:
    """The longest run of 16-byte feelings between the mood block and the person row."""
    runs = [m for m in _RELATION.finditer(game_db, block, row) if (m.end() - m.start()) % _RELATION_SIZE == 0]
    if not runs:
        return []
    run = max(runs, key=lambda m: m.end() - m.start())
    found = []
    for at in range(run.start(), run.end(), _RELATION_SIZE):
        subject, = struct.unpack_from("<I", game_db, at + 1)
        if game_db[at + 11] == RELATION_PERSON and game_db[at + 12] in (1, 2) and subject in uid_of:  # 1 likes, 2 dislikes
            reason, = struct.unpack_from("<H", game_db, at + 9)
            found.append(Feeling(uid_of[subject][0], reason, game_db[at + 12] == 1, game_db[at + 13]))
    return found


@dataclass(frozen=True, slots=True)
class StaffContract:
    team: int  # team number
    job: int  # JOB_NAMES
    started: date | None = None  # the day he joined the club (Júnior 2050, Jimenez 2045 from the academy)
    wage: int | None = None  # pounds a week, exact (the game rounds it on screen: 7 087 shows as £7,000)
    expires: date | None = None
    happiness: int | None = None  # -100..100 (HAPPINESS_WORDS)
    concerns: tuple[Concern, ...] = ()
    feelings: tuple[Feeling, ...] = ()
    signed: date | None = None  # the current deal's start, the Contract screen's "Started" (Júnior 5 Jul 2062, Rogério José 16 Jun 2063)

    @property
    def job_name(self) -> str:
        return JOB_NAMES[self.job]


# -- staff profiles ----------------------------------------------------------------------------------------------------
# A staff member's coaching block lies in his record before his person row (1.7-2.5 kB before it for most; much further
# for the human manager, whose record holds his history). A, the place of the Attacking byte, is found by the block's
# shape: A-35 is 10, A-34..A-22 and A-20..A-9 are tactical preferences (1-20), A-21 and A-8..A+17 attributes (0-100),
# the four formation bytes at A-39..A-36 at most 70, reputations (u16 x3) at A-50, ability (u16) and potential (i16)
# at A-44. Verified on four managers' profile screens (Garcia, Beutel, Pladevall, Dimario, October 2063): the coaching
# attributes, the formations and the tactical style; found for 2 788 of 2 804 managers.
_STAFF_BLOCK = re.compile(rb"(?=\x0a[\x01-\x14]{13}[\x00-\x64][\x01-\x14]{12}[\x00-\x64]{26})")
_STAFF_BLOCK_AT = 35  # from the match to A
_STAFF_REACH, _STAFF_LONG_REACH = 10_000, 30_000
STAFF_COACHING = {"attacking": 0, "defending": 1, "fitness": 2, "mental": 3, "technical": 4, "tactical": 5, "set_pieces": 17}
_STAFF_WORKING_WITH_YOUNGSTERS = -22  # stored 1-20
_STAFF_FORMATIONS = (-39, -38, -37, -36)  # preferred, second (unsure), and the defensive and attacking ones (in an unknown order)
_STAFF_STYLE = 47  # 255 when not stored (most managers)
_STAFF_MENTALITY, _STAFF_PRESSING = -10, -24  # 1-20 preferences; NOT the game's mentality or pressing words (ten screens
# disagree: Very Cautious 8 and Adventurous 9), which it seems to work out from several preferences
# The game's names of its formation numbers and tactical styles, as far as its screens have shown them.
# Checked on ten managers' profile screens (October 2063); the second stored formation is not shown by the game.
FORMATION_NAMES = {3: "4-4-2", 4: "4-3-3 DM Wide", 29: "4-2-3-1 DM AM Wide", 35: "5-2-3 DM Wide", 64: "5-2-1-2 DM AM"}
TACTICAL_STYLES = {0: "Control Possession", 1: "Gegenpress", 3: "Vertical Tiki-Taka", 4: "Wing Play", 5: "Route One", 9: "Park the Bus"}
COACHING_STYLES = ("attacking", "defending", "fitness", "mental", "technical", "tactical")


@dataclass(frozen=True, slots=True)
class StaffProfile:
    coaching: dict[str, int]  # 1-20, as the game shows them
    working_with_youngsters: int
    formations: tuple[int, int, int, int]  # the game's formation numbers (FORMATION_NAMES); 0 for none
    tactical_style: int | None  # TACTICAL_STYLES; None when not stored
    mentality: int  # 1-20 (probable)
    pressing: int  # 1-20 (probable)

    @property
    def coaching_style(self) -> str:
        """The game shows the highest of the six coaching attributes as the coaching style (true for all four checked)."""
        return max(COACHING_STYLES, key=lambda name: self.coaching[name]).capitalize()


def _staff_block_at(game_db: bytes, at: int) -> bool:
    if at < 60 or game_db[at - 35] != 10 or game_db[at - 40] not in (0x10, 0):
        return False
    if max(struct.unpack_from("<HHH", game_db, at - 50)) > MAX_REPUTATION:
        return False
    ability, potential = struct.unpack_from("<Hh", game_db, at - 44)
    if not (1 <= ability <= 200 and (1 <= potential <= 200 or -10 <= potential <= -1)):
        return False
    return (all(game_db[at + i] <= 70 for i in range(-39, -35)) and all(1 <= game_db[at + i] <= 20 for i in [*range(-34, -21), *range(-20, -8)])
            and all(game_db[at + i] <= 100 for i in [-21, *range(-8, 18)]))


def read_staff_profile(game_db: bytes, row: int) -> StaffProfile | None:
    """The coaching profile in the record whose person row is at ``row`` (see the notes above), or None."""
    for reach in (_STAFF_REACH, _STAFF_LONG_REACH):
        found = [m.start() + _STAFF_BLOCK_AT for m in _STAFF_BLOCK.finditer(game_db, max(0, row - reach), row)]
        found = [at for at in found if _staff_block_at(game_db, at)]
        if found:
            at = found[-1]
            style = game_db[at + _STAFF_STYLE]
            return StaffProfile({name: display_value(game_db[at + offset]) for name, offset in STAFF_COACHING.items()},
                                game_db[at + _STAFF_WORKING_WITH_YOUNGSTERS], tuple(game_db[at + o] for o in _STAFF_FORMATIONS),
                                None if style == 255 else style, game_db[at + _STAFF_MENTALITY], game_db[at + _STAFF_PRESSING])
    return None


def read_staff_profiles(game_db: bytes, contracts: dict[int, StaffContract], jobs: tuple[int, ...] = (16,),
                        person_rows: dict[int, tuple[int, int]] | None = None) -> dict[int, StaffProfile]:
    """The coaching profiles of the staff with one of ``jobs`` (managers by default), by FM uid."""
    rows = {uid: row for uid, row in (person_rows if person_rows is not None else _person_rows(game_db)).values()}
    found = {}
    for uid, contract in contracts.items():
        if contract.job in jobs and uid in rows:
            profile = read_staff_profile(game_db, rows[uid])
            if profile:
                found[uid] = profile
    return found


# -- the human club's partnerships --------------------------------------------------------------------------------------
# The pairs of players the game draws arcs between on the human club's tactics screen ("X and Y have a strong partnership
# and play well together in their selected roles"). Only the human club has the table (AI clubs keep none): a u16 count,
# then 25-byte records. Verified against the match reports, match by match, between two saves (19 of 19 records): a
# match counts when the two were on the pitch together for about an hour (62 minutes did, 58 did not); the game prunes
# small records over time. Which pairs get an arc is not known (the roles in the record, probably).
_PARTNERSHIP = re.compile(rb"(?=\x03[\s\S]{16}\x00[\x00\x01]\x03[\s\S]{2}\x03[\s\S]{2})")
_PARTNERSHIP_SIZE = 25


@dataclass(frozen=True, slots=True)
class Partnership:
    person_a: int  # the save's person numbers
    person_b: int
    rating_sum: int  # per match, floor(5 x (rating A + rating B))
    goals_for: int
    goals_against: int
    matches: int
    departed: bool  # one of the two has left the club
    role_a: int  # the roles they played in (codes not named)
    role_b: int

    @property
    def average_rating(self) -> float:
        return round(self.rating_sum / self.matches / 10, 2) if self.matches else 0.0


def read_partnerships(game_db: bytes) -> list[Partnership]:
    """The human club's partnership table (the largest run of records preceded by its count)."""
    starts = {m.start() for m in _PARTNERSHIP.finditer(game_db)}
    best: tuple[int, int] | None = None
    for start in sorted(starts):
        if start - _PARTNERSHIP_SIZE in starts:
            continue
        count = 0
        while start + _PARTNERSHIP_SIZE * count in starts:
            count += 1
        if count >= 2 and struct.unpack_from("<H", game_db, start - 2)[0] == count and (best is None or count > best[1]):
            best = (start, count)
    if best is None:
        return []
    found = []
    for i in range(best[1]):
        at = best[0] + _PARTNERSHIP_SIZE * i
        a, b, total, scored, conceded, matches = struct.unpack_from("<IIHHHH", game_db, at + 1)
        found.append(Partnership(a, b, total, scored, conceded, matches, game_db[at + 18] == 1,
                                 struct.unpack_from("<H", game_db, at + 20)[0], struct.unpack_from("<H", game_db, at + 23)[0]))
    return found


def read_staff_contracts(game_db: bytes, teams: dict[int, Team], rows: dict[int, tuple[int, int]] | None = None) -> dict[int, StaffContract]:
    """Each person's current contract (players and staff), by FM uid (``rows``: _person_rows, when already read)."""
    rows = rows if rows is not None else _person_rows(game_db)
    found: dict[int, StaffContract] = {}
    for match in _CONTRACT.finditer(game_db):
        number, team = struct.unpack("<II", match.group(1) + match.group(2))
        job = match.group(3)[0]
        if job not in JOB_NAMES or team not in teams or number not in rows:
            continue
        uid, row = rows[number]
        if uid in found or not 0 < row - match.start() < _CONTRACT_REACH:
            continue
        started = expires = happiness = None
        concerns: list[Concern] = []
        feelings: list[Feeling] = []
        block = match.start() + _MOOD_BLOCK
        if block > 0 and game_db[block] == 0:
            happiness = struct.unpack_from("<b", game_db, block + _HAPPINESS)[0]
            concerns, feelings = _concerns(game_db, block, rows), _feelings(game_db, block, row, rows)
        if game_db[match.start() - len(_CONTRACT_USUAL):match.start() - 1] == _CONTRACT_USUAL[:-1]:
            # the dates stand before the concerns: a player with concerns has them further back. The marker's last byte
            # is a count (0 for most; Júnior 1, 24 Oct 2063) that moves nothing: the dates sit at the same place.
            shift = -_CONCERN_SIZE * len(concerns)
            day, year = struct.unpack_from("<HH", game_db, match.start() + _CONTRACT_START + shift)
            started = _date_from_day_of_year(day & _DAY_BITS, year)
            day, year = struct.unpack_from("<HH", game_db, match.start() + _CONTRACT_EXPIRES + shift)
            expires = _date_from_day_of_year(day & _DAY_BITS, year)
            if started and expires and expires < started:  # not a contract's pair of dates
                started = expires = None
            started = started if started and started.year > 1950 else None  # 1 January 1900 is the game's "no date"
            expires = expires if expires and expires.year > 1950 else None
        signed = None
        if game_db[match.start() - 5:match.start() - 1] == _CONTRACT_USUAL[:-1]:
            day, year = struct.unpack_from("<HH", game_db, match.start() + _CONTRACT_SIGNED)
            signed = _date_from_day_of_year(day & _DAY_BITS, year)
            if signed and (signed.year <= 1950 or (expires and signed > expires)):
                signed = None
        wage = struct.unpack_from("<I", game_db, match.start() + _CONTRACT_WAGE)[0]
        found[uid] = StaffContract(team, job, started, wage if wage < 10_000_000 else None, expires, happiness,
                                   tuple(concerns), tuple(feelings), signed)
    return found


# The human club's social groups (the Dynamics screen), in its manager's record: 01, the team number (u32), then per group a
# u16 count and the members' person numbers (u32). Found for Chelsea on 19 October 2063 (the game's groups, in its order).
_MAX_GROUP = 40
_MIN_SQUAD = 11


def read_social_groups(game_db: bytes, team: int, rows: dict[int, tuple[int, int]] | None = None) -> list[list[int]]:
    """The social groups of a team (only the human club has them), as lists of FM uids; empty when none is found."""
    rows = rows if rows is not None else _person_rows(game_db)
    for match in re.finditer(re.escape(b"\x01" + struct.pack("<I", team)), game_db):
        at, groups = match.end(), []
        while len(groups) < 6 and at + 2 <= len(game_db):
            count, = struct.unpack_from("<H", game_db, at)
            if not 0 < count < _MAX_GROUP or at + 2 + 4 * count > len(game_db):
                break
            members = struct.unpack_from(f"<{count}I", game_db, at + 2)
            if not all(number in rows for number in members):
                break
            groups.append([rows[number][0] for number in members])
            at += 2 + 4 * count
        if sum(map(len, groups)) >= _MIN_SQUAD:
            return groups
    return []


# -- manager history ------------------------------------------------------------------------------
# tc_manager_history: one list per team of its managers' spells since the game began (and the spell that
# ended then), oldest first. A 31-byte record: person number, start and end dates (u16 day of year in the
# low 9 bits, u16 year), 8 bytes, games won, drawn and lost (u16 each), the reason for leaving (a byte)
# and the league titles (a byte), the cup wins (a byte). The lists do not name their team (the game keeps
# that elsewhere): manager_history.link_lists finds it from the results.
MANAGER_RECORD = 31
MANAGER_LEAVING = {0: "Resigned", 2: "Sacked", 3: "Left manager role", 5: "Left interim manager role",
                   6: "Retired from management", 10: "Left caretaker position"}
CARETAKER_LEFT = 10


@dataclass(frozen=True, slots=True)
class ManagerSpell:
    person: int  # person number
    started: date | None
    ended: date | None
    won: int
    drawn: int
    lost: int
    reason: int  # MANAGER_LEAVING
    league_wins: int
    cup_wins: int


def read_manager_history(dt: bytes, ls: bytes) -> list[list[ManagerSpell]]:
    """Every team's list of manager spells (``tc_manager_history_dt.cmt`` and ``_ls.dat``), by list number."""
    found = []
    for offsets in _stat_groups(ls):
        spells = []
        for offset in offsets:
            at = _STATS_HEADER + offset
            if offset % MANAGER_RECORD or at + MANAGER_RECORD > len(dt):
                continue
            person, start_day, start_year, end_day, end_year = struct.unpack_from("<IHHHH", dt, at)
            won, drawn, lost, reason, titles, cups = struct.unpack_from("<HHHBBB", dt, at + 20)
            spells.append(ManagerSpell(person, _date_from_day_of_year(start_day & _DAY_BITS, start_year),
                                       _date_from_day_of_year(end_day & _DAY_BITS, end_year), won, drawn, lost, reason, titles, cups))
        found.append(spells)
    return found


# -- injuries -------------------------------------------------------------------------------------
# The game's injuries are section 5 of the English block of ``lang_db.dat``: per injury its number, its name and the
# name with an article ("twisted ankle", "a twisted ankle"), in lower case. The save names an injury by its POSITION
# in that list (0 = cold), as it names competitions by key. The first five are illnesses.
_INJURY_SECTION = 5
ILLNESSES = 5  # cold, flu, virus, food poisoning, serious viral infection: no match, no training
# Codes checked on the game's injury history screens (Ibrahim Ramadan, Kemal Aydın); the rarer ones are unknown.
INJURY_CAUSES = {1: "Impact from player", 2: "Impact from fall", 4: "Turning sharply", 5: "Sprinting", 6: "Jumping",
                 11: "Tackling", 12: "Being tackled", 17: "Innocuously"}
INJURY_TREATMENTS = {1: "Physio", 2: "Specialist", 32: "Club Doctor"}
NOT_IN_MATCH = 3  # the kind of an injury in training (or an illness); 1 league, 2 other club match, 4 international
_INJURY_RECORD = 16
_NO_TEAM = 0xFFFFFFFF


@dataclass(frozen=True)
class Injury:
    """One injury: what, when, for how long, how and where. ``days`` is the time out; for a current injury, the
    expected time out from the day it happened. ``team`` is the player's team number in the match (none in training)."""

    injury: str
    injured_on: date | None
    days: int
    cause: int
    treatment: int
    kind: int
    team: int | None = None

    @property
    def severity(self) -> str:
        """As the game labels it, by the time out (checked on 30 injuries of the game's screens)."""
        return "Slight" if self.days <= 3 else "Minor" if self.days <= 8 else "Moderate" if self.days <= 29 else "Major"

    @property
    def place(self) -> str | None:
        """"In match", "In training", or nothing for an illness."""
        if self.team is not None:
            return "In match"
        return "In training" if self.kind == NOT_IN_MATCH and self.cause else None


def read_injury_names(lang_db: str | Path) -> list[str]:
    """The game's injuries in their order, by which the save names them: "cold", "flu", ... """
    with open(lang_db, "rb") as handle:
        raw = handle.read()
    data = _decompress_prefix(raw, _DB_SECTION, occurrences=_INJURY_SECTION + 2)
    section = -1
    for _ in range(_INJURY_SECTION + 1):
        section = data.find(_DB_SECTION, section + 1)
        if section < 0:
            raise FmSaveError(f"{lang_db} does not have the expected sections.")
    count = struct.unpack_from("<I", data, section + 8)[0]
    position = section + _DB_SECTION_HEADER
    names = []
    for _ in range(count):
        position += 4  # the injury's number
        texts = []
        for _ in range(2):
            length = struct.unpack_from("<I", data, position)[0]
            texts.append(data[position + 4: position + 4 + length].decode("utf-8", "replace"))
            position += 4 + length + 1
        position += 1
        names.append(texts[0])
    return names


def read_injury_history(dt: bytes, ls: bytes, names: list[str]) -> list[list[Injury]]:
    """Every player's past injuries, by group number (``read_injury_groups``), oldest first.

    ``injury_histories_dt.cmt`` has an 8-byte header and 16-byte records: injury (u16, position in
    ``read_injury_names``), the player's team number in the match (u32, ffffffff in training), kind (byte 6), a byte,
    the date (u16 day of the year in the low 9 bits, u16 year), the days out (u16), the cause (byte 14) and the
    treatment (byte 15). ``injury_histories_ls.dat`` groups them as the player statistics are (``_stat_groups``).
    The current injuries are not in it (``read_current_injuries``).
    """
    found = []
    for offsets in _stat_groups(ls):
        injuries = []
        for offset in offsets:
            at = _STATS_HEADER + offset
            if offset % _INJURY_RECORD or at + _INJURY_RECORD > len(dt):
                continue
            kind_of, team = struct.unpack_from("<HI", dt, at)
            day, year, days = struct.unpack_from("<HHH", dt, at + 8)
            if kind_of >= len(names):
                continue
            injuries.append(Injury(names[kind_of], _date_from_day_of_year(day & _DAY_BITS, year), days, dt[at + 14], dt[at + 15],
                                   dt[at + 6], None if team == _NO_TEAM else team))
        found.append(sorted(injuries, key=lambda i: i.injured_on or date.min))
    return found


# A player record's contract part: a date, the club's team number, the same number again (or ffffffff), two dates,
# two bytes, the injury group number (ffffffff for none), then 03 00 00 00 01. It lies 300 to 3,000 bytes before the
# player's person row; other short person rows can come between (Ibrahim Ramadan, Kemal Aydın).
_INJURY_ANCHOR = re.compile(rb"(.{3}[\x07\x08])(.{4})(.{4})(.{3}[\x07\x08])(.{3}[\x07\x08])(.{2})(.{4})\x03\x00\x00\x00\x01", re.DOTALL)
_ANCHOR_REACH = (300, 3_000)


def _injury_anchors(game_db: bytes) -> list[tuple[int, int, int | None]]:
    """(position, FM uid, injury group or None) of every player record's contract part."""
    ordered = sorted((row, uid) for uid, row in _person_rows(game_db).values() if row >= 0)
    rows = [row for row, _ in ordered]
    anchors = []
    for match in _INJURY_ANCHOR.finditer(game_db):
        if match.group(3) != match.group(2) and match.group(3) != b"\xff" * 4:
            continue
        k = bisect.bisect_left(rows, match.start() + _ANCHOR_REACH[0])
        if k < len(rows) and rows[k] - match.start() < _ANCHOR_REACH[1]:
            group = struct.unpack("<I", match.group(7))[0]
            anchors.append((match.start(), ordered[k][1], None if group == _NO_TEAM else group))
    return anchors


def read_injury_groups(game_db: bytes, anchors: list | None = None) -> dict[int, int]:
    """Each player's injury history group, by FM uid (43,573 players in an August 2063 save; Ibrahim Ramadan 43718)."""
    claims: dict[int, set[int]] = {}
    for _, uid, group in anchors if anchors is not None else _injury_anchors(game_db):
        if group is not None:
            claims.setdefault(uid, set()).add(group)
    return {uid: next(iter(groups)) for uid, groups in claims.items() if len(groups) == 1}


# A current injury in the player record, before its contract part: 01 02, the injury (u16), then in a match 01 01, the
# date, the competition's stage, 2 bytes, the team, 00, the opponent, 2 bytes, the fixture id; in training only 00;
# then the date, a u16, the expected days out, the kind, the treatment, 00, a person, 00, the team, a byte, ffffffff,
# 00, the cause (u16), ffff. Checked on the 29 Premier Division injuries of 11 Aug 2063 (the game's injury table).
_CURRENT_INJURY = re.compile(
    rb"\x01\x02(?P<injury>.{2})(?:\x00|\x01\x01.{4}.{4}.{2}.{4}\x00(?P<opponent>.{4}).{2}.{4})"
    rb"(?P<date>.{3}\x08).{2}(?P<days>.{2})(?P<kind>[\x01-\x04])(?P<treatment>.)\x00.{4}\x00(?P<team>.{4}).\xff{4}\x00(?P<cause>.{2})\xff\xff",
    re.DOTALL)
_CURRENT_REACH = 40_000  # from a current injury to its player's contract part


def read_current_injuries(game_db: bytes, names: list[str], anchors: list | None = None) -> dict[int, list[Injury]]:
    """The injuries players have now, by FM uid (about 2,600 players in an August 2063 save)."""
    anchors = anchors if anchors is not None else _injury_anchors(game_db)
    positions = [position for position, _, _ in anchors]
    found: dict[int, list[Injury]] = {}
    for match in _CURRENT_INJURY.finditer(game_db):
        injury = struct.unpack("<H", match["injury"])[0]
        k = bisect.bisect_left(positions, match.end())
        if injury >= len(names) or k >= len(anchors) or positions[k] - match.start() > _CURRENT_REACH:
            continue
        team = struct.unpack("<I", match["team"])[0]
        found.setdefault(anchors[k][1], []).append(Injury(
            names[injury], _game_date(match["date"], 0), struct.unpack("<H", match["days"])[0], struct.unpack("<H", match["cause"])[0],
            match["treatment"][0], match["kind"][0], team if match["opponent"] and team != _NO_TEAM else None))
    return found


# -- match reports --------------------------------------------------------------------------------
_MATCH_TEAMS = re.compile(rb"\x02\x02(.{4})\x02.{4}[\x00-\x1f]\x00[\x00\x01].{8}\x02\x02(.{4})", re.DOTALL)
# a club's part has 01 and its club index after a squad-kind byte and a zero; a national team's has 00 and its nation
# index: the game keeps full reports of internationals too (Ukraine 0-3 Romania, 17 Nov 2063, unread until 24 Oct 2026).
# The squad kind is 0 for a first team and 1, 9, 10, 11, 12 or 18 for youth and reserve sides (U21, U19, U18, U20
# leagues and their internationals): 152 of 572 blocks in one save, unread until 24 Oct 2026
_REFEREE = re.compile(rb"\x02(.{4})\x02.{4}\x02\xff{4}", re.DOTALL)
_MATCH_HEAD_REACH = 120  # the stadium name and the fixture id lie this close before the teams
_MATCH_PART_REACH = 12_000  # from the teams (or the home players' end) to the next team part
_MATCH_PLAYERS_REACH = 1_200  # from a team part to its first player record
_POM_BEFORE = 14  # the Player of the Match block starts this far before the home part
_MP_SIZE = 200  # with no shots and no items
_MP_SHOT_HEAD, _MP_SHOT_PART = 3, 15
_MP_TAIL, _MP_ITEM = 5, 11
_MP_SHOT_COUNT = 194
_MP_MANAGER = 14  # ``02`` manager (u32) + 8 bytes before the first record
_MAX_MINUTE = 130
_MAX_SQUAD = 60
# Offsets in a player record, counted from its person number (verified on the game's screens).
_MP = {
    "goals": 19, "shots": 40, "shots_on_target": 41, "assists": 55, "clear_cut_chances": 56, "dribbles": 57,
    "fouls": 59, "yellow_cards": 62, "red_cards": 63, "minute_off": 66, "added_time_off": 68, "minute_on": 70, "injury_minute": 71,
    "booked_minute": 72, "sent_off_minute": 73,
    "passes": 85, "passes_completed": 86, "key_passes": 87, "crosses": 88, "crosses_completed": 89,
    "tackles": 91, "tackles_won": 92, "key_tackles": 93, "headers": 94, "headers_won": 95, "key_headers": 96,
    "interceptions": 97, "clearances": 139,
    # Goals from outside the box: the squad screen's "Goals Outside Box" column summed over the season's reports (Chelsea,
    # 10 Nov 2063: six of seven players exact, the seventh's match missing from the save); 134 of 940 goals in one save
    # (14%), never a goalkeeper's, never above the record's goals in 99.9% of 14 466 records.
    "goals_outside_box": 165,
    # Mistakes leading to a goal (the engine's analysis event 49): the three players the game's reports blamed on 10 Nov 2063
    # and nobody else in 160 records; never above the goals conceded on 696 of 696 sides; behind 147 of 939 goals (16%).
    "mistakes_leading_to_goal": 130,
    # From the engine's GAME_MATCH_PLAYER_STATS layout (FM-24-Match-Harness, match_stats.c), where shots sit at 132 against
    # our 40: a shift of 92 holds for 20 (goals conceded: the keeper's, on 686 of 696 sides), 42 (the player's shots that
    # were blocked: blocked + on target <= shots in 14 462 of 14 514 records), 43 (long shots), 45 (half chances) and
    # 49-51 (saves held, parried, tipped: keepers only; their sum is the opponent's shots on target less goals on 548 of
    # 696 sides). 166 pairs with 165: shots from outside the box.
    "goals_conceded": 20, "blocked_shots": 42, "long_shots": 43, "half_chances": 45, "saves_held": 49, "saves_parried": 50,
    "saves_tipped": 51, "shots_outside_box": 166,
}
_MP_PARTS = (("shots_on_target", "shots"), ("passes_completed", "passes"), ("crosses_completed", "crosses"),
             ("tackles_won", "tackles"), ("headers_won", "headers"))
_MP_ONLY_WHEN_PLAYED = ("goals", "assists", "shots", "passes", "crosses", "tackles", "headers", "fouls", "interceptions",
                        "clearances", "minute_on", "minute_off")  # any of them: the player was on the pitch
_MP_LOWEST_RATING = 100  # rating x 100
_MP_XG = 24
_MP_SHIRT = 4  # the shirt number
# The slot he filled (a POSITION_NAMES bit, u16): at kick-off, and at the end (or when he went off). A substitute has no kick-off
# slot and the one he took; an unused one has neither. Verified on the game's match screen (Newcastle 1-3 Chelsea, 22 September
# 2063: both formations, every substitute's slot) and on every side of a save (697 of 697: one slot each, one goalkeeper).
_MP_START_SLOT, _MP_END_SLOT = 7, 109
_MP_RATING = 122  # u16, rating x 100
_MP_DISTANCE = 126  # f32, metres
# The team block, counted from the team's passes (u16 attempted, then completed).
_MT = {"clear_cut_chances": -31, "woodwork": -29, "corners": -19, "fouls": -16, "yellow_cards": -14, "red_cards": -13}
_MT_TACKLES = 6  # then tackles won, headers, headers won (u16 each)


@dataclass(frozen=True, slots=True)
class MatchPlayer:
    person: int  # the save's person number
    rating: float  # 0 for a substitute who did not play
    distance: float  # metres; 0 in matches where the game did not measure it
    xg: float
    goals: int
    assists: int
    shots: int
    shots_on_target: int
    passes: int
    passes_completed: int
    key_passes: int
    clear_cut_chances: int  # created
    crosses: int
    crosses_completed: int
    dribbles: int
    tackles: int
    tackles_won: int
    key_tackles: int
    interceptions: int
    clearances: int
    headers: int
    headers_won: int
    key_headers: int
    fouls: int
    yellow_cards: int
    booked_minute: int  # 0 when not booked
    minute_on: int  # 0 when he started
    minute_off: int  # 0 when he was not substituted
    added_time_off: int
    injury_minute: int  # 0 when not injured (verified once: Ramadan, Barcelona 0-1 Chelsea, 68')
    red_cards: int  # verified once: Rogério José, Chelsea v A. Madrid, 2 June 2063
    sent_off_minute: int  # 0 when not sent off; the added time is in added_time_off (45+1)
    shot_list: tuple[Shot, ...] = ()  # each shot, in match order
    shirt: int = 0
    start_slot: int = 0  # POSITION_NAMES bits; 0 when he did not start
    end_slot: int = 0  # 0 when he did not play
    goals_outside_box: int = 0  # read since 24 Oct 2026; 0 in older rows
    mistakes_leading_to_goal: int = 0  # read since 24 Oct 2026
    goals_conceded: int = 0  # the keeper's; read since 24 Oct 2026, with the seven below
    blocked_shots: int = 0  # his shots that were blocked
    long_shots: int = 0
    half_chances: int = 0
    saves_held: int = 0
    saves_parried: int = 0
    saves_tipped: int = 0
    shots_outside_box: int = 0

    @property
    def saves(self) -> int:
        return self.saves_held + self.saves_parried + self.saves_tipped

    @property
    def played(self) -> bool:
        """The game does not record the distance in every match, so a rating tells."""
        return self.rating > 0


@dataclass(frozen=True, slots=True)
class Shot:
    """One shot of a player record's shot list: when (the match clock), how it ended (SHOT_OUTCOMES), and where the ball
    crossed the goal line, in metres: across from the goal's centre (the posts at +-3.66) and the height (the bar at 2.44).
    Checked shot by shot on the game's FA Cup final 2063 screens (Liverpool 2-1 Chelsea: every player's goals, misses, woodwork,
    saved and blocked shots; Schwindt's goal at 18:38 shown as 19'), and over a whole save: woodwork shots = the teams'
    woodwork counts on 543 of 543 sides, goal shots = the players' goals."""

    minute: int
    second: int
    outcome: int
    across: float
    height: float
    tick: int = 0  # the match clock the highlight events use (about 4 a second)
    kind: str | None = None  # SHOT_KINDS, from the match's highlight events; None when the game kept no event for it
    range: str | None = None  # SHOT_RANGES: "close" or "outside" (the box), from the same event; None in between or unknown


SHOT_GOAL, SHOT_OFF_TARGET, SHOT_WOODWORK, SHOT_SAVED, SHOT_BLOCKED = 1, 2, 3, 4, 5
SHOT_OUTCOMES = {SHOT_GOAL: "goal", SHOT_OFF_TARGET: "off target", SHOT_WOODWORK: "woodwork", SHOT_SAVED: "saved", SHOT_BLOCKED: "blocked"}


def _match_shots(buffer: bytes, at: int) -> tuple[Shot, ...]:
    """The shot list after a player record's fixed part: a count, then per entry 2 flag bytes, a part count and that many
    15-byte shots (f32 across, f32 height, the outcome, minute, second, 2 bytes, the flags again)."""
    shots, end = [], at + _MP_SHOT_COUNT + 1
    for _ in range(buffer[at + _MP_SHOT_COUNT]):
        parts = buffer[end + _MP_SHOT_HEAD - 1]
        for k in range(parts):
            part = end + _MP_SHOT_HEAD + _MP_SHOT_PART * k
            if part + _MP_SHOT_PART > len(buffer):
                return tuple(shots)
            across, height = struct.unpack_from("<ff", buffer, part)
            if buffer[part + 8] in SHOT_OUTCOMES and math.isfinite(across) and math.isfinite(height):
                shots.append(Shot(buffer[part + 9], buffer[part + 10], buffer[part + 8], round(across, 2), round(height, 2),
                                  struct.unpack_from("<H", buffer, part + 11)[0]))
        end += _MP_SHOT_HEAD + _MP_SHOT_PART * parts
    return tuple(sorted(shots, key=lambda shot: (shot.minute, shot.second)))


@dataclass(frozen=True, slots=True)
class MatchSide:
    team: int  # team number
    squad: int
    players: tuple[MatchPlayer, ...]
    passes: int | None = None  # the team's own totals; None when the team block was not found
    passes_completed: int | None = None
    corners: int | None = None
    woodwork: int | None = None
    clear_cut_chances: int | None = None
    fouls: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None

    def total(self, field: str) -> float:
        return sum(getattr(player, field) for player in self.players)


@dataclass(frozen=True, slots=True)
class MatchReport:
    position: int  # in the joined stream
    fixture_id: int
    stadium: str
    referee: int | None  # person number
    referee_name: str | None
    player_of_the_match: int | None  # person number
    minutes: int  # 90, or 120 after extra time
    home: MatchSide
    away: MatchSide

    def possession(self) -> tuple[int, int] | None:
        """The game's possession figure: each side's share of the completed passes."""
        home, away = (side.passes_completed if side.passes_completed is not None else side.total("passes_completed")
                      for side in (self.home, self.away))
        if not home + away:
            return None
        share = round(100 * home / (home + away))
        return share, 100 - share


def _match_player(buffer: bytes, at: int) -> MatchPlayer | None:
    """The player record whose person number is at ``at``, if it looks like one."""
    if at < 1 or at + _MP_SIZE > len(buffer) or buffer[at - 1] != 2:
        return None
    rating = struct.unpack_from("<H", buffer, at + _MP_RATING)[0]
    distance, xg = struct.unpack_from("<f", buffer, at + _MP_DISTANCE)[0], struct.unpack_from("<f", buffer, at + _MP_XG)[0]
    if not (1 <= rating <= 1000 and math.isfinite(distance) and 0 <= distance < 25_000 and math.isfinite(xg) and 0 <= xg < 10):
        return None
    if max(buffer[at + _MP[field]] for field in ("minute_off", "minute_on", "booked_minute", "injury_minute", "sent_off_minute")) > _MAX_MINUTE:
        return None
    values = {field: buffer[at + offset] for field, offset in _MP.items()}
    if any(values[part] > values[whole] for part, whole in _MP_PARTS):
        return None
    played = distance > 0 or any(values[field] for field in _MP_ONLY_WHEN_PLAYED)
    if played and rating < _MP_LOWEST_RATING:
        return None
    return MatchPlayer(struct.unpack_from("<I", buffer, at)[0], rating / 100 if played else 0.0, distance, xg, **values,
                       shot_list=_match_shots(buffer, at), shirt=buffer[at + _MP_SHIRT],
                       start_slot=struct.unpack_from("<H", buffer, at + _MP_START_SLOT)[0],
                       end_slot=struct.unpack_from("<H", buffer, at + _MP_END_SLOT)[0])


def _match_record_size(buffer: bytes, at: int) -> int:
    """The distance from this record's person number to the next one's.

    After the fixed part comes the shot list: a count at ``_MP_SHOT_COUNT``, then per
    shot 2 flag bytes, a part count and that many 15-byte parts (13 bytes and the flags
    again). Then 3 bytes, a count of 11-byte items, the items, and the next ``02``.
    """
    end = at + _MP_SHOT_COUNT + 1
    for _ in range(buffer[at + _MP_SHOT_COUNT]):
        end += _MP_SHOT_HEAD + _MP_SHOT_PART * buffer[end + _MP_SHOT_HEAD - 1]
    return end + _MP_TAIL + _MP_ITEM * buffer[end + _MP_TAIL - 2] - at


def _match_players(buffer: bytes, first: int, squad: int) -> tuple[list[MatchPlayer], int] | None:
    """``squad`` consecutive player records from ``first``, and the end of the last one."""
    players, at = [], first
    for _ in range(squad):
        player = _match_player(buffer, at)
        if player is None:
            return None
        players.append(player)
        at += _match_record_size(buffer, at)
    return players, at - 1


def _match_team_block(buffer: bytes, side: MatchSide, start: int, end: int) -> int:
    """Where the team block's passes are: found by the players' passes, or (in matches
    where the game keeps other pass totals for the team) by their tackles and headers."""
    passes = struct.pack("<HH", int(side.total("passes")), int(side.total("passes_completed")))
    block = buffer.find(passes, start, end)
    if block >= 0:
        return block
    duels = struct.pack("<HHHH", *(int(side.total(field)) for field in ("tackles", "tackles_won", "headers", "headers_won")))
    block = buffer.find(duels, start, end)
    return block - _MT_TACKLES if block >= 0 else -1


def _match_side(buffer: bytes, start: int, team: int) -> tuple[MatchSide, int] | None:
    """The team part ``02 team squad`` found after ``start``, its players and where it ends."""
    marker = b"\x02" + struct.pack("<I", team)
    at = buffer.find(marker, start, start + _MATCH_PART_REACH)
    while at >= 0:
        squad = buffer[at + 5]
        if 0 < squad <= _MAX_SQUAD:
            for first in range(at + 6, at + _MATCH_PLAYERS_REACH):
                if buffer[first - _MP_MANAGER] != 2 or buffer[first - 1] != 2:
                    continue
                walked = _match_players(buffer, first, squad)
                if walked is None:
                    continue
                players, end = walked
                side = MatchSide(team, squad, tuple(players))
                block = _match_team_block(buffer, side, at, first)
                if block >= 0:
                    passes, completed = struct.unpack_from("<HH", buffer, block)
                    side = MatchSide(team, squad, tuple(players), passes, completed,
                                     **{field: buffer[block + offset] for field, offset in _MT.items()})
                return side, end
        at = buffer.find(marker, at + 1, start + _MATCH_PART_REACH)
    return None


def read_match_reports(match_stats_cfg: bytes) -> list[MatchReport]:
    """Every full match report in ``match_stats_cfg.dat`` (see the module notes)."""
    stream = b"".join(iter_inner_blocks(match_stats_cfg))
    reports = []
    for match in _MATCH_TEAMS.finditer(stream):
        home_team, away_team = struct.unpack("<II", match.group(1) + match.group(2))
        head = stream[max(0, match.start() - _MATCH_HEAD_REACH):match.start()]
        named = next(((length, at) for length in range(1, _MATCH_HEAD_REACH - 9)
                      if (at := len(head) - length - 4) >= 9 and struct.unpack_from("<I", head, at)[0] == length
                      and head[at - 9] == 2), None)
        if named is None:
            continue
        length, at = named
        fixture_id = struct.unpack_from("<I", head, at - 8)[0]
        home = _match_side(stream, match.end(), home_team)
        if home is None:
            continue
        home_side, home_end = home
        away = _match_side(stream, home_end, away_team)
        if away is None:
            continue
        part = stream.find(b"\x02" + struct.pack("<I", home_team), match.end())
        pom_at = part - _POM_BEFORE
        pom = minutes = None
        if stream[pom_at] == 2:
            pom = struct.unpack_from("<I", stream, pom_at + 1)[0]
            minutes = struct.unpack_from("<H", stream, pom_at + 7)[0]
        referee = referee_name = None
        found = _REFEREE.search(stream, match.end(), part)
        if found:
            referee = struct.unpack("<I", found.group(1))[0]
            first, after = _read_string_at(stream, found.end())
            last, _ = _read_string_at(stream, after)
            referee_name = " ".join(name for name in (first, last) if name) or None
        reports.append(MatchReport(match.start(), fixture_id, head[at + 4:].decode("utf-8", "replace"), referee,
                                   referee_name, pom, minutes or 90, home_side, away[0]))
    ends = [r.position for r in reports[1:]] + [len(stream)]
    return [_with_shot_kinds(stream, report, end) for report, end in zip(reports, ends)]


# -- the highlight events of a match report (2 Nov 2026, docs/goal-labels.md) ------------------------------------------------
# After the player records, a report keeps some moments of the match as events: `02 <side> <slot> <code> <tick u16> 00 00
# <minute> 00`, the slot being the player's place in his side's records (836 of 836 goals linked) and the tick the clock of
# the shot records (bytes 11-12 of a shot part), which links a shot event (code 1) to its shot. Byte 10 after the event's
# start is a type: bits 01 foot, 02 head, 04 volley, 20 drive, 80 not known. Checked against the game's own words for
# 25 goals (header 6 of 6, finish 5 of 5, drive 4 of 4, volley 3 of 3); a header never comes from outside the box (0 of 82).
# Only some shots have an event (Marks and Myers, 30 Jan 2064, have none); the value before the type is not the distance.
# The byte after the type tells the range: bit 40 close range (up to about 7.5 m on the game's analysis screen), bit 80 from
# outside the box; neither, between the two (on either side of the box line). Over a save: 510 goals with 40, all by scorers
# whose goals were all inside the box; 28 with 80, all by scorers whose goals were all outside (3 Nov 2026).
_EVENT = re.compile(rb"(?=\x02([\x00\x01])(.)\x01(..)\x00\x00(.)\x00)", re.DOTALL)
SHOT_KINDS = ("header", "volley", "drive", "finish")
SHOT_RANGES = ("close", "outside")


def shot_range(code: int) -> str | None:
    """The range of a shot from the byte after its event's type: close range, from outside the box, or not known."""
    if code & 0x40:
        return "close"
    return "outside" if code & 0x80 else None


def shot_kind(code: int) -> str | None:
    """The kind of shot from an event's type byte (a volley struck hard is a volley)."""
    if code & 0x02:
        return "header"
    if code & 0x04:
        return "volley"
    if code & 0x20:
        return "drive"
    return "finish" if code & 0x01 else None


# A shot event and its shot record carry nearly the same tick: in the save of 4 Nov 2026, 766 goals lie 0-4 ticks from their
# event and 54 lie 5-7 (Júnior's 25-metre drive at Plymouth, 1 Mar 2064: 5), then none until 481. 4 lost those 54.
SHOT_EVENT_TICKS = 7


def _with_shot_kinds(stream: bytes, report: MatchReport, end: int) -> MatchReport:
    kinds: dict[tuple[int, int], list[tuple[int, str, str | None]]] = {}
    for m in _EVENT.finditer(stream, report.position, end):
        tick, minute = struct.unpack("<H", m.group(3))[0], m.group(4)[0]
        kind = shot_kind(stream[m.start() + 14]) if m.start() + 15 < len(stream) else None
        if kind and 1 <= minute <= 125 and abs(tick / 248 - minute) <= 8:
            kinds.setdefault((m.group(1)[0], m.group(2)[0]), []).append((tick, kind, shot_range(stream[m.start() + 15])))
    if not kinds:
        return report

    def side(number: int, team: MatchSide) -> MatchSide:
        players = []
        for slot, player in enumerate(team.players):
            mine = kinds.get((number, slot))
            if mine:
                def event(shot: Shot) -> tuple:
                    near = [(abs(tick - shot.tick), kind, far) for tick, kind, far in mine if abs(tick - shot.tick) <= SHOT_EVENT_TICKS]
                    return min(near)[1:] if near else (None, None)
                player = replace(player, shot_list=tuple(
                    replace(shot, kind=event(shot)[0], range=event(shot)[1]) for shot in player.shot_list))
            players.append(player)
        return replace(team, players=tuple(players))

    return replace(report, home=side(0, report.home), away=side(1, report.away))


def _read_string_at(buffer: bytes, at: int, longest: int = 80) -> tuple[str | None, int]:
    length = struct.unpack_from("<I", buffer, at)[0]
    if not 0 < length <= longest:
        return None, at + 4
    return buffer[at + 4:at + 4 + length].decode("utf-8", "replace"), at + 4 + length


# -- a tracked player's recent games, team by team -------------------------------------------------------------------------
# The game's "Recent games for <team>" screen (Reports > Form): up to 20 games per team a player was in the squad for, kept
# only for players the game tracks (the human's squads, the shortlist: "Add him to a shortlist to track his form"). They
# live in game_db.dat inside each tracked player's data block, before his ``[number][uid][uid]`` person row, as lists:
# ``team u32, count u32``, then records ``01 date opponent u32 competition u32 status u16``. A played record (status 0x0100)
# is 54 bytes from its marker; one for a game he did not play ends at the status (15 bytes): 12 unused substitute, 6, 0, 13,
# 76... (injured, suspended, not in the squad; unnamed). Found on 24 Oct 2026 with saves made before and after an
# international break and verified row by row on Jimenez (England, 20 games from June 2062), San Martin (Spain) and
# Bellingham; the full match reports of the older games were long gone from the save.
_PG_PLAYED = 0x0100
_PG_UNUSED = 0x000c
_PG_RECORD, _PG_SHORT = 54, 15
_PG_MAX = 20
_PG_MARK = re.compile(rb"\x01..[\xd0-\xff]\x07|\x01..[\x00-\x40]\x08", re.DOTALL)  # a marker and a year 2000-2112
PLAYER_GAME_STATUS = {_PG_PLAYED: "played", _PG_UNUSED: "unused", 5: "suspended", 6: "injured"}  # the rest are unnamed
# Offsets from the record's marker, verified against the screens. For a goalkeeper the dribbles byte holds his saves and
# the headers bytes his saves held, parried and tipped; "conceded" is the goals his side let in while he was on.
_PG = {"xg": 21, "role": 23, "goals": 24, "conceded": 25, "shots": 26, "shots_on_target": 27, "assists": 28, "dribbles": 29,
       "fouls": 30, "fouled": 31, "offsides": 32, "yellow_cards": 33, "minute_off": 36, "minutes": 39, "rating": 40,
       "passes": 41, "passes_completed": 42, "key_passes": 43, "tackles": 44, "tackles_won": 45, "key_tackles": 46,
       "headers": 47, "headers_won": 48, "key_headers": 49, "interceptions": 50, "distance": 51, "clear_cut_chances": 53}
PLAYER_GAME_FIGURES = tuple(_PG)
_PG_POSITION = 17  # u16 position bits (POSITION_NAMES), after a constant 02 02


@dataclass(frozen=True)
class PlayerGame:
    person: int  # the player's number
    team: int  # the team he was in the squad of (number)
    date: date
    opponent: int  # team number
    competition: int  # the save's competition key
    status: int
    position: int = 0  # the slot's position bits: 1 GK, 4 D (R), 0x10 D (C), 0x1000 AM (L), 0x2000 AM (C) (POSITION_NAMES)
    figures: dict[str, int] | None = None  # the _PG figures, when played

    @property
    def played(self) -> bool:
        return self.status == _PG_PLAYED

    @property
    def status_word(self) -> str:
        return PLAYER_GAME_STATUS.get(self.status, f"status {self.status}")

    @property
    def position_name(self) -> str | None:
        return position_name(self.position) if self.position else None


def _person_row_positions(game_db: bytes, person_uid: dict[int, int]) -> tuple[list[int], list[int]]:
    """The positions of the ``[number][uid][uid]`` rows of the known people, sorted, with their numbers."""
    found = []
    for match in re.finditer(rb"(.{4})(.{4})\2", game_db, re.DOTALL):
        number, uid = struct.unpack("<II", match.group(0)[:8])
        if person_uid.get(number) == uid:
            found.append((match.start(), number))
    found.sort()
    return [at for at, _ in found], [number for _, number in found]


def _player_game_list_end(game_db: bytes, at: int, count: int) -> int | None:
    """Where a list of ``count`` records starting at ``at`` ends, or None when the bytes are not such a list."""
    for _ in range(count):
        if at + _PG_SHORT > len(game_db) or game_db[at] != 1:
            return None
        packed, year = struct.unpack_from("<HH", game_db, at + 1)
        if not (2000 <= year <= 2112 and 1 <= (packed & 0x1FF) <= 366):
            return None
        at += _PG_RECORD if struct.unpack_from("<H", game_db, at + 13)[0] == _PG_PLAYED else _PG_SHORT
    return at


def read_player_games(game_db: bytes, person_uid: dict[int, int]) -> list[PlayerGame]:
    """Every tracked player's recent games, team by team. A list belongs to the person whose row comes next after it;
    ``person_uid`` (number -> uid) says which rows are people. Lists before no known row are dropped."""
    rows, numbers = _person_row_positions(game_db, person_uid)
    games: list[PlayerGame] = []
    covered = 8
    for match in _PG_MARK.finditer(game_db):
        at = match.start()
        if at < covered:
            continue
        count = struct.unpack_from("<I", game_db, at - 4)[0]
        if not 1 <= count <= _PG_MAX:
            continue
        end = _player_game_list_end(game_db, at, count)
        if end is None:
            continue
        covered = end
        owner = bisect.bisect_right(rows, end)
        if owner >= len(rows):
            continue
        person, team = numbers[owner], struct.unpack_from("<I", game_db, at - 8)[0]
        for _ in range(count):
            when = decode_match_date(game_db[at + 1:at + 5])
            opponent, competition, status = struct.unpack_from("<IIH", game_db, at + 5)
            if status == _PG_PLAYED:
                record = game_db[at:at + _PG_RECORD]
                games.append(PlayerGame(person, team, when, opponent, competition, status, struct.unpack_from("<H", record, _PG_POSITION)[0],
                                        {field: record[offset] for field, offset in _PG.items()}))
                at += _PG_RECORD
            else:
                games.append(PlayerGame(person, team, when, opponent, competition, status))
                at += _PG_SHORT
    return games


# -- press conferences (26 Oct 2026, Exhibit 5) ---------------------------------------------------------------------------
# press_conference_manager.dat (plain) keeps every answer given in the season's press conferences. An answer record:
# the date (u16 day of year | flags, u16 year), the person the question is about (a person NUMBER) and his team (a team
# NUMBER), `10 ?? 00 ?? 3d 01 00 00`, twelve FF bytes, ..., at +50 from the date the question id (u16, the game's
# media.fmf question_id) and at +71 the answer chosen (1-5; 0, 6 and 255 = none). Before a conference's answers, its head:
# `01 08 01 ?? ?? 00` team (u32) date `00` team (u32) `00` opponent (u32). The speaker is that team's manager on the day.
# Verified on four answers the user read in the game: Dysart (Reading) on Pladevall 20331/5 'claims Liverpool were lucky',
# Pladevall (Liverpool) on Dysart 20344/4 'lacks respect', Novaes Campos (Man Utd) on Carvalho 21021/5 "dismisses
# Carvalho's 'fluke result'", Carvalho (Luton) on Novaes Campos 21024/4 'hits out at'. The words are the game's
# (data/game_simulation/media.fmf, read by read_media_questions); the save holds only the numbers.
_PRESS_ANSWER = re.compile(rb"(.{3}[\x07\x08])(.{4})(.{4})\x10.\x00.\x3d\x01\x00\x00\xff{12}", re.DOTALL)
_PRESS_HEAD = re.compile(rb"\x01\x08\x01..\x00(.{4})(.{3}[\x07\x08])\x00(.{4})\x00(.{4})", re.DOTALL)
_PRESS_QUESTION_AT, _PRESS_ANSWER_AT = 50, 71
_PRESS_HEAD_REACH = 4000  # an answer belongs to the nearest conference head this close before it


@dataclass(frozen=True, slots=True)
class PressAnswer:
    day: date
    team: int | None  # the conference's team NUMBER (its manager speaks)
    opponent: int | None  # the team NUMBER of the match it is about
    question: int  # media.fmf question_id
    answer: int  # 1-5
    subject: int  # the person NUMBER the question is about
    subject_team: int  # his team NUMBER


def read_press_answers(buffer: bytes) -> list[PressAnswer]:
    """Every answer given in the season's press conferences (press_conference_manager.dat)."""
    def day(raw: bytes) -> date | None:
        packed, year = struct.unpack("<HH", raw)
        return _date_from_day_of_year(packed & 0x1FF, year) if 1990 <= year <= 2200 else None

    heads = [(m.start(), struct.unpack("<I", m.group(1))[0], struct.unpack("<I", m.group(4))[0]) for m in _PRESS_HEAD.finditer(buffer)]
    starts = [h[0] for h in heads]
    found = []
    for m in _PRESS_ANSWER.finditer(buffer):
        when = day(m.group(1))
        at = m.start() + _PRESS_QUESTION_AT
        if when is None or at + 22 > len(buffer):
            continue
        answer = buffer[m.start() + _PRESS_ANSWER_AT]
        if not 1 <= answer <= 5:
            continue
        i = bisect.bisect_right(starts, m.start()) - 1
        head = heads[i] if i >= 0 and m.start() - heads[i][0] <= _PRESS_HEAD_REACH else None
        found.append(PressAnswer(when, head[1] if head else None, head[2] if head else None, struct.unpack_from("<H", buffer, at)[0],
                                 answer, struct.unpack("<I", m.group(2))[0], struct.unpack("<I", m.group(3))[0]))
    return found


GAME_DATA_ROOT = GAME_DATABASE_ROOT.parents[1]  # .../Football Manager 2024/data
MEDIA_FILE = Path("game_simulation") / "media.fmf"


def read_media_questions(root: Path = GAME_DATA_ROOT) -> dict[int, dict]:
    """The game's press questions by question_id: the topic (the QID name) and each answer's tone ('very_negative'...)
    and headline template ("[%person#1-surname] dismisses [%person#2-surname]{s} 'fluke result'"). Empty when the game
    is not installed here."""
    path = root / MEDIA_FILE
    if not path.is_file():
        return {}
    text = "\n".join(block.decode("utf-8", "replace") for block in iter_inner_blocks(path.read_bytes()))
    marks = [m for m in re.finditer(r'<integer id="question_id" value="(\d+)"', text)]
    found: dict[int, dict] = {}
    for index, mark in enumerate(marks):
        record = text[mark.start():marks[index + 1].start() if index + 1 < len(marks) else len(text)]
        before = text[max(0, mark.start() - 400):mark.start()]
        names = re.findall(r"QID_[A-Z0-9_]+", before)
        name = names[-1] if names else (re.findall(r"QID_[A-Z0-9_]+", record) or [None])[0]
        answers = {}
        for a in re.finditer(r'<record id="answer_(\d)">(.*?)</record>', record, re.S):
            tone = re.search(r'id="meaning" value="(\w+)"', a.group(2))
            headline = re.search(r'id="headline"[^>]*value="([^"]*)"', a.group(2))
            answers[int(a.group(1))] = {"tone": tone.group(1) if tone else None,
                                        "headline": re.sub(r"\[COMMENT[^\]]*\]", "", headline.group(1)).strip() if headline else None}
        question = re.search(r'id="question_string"[^>]*value="([^"]*)"', record)
        found.setdefault(int(mark.group(1)), {"topic": name, "answers": answers,
                                              "question": re.sub(r"\[COMMENT[^\]]*\]", "", question.group(1)).strip() if question else None})
    return found


_PRONOUNS = {"he": "he", "He": "He", "his": "his", "His": "His", "him": "him", "I": "I", "my": "my", "My": "My", "me": "me",
             "you": "he", "You": "He", "your": "his", "Your": "His"}


def render_headline(template: str | None, people: dict[int, str | tuple[str, str | None] | None], teams: dict[int, str | None]) -> str | None:
    """A press headline with its people (a name, or (name, surname): 'Novaes Campos', not 'Campos') and teams filled in;
    None when a name it needs is missing."""
    if not template:
        return None
    missing = False

    def person(m: re.Match) -> str:
        nonlocal missing
        name = people.get(int(m.group(1)))
        name, surname = name if isinstance(name, tuple) else (name, None)
        form = m.group(2) or ""
        if form in _PRONOUNS:
            return _PRONOUNS[form]
        if not name:
            missing = True
            return ""
        return (surname or name.split()[-1]) if form == "surname" else name.split()[0] if form == "first" else name

    def team(m: re.Match) -> str:
        nonlocal missing
        name = teams.get(int(m.group(1)))
        if not name:
            missing = True
        return name or ""

    text = re.sub(r"\[%(?:person|male|female)#(\d)(?:-(\w+))?\]", person, template)
    text = re.sub(r"\[%team#(\d)(?:-\w+)?\]", team, text)
    text = text.replace("{s}", "'s").replace("s's ", "s' ")
    text = re.sub(r"\[%[^\]]*\]", "", text)
    return None if missing else re.sub(r"\s+", " ", text).strip()


# -- news items (26 Oct 2026, Exhibits 5, 6 and 7) -------------------------------------------------------------------------
# news.dat (plain) keeps the inbox's items. The game writes their words at display time, so only the facts are read. An
# item ends in `date(news) item_id(u32) 18|19|11 40|60|c0 ...`; its fields stand before that, at places fixed for each
# kind (from the item id): found by diffing Exhibit 6 and 7 (one game day) against the items the user read, and kept only
# where every field holds a valid person or team. Four kinds so far:
#   danger_man:   an ex-player or pundit singles out a player before a match ('Rojas singles out Bradford'):
#                 speaker -23, the player -19, the player's team -27, the match date -11, its kind bytes -13 (04 02, 05 01...)
#   job_link:     a manager linked with a vacant job ('Dimario rubbishes Arsenal links', 'Déniz has nothing to say'):
#                 manager -55, his club -50, the club with the vacancy -45, the manager who left it -18; `04 01`/`46 01` at -10
#   board_pressure: the board losing patience ('Stoke fixture decisive for Novaes Campos'): team -72, manager -68,
#                 the opponent of the decisive match -47, the match date -15, kind byte 0x47 at -11
#   national_scouting: a national manager seen watching players (Exhibits 8 and 9: 'England manager Cunningham ... Konneh and
#                 Broe thought to be the focus'): manager -17-7(n-1)-17, his nation's team 8 before the count, the count n (u32)
#                 4 before the first entry, entries of 7 bytes `01` player(u32) `01 00`, the last starting at -18
#   transfer_rumour: a club after a player with a fee ('Hertha Berlin chase López', £43M): a record of its own: player(u32)
#                 `03 ?? ??` FF*4 `00` x(u32) kind `05 01` fee(u32, pounds) ... the bidding team at +85.
# the year's high byte, 07 or 08 (1990-2303): the patterns once said 0f 08, 2063 only, and read nothing dated 2064
_NEWS_ITEM = re.compile(rb"(.{3}[\x07\x08])(.{4})[\x11\x18\x19][\x40\x60\xc0]", re.DOTALL)
# kind 05 01 only: 06 01 copies the same rumours and adds a few that are not bids (Chelsea's Spyrou 'to King's Lynn' at his value)
_RUMOUR = re.compile(rb"(.{4})\x03.{2}\xff{4}\x00.{4}(\x05\x01)(.{4})", re.DOTALL)
_RUMOUR_BIDDER = 85
_DANGER_KINDS = {b"\x04\x02", b"\x05\x01", b"\x06\x01", b"\x06\x02", b"\x04\x01", b"\x03\x01"}
_JOB_LINK_KINDS = {b"\x04\x01", b"\x46\x01"}
BOARD_PRESSURE = 0x47
_SCOUTED_MAX = 6


@dataclass(frozen=True, slots=True)
class NewsItem:
    kind: str  # danger_man, job_link, board_pressure, national_scouting, transfer_rumour
    item_id: int | None
    day: date | None  # the news date (None for a rumour record)
    person: int | None  # person NUMBERS and team NUMBERS: the speaker / the manager / the player
    other_person: int | None  # the player singled out / the manager who left the job
    team: int | None  # the player's team / the manager's club / the pressured team / the bidder
    other_team: int | None  # the club with the vacancy / the opponent of the decisive match
    match_day: date | None = None
    fee: int | None = None


def read_news_items(buffer: bytes) -> list[NewsItem]:
    """The news items of the kinds decoded so far, each once (an item is kept in several lists)."""
    def u32(at: int) -> int | None:
        return struct.unpack_from("<I", buffer, at)[0] if 0 <= at <= len(buffer) - 4 else None

    def day(at: int) -> date | None:
        if not 0 <= at <= len(buffer) - 4:
            return None
        packed, year = struct.unpack_from("<HH", buffer, at)
        return _date_from_day_of_year(packed & 0x1FF, year) if 1990 <= year <= 2200 else None

    found: dict[tuple, NewsItem] = {}
    for m in _NEWS_ITEM.finditer(buffer):
        p = m.start() + 4  # the item id
        item_id, news_day = u32(p), day(m.start())
        if news_day is None or item_id is None:
            continue
        if buffer[p - 13:p - 11] in _DANGER_KINDS and (match := day(p - 11)) is not None:
            item = NewsItem("danger_man", item_id, news_day, u32(p - 23), u32(p - 19), u32(p - 27), None, match)
            found.setdefault((item.kind, item.person, item.other_person, item.match_day), item)
        if buffer[p - 10:p - 8] in _JOB_LINK_KINDS:
            item = NewsItem("job_link", item_id, news_day, u32(p - 55), u32(p - 18), u32(p - 50), u32(p - 45))
            found.setdefault((item.kind, item.person, item.other_team, item.day), item)
        for count in range(1, _SCOUTED_MAX + 1):
            first = p - 18 - 7 * (count - 1)
            if first < 20 or u32(first - 4) != count or any(buffer[first + 7 * k] != 1 for k in range(count)):
                continue
            for k in range(count):  # one item per player watched
                item = NewsItem("national_scouting", item_id, news_day, u32(first - 17), u32(first + 1 + 7 * k), u32(first - 8), None)
                found.setdefault((item.kind, item.person, item.other_person, item.day), item)
            break
        if p >= 15 and buffer[p - 11] == BOARD_PRESSURE and (match := day(p - 15)) is not None:
            item = NewsItem("board_pressure", item_id, news_day, u32(p - 68), None, u32(p - 72), u32(p - 47), match)
            found.setdefault((item.kind, item.person, item.match_day), item)
    for m in _RUMOUR.finditer(buffer):
        fee = struct.unpack("<I", m.group(3))[0]
        if not 50_000 <= fee <= 500_000_000:
            continue
        item = NewsItem("transfer_rumour", None, None, struct.unpack("<I", m.group(1))[0], None, u32(m.start() + _RUMOUR_BIDDER), None, fee=fee)
        found.setdefault((item.kind, item.person, item.team, item.fee), item)
    return list(found.values())
