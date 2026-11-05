# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Goal awards in the papers (story_leads._goal_awards): the winner first, each goal in its game and how it went in."""

from datetime import date

from app.models import world
from app.services.story_leads import _goal_awards, _World
from tests.test_world_pages import AS_OF, CHELSEA, LEAGUE, LIVERPOOL, _fixture, app, small_world  # noqa: F401

HILGERS, OTHER = 2002511657, 2002510539


def test_the_goal_of_the_month_tells_each_goal_in_its_game(small_world):
    with small_world.begin() as c:
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2064-02-16", CHELSEA, LIVERPOOL, (6, 0), LEAGUE, 162)])
        fixture = c.execute(world.WorldFixture.__table__.select().where(world.WorldFixture.__table__.c.date == date(2064, 2, 16))).first().id
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": HILGERS, "name": "Bruce Hilgers", "as_of": AS_OF},
                                                         {"fm_uid": OTHER, "name": "Samuel", "as_of": AS_OF}])
        c.execute(world.WorldGoal.__table__.insert(), [{"fixture_id": fixture, "seq": 0, "scorer_uid": HILGERS, "side": 0, "minute": 43,
                                                        "added_time": 0, "kind": 0}])
        c.execute(world.WorldMatchShot.__table__.insert(), [{"fixture_id": fixture, "side": 0, "seq": 0, "person_uid": HILGERS, "minute": 42,
                                                             "second": 10, "outcome": "goal", "across": 3.5, "height": 0.18, "as_of": AS_OF,
                                                             "kind": "drive", "range": None, "outside_box": 1}])
        c.execute(world.WorldAward.__table__.insert(), [{"fm_uid": 5004896, "name": "English Premier Division Goal of the Month",
                                                         "short_name": "Goal of the Month"}])
        row = {"award_uid": 5004896, "given": date(2064, 3, 1), "minute": 43, "kind": "drive", "range": "outside",
               "season_start": date(2063, 8, 11), "as_of": AS_OF}
        c.execute(world.WorldGoalAward.__table__.insert(), [
            {**row, "place": 1, "person_uid": HILGERS, "team_uid": CHELSEA, "against_uid": LIVERPOOL, "scored_on": date(2064, 2, 16)},
            {**row, "place": 2, "person_uid": OTHER, "team_uid": LIVERPOOL, "against_uid": CHELSEA, "scored_on": date(2064, 2, 2), "minute": 25}])
    with small_world.connect() as c:
        (lead,) = _goal_awards(_World(c, date(2064, 2, 22), date(2064, 3, 1), (CHELSEA,)))
        assert _goal_awards(_World(c, date(2064, 3, 1), date(2064, 3, 8), (CHELSEA,))) == []  # given once
    assert lead.headline == "Bruce Hilgers (Chelsea) wins the English Premier Division Goal of the Month for February 2064"
    first, second = lead.facts["the_goals_in_order"]
    assert first.startswith("1. Bruce Hilgers (Chelsea), 43' against Liverpool, 2064-02-16 (Chelsea 6-0 Liverpool, Chelsea won): a drive")
    assert first.endswith("from outside the box, along the ground, near a post")  # the shot record: 0.18 m high, 3.5 across
    assert second == "2. Samuel (Liverpool), 25' against Chelsea, 2064-02-02: a drive (struck low and hard) from outside the box"  # no match kept
    assert "home" in lead.beats and lead.refs[:2] == [f"person:{HILGERS}", f"club:{CHELSEA}"]
