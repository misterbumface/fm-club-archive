# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Expected goals and points: the xG table of a league, the story of a team far from its expected points, and the line a
preview gets."""

from datetime import date

import pytest

from app.models import world
from app.services import expected
from app.services import story_leads_now as now
from app.services.story_leads import _World
from tests.test_tactics import USUAL, _match, _people
from tests.test_world_pages import CHELSEA, LEAGUE, app, pages, small_world  # noqa: F401

DAYS = ("2063-08-10", "2063-08-17", "2063-08-24", "2063-08-31", "2063-09-07", "2063-09-14")


def test_expected_points_come_from_both_sides_xg():
    assert expected.expected_points(0, 0) == pytest.approx(1.0)  # nobody scores: a draw
    assert expected.expected_points(2.5, 0.3) > 2.3 > expected.expected_points(1.2, 1.2) > expected.expected_points(0.3, 2.5)
    assert expected.ordinal(1) == "1st" and expected.ordinal(12) == "12th" and expected.ordinal(23) == "23rd"


def _season(engine) -> None:
    """Six 2-0 wins for Chelsea from 0.55 xG a game against 1.1: far above what the chances deserve."""
    with engine.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", level=1))
    _people(engine)
    fixtures = [_match(engine, day, USUAL) for day in DAYS]
    with engine.begin() as c:
        players = world.WorldMatchPlayer.__table__
        for fixture in fixtures:
            c.execute(players.update().where(players.c.fixture_id == fixture).values(xg=0.05))
            c.execute(players.update().where((players.c.fixture_id == fixture) & (players.c.side == 1)).values(xg=0.1))


def test_the_xg_table_adds_each_sides_xg(small_world):
    _season(small_world)
    with small_world.connect() as c:
        table = expected.xg_table(c, LEAGUE, "2063-07-01", "2064-06-30")
    chelsea = next(t for t in table if t["team_uid"] == CHELSEA)
    assert (chelsea["games"], chelsea["points"], chelsea["goals_for"]) == (6, 18, 12)
    assert chelsea["xg"] == pytest.approx(3.3) and chelsea["xga"] == pytest.approx(6.6)
    assert chelsea["points_vs_xpts"] > 10 and chelsea["place"] == 1 and chelsea["xpts_place"] == 2
    assert "18 points from 6 games" in expected.words(chelsea) and "1st in the table, 2nd on expected points" in expected.words(chelsea)


def test_a_team_far_above_its_expected_points_is_a_story(small_world):
    _season(small_world)
    with small_world.connect() as c:
        w = _World(c, date(2063, 8, 20), date(2063, 9, 15), (CHELSEA,))  # four league games in the cycle
        leads = now.expected_points(w)
    chelsea = next(lead for lead in leads if lead.refs[0] == f"club:{CHELSEA}")
    assert chelsea.headline.startswith("Chelsea +") and chelsea.facts["finishing"] == "+8.7 goals against their xG"


def test_too_few_reported_games_give_no_table(small_world):
    with small_world.begin() as c:
        c.execute(world.WorldFixture.__table__.insert(), [{"date": date(2063, 12, 10 + i), "home_uid": CHELSEA, "away_uid": 650, "competition_uid": LEAGUE,
                                                           "stage": 162, "home_goals": 1, "away_goals": 0, "as_of": date(2063, 9, 1)} for i in range(5)])
    with small_world.connect() as c:
        assert expected.xg_table(c, LEAGUE, "2063-07-01", "2064-06-30") == []


def test_the_league_page_shows_the_xg_table(small_world, pages):
    _season(small_world)
    page = pages.get(f"/competitions/{LEAGUE}").data.decode()
    assert "Expected goals and points" in page and "xPts pos" in page
