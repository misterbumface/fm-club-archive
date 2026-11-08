# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""National squads in the papers (story_leads_squads): who is left out and why, who comes in, and the shape."""

from datetime import date

from app.models import world
from app.services.story_leads import _World
from app.services.story_leads_now import _record_chance
from app.services.story_leads_squads import squads
from tests.test_world_pages import AS_OF, CHELSEA, ENGLAND, _fixture, app, small_world  # noqa: F401

NATION = 7650
OTHER = 761
VETERAN, INJURED, YOUNG, KEPT, MANAGER = 9001, 9002, 9003, 9004, 9005
BEFORE, NOW = date(2064, 3, 1), date(2064, 4, 5)
ZEROS = dict.fromkeys(("rated_appearances", "assists", "player_of_match", "goals_conceded", "clean_sheets", "yellow_cards", "red_cards", "minutes", "rating_total"), 0)


def _setup(c):
    c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 77, "name": "European Championship Qualifying", "short_name": "Q", "abbreviation": "Q"}])
    c.exec_driver_sql("update competitions set scope = 'national_teams', reputation = 150 where fm_uid = 77")
    c.execute(world.WorldNation.__table__.insert(), [{"fm_uid": u, "name": n, "short_name": n, "abbreviation": n[:3], "adjective": n} for u, n in ((NATION, "Testland"), (OTHER, "Otherland"))])
    c.execute(world.WorldTeam.__table__.insert(), [
        {"fm_uid": NATION, "club_uid": None, "nation_uid": ENGLAND, "name": "Testland", "age_limit": None, "as_of": AS_OF, "reputation": 8000},
        {"fm_uid": OTHER, "club_uid": None, "nation_uid": None, "name": "Otherland", "age_limit": None, "as_of": AS_OF, "reputation": 5000}])
    people = [(VETERAN, "Old Hand"), (INJURED, "Crocked Man"), (YOUNG, "New Boy"), (KEPT, "Still Here")]
    c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": u, "name": n, "as_of": AS_OF, "team_uid": CHELSEA, "current_ability": 170, "birth_date": date(2034, 1, 1)} for u, n in people]
              + [{"fm_uid": MANAGER, "name": "The Boss", "as_of": AS_OF, "team_uid": None, "current_ability": None, "birth_date": None}])
    c.exec_driver_sql(f"update people set staff_job = 'Manager', staff_team_uid = {NATION} where fm_uid = {MANAGER}")
    c.execute(world.WorldPlayerSeason.__table__.insert(), [
        {"person_uid": VETERAN, "team_uid": CHELSEA, "season_start": 2062, "kind": 4, "seq": 0, "starts": 60, "substitute_appearances": 4, "goals": 12, "as_of": AS_OF, **ZEROS},
        {"person_uid": INJURED, "team_uid": CHELSEA, "season_start": 2062, "kind": 4, "seq": 0, "starts": 25, "substitute_appearances": 0, "goals": 1, "as_of": AS_OF, **ZEROS}])
    c.execute(world.WorldInjury.__table__.insert(), [{"person_uid": INJURED, "seq": 0, "injury": "broken lower leg", "injured_on": date(2064, 3, 20), "days": 60,
                                                      "severity": "Major", "place": "In a match", "cause": None, "treatment": None, "team_uid": None, "current": 1, "as_of": AS_OF}])
    c.execute(world.WorldPersonFeeling.__table__.insert(), [{"person_uid": VETERAN, "other_uid": MANAGER, "likes": 0, "reason": 0, "level": 50, "as_of": AS_OF}])
    rows = [(BEFORE, VETERAN, "captain"), (BEFORE, INJURED, None), (BEFORE, KEPT, None), (NOW, KEPT, "captain"), (NOW, YOUNG, None)]
    c.execute(world.WorldNationalSquad.__table__.insert(), [{"team_uid": NATION, "as_of": d, "person_uid": p, "role": r} for d, p, r in rows])


def test_a_new_squad_says_who_is_out_why_and_who_came_in(small_world):
    with small_world.begin() as c:
        _setup(c)
    with small_world.connect() as c:
        leads = squads(_World(c, date(2064, 3, 15), NOW, ()))
        assert squads(_World(c, NOW, date(2064, 4, 12), ())) == []  # told once
    by_kind = {lead.kind: lead for lead in leads}
    squad = by_kind["national_squad"]
    assert squad.headline == "Testland: the new squad, 1 in and 2 out, Old Hand left out"
    out = {p["name"]: p for p in squad.facts["left_out"]}
    assert out["Crocked Man"]["injured"] == "broken lower leg (since 2064-03-20)"
    assert out["Old Hand"]["no_reason_known"] is True and out["Old Hand"]["caps"] == "64 caps, 12 international goals"
    assert out["Old Hand"]["with_the_manager"] == "does not get on with the manager"
    assert squad.facts["brought_in"][0]["caps"] == "uncapped"
    assert squad.facts["captain"] == "Still Here" and squad.facts["the_old_captain"] == "Old Hand"


def test_an_injured_man_is_not_dropped_and_a_small_nation_needs_a_followed_player(small_world):
    with small_world.begin() as c:
        _setup(c)
        c.execute(world.WorldNationalSquad.__table__.insert(), [{"team_uid": OTHER, "as_of": d, "person_uid": p, "role": None}
                                                                for d, p in ((BEFORE, VETERAN), (NOW, YOUNG))])
        c.execute(world.WorldFixture.__table__.insert(), [{**_fixture("2064-04-20", NATION, OTHER, (0, 0), 77, 111), "home_goals": None, "away_goals": None}])
    with small_world.connect() as c:
        leads = squads(_World(c, date(2064, 3, 15), NOW, ()))
    assert {lead.headline.split(":")[0] for lead in leads if lead.kind == "national_squad"} == {"Testland"}  # Otherland is too small
    dropped = [lead for lead in leads if lead.kind == "squad_dropped"]
    assert [lead.facts["left_out"]["name"] for lead in dropped] == ["Old Hand"]  # 64 caps; Crocked Man has 25 and is hurt
    assert dropped[0].headline == "Old Hand, 64 caps, left out of the Testland squad"
    assert "likely_replacements" not in dropped[0].facts  # New Boy has no known position


def test_it_is_the_finals_squad_only_when_no_game_was_played_with_it(small_world):
    with small_world.begin() as c:
        _setup(c)
        c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 78, "name": "Copa América", "short_name": "Copa", "abbreviation": "CA"}])
        c.exec_driver_sql("update competitions set scope = 'national_teams', reputation = 170 where fm_uid = 78")
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2064-06-13", NATION, OTHER, None, 78, 111)])
    with small_world.connect() as c:
        (finals, *_) = squads(_World(c, date(2064, 3, 15), NOW, ()))
    assert finals.facts["the_finals_squad_for"] == "Copa América"
    with small_world.begin() as c:
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2064-03-29", NATION, OTHER, (1, 0), 77, 111)])
    with small_world.connect() as c:
        (played, *_) = squads(_World(c, date(2064, 3, 15), NOW, ()))
    assert "the_finals_squad_for" not in played.facts and len(played.facts["games_played_since_the_last_squad"]) == 1
    assert played.facts["next_game"].startswith("Copa América, 2064-06-13")


def test_a_player_on_99_caps_in_the_squad_is_told_though_his_caps_are_filed_under_his_club(small_world):
    with small_world.begin() as c:
        _setup(c)
        c.exec_driver_sql(f"update player_seasons set starts = 90, substitute_appearances = 9 where person_uid = {VETERAN}")
    comp = {"scope": "national_teams"}
    with small_world.connect() as c:
        assert _record_chance(_World(c, BEFORE, date(2064, 3, 15), ()), NATION, 77, comp) == "Old Hand would win his 100th cap"
        assert _record_chance(_World(c, NOW, date(2064, 4, 12), ()), NATION, 77, comp) is None  # no longer in the squad
