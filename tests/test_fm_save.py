"""The FM save reader, tested on a small synthetic save.

A real save is 400+ MB, so these tests build a tiny file with the same layout:
a header, zstd frames, and a directory section. One optional test reads a real
save when FM_SAVE_TEST_PATH points at one.
"""

import os
import struct
from datetime import date

import pytest

from app.services import fm_save
from app.services.fm_save import ATTRIBUTE_OFFSETS, FmSave, FmSaveError

zstandard = pytest.importorskip("zstandard")
np = pytest.importorskip("numpy")

HEADER = b"\x02\x01fmf.\x08\x00\x00"


def _compress(data: bytes) -> bytes:
    return zstandard.ZstdCompressor().compress(data)


def _string(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<I", len(raw)) + raw


def build_save(path, files: dict[str, list[bytes]]):
    """Write a save whose named files each span one or more frames."""
    body = b""
    entries = []
    for name, chunks in files.items():
        stem, ext = name.rsplit(".", 1)
        frames = [_compress(chunk) for chunk in chunks]
        entries.append((stem, "." + ext, len(body), sum(len(f) for f in frames), sum(len(c) for c in chunks)))
        body += b"".join(frames)
    directory = _string("Test Save")
    for stem, ext, offset, compressed, size in entries:
        directory += _string(stem) + _string(ext) + struct.pack("<QQQ", offset, compressed, size) + b"\x00" * 9
    header = HEADER + b"\x00" * (26 - len(HEADER))  # the first frame starts at byte 26
    path.write_bytes(header + body + HEADER + _compress(directory))
    return path


def test_reads_directory_and_multi_frame_files(tmp_path):
    save_path = build_save(
        tmp_path / "test.fm",
        {"game_info.dat": [b"hello"], "game_db.dat": [b"first-", b"second-", b"third"]},
    )
    with FmSave(save_path) as save:
        assert save.names() == ["game_db.dat", "game_info.dat"]
        assert save.read("game_info.dat") == b"hello"
        assert save.read("game_db.dat") == b"first-second-third"
        assert save.directory["game_db.dat"].size == len(b"first-second-third")


def test_unknown_file_and_bad_header_are_errors(tmp_path):
    save_path = build_save(tmp_path / "test.fm", {"a_file.dat": [b"x"]})
    with FmSave(save_path) as save:
        with pytest.raises(FmSaveError):
            save.read("missing.dat")
    bad = tmp_path / "bad.fm"
    bad.write_bytes(b"not a save at all" * 10)
    with pytest.raises(FmSaveError):
        FmSave(bad)


def test_birth_date_is_day_of_year_then_year():
    # 16 May 2036 is day 137 (leap year); 16 May 2046 is day 136.
    assert fm_save.decode_birth_date(struct.pack("<HH", 137, 2036), 0) == date(2036, 5, 16)
    assert fm_save.decode_birth_date(struct.pack("<HH", 136, 2046), 0) == date(2046, 5, 16)
    assert fm_save.decode_birth_date(struct.pack("<HH", 366, 2045), 0) is None  # not a leap year
    assert fm_save.decode_birth_date(struct.pack("<HH", 0, 2036), 0) is None
    assert fm_save.decode_birth_date(b"\x01", 0) is None


def test_contract_date_counts_the_year_from_2048():
    assert fm_save.decode_contract_date(struct.pack("<HB", 181, 19), 0) == date(2067, 6, 30)
    assert fm_save.decode_contract_date(struct.pack("<HB", 366, 20), 0) == date(2068, 12, 31)


def test_attribute_bytes_are_on_a_0_to_100_scale():
    # The values seen in the game: 47 -> 9, 84 -> 17, 10 -> 2, 15 -> 3.
    assert [fm_save.display_value(raw) for raw in (47, 84, 10, 15, 100, 5)] == [9, 17, 2, 3, 20, 1]


# Roberto Ştefănescu in the game's editor: D (L) 5, D (C) 20, D (R) 18, AM (C) 2, the rest 1; feet 13 and 20
STEFANESCU = dict.fromkeys(fm_save.POSITION_CODES, 1) | {"D (L)": 5, "D (C)": 20, "D (R)": 18, "AM (C)": 2}
FINISHING = 20  # where the test record's Finishing byte stands


def _player_record(
    attributes: dict[str, int], uid: int, birth: date, first_id: int = 1, last_id: int = 1, positions: dict[str, int] = STEFANESCU
) -> bytes:
    """One record: position ratings, attribute block, filler, name ids, 4 zeros + date + 0xFF, filler, the person row."""
    record = bytearray(b"\x01" * 130)
    finishing = FINISHING
    record[finishing + fm_save.POSITION_OFFSET:finishing + fm_save.POSITION_OFFSET + 15] = bytes(positions[c] for c in fm_save.POSITION_CODES)
    record[finishing + fm_save.LEFT_FOOT_OFFSET], record[finishing + fm_save.RIGHT_FOOT_OFFSET] = 65, 100
    for name, offset in ATTRIBUTE_OFFSETS.items():
        record[finishing + offset] = attributes.get(name, 50)
    record[finishing + fm_save.WEIGHT_OFFSET] = 70
    record[finishing + fm_save.HEIGHT_OFFSET] = 180
    day_of_year = birth.timetuple().tm_yday
    names = struct.pack("<I", first_id) + b"\x00" + struct.pack("<I", last_id) + b"\x00" + b"\xff" * 4 + b"\x00"
    traits = bytes([11, 16, 13, 18, 15, 9, 14, 9])  # Parkins' eight, after 0xFF, five bytes and seven zeros
    return bytes(record) + b"\x01" * 30 + names + b"\x00\x00\x00\x00" + struct.pack("<HH", day_of_year, birth.year) + b"\xff" + (
        b"\x03\x10\x01\xff\x8b" + bytes(7) + traits + b"\x01" * 16 + struct.pack("<III", 1234, uid, uid) + b"\x02" + b"\x01" * 19  # ... the person row: number, uid twice
    )


def test_reads_attributes_height_weight_and_dates_from_a_record():
    record = _player_record({"Finishing": 47, "Pace": 84, "Crossing": 78}, 2002609493, date(2036, 5, 16))
    attrs = fm_save.read_attributes(record, FINISHING)
    assert (attrs["Finishing"], attrs["Pace"], attrs["Crossing"]) == (9, 17, 16)
    assert fm_save.read_body(record, FINISHING) == {"height_cm": 180, "weight_kg": 70}
    assert fm_save.read_positions(record, FINISHING) == STEFANESCU and fm_save.read_feet(record, FINISHING) == (13, 20)


def test_a_shape_match_inside_a_block_moves_back_to_the_position_ratings(monkeypatch):
    buffer = b"\x01" * 50 + _player_record({}, 2000000001, date(2036, 5, 16))
    true_block = 50 + FINISHING
    monkeypatch.setattr(fm_save, "find_attribute_blocks", lambda data: iter([true_block + 8]))  # the true one failed the shape test
    assert fm_save.player_blocks(buffer) == [true_block]
    assert fm_save.read_positions(b"\x01" * 20 + bytes(range(30, 45)) + b"\x01" * 20, 37) is None  # not ratings


def test_iter_players_pairs_each_block_with_its_date_and_id():
    first = _player_record({"Finishing": 47}, 2000000001, date(2036, 5, 16))
    second = _player_record({"Finishing": 100}, 2000000002, date(2041, 7, 29))
    buffer = b"\x01" * 50 + first + b"\x01" * 50 + second + b"\x01" * 50

    players = list(fm_save.iter_players(buffer))

    assert [p.uid for p in players] == [2000000001, 2000000002]
    assert [p.birth_date for p in players] == [date(2036, 5, 16), date(2041, 7, 29)]
    assert [p.attributes["Finishing"] for p in players] == [9, 20]
    assert fm_save.find_uid(buffer, 2000000002) != []


def test_the_hidden_traits_follow_the_birth_date_and_five_sit_in_the_block():
    """Howard Parkins, edited in the in-game editor to 1, 2, 3, 20, 19, 18, 17, 16 and the save diffed (Exhibit 4, 26 Oct 2026):
    eight bytes on the 1-20 scale, 17 bytes after the birth date; Dirtiness, Consistency, Important Matches, Injury Proneness
    and Versatility at +39, +42, +45, +46, +47 of the block, on the 0-100 scale like the shown attributes."""
    record = bytearray(_player_record({}, 2002696279, date(2040, 3, 30)))
    for name, offset in fm_save.HIDDEN_ATTRIBUTE_OFFSETS.items():
        record[FINISHING + offset] = {"Dirtiness": 50, "Consistency": 86, "Important Matches": 100, "Injury Proneness": 15, "Versatility": 70}[name]
    (player,) = fm_save.iter_players(bytes(record))
    assert player.traits == {"Adaptability": 11, "Ambition": 16, "Loyalty": 13, "Pressure": 18, "Professionalism": 15, "Sportsmanship": 9,
                             "Temperament": 14, "Controversy": 9, "Dirtiness": 10, "Consistency": 17, "Important Matches": 20,
                             "Injury Proneness": 3, "Versatility": 14}
    broken = bytearray(record)
    broken[record.index(bytes([11, 16, 13, 18, 15, 9, 14, 9]))] = 0  # a value off the 1-20 scale: not the traits' shape
    (player,) = fm_save.iter_players(bytes(broken))
    assert player.traits is None


def test_a_block_without_a_date_or_id_is_skipped():
    record = bytearray(_player_record({}, 2000000001, date(2036, 5, 16)))
    record[-30:] = b"\x01" * 30  # remove the doubled id
    assert list(fm_save.iter_players(b"\x01" * 50 + bytes(record))) == []


def test_diff_files_reports_positions():
    count, sample = fm_save.diff_files(b"abcdef", b"abXdeY")
    assert count == 2 and sample == [(2, ord("c"), ord("X")), (5, ord("f"), ord("Y"))]
    assert fm_save.diff_files(b"same", b"same") == (0, [])
    with pytest.raises(ValueError):
        fm_save.diff_files(b"a", b"ab")


@pytest.mark.skipif(not os.environ.get("FM_SAVE_TEST_PATH"), reason="set FM_SAVE_TEST_PATH to a real .fm save")
def test_real_save_has_tens_of_thousands_of_players():
    with FmSave(os.environ["FM_SAVE_TEST_PATH"]) as save:
        assert "game_db.dat" in save.directory
        players = list(fm_save.iter_players(save.read("game_db.dat")))
    assert len(players) > 40_000
    assert len({p.uid for p in players}) == len(players)


def _name_table(entries: list[tuple[int, str]]) -> bytes:
    return b"".join(struct.pack("<I", ident) + _string(text) for ident, text in entries)


def test_name_tables_and_player_names():
    first = _name_table([(0, "Ayoub"), (1, "Norberto"), (2, "Emil")])
    last = _name_table([(0, "Aarab"), (1, "Pico"), (2, "Aakenes")])
    common = _name_table([(0, "DJ Buffonge"), (1, "Zico"), (2, "Pele")])
    gap = b"\x01" * 20
    buffer = (
        b"\x01" * 40 + first + gap + last + gap + common + gap
        + _player_record({}, 2000000001, date(2036, 5, 16), first_id=1, last_id=1)
        + b"\x01" * 50
        + _player_record({}, 2000000002, date(2043, 4, 21), first_id=2, last_id=2)
    )

    tables = fm_save.read_name_tables(buffer, min_entries=3)
    assert tables.first[1] == "Norberto" and tables.last[1] == "Pico" and tables.common[1] == "Zico"

    players = list(fm_save.iter_players(buffer, names=tables))

    assert [(p.first_name, p.last_name) for p in players] == [("Norberto", "Pico"), ("Emil", "Aakenes")]


def test_unknown_name_ids_give_none():
    tables = fm_save.NameTables(first={1: "A"}, last={1: "B"}, common={})
    record = _player_record({}, 2000000001, date(2036, 5, 16), first_id=99, last_id=1)

    player = next(fm_save.iter_players(b"\x01" * 50 + record, names=tables))

    assert (player.first_name, player.last_name) == (None, "B")


def _fixture_header(competition: int, fixture_id: int, home: int, away: int, when: date) -> bytes:
    packed = when.timetuple().tm_yday | (57 << 9)  # the high bits are not the date
    return (
        b"\x18" + struct.pack("<II", competition, fixture_id) + b"\x02\x04\x00"
        + struct.pack("<I", home) + b"\xff\x00\x00" + struct.pack("<I", away) + b"\xff\x00"
        + struct.pack("<HH", packed, when.year) + b"\xff" * 5 + b"\x0f\x08"
    )


def _result(home_goals: int, away_goals: int, attendance: int, goals: list[tuple[int, int, int, int, int]]) -> bytes:
    return (
        b"\x01\x18" + struct.pack("<I", 854579)
        + bytes([home_goals]) + b"\xff" * 3 + bytes([away_goals]) + b"\xff" * 3
        + struct.pack("<II", attendance, 3000) + bytes.fromhex("0a013f48") + struct.pack("<I", len(goals))
        + b"".join(struct.pack("<IBBBB", *goal) for goal in goals)
    )


def test_fixtures_with_and_without_results():
    buffer = (
        b"\x07" * 30 + b"\x00" + _fixture_header(162, 67, 586, 513, date(2064, 1, 1))
        + b"\x07" * 30 + _result(0, 2, 60704, [(554624, 1, 0, 13, 0), (507855, 1, 0, 90, 2)])
        + _fixture_header(162, 7727, 513, 586, date(2063, 8, 10)) + b"\x07" * 30
    )

    upcoming, played = fm_save.iter_fixtures(buffer)

    assert (upcoming.home_id, upcoming.away_id, upcoming.date, upcoming.played) == (586, 513, date(2064, 1, 1), False)
    assert (played.competition_id, played.fixture_id, played.date) == (162, 7727, date(2063, 8, 10))
    assert (played.home_goals, played.away_goals, played.attendance) == (0, 2, 60704)
    assert [(g.player_id, g.side, g.minute, g.added_time) for g in played.goals] == [(554624, 1, 13, 0), (507855, 1, 90, 2)]


def test_a_goal_of_kind_3_is_not_counted():
    buffer = _result(1, 0, 100, [(1, 0, 0, 20, 0), (2, 1, 3, 54, 0)]) + _fixture_header(1, 2, 3, 4, date(2063, 1, 21))

    (fixture,) = fm_save.iter_fixtures(buffer)

    counted = [(g.side, g.minute) for g in fixture.goals if g.counts]
    assert (fixture.home_goals, fixture.away_goals) == (1, 0) and counted == [(0, 20)]


def _table_row(club: int, position: int, played: int, won: int, lost: int, goals: tuple[int, int], points: int,
               length: int = 401) -> bytes:
    row = bytearray(length)  # every row ends with a zero byte
    row[5], row[21] = position - 1, played
    struct.pack_into("<H", row, length - 67, points)
    row[length - 64], row[length - 63] = goals
    row[length - 48], row[length - 47] = won, lost
    return struct.pack("<I", club) + b"\x00" + b"\xff" * 6 + bytes.fromhex("a026cb04") + bytes(row)


def _table(*rows: bytes) -> bytes:
    # count, a zero byte, the rows, then ff and a u32 count that starts the next list
    return b"\x05" * 40 + struct.pack("<I", len(rows)) + b"\x00" + b"".join(rows) + b"\xff\xa0\x00\x00\x00" + b"\x05" * 40


def test_league_table_rows_are_read_and_sorted():
    buffer = _table(
        _table_row(513, 3, 3, 1, 0, (3, 2), 5),
        _table_row(586, 1, 3, 2, 0, (10, 2), 7, length=414),  # rows differ in length
        _table_row(541, 2, 3, 2, 0, (6, 3), 7, length=405),
    )

    rows = fm_save.read_league_table(buffer)

    assert [(r.club_id, r.position) for r in rows] == [(586, 1), (541, 2), (513, 3)]
    liverpool = rows[0]
    assert (liverpool.played, liverpool.won, liverpool.drawn, liverpool.lost) == (3, 2, 1, 0)
    assert (liverpool.goals_for, liverpool.goals_against, liverpool.goal_difference, liverpool.points) == (10, 2, 8, 7)


def test_rows_that_do_not_form_a_table_are_refused():
    assert fm_save.read_league_table(b"\x05" * 500) == []
    two_firsts = _table(_table_row(1, 1, 1, 1, 0, (1, 0), 3), _table_row(2, 1, 1, 1, 0, (1, 0), 3))
    with pytest.raises(FmSaveError):
        fm_save.read_league_table(two_firsts)
    too_many_points = _table(_table_row(1, 1, 1, 1, 0, (1, 0), 9), _table_row(2, 2, 1, 0, 1, (0, 1), 0))
    with pytest.raises(FmSaveError):
        fm_save.read_league_table(too_many_points)


@pytest.mark.skipif(not os.environ.get("FM_SAVE_TEST_PATH"), reason="set FM_SAVE_TEST_PATH to a real .fm save")
def test_real_save_has_fixtures_and_a_premier_division_table():
    with FmSave(os.environ["FM_SAVE_TEST_PATH"]) as save:
        fixtures = list(fm_save.iter_fixtures(save.read("fix_man.dat")))
        table = fm_save.read_league_table(save.read("comp_11.dat"))
    assert len(fixtures) > 10_000 and any(f.played for f in fixtures)
    assert [r.position for r in table] == list(range(1, 21))


def _club_record(index: int, name: str, short: str) -> bytes:
    return (
        struct.pack("<III", index, index + 215, index + 215) + b"\x00" + struct.pack("<I", 139)
        + b"\xff" * 4 + b"\x8b\x00\x00\x00" * 2 + b"\x10\x04\x00\x00" + b"\x00\x02\x00\x00\x00\x00"
        + _string(name) + _string(short) + b"\x07" * 30
    )


def _team_record(
    number: int, club_index: int, fm_uid: int, age: int = 100, own: tuple[str, str] = ("", ""), national: bool = False
) -> bytes:
    return (
        b"\xff\xff\xff\x00" + bytes([0, int(national), 0]) + bytes([age]) + b"\xff\x04" + struct.pack("<I", club_index + 1)
        + _string(own[0]) + b"\x00" + _string(own[1]) + b"\x00"
        + struct.pack("<II", number, fm_uid) + b"\x07" * 20
    )


def test_clubs_and_teams_give_names_to_team_numbers():
    buffer = (
        b"\x07" * 50 + _club_record(386, "Arsenal", "Arsenal") + _club_record(617, "Borussia Dortmund", "Dortmund")
        + _team_record(513, 386, 602) + _team_record(81445, 386, 2000230583, age=21)
        + _team_record(74366, 617, 2000069494, age=19, own=("Borussia Dortmund U19", "B. Dortmund U19"))
        + _team_record(99999, 5, 1)  # its club is not in the save: skipped
    )

    clubs = fm_save.read_clubs(buffer)
    teams = fm_save.read_teams(buffer, clubs)

    assert {i: c.name for i, c in clubs.items()} == {386: "Arsenal", 617: "Borussia Dortmund"}
    assert sorted(teams) == [513, 74366, 81445]
    assert (teams[513].club_index, teams[513].fm_uid, teams[513].age_limit) == (386, 602, None)
    assert [fm_save.team_name(teams[n], clubs) for n in (513, 81445, 74366)] == [
        "Arsenal", "Arsenal U21", "Borussia Dortmund U19"
    ]


def _stage_row(number: int, key: int) -> bytes:
    return struct.pack("<III", number - 1, number, number) + b"\x00" + struct.pack("<I", key) + b"\xff" * 16


def test_stages_name_the_competition_of_each_fixture():
    table = _stage_row(3806, 664) + _stage_row(3807, 664) + _stage_row(3808, 341) + _stage_row(3809, 9999)  # key unknown
    lone = b"\x07" * 200 + _stage_row(162, 664) + b"\x07" * 200  # not next to another row
    keys = {664: 1301426, 341: 13}

    assert fm_save.read_stages(lone + table, keys) == {3806: 1301426, 3807: 1301426, 3808: 13}


def test_a_short_list_before_the_stage_table_does_not_name_its_numbers():
    """The save holds a 33-row list shaped like stage rows well before the table (number 13: Sky Bet League One); the table's
    own 13 is the East Asian Cup, and South Korea v Japan was filed as a League One game (15 Dec 2063)."""
    early = _stage_row(12, 9) + _stage_row(13, 9) + _stage_row(14, 11)  # the short list: League One twice, the Premier Division
    table = b"".join(_stage_row(n, 700 + n) for n in range(1, 40))  # the stage table, 13 -> key 713
    keys = {9: 13, 11: 11, **{700 + n: 5_000_000 + n for n in range(1, 40)}}
    stages = fm_save.read_stages(early + b"\x07" * 5000 + table, keys)
    assert stages[13] == 5_000_013 and stages[12] == 5_000_012 and stages[14] == 5_000_014 and stages[1] == 5_000_001


@pytest.mark.skipif(not os.environ.get("FM_SAVE_TEST_PATH"), reason="set FM_SAVE_TEST_PATH to a real .fm save")
def test_real_save_names_nearly_every_fixture_team():
    with FmSave(os.environ["FM_SAVE_TEST_PATH"]) as save:
        data = save.read("game_db.dat")
        fixtures = list(fm_save.iter_fixtures(save.read("fix_man.dat")))
    lang_db = fm_save.find_language_database()
    order = fm_save.competition_uids_in_order(lang_db) if lang_db else None
    stages = fm_save.read_stages(data, fm_save.read_competition_keys(data, order))
    clubs = fm_save.read_clubs(data)
    teams = fm_save.read_teams(data, clubs)
    slots = [number for f in fixtures for number in (f.home_id, f.away_id)]
    assert len(clubs) > 10_000
    assert sum(number in teams for number in slots) / len(slots) > 0.95
    assert sum(f.competition_id in stages for f in fixtures) / len(fixtures) > (0.99 if order else 0.85)


def _db_section(count: int, body: bytes) -> bytes:
    return b"\x03\x01tad.\xef\x0d" + struct.pack("<I", count) + body


def test_competition_names_come_from_the_game_language_database(tmp_path):
    nations = _db_section(1, struct.pack("<I", 5) + _string("Algeria") + b"\xff" + _string("Algeria") + b"\xff"
                          + _string("ALG") + _string("Algerian") + b"\x00")
    competitions = _db_section(2, b"".join(
        struct.pack("<I", uid) + _string(name) + b"\xff" + _string(short) + b"\xff" + _string(abbreviation) + b"\x00"
        for uid, name, short, abbreviation in (
            (11, "English Premier Division", "Premier Division", "PRM"),
            (1301426, "English FA Cup", "FA Cup", "FA"),
        )
    ))
    clubs = _db_section(0, b"")
    data = nations + competitions + clubs
    lang_db = tmp_path / "2400" / "2400_fm" / "lang_db.dat"
    lang_db.parent.mkdir(parents=True)
    lang_db.write_bytes(b"\x03\x01tad.\xef\x0d" + _compress(data[:50]) + _compress(data[50:]))

    assert fm_save.find_language_database(tmp_path) == lang_db
    names = fm_save.read_competition_names(lang_db)

    assert names[11].name == "English Premier Division" and names[11].abbreviation == "PRM"
    assert names[1301426].short_name == "FA Cup"
    assert fm_save.find_language_database(tmp_path / "missing") is None


def _history_row(key: int, year: int, start: int, slots: list[int], decided: date) -> bytes:
    filled = slots + [0xFFFFFFFF] * (9 - len(slots))
    packed = decided.timetuple().tm_yday | (51 << 9)
    return (
        struct.pack("<IHH", key, year, start) + struct.pack("<9I", *filled)
        + b"\x00\x04\x00" + struct.pack("<HH", start or year, year) + struct.pack("<HH", packed, decided.year)
    )


def test_competition_history_rows_give_winners_by_year():
    buffer = b"\x03\x01tmc.\x03\x00" + _history_row(7, 2062, 2061, [541, 586, 649], date(2062, 5, 21)) + _history_row(
        664, 2014, 0, [513, 99], date(2014, 5, 17)
    )

    first, second = fm_save.read_competition_history(buffer)

    assert (first.competition_key, first.year, first.season_start) == (7, 2062, 2061)
    assert (first.winner, first.runner_up, first.slots[2], first.slots[3]) == (541, 586, 649, None)
    assert first.decided_on == date(2062, 5, 21)
    assert (first.season, first.season_label) == ((2061, 2062), "2061/62")
    assert (second.season_start, second.winner, second.season_label) == (None, 513, "2014")


def test_holders_list_gives_the_final_order_of_the_latest_edition():
    order = [541, 560, 586]
    entries = b"".join(b"\x00" + struct.pack("<I", team) for team in order)
    buffer = b"\x00" * 24 + struct.pack("<I", 3) + entries + b"\x01\x0a\x00\x00\x00\x01\x08\x20\x00\x00"

    assert fm_save.read_holders(buffer) == order
    assert fm_save.read_holders(b"\x00" * 40 + struct.pack("<I", 2) + entries[:10] + b"\x00" * 8) == []


def test_latest_edition_is_a_history_row_inside_the_competition_record():
    row = _history_row(17, 2063, 0, [791, 793, 3477], date(2063, 5, 29))
    game_db = b"\x06\x00" * 20 + b"\x01\x03" + row + b"\x01\x03" + _history_row(99, 2063, 0, [1], date(2063, 5, 1))

    latest = fm_save.read_latest_editions(game_db, {17: 23})

    assert list(latest) == [23]
    assert (latest[23].winner, latest[23].runner_up, latest[23].year) == (791, 793, 2063)
    assert latest[23].decided_on == date(2063, 5, 29)


def test_latest_edition_row_can_have_a_byte_before_its_type():
    row = bytearray(_history_row(633, 2062, 2062, [681, 1438, 701], date(2062, 7, 16)))
    row[44] = 3  # the 2062 World Cup row in the main save
    latest = fm_save.read_latest_editions(b"\x00" * 6 + b"\x01\x03" + bytes(row), {633: 1301385})

    assert (latest[1301385].winner, latest[1301385].year) == (681, 2062)


def _competition_record(key: int, uid: int) -> bytes:
    return b"\x01\x00\x6c\x07\x01\x00\x6c\x07\x00\x00\xff" + struct.pack("<III", key, uid, uid) + b"\x00\x00\x00\x01\xff\x7f"


def test_competition_keys_come_from_records_and_fill_gaps_from_the_database_order():
    order = [101, 102, 103, 104, 105, 199, 106, 107, 108]  # the save's list drops 199
    buffer = b"\x05" * 30 + _competition_record(0, 101) + b"\x05" * 10 + _competition_record(3, 104) + (
        b"\x05" * 10 + _competition_record(5, 106) + b"\x05" * 10 + _competition_record(7, 108)
    )

    assert fm_save.read_competition_keys(buffer) == {0: 101, 3: 104, 5: 106, 7: 108}
    keys = fm_save.read_competition_keys(buffer, order)

    assert keys[1] == 102 and keys[2] == 103  # same spacing as the database list: filled
    assert 4 not in keys  # the list has 105 and 199 between 104 and 106: not certain, left out
    assert keys[6] == 107


def test_archived_fixtures_in_inner_blocks_are_read_with_the_current_ones():
    current = _result(0, 2, 60704, [(554624, 1, 0, 13, 0)]) + _fixture_header(162, 7727, 513, 586, date(2063, 8, 10))
    archived = _result(12, 0, 35562, [(1, 0, 0, 2, 0)] * 12) + _fixture_header(162, 15, 541, 611, date(2025, 9, 27))
    fix_man = b"\x07" * 20 + current + b"\x07" * 20 + _compress(b"\x07" * 30 + archived + b"\x07" * 30) + b"\x07" * 10

    fixtures = list(fm_save.iter_all_fixtures(fix_man))

    assert [(f.date, f.home_goals, f.away_goals) for f in fixtures] == [
        (date(2063, 8, 10), 0, 2), (date(2025, 9, 27), 12, 0)
    ]
    assert [f.away_fans for f in fixtures] == [3000, 3000]  # _result writes 3000 away fans
    assert list(fm_save.iter_fixtures(fix_man))[0].date == date(2063, 8, 10)  # only the current one


def test_inner_blocks_skip_bytes_that_only_look_like_a_frame():
    data = b"x" * 5 + bytes.fromhex("28b52ffd") + b"not a frame" + _compress(b"hello") + b"y" * 5
    assert list(fm_save.iter_inner_blocks(data)) == [b"hello"]


def test_an_archived_fixture_split_across_two_blocks_is_found():
    archived = _result(4, 2, 74000, [(1, 0, 0, 10, 0)] * 4 + [(2, 1, 0, 20, 0)] * 2) + _fixture_header(162, 9, 590, 541, date(2022, 8, 27))
    half = len(archived) // 2
    fix_man = _compress(b"\x07" * 30 + archived[:half]) + _compress(archived[half:] + b"\x07" * 30)

    (fixture,) = fm_save.iter_all_fixtures(fix_man)

    assert (fixture.date, fixture.home_goals, fixture.away_goals, len(fixture.goals)) == (date(2022, 8, 27), 4, 2, 6)


def _person_row(number: int, uid: int, short: bool = False) -> bytes:
    tail = b"\x13\xff\xff\xff\xff\x00" + b"\x01" * 16 if short else b"\x10\x00" + b"\x01" * 16
    return struct.pack("<III", number, uid, uid) + tail


def test_person_numbers_map_to_fm_ids_in_runs():
    run = _person_row(331776, 2002312310) + _person_row(331777, 2002312311, short=True) + _person_row(331778, 2002312312)
    lonely = b"\x07" * 100 + _person_row(5, 2000000005) + b"\x07" * 100  # no neighbour: chance match
    people = fm_save.read_person_numbers(b"\x07" * 20 + run + lonely)

    assert people == {331776: 2002312310, 331777: 2002312311, 331778: 2002312312}


def _knockout_header(competition: int, fixture_id: int, home: int, away: int, when: date, season: int) -> bytes:
    packed = when.timetuple().tm_yday | (57 << 9)
    return (
        b"\x18" + struct.pack("<II", competition, fixture_id) + b"\x02\x00\x00"
        + struct.pack("<I", home) + b"\x05\x14\x00" + struct.pack("<I", away) + b"\x00\x15"
        + struct.pack("<HH", packed, when.year) + struct.pack("<HH", packed, when.year)
        + b"\xff" * 5 + struct.pack("<H", season)
    )


def _scored(home: tuple[int, int, int], away: tuple[int, int, int], goals: list[tuple[int, int, int, int, int]]) -> bytes:
    side = lambda s: bytes([s[0], 0xFF if s[1] is None else s[1], 0xFF if s[2] is None else s[2], 0xFF])  # noqa: E731
    return (
        b"\x01\x18" + struct.pack("<I", 1) + side(home) + side(away) + struct.pack("<II", 30503, 3000)
        + bytes.fromhex("0a013f48") + struct.pack("<I", len(goals))
        + b"".join(struct.pack("<IBBBB", *goal) for goal in goals)
    )


def test_second_legs_and_penalty_shootouts_are_read():
    second_leg = _result(6, 1, 39000, [(1, 0, 0, 3, 0)] * 6 + [(2, 1, 0, 50, 0)]) + _knockout_header(3016, 7, 541, 1480, date(2023, 3, 14), 2022)
    shootout = _scored((1, None, 5), (1, None, 3), [(1, 0, 0, 76, 0), (2, 1, 0, 10, 0), (3, 0, 4, 90, 5), (4, 1, 5, 90, 5)])
    shootout += _fixture_header(3017, 8, 541, 511, date(2022, 12, 20))
    second_xi = _result(3, 1, 500, []) + _fixture_header(2380, 9, 541, 143502, date(2022, 6, 28))

    leg, cup, friendly = fm_save.iter_fixtures(b"\x07" * 20 + second_leg + b"\x07" * 9 + shootout + b"\x07" * 9 + second_xi)

    assert (leg.date, leg.home_id, leg.away_id, leg.home_goals, leg.away_goals) == (date(2023, 3, 14), 541, 1480, 6, 1)
    assert (cup.home_goals, cup.away_goals, cup.home_penalties, cup.away_penalties) == (1, 1, 5, 3)
    assert cup.home_goals_after_extra_time is None
    assert [g.counts for g in cup.goals] == [True, True, False, False]  # shoot-out kicks are not goals
    assert friendly.away_id == 143502


def test_person_names_from_short_rows_full_records_and_name_triples():
    names = fm_save.NameTables(
        first={10777: "Paul", 74655: "Jude", 72334: "Joe", 9: "Francisco"},
        last={7088: "Malcolm", 221637: "Soonsup-Bell", 208038: "Haigh", 8: "da Silva"},
        common={29575: "Pepe"},
    )
    row = lambda number, uid: struct.pack("<III", number, uid, uid) + b"\x10\x00" + b"\x01" * 16  # noqa: E731
    run = (
        b"\x10\x00" + struct.pack("<II", 10777, 7088) + b"\x09\x22\x40\x10\x04\x00\x00\x00" + struct.pack("<III", 331777, 2002312311, 2002312311)
        + b"\x10\x01" + struct.pack("<I", 29575) + b"\x01\x21\x58\x30\x00\x00\x00\x00" + struct.pack("<III", 331778, 2002312312, 2002312312)
        + b"\x10\x00" + struct.pack("<II", 1, 1) + b"\x00" * 8 + struct.pack("<III", 331779, 2002312313, 2002312313)
    )
    # a full record: name ids 19/14/9 bytes before a marked birth date (common = first: "Francisco")
    record = (struct.pack("<I", 9) + b"\x00" + struct.pack("<I", 8) + b"\x00" + struct.pack("<I", 9) + b"\x00" * 5
              + struct.pack("<HH", 137, 2031) + b"\xff" + b"\x05" * 80 + row(530491, 2002511025))
    # a staff record: the name block (triple, full name as text, birth date); later in the record a
    # triple of small numbers that are valid name ids by chance must not win (Alan Kardec's record)
    chance = struct.pack("<I", 9) + b"\x00" + struct.pack("<I", 8) + b"\x00" + b"\xff" * 4 + b"\x00" + b"\x05" * 8
    staff = (struct.pack("<I", 72334) + b"\x00" + struct.pack("<I", 208038) + b"\x00" + b"\xff" * 4 + b"\x00"
             + b"\x10\x00\x00\x00Joe Samuel Haigh" + struct.pack("<HH", 120, 1985) + b"\x05" * 60 + chance + b"\x05" * 20
             + row(19211, 28124580))

    people, found = fm_save.read_person_names(b"\x07" * 40 + run + b"\x07" * 90 + record + b"\x07" * 90 + staff, names)

    assert people[331777] == 2002312311 and people[19211] == 28124580
    assert found[2002312311] == fm_save.PersonName("Paul Malcolm", "Paul", "Malcolm")
    assert found[2002312312].name == "Pepe"
    assert 2002312313 not in found  # its ids are not in the name tables
    assert (found[2002511025].name, found[2002511025].birth_date) == ("Francisco", date(2031, 5, 17))
    assert (found[28124580].name, found[28124580].birth_date) == ("Joe Haigh", date(1985, 4, 30))


def _stats_record(team: int, year: int, kind: int, starts: int, subs: int, goals: int, rating_total: int, minutes: int) -> bytes:
    record = bytearray(152)
    struct.pack_into("<IH", record, 1, team, 22)
    struct.pack_into("<HHH", record, 11, year, rating_total, minutes)
    record[17:23] = bytes([0, 3, 3, 1, 4, 0])  # conceded, 2 unknown, red, yellow, clean sheets
    record[24:30] = bytes([starts, subs, starts + subs, goals, 9, 12])
    record[31] = kind
    return bytes(record)


def test_season_stats_come_in_groups_with_the_kind_of_competition():
    records = [
        _stats_record(988, 2051, fm_save.LEAGUE, 28, 0, 37, 2302, 2349),
        _stats_record(1, 2030, fm_save.LEAGUE, 1, 0, 0, 60, 90),  # in no group
        _stats_record(988, 2051, fm_save.CUP, 4, 0, 3, 313, 384),
        _stats_record(988, 2052, fm_save.LEAGUE, 25, 1, 27, 2096, 2196),
    ]
    dt = b"\x03\x01tmc.\x06\x00" + b"".join(records)
    offsets = [0, 2 * 152, 3 * 152]
    stored = [offsets[0]]
    for offset in offsets[1:]:
        stored.append(offset - stored[-1])  # each next offset is stored minus the value before it
    ls = (b"\x03\x01tad.\x04\x00" + struct.pack("<II", 1, 152) + struct.pack("<II", 2, 3)
          + struct.pack("<3i", *stored) + struct.pack("<II", 0, 0) + b"\x98\x00\x00\x00\x06\x00")

    groups = fm_save.read_season_stats(dt, ls)

    assert [len(group) for group in groups] == [3, 0]
    league, cup, next_season = groups[0]
    assert (league.league, cup.kind, next_season.league) == (True, fm_save.CUP, True)
    assert (league.appearances, league.goals, league.assists, league.player_of_match) == (28, 37, 9, 12)
    assert (league.average_rating, league.minutes) == (8.22, 2349)
    assert (league.yellow_cards, league.red_cards) == (4, 1)
    assert (next_season.appearances, next_season.substitute_appearances) == (26, 1)


def test_a_player_record_holds_its_statistics_group_before_the_birth_date():
    names = struct.pack("<I", 9) + b"\x00" + struct.pack("<I", 8) + b"\x00" + struct.pack("<I", 9) + b"\x00" * 5
    record = (struct.pack("<I", 69350) + b"\x05" * 17 + names + struct.pack("<HH", 137, 2031) + b"\xff"
              + b"\x05" * 150 + struct.pack("<III", 402195, 2002382729, 2002382729) + b"\x10\x00" + b"\x01" * 16)

    stats = [[]] * 69350 + [[fm_save.SeasonStats(988, 22, 2051, fm_save.LEAGUE, *[1] * 12)]]
    tables = fm_save.NameTables(first={9: "Pedro"}, last={8: "Gomes"}, common={})

    assert fm_save.read_stat_groups(b"\x07" * 60 + record, stats, tables) == {2002382729: 69350}
    too_old = [[]] * 69350 + [[fm_save.SeasonStats(988, 22, 2080, fm_save.LEAGUE, *[1] * 12)]]
    assert fm_save.read_stat_groups(b"\x07" * 60 + record, too_old, tables) == {}  # born 2031: 49 in 2080


def test_retired_players_keep_their_statistics_group_in_the_retirement_file():
    block = bytearray(53)
    struct.pack_into("<I", block, 13, 33987)
    struct.pack_into("<H", block, 27, 2019)
    no_stats = bytearray(53)
    struct.pack_into("<I", no_stats, 13, 0xFFFFFFFF)
    frames = [zstandard.ZstdCompressor().compress(bytes(b)) for b in (block, no_stats)]
    index = struct.pack("<I", 2) + struct.pack("<III", 331777, 0, len(frames[0])) + struct.pack("<III", 5, len(frames[0]), len(frames[1]))
    data = b"\x03\x01tad.\x02\x00" + b"\x00" * 12 + index + b"\x00" * 17 + frames[0] + frames[1]

    assert fm_save.read_retired_players(data) == {331777: (33987, 2019)}


def _award_place(person: int, club: int, age: int, rating_total: int, apps: int, goals: int, assists: int, votes: int) -> bytes:
    return struct.pack("<IIBHBxBxBxH", person, club, age, rating_total, apps, goals, assists, votes) + b"\x02\x00\x40\x00\x00\xbd\x00"


def test_award_history_rows_give_three_places():
    empty = b"\xff" * 4 + b"\x00" * 22
    row = struct.pack("<HH", 2062, 7) + _award_place(530491, 630, 27, 2279, 31, 22, 4, 335) + _award_place(5, 985, 31, 3309, 44, 34, 14, 333) + empty
    awards = fm_save.read_award_history(b"\x03\x01tmc.\x01\x00" + row)

    (year,) = awards
    assert (year.year, year.award_key, len(year.places)) == (2062, 7, 2)
    winner = year.places[0]
    assert (winner.person, winner.club, winner.age, winner.appearances, winner.goals, winner.votes) == (530491, 630, 27, 31, 22, 335)
    assert winner.average_rating == 7.35


def test_award_keys_come_from_award_records():
    record = lambda key, uid: b"\x01\x00\x6c\x07" + struct.pack("<III", key, uid, uid) + b"\x00\x00\x00\x01"  # noqa: E731
    game_db = b"\x05" * 20 + record(0, 102406) + b"\x05" * 7 + record(3, 102409) + record(77, 999)  # 999 is not an award

    assert fm_save.read_award_keys(game_db, [102406, 102407, 102408, 102409]) == {0: 102406, 1: 102407, 2: 102408, 3: 102409}


def test_best_elevens_hold_eighteen_places_grouped_by_team():
    entry = lambda person, apps, goals, rating, position: (  # noqa: E731
        struct.pack("<IHHI", person, apps, goals, rating) + b"\x02" + struct.pack("<I", position) + b"\x02" + b"\x00" * 4 + b"\x02" + struct.pack("<I", position)
    )
    players = [entry(100 + k, 30, k, 2100, 1 << (k % 15)) for k in range(17)] + [b"\xff" * 4 + b"\x00" * 23]
    row = struct.pack("<H", 2022) + b"".join(players) + b"\x01" + b"\x00" * 11 + struct.pack("<HHIB", 7, 18, 36405, 255)
    ls = b"\x03\x01tad.\x04\x00" + struct.pack("<IIIi", 0, 1, 1, 0) + b"\xfd\x01\x00\x00\x02\x00"

    (team,) = fm_save.read_best_elevens(b"\x03\x01tmc.\x02\x00" + row, ls)

    (season,) = team
    assert (season.year, season.competition_key, len(season.players)) == (2022, 7, 17)
    assert [p.starting for p in season.players].count(True) == 11
    assert fm_save.position_name(season.players[0].position) == "GK" and season.players[0].average_rating == 7.0


def _transfer(person: int, to: int, frm: int, squad: int, fee: int, add_ons: int, kind: int, when: date) -> bytes:
    return (
        b"\x06" + struct.pack("<I", person) + b"\x00" + struct.pack("<I", to) + b"\x00" + struct.pack("<I", frm) + b"\x00"
        + struct.pack("<IIII", squad, fee, fee, add_ons) + bytes([kind]) + struct.pack("<HH", when.timetuple().tm_yday | (11 << 9), when.year)
        + b"\x01\x01" + b"\xff" * 30
    )


def test_transfers_are_read_from_the_plain_part_and_the_inner_blocks():
    old = _transfer(513990, 1467, 541, 1467, 105632104, 105632104, fm_save.TRANSFER, date(2059, 7, 1))
    youth = _transfer(869921, 541, 0xFFFFFFFF, 0xFFFFFFFF, 0, 0, fm_save.YOUTH_INTAKE, date(2059, 3, 15))
    current = _transfer(869921, 1467, 541, 1467, 1000000, 1000000, fm_save.LOAN, date(2061, 7, 9))
    data = b"\x03\x01tad.\x0c\x00" + current + b"\x00" * 7 + _compress(old + youth)

    found = sorted(fm_save.read_transfers(data), key=lambda t: t.date)

    assert [(t.person, t.kind) for t in found] == [(869921, fm_save.YOUTH_INTAKE), (513990, fm_save.TRANSFER), (869921, fm_save.LOAN)]
    assert (found[0].from_team, found[0].to_team) == (None, 541)
    assert (found[1].fee, found[1].date, found[1].squad) == (105632104, date(2059, 7, 1), 1467)


def test_national_teams_belong_to_nations_not_clubs():
    buffer = (
        b"\x07" * 50 + _club_record(138, "Racing White Daring Molenbeek", "RWDM")
        + _team_record(670, 138, 765, national=True) + _team_record(142771, 138, 2000291910, age=21, national=True)
        + _team_record(700, 138, 900)
    )
    clubs = fm_save.read_clubs(buffer)
    teams = fm_save.read_teams(buffer, clubs)
    nations = fm_save.national_team_nations(teams, {765: fm_save.NationName(765, "England", "England", "ENG", "English")})
    names = {index: nation.name for index, nation in nations.items()}

    assert (teams[670].national, teams[700].national) == (True, False)
    assert [fm_save.team_name(teams[n], clubs, names) for n in (670, 142771, 700)] == [
        "England", "England U21", "Racing White Daring Molenbeek"
    ]


def test_team_number_zero_is_a_real_team():
    # Algeria's senior team is the first in the game's list (team 0, nation index 0 stored as 0)
    buffer = b"\x07" * 50 + _team_record(0, -1, 5, national=True) + b"\x00" + _fixture_header(5097, 9, 0, 698, date(2062, 6, 26))

    teams = fm_save.read_teams(buffer, {})
    fixture = next(fm_save.iter_fixtures(buffer))

    assert (teams[0].fm_uid, teams[0].national) == (5, True)
    assert (fixture.home_id, fixture.away_id, fixture.date) == (0, 698, date(2062, 6, 26))


def test_appearance_and_nationality_follow_the_birth_date():
    names = struct.pack("<I", 9) + b"\x00" + struct.pack("<I", 8) + b"\x00" + struct.pack("<I", 9) + b"\x00" * 5
    after_birth = bytes([0xff, 3, 5, 2, 0xff, 139])  # +4 unused, +5 ethnicity, +6 hair colour, +7 hair length, +8 skin tone, +9 nation + 1
    record = (b"\x05" * 21 + names + struct.pack("<HH", 21, 2043) + after_birth + b"\x05" * 150
              + struct.pack("<III", 869921, 2002850455, 2002850455) + b"\x10\x00" + b"\x01" * 16)

    found = fm_save.read_appearances(b"\x07" * 60 + record)

    assert found == {2002850455: fm_save.Appearance(138, 3, None, 5, 2)}
    assert fm_save.ETHNICITIES[3] == "African/Caribbean" and fm_save.HAIR_LENGTHS[2] == "medium"


def test_the_current_team_is_the_newest_club_in_the_team_history():
    teams = {541: fm_save.Team(541, 0, 630, None, "", ""), 676: fm_save.Team(676, 4, 1755, None, "", ""),
             670: fm_save.Team(670, 30, 765, None, "", "", national=True)}
    assert fm_save.current_team((541, 670, 541), teams) == 541
    assert fm_save.current_team((670, 676, 541), teams) == 676  # the newest entry is the country of the league
    assert fm_save.current_team((0xFFFFFFFF, 12345678, 670), teams) is None  # no club


def test_a_club_has_its_own_country_and_the_country_of_its_league():
    england, wales = 138, 174
    record = (struct.pack("<III", 409, 624, 624) + b"\x00" + struct.pack("<I", england + 1) + b"\xff" * 4
              + struct.pack("<II", wales + 1, england + 1) + b"\x7b\x1f\x00\x00" + b"\x00\x0a\x00\x00\x00\x00"
              + _string("Cardiff City") + _string("Cardiff") + b"\x07" * 30)

    club = fm_save.read_clubs(b"\x07" * 50 + record)[409]

    assert (club.name, club.nation_index, club.league_nation_index) == ("Cardiff City", wales, england)


def test_a_historic_person_has_the_written_name_before_the_row():
    def row(number: int, uid: int) -> bytes:
        return struct.pack("<III", number, uid, uid)

    def named(text: str) -> bytes:
        data = text.encode()
        return b"\x05\x00\x00\x00\x00" + struct.pack("<I", len(data)) + data + b"\x00\x01\x40\x10\x00\x00\x00\x00"

    data = b"\x07" * 40 + row(63300, 2002047664) + named("Chris Solly") + row(63301, 2002047665) + named("Peter Osgood") + row(63302, 2002047666)
    osgood = data.rindex(struct.pack("<I", 63302) + struct.pack("<I", 2002047666))
    solly = data.rindex(struct.pack("<I", 63301) + struct.pack("<I", 2002047665))

    assert fm_save._written_name_before_row(data, osgood) == fm_save.PersonName("Peter Osgood", "Peter", "Osgood")
    assert fm_save._written_name_before_row(data, solly).name == "Chris Solly"
    assert fm_save._written_name_before_row(data, data.index(struct.pack("<I", 63300))) is None


def _relation(subject: int, kind: tuple[int, int], level: int, reason: int = 1, permanent: bool = False, stadium: bool = False) -> bytes:
    return (struct.pack("<I", subject) + b"\x00" * 4 + struct.pack("<H", reason) + bytes(kind)
            + bytes([level, 0x4F if permanent else 0, int(stadium), 0xFF]))


def test_club_relations_belong_to_the_club_record_they_lie_in():
    favourites = [_relation(63301, (3, 1), 100, permanent=True, stadium=True), _relation(830949, (3, 1), 100, reason=2)]
    disliked = [_relation(7001, (3, 2), 60, reason=2, permanent=True)]
    rivals = [_relation(387, (1, 5), 75, reason=2)]  # Arsenal: club index 386, kept as index + 1
    stray = [_relation(5, (3, 1), 40), _relation(6, (3, 1), 40)]  # its count byte does not match: not a list
    chelsea = (_club_record(400, "Chelsea", "Chelsea") + bytes([2]) + b"".join(favourites) + b"\x07" * 3 + bytes([1]) + b"".join(disliked)
               + b"\x07" * 3 + bytes([1]) + b"".join(rivals) + b"\x07" * 3 + bytes([9]) + b"".join(stray))
    arsenal = _club_record(386, "Arsenal", "Arsenal") + bytes([1]) + _relation(7001, (3, 1), 85, reason=1) + b"\x07" * 10

    found = fm_save.read_club_relations(b"\x07" * 50 + chelsea + arsenal)

    assert sorted(found) == [386, 400]
    by_kind = {(r.relation, r.subject): r for r in found[400]}
    assert set(by_kind) == {("favourite", 63301), ("favourite", 830949), ("disliked", 7001), ("rival", 386)}
    legend = by_kind[("favourite", 63301)]
    assert (legend.level, legend.permanent, legend.names_stadium, legend.reason) == (100, True, True, 1)
    assert not by_kind[("favourite", 830949)].permanent
    assert fm_save.RIVAL_REASONS[by_kind[("rival", 386)].reason] == "Competitive"
    assert fm_save.PERSON_REASONS[by_kind[("disliked", 7001)].reason] == "Manager"
    assert [(r.relation, r.subject, r.level) for r in found[386]] == [("favourite", 7001, 85)]


def _match_player(person: int, rating: int, metres: float, xg: float = 0.0, shot_parts: tuple[int, ...] = (),
                  items: int = 0, shirt: int = 0, slots: tuple[int, int] = (0, 0), **figures: int) -> bytes:
    """``02`` and one player record of a match report; ``shot_parts`` gives each shot's part count, ``slots`` the
    position bits at kick-off and at the end."""
    body = bytearray(fm_save._MP_SHOT_COUNT + 1)
    struct.pack_into("<I", body, 0, person)
    body[fm_save._MP_SHIRT] = shirt
    struct.pack_into("<H", body, fm_save._MP_START_SLOT, slots[0])
    struct.pack_into("<H", body, fm_save._MP_END_SLOT, slots[1])
    struct.pack_into("<f", body, fm_save._MP_XG, xg)
    struct.pack_into("<H", body, fm_save._MP_RATING, rating)
    struct.pack_into("<f", body, fm_save._MP_DISTANCE, metres)
    figures.setdefault("shots", len(shot_parts))
    for field, value in figures.items():
        body[fm_save._MP[field]] = value
    body[fm_save._MP_SHOT_COUNT] = len(shot_parts)
    for parts in shot_parts:
        body += b"\x00\x10" + bytes([parts]) + (b"\x07" * 13 + b"\x00\x10") * parts
    body += b"\x00\x00\x00" + bytes([items]) + b"\x05" * 11 * items
    return b"\x02" + bytes(body)


def _match_part(team: int, players: list[bytes], passes: tuple[int, int], corners: int) -> bytes:
    block = bytearray(40)
    block[31 + fm_save._MT["corners"]] = corners
    struct.pack_into("<HH", block, 31, *passes)
    manager = b"\x02" + struct.pack("<I", 88691) + b"\x7a\x26" * 3 + b"\x00\x00"
    return b"\x02" + struct.pack("<I", team) + bytes([len(players)]) + b"\x02\x00\x00" + bytes(block) + manager + b"".join(players)


def test_match_reports_read_teams_players_referee_and_player_of_the_match():
    home, away = 541, 1467
    head = (_string("Friendly") * 2 + b"\x02" + struct.pack("<I", 46718) + b"\xdd\x1c\x39\x77" + _string("Drogba Arena")
            + b"\x02\x02" + struct.pack("<I", home) + b"\x02" + struct.pack("<I", 630) + b"\x00\x00\x01"
            + struct.pack("<II", 415, 630) + b"\x02\x02" + struct.pack("<I", away))
    referee = b"\x09\x02" + struct.pack("<I", 628921) + b"\x02\x2f\x65\x5d\x77\x02" + b"\xff" * 4 + _string("Wes") + _string("Noble")
    pom = b"\x02" + struct.pack("<I", 531574) + b"\x13\x00" + struct.pack("<H", 90) + b"\x00" * 5
    laing = _match_player(531574, 734, 8136.0, xg=0.14, shot_parts=(1, 2), items=1, goals=1, shots_on_target=1,
                          passes=34, passes_completed=26, key_passes=3, assists=1, minute_on=43, shirt=10, slots=(0, 1024), goals_outside_box=1,
                          mistakes_leading_to_goal=1, long_shots=1, half_chances=1, blocked_shots=1, shots_outside_box=1)
    unused = _match_player(719289, 670, 0.0, shirt=33)
    keeper = _match_player(455161, 697, 4267.0, passes=21, passes_completed=7, clearances=9, shirt=1, slots=(1, 1))
    stream = (b"\x00" * 30 + head + referee + b"\x00" * 20 + pom
              + _match_part(home, [laing, unused], (34, 26), corners=15) + b"\x00" * 300
              + _match_part(away, [keeper], (21, 7), corners=3) + b"\x00" * 50)
    # an international: a national team's part has 00 and a nation index where a club's has 01 and its club index
    ukraine, romania = 705, 695
    intl_head = (_string("Euro Qualifying") * 2 + b"\x02" + struct.pack("<I", 6773) + b"\x1e\xdb\x13\x00" + _string("NSC Olympiyskyi")
                 + b"\x02\x02" + struct.pack("<I", ukraine) + b"\x02" + struct.pack("<I", 800) + b"\x00\x00\x00"
                 + struct.pack("<II", 174, 800) + b"\x02\x02" + struct.pack("<I", romania))
    gk = _match_player(563794, 738, 6700.0, passes=21, passes_completed=20, shirt=1, slots=(1, 1), goals_conceded=0, saves_held=0)
    stream += (intl_head + referee + b"\x00" * 20 + pom + _match_part(ukraine, [keeper], (415, 477), corners=2) + b"\x00" * 300
               + _match_part(romania, [gk], (550, 496), corners=6) + b"\x00" * 50)

    # a youth game: a squad-kind byte (10 for an U21 side) where a first team has 0
    chelsea_u21, fulham_u21 = 81445, 81495
    youth_head = (_string("U21 Premier Division Cup") * 2 + b"\x02" + struct.pack("<I", 9001) + b"\xb7\x18\x39\x77" + _string("Cobham")
                  + b"\x02\x02" + struct.pack("<I", chelsea_u21) + b"\x02" + struct.pack("<I", 630) + b"\x0a\x00\x01"
                  + struct.pack("<II", 387, 602) + b"\x02\x02" + struct.pack("<I", fulham_u21))
    stream += (youth_head + referee + b"\x00" * 20 + pom + _match_part(chelsea_u21, [keeper], (21, 7), corners=4) + b"\x00" * 300
               + _match_part(fulham_u21, [gk], (21, 20), corners=1) + b"\x00" * 50)

    reports = fm_save.read_match_reports(b"\x03\x01tad." + _compress(stream))
    # the international and the youth game are read too (missed until 24 Oct 2026)
    assert [r.stadium for r in reports] == ["Drogba Arena", "NSC Olympiyskyi", "Cobham"]
    assert (reports[1].home.team, reports[1].away.team, reports[1].away.players[0].rating) == (ukraine, romania, 7.38)
    assert (reports[2].home.team, reports[2].away.team, reports[2].home.corners) == (chelsea_u21, fulham_u21, 4)
    reports = reports[:1]

    assert len(reports) == 1
    report = reports[0]
    assert (report.fixture_id, report.stadium, report.referee_name, report.player_of_the_match, report.minutes) == (
        46718, "Drogba Arena", "Wes Noble", 531574, 90)
    assert (report.home.team, report.away.team, report.home.corners, report.away.corners) == (home, away, 15, 3)
    scorer, substitute = report.home.players
    assert (scorer.person, scorer.rating, round(scorer.xg, 2), scorer.goals, scorer.shots, scorer.minute_on) == (531574, 7.34, 0.14, 1, 2, 43)
    assert scorer.goals_outside_box == 1 and scorer.mistakes_leading_to_goal == 1  # offsets 165 and 130
    assert (scorer.long_shots, scorer.half_chances, scorer.blocked_shots, scorer.shots_outside_box, scorer.saves) == (1, 1, 1, 1, 0)
    assert scorer.played and not substitute.played and substitute.rating == 0
    assert report.away.players[0].clearances == 9
    # the team sheet: a substitute has the slot he took, an unused one none, the keeper his slot from kick-off
    assert (scorer.shirt, fm_save.position_name(scorer.start_slot), fm_save.position_name(scorer.end_slot)) == (10, None, "M (C)")
    assert (substitute.shirt, substitute.start_slot, substitute.end_slot) == (33, 0, 0)
    assert fm_save.position_name(report.away.players[0].start_slot) == "GK"
    assert report.possession() == (79, 21)  # completed passes 26 against 7


@pytest.mark.skipif(not os.environ.get("FM_SAVE_TEST_PATH"), reason="set FM_SAVE_TEST_PATH to a real .fm save")
def test_real_save_match_reports_agree_with_their_scores():
    with FmSave(os.environ["FM_SAVE_TEST_PATH"]) as save:
        reports = fm_save.read_match_reports(save.read("match_stats_cfg.dat"))
    assert len(reports) > 100
    assert all(len(r.home.players) == r.home.squad and r.home.corners is not None for r in reports)


def test_a_person_number_before_a_common_doubled_value_still_finds_its_uid():
    # Alan Kardec's number 529760 also stands once before 60000 60000, a value that follows other numbers too
    setting = lambda number: struct.pack("<III", number, 60000, 60000) + b"\x00" * 9  # noqa: E731
    rows = fm_save._person_rows(b"\x07" * 30 + setting(529760) + setting(12345) + b"\x07" * 30
                                + struct.pack("<III", 529760, 2002510294, 2002510294) + b"\x02" + b"\x07" * 30)
    assert rows[529760][0] == 2002510294 and 12345 not in rows


def test_a_person_number_before_two_doubled_values_takes_the_one_between_its_neighbours():
    # Mathias Stokkeland's number 846657 also stands before a doubled 120591360 far from the person rows
    row = lambda number, uid, mark=b"\x10": struct.pack("<III", number, uid, uid) + mark + b"\x07" * 17  # noqa: E731
    game_db = (row(100, 120591360, b"\x02") + b"\x07" * 50_000 + row(98, 2000000098) + row(99, 2000000099)
               + b"\x07" * 300 + row(100, 2002827191, b"\x02") + b"\x07" * 300 + row(101, 2000000101) + row(102, 2000000102))
    assert fm_save._person_rows(game_db)[100][0] == 2002827191


def test_an_old_history_row_with_a_placeholder_season_is_labelled_by_its_year():
    world_cup = fm_save.CompetitionSeason(1, 1930, None, (1,), date(1930, 7, 30), season=(2000, 2000))
    old_league = fm_save.CompetitionSeason(2, 1981, None, (1,), None, season=(1999, 2000))
    current = fm_save.CompetitionSeason(3, 2063, 2062, (1,), date(2063, 5, 20), season=(2062, 2063))
    assert (world_cup.season_label, old_league.season_label, current.season_label) == ("1930", "1980/81", "2062/63")


def _game_day(day_of_year: int, year: int) -> bytes:
    return struct.pack("<HH", day_of_year | 0x3200, year)  # the high bits are not the day (as in the saves)


def test_a_clubs_finances_are_read_from_its_record_and_its_debt_list():
    index = 400
    names = _club_record(index, "Chelsea", "Chelsea")[:-30]
    budgets = (b"\x01\xdd\x1f" + b"\x58\x00\x00\x00" + b"\x00" + bytes([2]) + struct.pack("<II", 666436, 666437)
               + b"\x01" + struct.pack("<I", 500_000_000) + struct.pack("<i", 1_160_074_559) + b"\x07" * 58 + struct.pack("<i", 8_000_000))
    income = (b"\x01\x04" + _game_day(213, 2062) + _game_day(213, 2065) + b"\x01" + struct.pack("<I", 3_901_374)
              + struct.pack("<H", 10000) + b"\x00\x00\x00" + struct.pack("<I", 1_300_458))
    bank = (b"\x00\x00\x48\x42\xff\x00" + struct.pack("<i", -186_079_540) + b"\x00\x00\x01\x00" + b"\x01\x00\x6c\x07" * 3
            + b"\x07" * 25 + bytes([1]) + income)
    record = names + budgets + b"\x07" * 300 + bank + b"\x07" * 40
    debt = (b"\x01" + struct.pack("<IIII", 368_142_402, 1_346_160, 119_020, 0) + _game_day(35, 2086) + bytes([1, 1, 0])
            + _game_day(41, 2061))
    debts = struct.pack("<I", index + 1) + b"\x10\x0c\x00" + struct.pack("<H", 2) + struct.pack("<II", 7, 8) + b"\x07" * 40 + struct.pack("<I", 1) + debt
    buffer = b"\x07" * 50 + record + _club_record(401, "Arsenal", "Arsenal") + b"\x07" * 3000 + debts + b"\x07" * 50

    clubs = fm_save.read_clubs(buffer)
    finances = fm_save.read_club_finances(buffer, clubs)[index]

    assert (finances.bank, finances.transfer_budget, finances.wage_budget) == (-186_079_540, 1_160_074_559, 8_000_000)
    assert finances.incomes == (fm_save.Income(4, date(2062, 8, 1), date(2065, 8, 1), 3_901_374, 1_300_458, True),)
    assert fm_save.INCOME_TYPES[finances.incomes[0].kind] == "General Sponsor"
    (loan,) = finances.debts
    assert (fm_save.DEBT_SOURCES[loan.source], loan.amount, loan.monthly_repayment, loan.monthly_interest) == ("Bank", 368_142_402, 1_346_160, 119_020)
    assert (loan.start, loan.end, loan.in_fpp) == (date(2061, 2, 10), date(2086, 2, 4), True)


def test_a_doubled_value_read_one_byte_early_is_doubtful():
    # "97 19 00 00 de d8 13 00 de d8 13 00 00": read at the 97 it looks like number 0x00199741 with uid 0x13d8de00
    misread = b"\x41\x97\x19\x00\x00\xde\xd8\x13\x00\xde\xd8\x13\x00\x00\xd0\x04"
    assert fm_save._misread_row(misread, 0, struct.unpack_from("<I", misread, 4)[0])
    # Jens Keller's true row: his uid ends in 00 too, but the same form does not fit one byte on
    real = struct.pack("<III", 198144, 0x003A6900, 0x003A6900) + b"\x10\x00\x07\x07"
    assert not fm_save._misread_row(real, 0, 0x003A6900)


def test_a_staff_contract_has_its_wage_and_dates():
    # Ekhi Larrañaga, Zaragoza's assistant manager: £7,087 a week (the game shows £7,000), 10 July 2055 to 30 June 2065
    # (the day he joined, then the current deal's start 15 bytes on: renewed on 1 July 2062; the marker's last byte is a
    # count that moves nothing: 1 here, as Júnior's)
    number, team = 759686, 1507
    before = (struct.pack("<HH", 181, 2065) + struct.pack("<HH", 191 | 0x3200, 2055) + b"\x07" * 11 + struct.pack("<HH", 182 | 0x2a00, 2062)
              + b"\x07" * 15 + b"\x01\x00\x6c\x07\x01")
    contract = struct.pack("<II", number, team) + b"\x00" * 4 + struct.pack("<I", 7087) + bytes([20]) + b"\x00\x00"
    rows = _person_row(number - 1, 2002740219) + _person_row(number, 2002740220) + _person_row(number + 1, 2002740221)
    buffer = b"\x07" * 60 + before + contract + b"\x07" * 150 + rows + b"\x07" * 20
    teams = {team: fm_save.Team(team, 0, 1708, None, "", "")}

    found = fm_save.read_staff_contracts(buffer, teams)[2002740220]

    assert (found.job_name, found.wage, found.started, found.expires, found.signed) == (
        "Assistant Manager", 7087, date(2055, 7, 10), date(2065, 6, 30), date(2062, 7, 1))


def _spell(person: int, start: tuple[int, int], end: tuple[int, int], won: int, drawn: int, lost: int, reason: int,
           titles: int = 0, cups: int = 0) -> bytes:
    return (struct.pack("<I", person) + _game_day(*start) + _game_day(*end) + b"\xff" * 8
            + struct.pack("<HHHBBB", won, drawn, lost, reason, titles, cups) + b"\x00\x00")


def test_the_manager_history_is_one_list_of_spells_per_team():
    # Arsenal's last spells in a 2063 save: Felipe Toro 3 Dec 2062 - 5 Jul 2063, 14-10-12, left the manager role
    records = [_spell(18454, (308, 2062), (337, 2062), 0, 4, 0, 10), _spell(528136, (337, 2062), (186, 2063), 14, 10, 12, 3),
               _spell(22693, (230, 2048), (178, 2053), 120, 40, 30, 3, titles=2, cups=1)]
    dt = b"\x03\x01tmc.\x02\x00" + b"".join(records)
    # list 0: records 0 and 1 (the second offset stored as its distance from the first); list 1: record 2
    ls = b"\x03\x01tad.\x04\x00" + struct.pack("<8i", 0, 2, 2, 0, 31, 1, 62, 0)

    lists = fm_save.read_manager_history(dt, ls)

    assert [len(spells) for spells in lists] == [2, 1]
    toro = lists[0][1]
    assert (toro.person, toro.started, toro.ended, toro.won, toro.drawn, toro.lost) == (528136, date(2062, 12, 3), date(2063, 7, 5), 14, 10, 12)
    assert fm_save.MANAGER_LEAVING[toro.reason] == "Left manager role" and lists[0][0].reason == fm_save.CARETAKER_LEFT
    assert (lists[1][0].league_wins, lists[1][0].cup_wins) == (2, 1)


INJURIES = ["cold", "flu", "virus", "food poisoning", "serious viral infection", "twisted ankle"] + [f"injury {n}" for n in range(6, 40)]
INJURIES[11], INJURIES[31], INJURIES[36] = "sprained ankle ligaments", "torn hamstring", "hip injury"


def _injury_record(injury: int, team: int, kind: int, day: int, year: int, days: int, cause: int, treatment: int) -> bytes:
    return struct.pack("<HIBBHHHBB", injury, team, kind, 1, day | (60 << 9), year, days, cause, treatment)


def test_injury_history_comes_in_groups_with_cause_treatment_and_team():
    records = [_injury_record(11, 541, 2, 192, 2063, 23, 5, 1),  # Ramadan, 11 Jul 2063: sprained ankle ligaments, 3 weeks
               _injury_record(3, 0xFFFFFFFF, 3, 233, 2062, 3, 0, 32)]  # 21 Aug 2062: food poisoning, the club doctor
    dt = b"\x03\x01tmc.\x02\x00" + b"".join(records)
    ls = (b"\x03\x01tad.\x04\x00" + struct.pack("<I", 0) + struct.pack("<II", 1, 2) + struct.pack("<2i", 0, 16)
          + struct.pack("<II", 0, 0))

    (group,) = fm_save.read_injury_history(dt, ls, INJURIES)

    illness, sprain = group  # oldest first
    assert (sprain.injury, sprain.injured_on, sprain.days, sprain.team) == ("sprained ankle ligaments", date(2063, 7, 11), 23, 541)
    assert (sprain.severity, sprain.place, fm_save.INJURY_CAUSES[sprain.cause], fm_save.INJURY_TREATMENTS[sprain.treatment]) == (
        "Moderate", "In match", "Sprinting", "Physio")
    assert (illness.injury, illness.severity, illness.place, illness.team) == ("food poisoning", "Slight", None, None)
    assert fm_save.INJURY_TREATMENTS[illness.treatment] == "Club Doctor"


def test_current_injuries_in_a_match_and_in_training_belong_to_the_next_contract_part():
    kemal = bytes.fromhex("01 02 1f 00 01 01 df 4a 0f 08 a2 00 00 00 02 00 66 02 00 00 00 4e 02 00 00 01 02 cf 0a 0d 00 df 58 0f 08"
                          "2a 00 3d 00 01 02 00 ff ff ff ff 00 66 02 00 00 01 ff ff ff ff 00 05 00 ff ff")
    arwyn = bytes.fromhex("01 02 24 00 00 d0 42 0f 08 2f 00 44 00 03 02 00 ff ff ff ff 00 6f 02 00 00 01 ff ff ff ff 00 07 00 ff ff")
    game_db = b"\x00" * 10 + kemal + b"\x00" * 50 + arwyn + b"\x00" * 50
    anchors = [(10 + len(kemal) + 20, 2002732458, 101755), (len(game_db) - 10, 2002533602, None)]

    found = fm_save.read_current_injuries(game_db, INJURIES, anchors)

    (torn,) = found[2002732458]
    assert (torn.injury, torn.injured_on, torn.days, torn.team, torn.severity) == ("torn hamstring", date(2063, 8, 11), 61, 614, "Major")
    assert (fm_save.INJURY_CAUSES[torn.cause], fm_save.INJURY_TREATMENTS[torn.treatment]) == ("Sprinting", "Specialist")
    (hip,) = found[2002533602]
    assert (hip.injury, hip.injured_on, hip.days, hip.team, hip.place) == ("hip injury", date(2063, 7, 27), 68, None, "In training")
    assert fm_save.read_injury_groups(game_db, anchors) == {2002732458: 101755}


def test_the_contract_part_holds_the_injury_group():
    ramadan = bytes.fromhex("df 4a 0f 08 1d 02 00 00 1d 02 00 00 df 58 0f 08 df 58 0f 08 06 00 c6 aa 00 00 03 00 00 00 01")
    match = fm_save._INJURY_ANCHOR.search(b"\x05" * 7 + ramadan)
    assert struct.unpack("<I", match.group(7))[0] == 43718


def test_a_player_records_shots_are_read_in_match_order():
    def shot(across, height, outcome, minute, second):
        return struct.pack("<ff", across, height) + bytes([outcome, minute, second, 0, minute, 0x20, 0x40])

    record = bytearray(fm_save._MP_SHOT_COUNT) + bytes([2])
    record += bytes([0x20, 0x40, 2]) + shot(0.72, 3.72, 2, 84, 47) + shot(-34.57, 0.11, 3, 57, 53)  # Parkins: two in one entry
    record += bytes([0x40, 0x04, 1]) + shot(-1.93, 0.2, 1, 18, 38)  # Schwindt's goal, 19'

    shots = fm_save._match_shots(bytes(record), 0)

    assert [(s.minute, s.second, fm_save.SHOT_OUTCOMES[s.outcome]) for s in shots] == [
        (18, 38, "goal"), (57, 53, "woodwork"), (84, 47, "off target")]
    assert (shots[0].across, shots[0].height) == (-1.93, 0.2)


def _staff_block(coaching: dict[str, int], formations=(29, 29, 4, 4), style=4, mentality=7, pressing=15, youngsters=11) -> tuple[bytes, int]:
    """A staff block as the save keeps it; returns the bytes and the place of the Attacking byte (A)."""
    block = bytearray(120)
    at = 60
    struct.pack_into("<HHH", block, at - 50, 9913, 8912, 9043)
    struct.pack_into("<Hh", block, at - 44, 160, 170)
    block[at - 40] = 0x10
    for i, value in zip(range(-39, -35), formations):
        block[at + i] = value
    block[at - 35] = 10
    for i in [*range(-34, -21), *range(-20, -8)]:
        block[at + i] = 10  # tactical preferences, 1-20
    block[at - 24], block[at - 22], block[at - 10] = pressing, youngsters, mentality
    for name, offset in fm_save.STAFF_COACHING.items():
        block[at + offset] = coaching[name] * 5
    block[at + fm_save._STAFF_STYLE] = style
    return bytes(block), at


def test_a_managers_profile_gives_his_coaching_formations_and_style():
    coaching = {"attacking": 17, "defending": 10, "fitness": 6, "mental": 16, "technical": 14, "tactical": 15, "set_pieces": 2}
    block, _ = _staff_block(coaching)
    game_db = b"\x07" * 500 + block + b"\x07" * 2000
    profile = fm_save.read_staff_profile(game_db, len(game_db) - 10)
    assert profile.coaching == coaching and profile.working_with_youngsters == 11
    assert profile.formations == (29, 29, 4, 4) and fm_save.FORMATION_NAMES[profile.formations[0]] == "4-2-3-1 DM AM Wide"
    assert fm_save.TACTICAL_STYLES[profile.tactical_style] == "Wing Play" and (profile.mentality, profile.pressing) == (7, 15)
    assert profile.coaching_style == "Attacking"  # his highest coaching attribute, as the game shows it
    block, _ = _staff_block(coaching, style=255)
    assert fm_save.read_staff_profile(b"\x07" * 500 + block + b"\x07" * 100, 700).tactical_style is None


def test_the_human_clubs_partnerships_are_read_from_their_table():
    def record(a, b, total, scored, conceded, games, departed, roles):
        return (b"\x03" + struct.pack("<IIHHHH", a, b, total, scored, conceded, games) + bytes([0, departed]) + b"\x03"
                + struct.pack("<H", roles[0]) + b"\x03" + struct.pack("<H", roles[1]))
    table = struct.pack("<H", 2) + record(10, 11, 1256, 58, 7, 17, 0, (0x79BF, 0x7BFA)) + record(12, 13, 220, 6, 4, 3, 1, (1, 2))
    found = fm_save.read_partnerships(b"\x07" * 300 + table + b"\x07" * 300)
    assert [(p.person_a, p.person_b, p.matches, p.average_rating, p.goals_for, p.goals_against, p.departed) for p in found] == [
        (10, 11, 17, 7.39, 58, 7, False), (12, 13, 3, 7.33, 6, 4, True)]
    assert found[0].role_a == 0x79BF


def _concern(kind: int, code: int, stage: int, ref: int | None, ref2: int | None, started: tuple[int, int], ended: tuple[int, int] = (1, 1900)) -> bytes:
    none = 0xFFFFFFFF
    return (bytes([7, kind]) + struct.pack("<HB", code, stage) + struct.pack("<II", none if ref is None else ref, none if ref2 is None else ref2)
            + struct.pack("<HH", *started) + struct.pack("<HH", *ended) + b"\x00" * 8)


def _feeling(subject: int, reason: int, kind: int, sub: int, level: int) -> bytes:
    return b"\xff" + struct.pack("<I", subject) + b"\x00" * 4 + struct.pack("<H", reason) + bytes([kind, sub, level]) + b"\x00\x00"


def test_a_players_mood_block_has_his_happiness_concerns_and_feelings():
    # Maurício on 19 October 2063: happiness -40, left out of the Champions League squad (told his team-mates), and a
    # team-mate who thinks someone has been treated unfairly; his contract dates stand before the concerns
    number, team = 759686, 541
    concerns = [_concern(32, 596, 2, 15, None, (252, 2063)), _concern(19, 808, 1, number + 1, 15, (252, 2063)),
                _concern(12, 652, 255, None, None, (40, 2062), (100, 2062))]
    block = b"\x00" + struct.pack("<HH", 191, 2055) + struct.pack("<b", -40) + b"\x07" * 14 + b"\x01\x00\x6c\x07\x00"
    before = (struct.pack("<HH", 181, 2065) + struct.pack("<HH", 191 | 0x3200, 2055) + b"\x07" * 6
              + struct.pack("<I", len(concerns)) + b"".join(concerns) + block)
    contract = struct.pack("<II", number, team) + b"\x00" * 4 + struct.pack("<I", 90_000) + bytes([fm_save.PLAYER_JOB]) + b"\x00\x00"
    feelings = (_feeling(number + 1, 5, 3, 1, 60) + _feeling(number - 1, 0, 3, 2, 50) + _feeling(77, 0, 1, 0, 100)
                + _feeling(number + 1, 6, 3, 1, 20))
    rows = _person_row(number - 1, 2002740219) + _person_row(number, 2002740220) + _person_row(number + 1, 2002740221)
    buffer = b"\x07" * 60 + before + contract + b"\x07" * 20 + feelings + b"\x07" * 100 + rows + b"\x07" * 20
    teams = {team: fm_save.Team(team, 0, 630, None, "", "")}

    found = fm_save.read_staff_contracts(buffer, teams)[2002740220]

    assert found.happiness == -40
    assert (found.started, found.expires) == (date(2055, 7, 10), date(2065, 6, 30))
    left_out, backing, settled = found.concerns
    assert ((left_out.kind, left_out.code), left_out.stage, left_out.ref, left_out.ended) == (fm_save.LEFT_OUT, fm_save.COMPLAINING, 15, None)
    assert left_out.started == date(2063, 9, 9)
    assert ((backing.kind, backing.code), backing.person, backing.ref2) == (fm_save.TREATED_UNFAIRLY, 2002740221, 15)
    assert settled.ended == date(2062, 4, 10)
    assert [(f.person, f.likes, f.level) for f in found.feelings] == [(2002740221, True, 60), (2002740219, False, 50), (2002740221, True, 20)]


def test_a_contract_without_a_mood_block_has_no_mood():
    number, team = 759686, 1507
    before = struct.pack("<HH", 181, 2065) + struct.pack("<HH", 191 | 0x3200, 2055) + b"\x07" * 30 + b"\x01\x00\x6c\x07\x00"
    contract = struct.pack("<II", number, team) + b"\x00" * 4 + struct.pack("<I", 7087) + bytes([20]) + b"\x00\x00"
    rows = _person_row(number - 1, 2002740219) + _person_row(number, 2002740220) + _person_row(number + 1, 2002740221)
    buffer = b"\x07" * 60 + before + contract + b"\x07" * 150 + rows + b"\x07" * 20

    found = fm_save.read_staff_contracts(buffer, {team: fm_save.Team(team, 0, 1708, None, "", "")})[2002740220]

    assert (found.happiness, found.concerns, found.feelings) == (None, (), ())


def test_morale_stands_in_the_reputation_row():
    uid = 2002740220
    row = bytearray(b"\x00" * 4 + struct.pack("<II", uid - 1, uid - 1) + b"\x02" + b"\x00" * 200)
    struct.pack_into("<HHH", row, fm_save._REPUTATION_VALUES, 5000, 6000, 4000)
    row[fm_save._MORALE] = 8
    struct.pack_into("<HhH", row, fm_save._FITNESS, 8600, -151, 9050)  # Maurício in the game's editor, 28 October 2063
    found = fm_save.read_person_reputations(bytes(row), [uid])[uid]
    assert (found.morale, found.sharpness, found.fatigue, found.condition) == (8, 8600, -151, 9050)
    struct.pack_into("<h", row, fm_save._FITNESS + 2, 30561)  # outside -500..500: not this layout
    assert fm_save.read_person_reputations(bytes(row), [uid])[uid].fatigue is None
    row[fm_save._MORALE] = 0
    assert fm_save.read_person_reputations(bytes(row), [uid])[uid].morale is None


def test_the_human_clubs_social_groups_are_lists_of_people():
    numbers = list(range(500, 515))
    rows = b"".join(_person_row(n, 2002000000 + n) for n in numbers)
    groups = b"\x01" + struct.pack("<I", 541)
    for members in (numbers[:6], numbers[6:11], numbers[11:]):
        groups += struct.pack("<H", len(members)) + b"".join(struct.pack("<I", n) for n in members)
    buffer = b"\x07" * 40 + b"\x01" + struct.pack("<I", 541) + b"\x02\x00\x07\x07" + b"\x07" * 20 + groups + b"\x00\x00" + b"\x07" * 40 + rows

    found = fm_save.read_social_groups(buffer, 541)

    assert [len(group) for group in found] == [6, 5, 4]
    assert found[1][0] == 2002000506
    assert fm_save.read_social_groups(buffer, 999) == []


def test_the_transfer_market_lists_and_bids_are_read():
    """A club's wanted players (record 09 00 01 00 + team, a count, 28-byte entries) and a bid (02 0d 00 + person, team, squad,
    fee, two u32, a status, dates); the file's duplicates are dropped."""
    def entry(person: int, wage: int, offered: int, flag: int = 0) -> bytes:
        return b"\x11\x00" + struct.pack("<I", person) + b"\x04" + struct.pack("<I", wage) + b"\x00" * 4 + struct.pack("<I", offered) + b"\x00" * 6 + bytes([flag]) + b"\x00\x00"

    juventus = b"\x09\x00\x01\x00" + struct.pack("<I", 988) + b"\x00" * 40 + struct.pack("<I", 2) + entry(798856, 36432, 37734) + entry(779308, 38932, 38932)
    bid = (b"\x02\x0d\x00" + struct.pack("<I", 953402) + b"\x00" + struct.pack("<I", 541) + b"\x00" + struct.pack("<I", 541) + struct.pack("<I", 5_906_135)
           + struct.pack("<II", 818, 916033) + bytes([0x13]) + b"\xff" * 3 + b"\x00" * 10
           + struct.pack("<HH", 328, 2063) + b"\x00" * 6 + struct.pack("<HH", 358, 2063) + b"\x00" * 6 + struct.pack("<HH", 161, 2064) + b"\x00" * 40)
    data = b"\x00" * 7 + juventus + b"\x00" * 9 + bid + b"\x00" * 5 + juventus + bid  # each stored twice, as the game does
    interest = fm_save.read_transfer_interest(data)
    assert [(i.team, i.person, i.wage, i.offered_wage, i.rumoured) for i in interest] == [(988, 798856, 36432, 37734, False), (988, 779308, 38932, 38932, True)]
    (offer,) = fm_save.read_transfer_offers(data)
    assert (offer.person, offer.team, offer.squad, offer.fee, offer.status_word) == (953402, 541, 541, 5_906_135, "agreed")
    assert (str(offer.offered_on), str(offer.until), str(offer.completes_on)) == ("2063-11-24", "2063-12-24", "2064-06-09")


def _player_game(day: date, opponent: int, competition: int, played: bool = True, **figures) -> bytes:
    """A record of a tracked player's recent game as the save keeps it: 54 bytes when played, 15 when not."""
    head = b"\x01" + struct.pack("<HH", day.timetuple().tm_yday, day.year) + struct.pack("<II", opponent, competition)
    if not played:
        return head + struct.pack("<H", fm_save._PG_UNUSED)
    record = bytearray(head + struct.pack("<H", fm_save._PG_PLAYED) + bytes(39))
    record[15:19] = b"\x02\x02\x04\x00"
    for field, value in figures.items():
        record[fm_save._PG[field]] = value
    return bytes(record)


def test_a_tracked_players_recent_games_belong_to_the_person_row_after_them():
    # San Martin's Spain list, as read on 24 Oct 2026: an unused-substitute row, then played rows (Argentina 8 Jul 2062: 58 of 51
    # passes, 7.2, a yellow card; Ukraine 20 Nov 2063: off at 61, xG 0.11)
    spain = (struct.pack("<II", 701, 3) + _player_game(date(2062, 7, 4), 1441, 633, played=False)
             + _player_game(date(2062, 7, 8), 1436, 633, rating=72, passes=58, passes_completed=51, yellow_cards=1, tackles=3, tackles_won=3, minutes=90, distance=117)
             + _player_game(date(2063, 11, 20), 705, 113, rating=68, passes=33, passes_completed=28, minute_off=61, minutes=61, xg=11, assists=1))
    row = lambda number, uid: struct.pack("<III", number, uid, uid)  # noqa: E731
    game_db = (b"\x00" * 40 + row(100, 2002000100) + b"\x07" * 30 + spain + b"\x09" * 50 + row(775067, 2002755601)
               + b"\x00" * 20 + struct.pack("<II", 541, 1) + _player_game(date(2063, 11, 24), 590, 12, rating=70, minutes=90) + b"\x00" * 30)  # no row after
    games = fm_save.read_player_games(game_db, {100: 2002000100, 775067: 2002755601})
    assert [(g.person, g.team, str(g.date), g.opponent, g.status_word) for g in games] == [
        (775067, 701, "2062-07-04", 1441, "unused"), (775067, 701, "2062-07-08", 1436, "played"), (775067, 701, "2063-11-20", 705, "played")]
    first, last = games[1], games[2]
    assert first.figures["rating"] == 72 and first.figures["passes_completed"] == 51 and first.figures["yellow_cards"] == 1 and first.position_name == "D (R)"
    assert last.figures["minute_off"] == 61 and last.figures["xg"] == 11 and last.figures["assists"] == 1 and games[0].figures is None


def test_press_answers_are_read_with_their_conference_and_rendered_in_spirit():
    """press_conference_manager.dat (Exhibit 5, 26 Oct 2026): Dysart (Reading) answered question 20331 with answer 5 about
    Pladevall (Liverpool) on 19 Dec 2063; the game's headline for it is 'Dysart claims Liverpool were lucky'."""
    when = struct.pack("<HH", 353 | 0x8000, 2063)  # 19 Dec, with a flag bit
    head = b"\x01\x08\x01\x02\x02\x00" + struct.pack("<I", 612) + when + b"\x00" + struct.pack("<I", 612) + b"\x00" + struct.pack("<I", 586)
    answer = bytearray(when + struct.pack("<II", 174264, 586) + b"\x10\x1a\x00\x80\x3d\x01\x00\x00" + b"\xff" * 12 + bytes(80))
    struct.pack_into("<H", answer, 50, 20331)
    answer[71] = 5
    unanswered = bytearray(answer)
    unanswered[71] = 0
    (found,) = fm_save.read_press_answers(b"\x07" * 40 + head + b"\x07" * 60 + bytes(answer) + bytes(unanswered))
    assert (found.day, found.team, found.opponent, found.question, found.answer, found.subject, found.subject_team) == \
        (date(2063, 12, 19), 612, 586, 20331, 5, 174264, 586)
    assert fm_save.render_headline("[%person#1-surname] claims [%team#2-short] were lucky", {1: "Bay Dysart"}, {2: "Liverpool"}) == \
        "Dysart claims Liverpool were lucky"
    assert fm_save.render_headline("[%person#1-surname] dismisses [%person#2-surname]{s} 'fluke result'",
                                   {1: ("Roger Novaes Campos", "Novaes Campos"), 2: "Fábio Carvalho"}, {}) == "Novaes Campos dismisses Carvalho's 'fluke result'"
    assert fm_save.render_headline("[%person#1-surname] hits out at [%person#2-surname]", {1: "Fábio Carvalho"}, {}) is None  # a name missing


def test_news_items_of_the_four_decoded_kinds_are_read_by_their_fixed_places():
    """Exhibits 5-7 (26 Oct 2026): 'Rojas singles out Bradford', 'Lee denies Arsenal interest' (after Secchi left),
    'Stoke fixture decisive for Novaes Campos', 'Hertha Berlin chase López' (£43M)."""
    def item(fields: dict[int, bytes], item_id: int, length: int = 80) -> bytes:
        body = bytearray(b"\x07" * length)
        for at, raw in fields.items():
            body[length + at:length + at + len(raw)] = raw
        news_day = struct.pack("<HH", 360 | 0x6800, 2063)
        return bytes(body[:-4]) + news_day + struct.pack("<I", item_id) + b"\x18\x40\x00\x5f\x00\xff\xff\xff\xff" + b"\x00" * 30

    u32 = lambda v: struct.pack("<I", v)  # noqa: E731
    match_day = struct.pack("<HH", 356, 2063)
    danger = item({-27: u32(513), -23: u32(15555), -19: u32(563560), -13: b"\x05\x01", -11: match_day}, 4931000)
    job = item({-55: u32(700001), -50: u32(611), -45: u32(513), -18: u32(700002), -10: b"\x04\x01"}, 4931001)
    board = item({-72: u32(590), -68: u32(527884), -47: u32(631), -15: match_day, -11: bytes([fm_save.BOARD_PRESSURE])}, 4931002)
    rumour = bytearray(u32(800756) + b"\x03\xfa\x7b" + b"\xff" * 4 + b"\x00" + u32(832) + b"\x05\x01" + u32(43_020_000) + bytes(100))
    rumour[85:89] = u32(1942)
    found = {i.kind: i for i in fm_save.read_news_items(danger + job + board + bytes(rumour))}
    assert (found["danger_man"].person, found["danger_man"].other_person, found["danger_man"].team, found["danger_man"].match_day) == (15555, 563560, 513, date(2063, 12, 22))
    assert (found["job_link"].person, found["job_link"].team, found["job_link"].other_team, found["job_link"].other_person) == (700001, 611, 513, 700002)
    assert (found["board_pressure"].person, found["board_pressure"].team, found["board_pressure"].other_team) == (527884, 590, 631)
    assert (found["transfer_rumour"].person, found["transfer_rumour"].team, found["transfer_rumour"].fee) == (800756, 1942, 43_020_000)
    assert found["job_link"].day == date(2063, 12, 26)


def test_a_counter_proposal_is_the_second_record_of_a_bid_with_blank_answer_dates():
    """Juventus's £71.5m bid for Valera (18 Jan 2064) and Chelsea's counter-proposal of £165m (newest save, 26 Jan 2064):
    the same bidder, player and day; the counter has status 00 and the game's blank 1 Jan 1900 for its answer dates."""
    def record(fee: int, status: int, dates: bytes) -> bytes:
        return (b"\x02\x0d\x00" + struct.pack("<I", 615438) + b"\x00" + struct.pack("<I", 988) + b"\x00" + struct.pack("<I", 988) + struct.pack("<I", fee)
                + struct.pack("<II", 5772, 0x0e0f2b) + bytes([status]) + b"\x16" + bytes(16) + dates + bytes(60))

    offered = struct.pack("<HH", 18 | 0x1000, 2064)
    bid = record(71_474_400, 0x03, b"\xff" * 4 + struct.pack("<HH", 24 | 0x1000, 2064) + b"\x00" + offered + b"\x00" + struct.pack("<HH", 34 | 0x1000, 2064))
    counter = record(165_000_000, 0x00, b"\xff" * 4 + b"\x01\x00\x6c\x07\x00\x01\x00\x6c\x07" + offered)
    offers = {o.fee: o for o in fm_save.read_transfer_offers(bytes(8) + bid + bytes(8) + counter + bytes(8))}
    assert offers[71_474_400].status_word == "withdrawn" and not offers[71_474_400].counter
    assert offers[165_000_000].status_word == "counter" and offers[165_000_000].offered_on == date(2064, 1, 18)
    (alone,) = fm_save.read_transfer_offers(bytes(8) + counter + bytes(8))
    assert alone.status_word == "made"  # without its bid it is not known as a counter


def test_a_loan_offer_is_the_bid_record_with_03_and_no_fee():
    """King's Lynn's loan offer for Chelsea's Spyrou (Exhibit 7, 26 Oct 2026): 03 0d 00, offered 3 Dec 2063, to 27 Mar 2064."""
    offered, until = struct.pack("<HH", 337 | 0x1a00, 2063), struct.pack("<HH", 87 | 0x5a00, 2064)
    record = (b"\x07" * 8 + b"\x03\x0d\x00" + struct.pack("<I", 1021112) + b"\x00" + struct.pack("<I", 67243) + b"\x00" + struct.pack("<I", 67243)
              + struct.pack("<I", 0) + struct.pack("<II", 0x167, 0x0dfb66) + b"\x02" + b"\xff" * 4 + bytes(10) + until + b"\x00" + offered + bytes(20))
    (offer,) = fm_save.read_transfer_offers(record)
    assert (offer.loan, offer.fee, offer.team, offer.person, offer.offered_on, offer.completes_on) == (True, 0, 67243, 1021112, date(2063, 12, 3), date(2064, 3, 27))
    assert offer.status_word == "rejected"  # the user rejected it in the game


def test_a_national_manager_watching_players_is_read_with_each_player():
    """Exhibit 9 (1 Jan 2064): 'The England manager, Jamie Cunningham, was also spotted in the stands, with Tony Konneh and
    Myles Broe thought to be the focus of his attention.' The dates are in 2064: the patterns once took 2063 only."""
    u32 = lambda v: struct.pack("<I", v)  # noqa: E731
    tail = (u32(700100) + bytes(5) + u32(670) + u32(2) + b"\x01" + u32(537843) + b"\x01\x00" + b"\x01" + u32(829178) + b"\x01\x00"
            + b"\x01\x00\x00\x00\x00\xa1\x00")
    news_day = struct.pack("<HH", 1 | 0x5800, 2064)
    record = b"\x07" * 40 + tail + news_day + struct.pack("<I", 4934641) + b"\x18\x40\x00\x00\x00\xff\xff\xff\xff" + bytes(30)
    found = [i for i in fm_save.read_news_items(record) if i.kind == "national_scouting"]
    assert [(i.person, i.team, i.other_person, i.day) for i in found] == [(700100, 670, 537843, date(2064, 1, 1)), (700100, 670, 829178, date(2064, 1, 1))]


def test_a_bids_clauses_give_the_stated_offer_and_its_ceiling():
    """Man Utd for Cosmin Lupu (Exhibit 9): fee 9 189 937 + a 01 01 clause 2 561 440 = 'an offer of £11,750,000'; with the
    18 06 add-on 2 494 411, 'may rise to £14,250,000'."""
    start = b"\x02\x0d\x00" + struct.pack("<I", 744937) + b"\x00" + struct.pack("<I", 590) + b"\x00" + struct.pack("<I", 590) + struct.pack("<I", 9_189_937)
    body = bytearray(start + bytes(200))
    body[118:122] = b"\xff" * 4
    body[122:130] = b"\x01\x01\x01\x02" + struct.pack("<I", 2_561_440)
    body[130:138] = b"\x18\x06\x01\x02" + struct.pack("<I", 2_494_411)
    body[60:64] = struct.pack("<HH", 1 | 0x1a00, 2064)
    (offer,) = fm_save.read_transfer_offers(bytes(body))
    assert (offer.fee, offer.fee_guaranteed, offer.fee_max) == (9_189_937, 11_751_377, 14_245_788)


def test_the_preferred_moves_are_a_bit_field_before_the_birth_date():
    """Exhibits 10-14 (2-3 Nov 2026): Geyer's four moves are bits 20, 36, 51 and 57; a field with too many bits is no field."""
    field = sum(1 << bit for bit in (20, 36, 51, 57))
    buffer = bytes(10) + struct.pack("<Q", field) + bytes(19) + b"DOB!"
    assert fm_save.read_preferred_moves(buffer, 10 + 27) == (
        "Plays One-Twos", "Tries Long Range Free Kicks", "Runs With Ball Often", "Cuts Inside From Left Wing")
    noise = bytes(10) + struct.pack("<Q", (1 << 20) - 1) + bytes(23)
    assert fm_save.read_preferred_moves(noise, 10 + 27) is None
    assert fm_save.read_preferred_moves(buffer, 5) is None  # no room before the birth date
    assert len(fm_save.PREFERRED_MOVES) == 64 and fm_save.PREFERRED_MOVES.count(None) == 5
