# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Stories from the team sheets: a change of system, a new role, a regular dropped, a youngster breaking through, and the
expected elevens of a preview."""

from datetime import date

from app.models import world
from app.services import story_leads_tactics as sheets
from app.services import tactics
from app.services.story_leads import _World
from tests.test_tactics import SIGNING, STAR, USUAL, YOUNGSTER, _match, _people
from tests.test_world_pages import AS_OF, ARSENAL, CHELSEA, LEAGUE, app, small_world  # noqa: F401

NEW_SHAPE = {uid: USUAL[uid] for uid in USUAL if uid not in (107, 109, 110, 111)} | {
    108: ("ST (C)", 7), SIGNING: ("ST (C)", 19), YOUNGSTER: ("M (C)", 45), 106: ("M (C)", 8), 130: ("M (L)", 22), 109: ("M (R)", 10)}


def _season(engine) -> None:
    """Six games of the usual 4-2-3-1, then two in a 4-4-2: the striker 111 on the bench while fit, the winger 108 up
    front, the youngster in midfield, a new signing in attack."""
    with engine.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", level=1))
        c.execute(world.WorldTransfer.__table__.insert(), [{"person_uid": SIGNING, "date": date(2063, 8, 20), "kind": 1, "from_uid": ARSENAL,
                                                            "to_uid": CHELSEA, "fee": 30_000_000, "fee_with_add_ons": 30_000_000}])
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 130, "name": "Wide Man", "as_of": AS_OF, "team_uid": CHELSEA,
                                                         "birth_date": date(2036, 1, 1), "reputation_world": 4000}])
    _people(engine)
    for day in ("2063-07-27", "2063-08-03", "2063-08-10", "2063-08-17", "2063-08-24", "2063-08-31"):
        _match(engine, day, USUAL, bench=(STAR,))
    for day in ("2063-09-07", "2063-09-14"):
        _match(engine, day, NEW_SHAPE, bench=(STAR, 111, 107, 110), score=(3, 0))


def _found(engine) -> dict[str, list]:
    with engine.connect() as c:
        w = _World(c, date(2063, 9, 1), date(2063, 9, 15), (CHELSEA,))
        found: dict[str, list] = {}
        for finder in sheets.FINDERS:
            for lead in finder(w):
                found.setdefault(lead.kind, []).append(lead)
    return found


def test_a_change_of_system_and_a_new_role_are_stories(small_world):
    _season(small_world)
    found = _found(small_world)
    (change,) = found["system_change"]
    assert change.headline == "Chelsea switch from 4-2-3-1 DM AM Wide to 4-4-2"
    assert change.facts["usual_system"] == "4-2-3-1 DM AM Wide (6 of the 6 games before)"
    assert change.facts["results_in_the_new_system"] == "2 games: 2 won, 0 drawn, 0 lost, 6-0"
    roles = {lead.facts["player"]: lead.facts for lead in found["position_change"]}
    assert roles["Chelsea 108"]["usual_role"] == "wide player" and roles["Chelsea 108"]["new_role"] == "striker"
    assert roles["Chelsea 108"]["as_striker"]["starts"] == 2


def test_a_fit_regular_on_the_bench_and_a_youngster_breaking_through(small_world):
    _season(small_world)
    found = _found(small_world)
    dropped = {lead.facts["player"]: lead.facts for lead in found["dropped"]}
    assert dropped["Chelsea 111"]["this_cycle"] == "started none of 2 games; on the bench in 2, out of the squad in 0"
    assert dropped["Chelsea 111"]["his_replacement"] == "New Man"
    assert dropped["Chelsea 111"]["replacement_is_a_new_signing"] == "from Arsenal, 2063-08-20, £30.0m"
    (kid,) = found["breakthrough"]
    assert (kid.facts["player"], kid.facts["age"], kid.facts["starts_this_cycle"]) == ("Young Kid", 18, 2) and "young" in kid.beats


def test_a_regular_out_of_every_squad_is_not_called_dropped(small_world):
    _season(small_world)
    with small_world.begin() as c:  # 111 not even on the bench: perhaps injured, perhaps suspended; the save does not say
        c.execute(world.WorldMatchPlayer.__table__.delete().where(world.WorldMatchPlayer.__table__.c.person_uid == 111)
                  .where(world.WorldMatchPlayer.__table__.c.played == 0))
    assert "Chelsea 111" not in {lead.facts["player"] for lead in _found(small_world).get("dropped", [])}


def test_the_expected_eleven_leaves_out_the_injured_and_picks_the_next_man(small_world):
    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldInjury.__table__.insert(), [{"person_uid": 106, "seq": 0, "injury": "torn hamstring", "injured_on": date(2063, 9, 15),
                                                          "days": 40, "severity": "Major", "team_uid": CHELSEA, "current": 1, "as_of": AS_OF}])
    with small_world.connect() as c:
        home = tactics.expected_eleven(c, CHELSEA, date(2063, 9, 20))
    assert home["shape"] == "4-2-3-1 DM AM Wide" and home["share"] == 6 and home["games"] == 8
    assert 106 not in home["chosen"] and len(home["chosen"]) == 11
    assert any(out.startswith("Chelsea 106 (defensive midfielder, injured: torn hamstring, back about 25 October") for out in home["regulars_out"])
    assert tactics.key_duels(home, home)[0].startswith("Chelsea 108 (right winger, average rating 7.0) against Chelsea 105 (left-back")


def test_a_preview_sets_up_the_duels_the_attributes_decide(small_world):
    _season(small_world)
    base = {name: 10 for name in ("Pace", "Acceleration", "Dribbling", "Tackling", "Heading", "Jumping Reach", "Strength",
                                  "Off The Ball", "Anticipation")}
    with small_world.begin() as c:
        save = c.execute(world.WorldSave.__table__.select()).first().id
        c.execute(world.WorldPlayerSnapshot.__table__.insert(), [
            {"person_uid": uid, "save_id": save, "height_cm": height, "weight_kg": 75, "attributes": base | extra}
            for uid, height, extra in ((108, 175, {"Pace": 18, "Acceleration": 17}), (105, 180, {"Pace": 11, "Acceleration": 12}),
                                       (111, 192, {"Heading": 17, "Jumping Reach": 18, "Strength": 16}), (103, 185, {}), (104, 186, {}))])
    with small_world.connect() as c:
        eleven = tactics.expected_eleven(c, CHELSEA, date(2063, 9, 20))
        duels = tactics.attribute_duels(c, eleven, eleven)
    assert any(d.startswith("Chelsea 108 (right winger: Pace 18, Acceleration 17, Dribbling 10) against Chelsea 105 (left-back: "
                            "Pace 11, Acceleration 12, Tackling 10); the edge in pace: Chelsea 108") for d in duels)
    assert any(d.startswith("In the air: Chelsea 111 (192 cm, Heading 17, Jumping Reach 18, Strength 16)") for d in duels)
    assert len(duels) <= tactics.DUELS_SHOWN


def test_a_team_that_changes_shape_against_stronger_sides(small_world):
    from tests.test_world_pages import LIVERPOOL, SPURS

    _people(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldCompetition.__table__.update().where(world.WorldCompetition.__table__.c.fm_uid == LEAGUE).values(scope="league"))
        teams = world.WorldTeam.__table__
        for team, reputation in ((CHELSEA, 7000), (LIVERPOOL, 9000), (SPURS, 5000)):
            c.execute(teams.update().where(teams.c.fm_uid == team).values(reputation=reputation))
    for day in ("2063-08-03", "2063-08-10", "2063-08-17", "2063-08-24", "2063-08-31", "2063-09-07"):
        _match(small_world, day, USUAL, opponent=SPURS)  # the usual 4-2-3-1 against weaker sides
    for day in ("2063-08-06", "2063-08-20", "2063-09-03"):
        _match(small_world, day, NEW_SHAPE, opponent=LIVERPOOL)  # a 4-4-2 against stronger ones
    with small_world.connect() as c:
        big = tactics.big_game_system(c, CHELSEA, LIVERPOOL, date(2063, 9, 20))
        assert tactics.big_game_system(c, CHELSEA, SPURS, date(2063, 9, 20)) is None  # their usual shape against them
        eleven = tactics.expected_eleven(c, CHELSEA, date(2063, 9, 20), big["shape"])
    assert big["words"] == ("Against stronger sides they play 4-4-2 (3 of 3 games); their usual is 4-2-3-1 DM AM Wide (6 of 9)")
    assert eleven["line_up"].startswith("4-4-2: GK Chelsea 101")
    with small_world.begin() as c:  # a new manager since 1 September: his matches only, too few against stronger sides
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 900, "name": "New Boss", "as_of": AS_OF, "staff_job": "Manager",
                                                         "staff_team_uid": CHELSEA, "staff_since": date(2063, 9, 1)}])
    with small_world.connect() as c:
        assert tactics.big_game_system(c, CHELSEA, LIVERPOOL, date(2063, 9, 20)) is None


def test_a_preview_names_the_danger_men_and_the_form(small_world):
    _season(small_world)
    mp = world.WorldMatchPlayer.__table__
    with small_world.begin() as c:
        c.execute(mp.update().where(mp.c.person_uid == 108).values(goals=1, xg=0.8, shots=3, rating=8.0))
        c.execute(mp.update().where(mp.c.person_uid == 102).values(rating=6.1))
        c.execute(mp.update().where(mp.c.person_uid == 103).values(rating=9.0))  # in form, but injured now
        c.execute(world.WorldInjury.__table__.insert(), [{"person_uid": 103, "seq": 0, "injury": "broken foot", "injured_on": date(2063, 9, 15),
                                                          "days": 60, "severity": "Major", "team_uid": CHELSEA, "current": 1, "as_of": AS_OF}])
    with small_world.connect() as c:
        form = tactics.danger_and_form(c, CHELSEA, date(2063, 9, 20))
    assert form["danger_men"][0] == ("Chelsea 108 (right winger): 5 goals, 0 assists, 4.0 expected goals, 15 shots in his last 5 games; "
                                     "ratings 8.0, 8.0, 8.0, 8.0, 8.0")
    assert form["in_form"][0].startswith("Chelsea 108 (right winger): average 8.0 over his last 5 games")
    assert form["out_of_form"] == ["Chelsea 102 (right-back): average 6.1 over his last 5 games (6.1, 6.1, 6.1, 6.1, 6.1)"]
    assert not any("Chelsea 103" in line for lines in form.values() for line in lines)


def test_a_player_back_before_the_match_is_in_the_expected_eleven(small_world):
    _season(small_world)
    with small_world.begin() as c:  # injured on 15 September for 10 days: back before a match on 1 October
        c.execute(world.WorldInjury.__table__.insert(), [{"person_uid": 106, "seq": 0, "injury": "bruised thigh", "injured_on": date(2063, 9, 15),
                                                          "days": 10, "severity": "Minor", "team_uid": CHELSEA, "current": 1, "as_of": AS_OF}])
    with small_world.connect() as c:
        assert 106 not in tactics.expected_eleven(c, CHELSEA, date(2063, 9, 20))["chosen"]
        assert 106 in tactics.expected_eleven(c, CHELSEA, date(2063, 10, 1))["chosen"]


def test_a_preview_recalls_the_last_meeting_and_what_changed_since(small_world):
    _season(small_world)
    with small_world.connect() as c:
        eleven = tactics.expected_eleven(c, CHELSEA, date(2063, 9, 20))
        meeting = tactics.last_meeting(c, CHELSEA, ARSENAL, date(2063, 9, 20), {CHELSEA: eleven})
    assert meeting["match"] == "2063-09-14, English Premier Division: Chelsea 3-0 Arsenal"
    chelsea = meeting["sides"]["Chelsea"]
    assert chelsea["shape"] == "4-4-2" and chelsea["eleven"].startswith("4-4-2: GK #1 Chelsea 101")
    assert "Young Kid (not in the likely eleven)" in chelsea["not_in_the_expected_eleven_now"]  # the usual 4-2-3-1 is expected
    assert "Chelsea 111" in chelsea["new_in_the_expected_eleven"]
    assert meeting["sides"]["Arsenal"]["shape"] == "4-4-2"


def test_a_fit_regular_who_lost_his_place_is_told_as_dropped_not_injured(small_world):
    """Belmonte was in Ponferrada's key duels but not their expected eleven, and the writer called him injured (1 Nov 2026):
    a regular who rotated between two slots and lost both to specialists is told with his recent starts and his rating."""
    _people(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldCompetition.__table__.update().where(world.WorldCompetition.__table__.c.fm_uid == LEAGUE).values(scope="league", level=1))
    as_striker = USUAL | {SIGNING: ("AM (L)", 19)} ; as_striker.pop(110)  # 111 up front, the signing wide, 110 on the bench
    as_winger = USUAL | {110: ("ST (C)", 11), 111: ("AM (L)", 9)}  # 111 wide, 110 up front, the signing on the bench
    specialists = USUAL | {SIGNING: ("ST (C)", 19)} ; specialists.pop(111)  # the signing up front, 110 wide, 111 on the bench
    for day in ("2063-07-27", "2063-08-03", "2063-08-06"):
        _match(small_world, day, as_striker, bench=(110,))
    for day in ("2063-08-10", "2063-08-13", "2063-08-17"):
        _match(small_world, day, as_winger, bench=(SIGNING,))
    for day in ("2063-08-24", "2063-08-31", "2063-09-07", "2063-09-14"):
        _match(small_world, day, specialists, bench=(111,))
    with small_world.connect() as c:
        home = tactics.expected_eleven(c, CHELSEA, date(2063, 9, 20))
    assert 111 not in home["chosen"] and SIGNING in home["chosen"] and 110 in home["chosen"]
    (why,) = [out for out in home["regulars_out"] if out.startswith("Chelsea 111")]
    assert "fit, but started 0 of the last 4 games, on the bench in 4; average rating 7.0 in his recent starts" in why
