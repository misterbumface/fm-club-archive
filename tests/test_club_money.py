# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Club money in the papers (story_leads_money) and the debt reader's choice of club."""

import struct
from datetime import date
from types import SimpleNamespace

from app.models import world
from app.services import fm_save, story_leads_money as money
from app.services.story_leads import _World
from app.services.world_queries import rough_money, rough_pounds
from tests.test_world_pages import AS_OF, app, small_world  # noqa: F401

LIVERPOOL, RICH, MINNOW = 676, 1736, 5555
NOW = date(2064, 4, 13)
BEFORE = date(2063, 7, 15)


def _setup(c):
    c.execute(world.WorldClub.__table__.insert(), [{"fm_uid": u, "name": n, "short_name": n, "as_of": AS_OF} for u, n in ((LIVERPOOL, "Liverpool"), (RICH, "Real Madrid"), (MINNOW, "Minnow Town"))])
    c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": u, "club_uid": u, "nation_uid": None, "name": n, "age_limit": None, "as_of": AS_OF, "reputation": r}
                                                   for u, n, r in ((LIVERPOOL, "Liverpool", 8900), (RICH, "Real Madrid", 8400), (MINNOW, "Minnow Town", 2000))])
    c.execute(world.WorldClubFinance.__table__.insert(), [
        {"club_uid": u, "game_date": d, "bank": b, "transfer_budget": 0, "wage_budget": 0, "yearly_income": 0, "debt": debt}
        for u, d, b, debt in ((LIVERPOOL, BEFORE, -186_079_540, 368_142_402), (LIVERPOOL, NOW, -310_177_463, 368_142_402), (RICH, NOW, 391_000_000, None), (MINNOW, NOW, -500_000_000, 400_000_000))])


def test_money_is_rounded_as_a_paper_would_know_it():
    assert [rough_pounds(p) for p in (-310_177_463, 368_142_402, 190_500_000, 7_345_000)] == [-300_000_000, 350_000_000, 190_000_000, 5_000_000]
    assert rough_money(1_132_000_000) == "around £1.1bn" and rough_money(-310_177_463) == "around £300m"


def test_the_accounts_are_for_great_clubs_at_extremes_once_a_year_and_never_exact(small_world, monkeypatch):
    monkeypatch.setattr(money, "TOP_CLUBS", 0)
    with small_world.begin() as c:
        _setup(c)
    assert money.accounts_day(LIVERPOOL, 2064) == date(2064, 4, 16)
    with small_world.connect() as c:
        assert money.accounts(_World(c, date(2064, 3, 1), date(2064, 4, 10), ())) == []  # not its day yet
        leads = money.accounts(_World(c, date(2064, 4, 10), date(2064, 4, 20), ()))
    assert [lead.refs for lead in leads] == [[f"club:{LIVERPOOL}"]]  # Minnow Town's sums are bigger; it is nobody's story
    facts = leads[0].facts
    assert facts["bank_balance_reported"] == "around £300m overdrawn" and facts["bank_debt_reported"] == "around £350m"
    assert facts["since_earlier_this_season"] == "the balance is around £120m worse than at 2063-07"
    assert "310" not in str(facts) and "368" not in str(facts)


def test_a_docked_club_is_told_once_and_a_small_one_not_at_all(small_world, monkeypatch):
    monkeypatch.setattr(money, "TOP_CLUBS", 0)
    with small_world.begin() as c:
        _setup(c)
        c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 12, "name": "Championship", "short_name": "C", "abbreviation": "C"}])
        c.execute(world.WorldPointDeduction.__table__.insert(), [{"competition_uid": 12, "team_uid": t, "as_of": d, "points": 12, "played": 30}
                                                                 for t, d in ((LIVERPOOL, date(2064, 2, 1)), (LIVERPOOL, date(2064, 3, 1)), (MINNOW, date(2064, 2, 1)))])
    with small_world.connect() as c:
        leads = money.docked(_World(c, date(2064, 1, 1), date(2064, 2, 10), ()))
        assert money.docked(_World(c, date(2064, 2, 10), date(2064, 3, 10), ())) == []  # the same points, a later save
    assert [lead.headline for lead in leads] == ["Liverpool docked 12 points"]
    assert leads[0].facts["points_taken_off"] == 12


def test_a_debt_list_goes_to_the_nearest_header_whatever_its_flag_byte():
    block = struct.pack("<I", 460) + bytes([0x10, 0x0f, 0xff]) + struct.pack("<H", 2) + struct.pack("<II", 4119, 12158)
    buffer = bytes(40) + block + bytes(20) + struct.pack("<I", 1) + bytes(40)
    assert fm_save._debt_owner(buffer, 40 + len(block) + 24, 0, {459: object()}) == 459


def test_debt_owners_are_the_longest_rising_chain():
    assert fm_save._rising_owners([5, 9000, 7, None, 8]) == {0: 5, 2: 7, 4: 8}


def test_a_table_with_one_short_row_is_a_deduction_and_a_split_league_is_not(monkeypatch):
    def row(team, won, drawn, points):
        return SimpleNamespace(club_id=team, won=won, drawn=drawn, points=points, played=40)
    tables = {"comp_12.dat": [row(n, 10, 0, 30) for n in range(1, 11)] + [row(50, 12, 4, 28)],
              "comp_99.dat": [row(n, 10, 0, 15) for n in range(1, 12)], "game_db.dat": []}
    monkeypatch.setattr(fm_save, "read_league_table", lambda buffer: buffer)
    save = SimpleNamespace(directory=tables, read=lambda name: tables[name])
    team = SimpleNamespace(national=False, age_limit=None, fm_uid=722)
    assert fm_save.read_point_deductions(save, {50: team}) == [fm_save.PointDeduction(12, 722, 12, 40)]
