"""The importer that fills the world database from a saved game."""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import select, text

from app.extensions import db
from app.models import world
from app.services import save_import


@pytest.fixture()
def world_connection(app):
    engine = db.engines[world.BIND]
    db.metadatas[world.BIND].create_all(engine)
    with engine.begin() as connection:
        yield connection


def _person(uid: int, name: str, as_of: date) -> dict:
    return {"fm_uid": uid, "name": name, "first_name": None, "last_name": None, "birth_date": None, "as_of": as_of}


def test_an_older_save_does_not_overwrite_a_newer_one(world_connection):
    table = world.WorldPerson.__table__
    save_import._upsert(world_connection, table, [_person(1, "Pedro Gomes", date(2063, 8, 11))], ["fm_uid"], guarded=True)
    save_import._upsert(world_connection, table, [_person(1, "Pedro", date(2062, 8, 29)), _person(2, "New", date(2062, 8, 29))], ["fm_uid"], guarded=True)
    save_import._upsert(world_connection, table, [_person(2, "Renamed", date(2063, 8, 11))], ["fm_uid"], guarded=True)

    names = dict(world_connection.execute(select(table.c.fm_uid, table.c.name)).all())
    assert names == {1: "Pedro Gomes", 2: "Renamed"}  # the older save added person 2, the newer one changed him


class _FakeReader(save_import._SaveReader):
    """A reader with its save already read, to test the fixture rules alone."""

    def __init__(self, game_date: date, fixtures: list, teams: dict[int, int]) -> None:
        super().__init__(path=None, lang_db=None, progress=lambda _: None)
        self.game_date = game_date
        self.fixtures = fixtures
        self.team_uid = teams
        self.known_teams = set(teams.values())
        self.stages = {162: 11}
        self.person_uid = {402195: 2002382729}


def _seed_teams(connection) -> None:
    connection.execute(world.WorldClub.__table__.insert(), [{"fm_uid": u, "name": str(u), "short_name": str(u), "as_of": date(2062, 1, 1)} for u in (630, 650)])
    connection.execute(
        world.WorldTeam.__table__.insert(),
        [{"fm_uid": u, "club_uid": u, "name": str(u), "age_limit": None, "as_of": date(2062, 1, 1)} for u in (630, 650)],
    )


def test_only_the_newest_save_keeps_a_schedule(world_connection):
    _seed_teams(world_connection)
    teams = {541: 630, 560: 650}
    played = save_import.fm_save.Fixture(0, 162, 1, 541, 560, date(2062, 8, 20), 2, 0, goals=(save_import.fm_save.Goal(402195, 0, 0, 10, 0),))
    planned = save_import.fm_save.Fixture(0, 162, 2, 560, 541, date(2062, 12, 26))
    moved = save_import.fm_save.Fixture(0, 162, 2, 560, 541, date(2062, 12, 27), 1, 1)

    older = _FakeReader(date(2062, 8, 29), [played, planned], teams)
    older._write_fixtures(world_connection)
    world_connection.execute(world.WorldSave.__table__.insert().values(file_name="old.fm", game_date=date(2062, 8, 29)))
    newer = _FakeReader(date(2063, 1, 5), [played, moved], teams)
    newer._write_fixtures(world_connection)

    table = world.WorldFixture.__table__
    rows = world_connection.execute(select(table.c.date, table.c.home_goals, table.c.competition_uid).order_by(table.c.date)).all()
    assert rows == [(date(2062, 8, 20), 2, 11), (date(2062, 12, 27), 1, 11)]  # the planned 26 December game is gone
    scorer = world_connection.execute(select(world.WorldGoal.__table__.c.scorer_uid)).scalar_one()
    assert scorer == 2002382729


def test_club_relations_come_from_the_newest_save_only(world_connection):
    rel = save_import.fm_save.ClubRelation
    FAVOURITE, RIVAL = save_import.fm_save.FAVOURITE, save_import.fm_save.RIVAL

    def import_relations(game_date: date, relations: dict) -> None:
        reader = _FakeReader(game_date, [], {})
        reader.club_uid = {5: 630, 6: 650}
        reader.parts = {"club_relations": relations, "players": []}
        reader.people = {2002382729: save_import.fm_save.PersonName("Pedro Gomes")}
        reader._write_club_relations(world_connection)
        world_connection.execute(world.WorldSave.__table__.insert().values(file_name=f"{game_date}.fm", game_date=game_date))

    import_relations(date(2063, 1, 5), {5: [rel(FAVOURITE, 402195, 1, 100, True, False), rel(RIVAL, 6, 2, 90, False, False),
                                            rel(FAVOURITE, 999, 1, 50, False, False)]})  # 999: no uid, skipped
    import_relations(date(2062, 8, 29), {5: []})  # older: changes nothing

    table = world.WorldClubRelation.__table__
    rows = world_connection.execute(select(table.c.club_uid, table.c.relation, table.c.subject_uid, table.c.level)
                                    .order_by(table.c.relation)).all()
    assert rows == [(630, "favourite", 2002382729, 100), (630, "rival", 650, 90)]

    import_relations(date(2063, 7, 1), {6: [rel(RIVAL, 5, 2, 85, False, False)]})  # newer: replaces all
    rows = world_connection.execute(select(table.c.club_uid, table.c.relation, table.c.subject_uid)).all()
    assert rows == [(650, "rival", 630)]


def test_club_finances_keep_their_history_and_the_newest_debts(world_connection):
    fm = save_import.fm_save

    def import_finances(game_date: date, bank: int, debt: int) -> None:
        reader = _FakeReader(game_date, [], {})
        reader.club_uid = {5: 630}
        reader.parts = {"club_finances": {5: fm.ClubFinances(bank, 1_000_000, 50_000,
                                                             (fm.Income(4, date(2062, 8, 1), date(2065, 8, 1), 300, 100, True),),
                                                             (fm.Debt(1, debt, 10, 1, 0, date(2060, 1, 1), date(2070, 1, 1), True),)),
                                              6: fm.ClubFinances(None, None, None)}}  # a club the game does not run in detail
        reader._write_club_finances(world_connection)
        world_connection.execute(world.WorldSave.__table__.insert().values(file_name=f"{game_date}.fm", game_date=game_date))

    import_finances(date(2063, 1, 5), 900, 5_000)
    import_finances(date(2062, 8, 29), 700, 4_000)  # older: its balance joins the history, its debts do not count
    table = world.WorldClubFinance.__table__
    rows = world_connection.execute(select(table.c.game_date, table.c.bank, table.c.yearly_income, table.c.debt).order_by(table.c.game_date)).all()
    assert rows == [(date(2062, 8, 29), 700, 100, 4_000), (date(2063, 1, 5), 900, 100, 5_000)]
    assert world_connection.execute(select(world.WorldClubDebt.__table__.c.amount)).scalars().all() == [5_000]


@pytest.mark.skipif(not os.environ.get("FM_SAVE_TEST_PATH"), reason="set FM_SAVE_TEST_PATH to a real .fm save")
def test_a_real_save_imports_twice_without_duplicates(app):
    first = save_import.import_save(os.environ["FM_SAVE_TEST_PATH"])
    second = save_import.import_save(os.environ["FM_SAVE_TEST_PATH"])

    assert first.counts["fixtures"] > 100_000 and first.counts["people"] > 100_000
    engine = db.engines[world.BIND]
    with engine.connect() as connection:
        fixtures = connection.execute(select(db.func.count()).select_from(world.WorldFixture.__table__)).scalar_one()
        saves = connection.execute(select(db.func.count()).select_from(world.WorldSave.__table__)).scalar_one()
    assert fixtures == second.counts["fixtures"] and saves == 1


def test_competition_facts_give_scope_country_and_league_level(world_connection):
    england, top, second, cup, youth = 765, 11, 12, 1301426, 900
    connection = world_connection
    day = date(2063, 7, 1)
    connection.execute(world.WorldCompetition.__table__.insert(), [
        {"fm_uid": u, "name": n, "short_name": n, "abbreviation": ""}
        for u, n in ((top, "English Premier Division"), (second, "Sky Bet Championship"), (cup, "English FA Cup"), (youth, "English U18 Cup"))])
    connection.execute(world.WorldNation.__table__.insert(), [{"fm_uid": england, "name": "England", "short_name": "England",
                                                               "abbreviation": "ENG", "adjective": "English"}])
    clubs = (1, 2, 3, 4)
    connection.execute(world.WorldClub.__table__.insert(), [{"fm_uid": c, "name": str(c), "short_name": str(c), "nation_uid": england,
                                                             "league_nation_uid": england, "as_of": day} for c in clubs])
    connection.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": c, "club_uid": c, "name": str(c), "as_of": day} for c in clubs]
                       + [{"fm_uid": 10 + c, "club_uid": c, "name": f"{c} U18", "age_limit": 18, "as_of": day} for c in clubs])
    stats = {"kind": 1, "seq": 0, "starts": 20, "substitute_appearances": 0, "rated_appearances": 0, "goals": 0, "assists": 0,
             "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 0,
             "rating_total": 0, "as_of": day}
    leagues = {(1, 2061): top, (2, 2061): top, (3, 2061): second, (4, 2061): second,
               (1, 2062): top, (2, 2062): second, (3, 2062): top, (4, 2062): second}  # club 3 promoted, club 2 relegated
    connection.execute(world.WorldPlayerSeason.__table__.insert(), [
        {**stats, "person_uid": 100 + club, "team_uid": club, "season_start": season, "league_uid": league}
        for (club, season), league in leagues.items()])

    def game(when, home, away, competition, crowd):
        return {"date": date.fromisoformat(when), "home_uid": home, "away_uid": away, "competition_uid": competition, "stage": 1,
                "home_goals": 1, "away_goals": 0, "attendance": crowd, "as_of": day}

    connection.execute(world.WorldFixture.__table__.insert(), [
        game("2062-09-01", 1, 2, top, 60000), game("2062-09-02", 3, 1, top, 50000), game("2062-09-08", 4, 2, second, 9000),
        game("2063-01-05", 2, 4, cup, 8000), game("2063-01-12", 11, 13, youth, 300), game("2063-01-19", 12, 14, youth, 200)])

    facts = save_import.competition_facts(connection, day)

    assert facts[top] == {"scope": "league", "nation_uid": england, "level": 1}
    assert facts[second] == {"scope": "league", "nation_uid": england, "level": 2}
    assert facts[cup] == {"scope": "cup", "nation_uid": england, "level": None}
    assert facts[youth]["scope"] == "youth"


def test_a_match_report_goes_to_the_fixture_whose_score_fits_its_goals():
    fm_save = save_import.fm_save
    scorer = fm_save.MatchPlayer(1, 7.5, 9000.0, 0.4, *([0] * 28))
    scorer = fm_save.MatchPlayer(**{**{f: getattr(scorer, f) for f in fm_save.MatchPlayer.__slots__}, "goals": 2})
    report = fm_save.MatchReport(0, 1169, "Barradão", None, None, None, 90,
                                 fm_save.MatchSide(306, 1, (scorer,)), fm_save.MatchSide(304, 0, ()))
    last_season = fm_save.Fixture(0, 431, 1169, 306, 304, date(2062, 7, 6), 0, 1)
    this_season = fm_save.Fixture(0, 431, 1169, 306, 304, date(2063, 7, 7), 2, 0)
    newer_but_wrong = fm_save.Fixture(0, 431, 1169, 306, 304, date(2063, 7, 9), 0, 0)

    assert save_import._report_fixture(report, [last_season, this_season, newer_but_wrong]) is this_season
    assert save_import._report_fixture(report, []) is None


def test_a_name_the_save_no_longer_confirms_goes_when_nothing_uses_the_person(world_connection):
    people = world.WorldPerson.__table__
    world_connection.execute(people.insert(), [
        _person(1, "DJ Buffonge", date(2063, 6, 9)),  # a stale name, nothing refers to him
        _person(2, "DJ Buffonge", date(2063, 6, 9)),  # a stale name, but he has a transfer
        _person(3, "DJ Buffonge", date(2063, 8, 1)),  # written by a newer save
        _person(4, "Alan Kardec", date(2063, 6, 9)),  # named in this save
    ])
    world_connection.execute(world.WorldTransfer.__table__.insert(), {"person_uid": 2, "date": date(2063, 1, 2), "kind": 1, "from_uid": 0,
                                                                    "to_uid": 0, "fee": 0, "fee_with_add_ons": 0})
    reader = _FakeReader(date(2063, 7, 11), [], {})
    reader.person_uid = {10: 1, 11: 2, 12: 3, 13: 4}
    reader.people = {4: object()}
    reader.parts = {"players": []}

    reader._drop_unconfirmed_names(world_connection)

    assert {row.fm_uid for row in world_connection.execute(select(people.c.fm_uid))} == {2, 3, 4}


def test_a_manager_list_belongs_to_the_team_whose_results_fit_it(world_connection):
    from app.services import manager_history
    fm = save_import.fm_save

    _seed_teams(world_connection)  # teams 630 and 650
    world_connection.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 11, "name": "League", "short_name": "League",
                                                                          "abbreviation": "L", "scope": "league"}])
    scores = [(630, 650, 2, 0), (650, 630, 1, 1), (630, 650, 3, 1), (650, 630, 0, 2), (630, 650, 0, 1)]
    world_connection.execute(world.WorldFixture.__table__.insert(), [
        {"competition_uid": 11, "stage": 162, "home_uid": h, "away_uid": a, "date": date(2062, 9, 1 + 7 * n),
         "home_goals": hg, "away_goals": ag, "as_of": date(2063, 1, 1)} for n, (h, a, hg, ag) in enumerate(scores)])
    # team 630 won 3, drew 1 and lost 1 in September; 650 the other way round
    spell = fm.ManagerSpell(1, date(2062, 8, 1), date(2062, 10, 31), 3, 1, 1, 3, 0, 0)
    other = fm.ManagerSpell(2, date(2062, 8, 1), date(2062, 10, 31), 1, 1, 3, 2, 0, 0)
    assert manager_history.link_lists(world_connection, [[spell], [other], []]) == {0: 630, 1: 650}


def test_a_played_fixture_already_stored_unchanged_is_not_written_again(world_connection):
    """A result never changes, so the 1.4 million stored ones are not rewritten at every import: only a fixture whose
    score or competition differs, or one not stored yet."""
    _seed_teams(world_connection)
    teams = {541: 630, 560: 650}
    first = save_import.fm_save.Fixture(0, 162, 1, 541, 560, date(2062, 8, 20), 2, 0)
    second = save_import.fm_save.Fixture(0, 162, 2, 560, 541, date(2062, 8, 27), 1, 1)
    older = _FakeReader(date(2062, 8, 29), [first, second], teams)
    older._write_fixtures(world_connection)
    world_connection.execute(world.WorldSave.__table__.insert().values(file_name="old.fm", game_date=date(2062, 8, 29)))

    corrected = save_import.fm_save.Fixture(0, 162, 2, 560, 541, date(2062, 8, 27), 3, 1)  # the second with another score
    third = save_import.fm_save.Fixture(0, 162, 3, 541, 560, date(2062, 9, 3), 0, 0)
    newer = _FakeReader(date(2063, 1, 5), [first, corrected, third], teams)
    newer._write_fixtures(world_connection)

    table = world.WorldFixture.__table__
    rows = world_connection.execute(select(table.c.date, table.c.home_goals, table.c.as_of).order_by(table.c.date)).all()
    assert rows == [(date(2062, 8, 20), 2, date(2062, 8, 29)),  # unchanged: kept as the older save wrote it
                    (date(2062, 8, 27), 3, date(2063, 1, 5)), (date(2062, 9, 3), 0, date(2063, 1, 5))]
    assert newer.counts["fixtures already stored"] == 1


def test_tactics_are_dated_by_the_season_the_crowd_and_the_squads(world_connection):
    """A match file holds its season and crowd, not its date: the season's fixture between its teams with that crowd; among
    several, the one whose squads hold the file's people."""
    from app.services import fm_tactics

    _seed_teams(world_connection)
    day = date(2063, 10, 1)
    world_connection.execute(world.WorldPerson.__table__.insert(), [
        {"fm_uid": 101, "name": "Old Timer", "as_of": day}, {"fm_uid": 102, "name": "New Boy", "as_of": day}])
    fixtures = world.WorldFixture.__table__
    world_connection.execute(fixtures.insert(), [
        {"date": date(2061, 9, 1), "home_uid": 630, "away_uid": 650, "competition_uid": 11, "stage": 1, "home_goals": 1, "away_goals": 0, "attendance": 40000, "as_of": day},
        {"date": date(2062, 9, 1), "home_uid": 630, "away_uid": 650, "competition_uid": 11, "stage": 1, "home_goals": 2, "away_goals": 0, "attendance": 40000, "as_of": day},
        {"date": date(2062, 12, 1), "home_uid": 630, "away_uid": 650, "competition_uid": 11, "stage": 1, "home_goals": 0, "away_goals": 0, "attendance": 39000, "as_of": day},
        {"date": date(2063, 3, 1), "home_uid": 630, "away_uid": 650, "competition_uid": 11, "stage": 1, "home_goals": 3, "away_goals": 0, "attendance": 40000, "as_of": day}])
    stats = {"substitute_appearances": 0, "rated_appearances": 0, "goals": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0,
             "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": day, "seq": 0, "kind": 1, "league_uid": 11, "starts": 1}
    world_connection.execute(world.WorldPlayerSeason.__table__.insert(), [
        {**stats, "person_uid": 101, "team_uid": 630, "season_start": 2061}, {**stats, "person_uid": 102, "team_uid": 630, "season_start": 2062}])
    slot = fm_tactics.TacticSlot("GK", None, "Support", "1.10", "Sweeper Keeper")
    eleven = [slot] + [fm_tactics.TacticSlot("D (C)", None, "Defend", "0.02", "Central Defender")] * 10
    used = fm_tactics.Tactic("GPT 5.6 x", "Tiki-Taka", "TIKI", "Positive", "Flexible", eleven,
                             [fm_tactics.PlayerRole(777, fm_tactics.TacticSlot("GK", None, "Attack", "0.01", "Goalkeeper"))])
    match = fm_tactics.MatchTactics(541, 630, 560, 650, 40000, 3000, 2062, [used])  # the 2062/63 season: two games with that crowd
    reader = _FakeReader(day, [], {541: 630, 560: 650})
    reader.people = {101: None, 102: None}
    unplayed = fm_tactics.MatchTactics(541, 630, 560, 650, None, None, None, [used])  # the next match: no season, no row
    reader.parts = {"tactic_slots": [used], "match_tactics": [("541_1.apm", match, frozenset({102, 5})), ("541_2.scm", unplayed, frozenset())],
                    "human_team": 541, "tactic_people": {777: 102}}
    reader._write_tactics(world_connection)
    rows = world_connection.execute(text("select slot, fixture_id, played_on, name, formation from team_tactics order by slot")).all()
    assert [(r.slot, str(r.played_on) if r.played_on else None, r.name) for r in rows] == [(None, "2063-03-01", "GPT 5.6 x"), (1, None, "GPT 5.6 x")]
    slots = world_connection.execute(text("select seq, position, role, duty, person_uid from team_tactic_slots where tactic_id = "
                                          "(select id from team_tactics where slot = 1) order by seq")).all()
    assert len(slots) == 12 and slots[0].role == "Sweeper Keeper" and (slots[-1].person_uid, slots[-1].duty) == (102, "Attack")
    reader._write_tactics(world_connection)  # again: replaced, not doubled
    assert world_connection.execute(text("select count(*) from team_tactics")).scalar_one() == 2



def test_a_goal_from_outside_the_box_is_marked_on_its_shot():
    from app.services import fm_save

    goal = fm_save.Shot(minute=70, second=10, outcome=fm_save.SHOT_GOAL, across=3.0, height=0.5)
    miss = fm_save.Shot(minute=12, second=0, outcome=fm_save.SHOT_OFF_TARGET, across=5.0, height=0.5)

    class Player:
        def __init__(self, goals, outside):
            self.goals, self.goals_outside_box = goals, outside

    assert save_import._outside_box(goal, Player(1, 1)) == 1 and save_import._outside_box(goal, Player(2, 2)) == 1
    assert save_import._outside_box(goal, Player(1, 0)) == 0 and save_import._outside_box(goal, Player(2, 1)) is None  # mixed: unknown
    assert save_import._outside_box(miss, Player(1, 1)) is None and save_import._outside_box(goal, Player(0, 0)) is None
    assert save_import._outside_box(goal, Player(1, 3)) == 1  # a count above his goals is clamped


def test_the_transfer_market_is_written_from_the_newest_save(world_connection):
    from app.services import fm_save

    _seed_teams(world_connection)
    day = date(2063, 11, 24)
    world_connection.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2002510270, "name": "Júnior", "as_of": day}])
    reader = _FakeReader(day, [], {541: 630, 560: 650, 3022: 650})
    reader.people = {2002510270: None}
    reader.person_uid = {529736: 2002510270}
    reader.parts = {"transfer_interest": [fm_save.TransferInterest(3022, 529736, 37867, 75734, 32), fm_save.TransferInterest(999999, 529736, 1, 1, 0)],
                    "transfer_offers": [fm_save.TransferOffer(529736, 541, 541, 5_906_135, 0x13, date(2063, 11, 24), date(2063, 12, 24), date(2064, 6, 9))]}
    reader._write_transfer_market(world_connection)
    rows = world_connection.execute(text("select team_uid, person_uid, rumoured, offered_wage from transfer_interest")).all()
    assert [tuple(r) for r in rows] == [(650, 2002510270, 0, 75734)]  # the unknown club is left out
    bids = world_connection.execute(text("select team_uid, person_uid, fee, status, offered_on, completes_on from transfer_offers")).all()
    assert [tuple(r) for r in bids] == [(630, 2002510270, 5_906_135, "agreed", "2063-11-24", "2064-06-09")]
    reader._write_transfer_market(world_connection)  # again: no doubles
    assert world_connection.execute(text("select count(*) from transfer_offers")).scalar_one() == 1


def test_a_bid_answered_in_a_later_save_keeps_the_day_of_its_answer(world_connection):
    """Juventus's bid for Valera, open in one save and rejected in the next (18 Jan 2064): the change is dated, and a
    save older than the newest read later does not open the bid again."""
    from app.services import fm_save

    _seed_teams(world_connection)
    open_day, answered = date(2064, 1, 18), date(2064, 1, 25)
    world_connection.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2002510270, "name": "Nacho Valera", "as_of": open_day}])

    def read(day, status):
        reader = _FakeReader(day, [], {541: 630})
        reader.people, reader.person_uid = {2002510270: None}, {529736: 2002510270}
        reader.parts = {"transfer_offers": [fm_save.TransferOffer(529736, 541, 541, 71_000_000, status, open_day, date(2064, 1, 25), date(2064, 1, 25))]}
        reader._write_transfer_market(world_connection)
        return tuple(world_connection.execute(text("select status, status_changed_on from transfer_offers")).one())

    assert read(open_day, 0x00) == ("made", None)
    assert read(answered, 0x02) == ("rejected", "2064-01-25")
    assert read(answered, 0x02) == ("rejected", "2064-01-25")  # the same save again: the day stays
    assert read(open_day, 0x00) == ("rejected", "2064-01-25")  # the older save is read again: it stays rejected


def test_a_counter_proposal_is_dated_the_day_a_save_first_shows_it(world_connection):
    from app.services import fm_save

    _seed_teams(world_connection)
    open_day, seen = date(2064, 1, 18), date(2064, 1, 26)
    world_connection.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2002510270, "name": "Nacho Valera", "as_of": open_day}])
    reader = _FakeReader(seen, [], {541: 630})
    reader.people, reader.person_uid = {2002510270: None}, {529736: 2002510270}
    reader.parts = {"transfer_offers": [fm_save.TransferOffer(529736, 541, 541, 71_000_000, 0x03, open_day, date(2064, 1, 24), date(2064, 2, 3)),
                                        fm_save.TransferOffer(529736, 541, 541, 165_000_000, 0x00, open_day, None, open_day, counter=True)]}
    reader._write_transfer_market(world_connection)
    rows = world_connection.execute(text("select fee, status, status_changed_on from transfer_offers order by fee")).all()
    assert [tuple(r) for r in rows] == [(71_000_000, "withdrawn", None), (165_000_000, "counter", "2064-01-26")]


def test_the_players_recent_games_are_written_for_his_own_teams_only(world_connection):
    from app.services import fm_save

    _seed_teams(world_connection)
    day = date(2063, 11, 24)
    world_connection.execute(world.WorldNation.__table__.insert(), [{"fm_uid": 800, "name": "Spain", "short_name": "Spain", "abbreviation": "ESP", "adjective": "Spanish", "as_of": day}])
    world_connection.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": 801, "nation_uid": 800, "name": "Spain", "age_limit": None, "as_of": day},
                                                                 {"fm_uid": 802, "nation_uid": 800, "name": "Ukraine", "age_limit": None, "as_of": day}])
    world_connection.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2002755601, "name": "San Martín", "nation_uid": 800, "as_of": day}])
    world_connection.execute(world.WorldPlayerSeason.__table__.insert(),
                             [{"person_uid": 2002755601, "team_uid": 630, "season_start": 2063, "kind": 1, "seq": 0, "starts": 5, "substitute_appearances": 0, "rated_appearances": 5, "goals": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 450, "rating_total": 350, "as_of": day}])
    world_connection.execute(world.WorldFixture.__table__.insert(),
                             [{"date": date(2063, 11, 20), "home_uid": 802, "away_uid": 801, "home_goals": 0, "away_goals": 3, "competition_uid": None, "stage": 11, "as_of": day}])
    reader = _FakeReader(day, [], {541: 630, 560: 650, 701: 801, 705: 802})
    reader.people = {2002755601: None}
    reader.person_uid = {775067: 2002755601}
    reader.competition_keys = {}
    figures = {f: 0 for f in fm_save.PLAYER_GAME_FIGURES}
    figures.update(rating=68, passes=33, passes_completed=28, minute_off=61, minutes=61, xg=11, assists=1, distance=91)
    reader.parts = {"player_games": [fm_save.PlayerGame(775067, 701, date(2063, 11, 20), 705, 113, fm_save._PG_PLAYED, 4, figures),
                                     fm_save.PlayerGame(775067, 701, date(2063, 11, 17), 705, 113, fm_save._PG_UNUSED),
                                     fm_save.PlayerGame(775067, 541, date(2063, 11, 24), 560, 12, fm_save._PG_PLAYED, 1, dict(figures)),
                                     fm_save.PlayerGame(775067, 560, date(2063, 11, 24), 541, 12, fm_save._PG_PLAYED, 1, dict(figures))]}  # not his club
    reader._write_player_games(world_connection)
    rows = world_connection.execute(text("select team_uid, played_on, status, position, minutes, rating, xg, assists, fixture_id, distance from player_games order by team_uid, played_on")).all()
    assert [tuple(r) for r in rows] == [(630, "2063-11-24", "played", "GK", 61, 6.8, 0.11, 1, None, 9.1), (801, "2063-11-17", "unused", None, None, None, None, None, None, None),
                                        (801, "2063-11-20", "played", "D (R)", 61, 6.8, 0.11, 1, 1, 9.1)]
    assert reader.counts["player_games skipped (team not the player's)"] == 1


def test_contracts_are_kept_as_history_by_person_club_and_wage(world_connection):
    from app.services import fm_save

    _seed_teams(world_connection)
    day = date(2063, 11, 24)
    world_connection.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2002510270, "name": "Júnior", "as_of": day, "team_uid": 630}])
    reader = _FakeReader(day, [], {541: 630, 560: 650})
    reader.people = {2002510270: None}
    reader.parts = {"staff_contracts": {2002510270: fm_save.StaffContract(541, fm_save.PLAYER_JOB, date(2050, 7, 7), 237663, date(2065, 6, 30), -35,
                                                                          signed=date(2062, 7, 5))}}
    reader._write_contracts(world_connection)
    older = _FakeReader(date(2062, 6, 9), [], {541: 630, 560: 650})  # an older save: the deal before, and this one seen earlier
    older.people = reader.people
    older.parts = {"staff_contracts": {2002510270: fm_save.StaffContract(541, fm_save.PLAYER_JOB, date(2050, 7, 7), 180000, date(2062, 6, 30), 40,
                                                                         signed=date(2059, 7, 1))}}
    older._write_contracts(world_connection)
    rows = world_connection.execute(text("select wage, signed, expires, happiness, first_seen, as_of from contracts order by wage")).all()
    assert [tuple(r) for r in rows] == [(180000, "2059-07-01", "2062-06-30", 40, "2062-06-09", "2062-06-09"),
                                        (237663, "2062-07-05", "2065-06-30", -35, "2063-11-24", "2063-11-24")]
    from app.services import world_queries as q
    assert [q.wage_words(w) for w in (237663, 1444770, 7087, 0)] == ["£240,000 a week", "£1,400,000 a week", "£7,000 a week", None]
    assert q.current_contract(world_connection, 2002510270)["wage_words"] == "£240,000 a week"


def test_a_goal_award_month_is_kept_once_given_and_a_running_vote_is_not():
    """The 1 Mar 2064 save had February's Goal of the Month as the latest (given that day); a later save has March's running
    vote there, which is not news yet; January's past month is given in February."""
    from app.services import fm_save

    goal = lambda person, day, place=1: fm_save.AwardGoal(place, person, 541, 560, day, 43, "drive", "outside")  # noqa: E731
    january = fm_save.GoalAwardMonth(634, date(2063, 8, 11), date(2064, 2, 1), (goal(1, date(2064, 1, 19)),))
    february = fm_save.GoalAwardMonth(634, date(2063, 8, 11), None, (goal(529736, date(2064, 2, 16)), goal(1, date(2064, 2, 2), 2)))
    reader = _FakeReader(date(2064, 3, 1), [], {541: 630, 560: 650})
    reader.person_uid = {529736: 2002510270}
    reader.parts = {"goal_awards": [january, february]}
    rows = reader._goal_award_rows({634: 5004896}, {5004896: None})
    assert sorted((r["given"], r["place"], r["person_uid"]) for r in rows) == [
        (date(2064, 2, 1), 1, None), (date(2064, 3, 1), 1, 2002510270), (date(2064, 3, 1), 2, None)]
    assert rows[-1]["team_uid"] == 630 and rows[-1]["against_uid"] == 650 and rows[-1]["kind"] == "drive"
    running = fm_save.GoalAwardMonth(634, date(2063, 8, 11), None, (goal(529736, date(2064, 3, 1)),))
    reader.parts = {"goal_awards": [running]}
    assert reader._goal_award_rows({634: 5004896}, {5004896: None}) == []


def test_a_national_squad_keeps_one_row_a_player_with_his_role():
    from app.services import fm_save
    from app.services.save_import import national_squad_rows

    day = date(2064, 4, 5)
    rows = national_squad_rows([fm_save.NationalSquad(765, (1, 2, 3), 2, 3)], day)
    assert sorted((r["person_uid"], r["role"]) for r in rows) == [(1, None), (2, "captain"), (3, "vice-captain")]
    assert {r["as_of"] for r in rows} == {day} and {r["team_uid"] for r in rows} == {765}
