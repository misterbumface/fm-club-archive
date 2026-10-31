# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""A club season's front page: results and the form, the squad in four lines, and the papers."""

from app.models import world
from app.services import world_queries as q
from tests.test_story_leads import _match_player
from tests.test_world_pages import ARSENAL, AS_OF, CHELSEA, LEAGUE, MYERS, _fixture, app, pages, small_world  # noqa: F401


def _season_under_way(engine) -> None:
    """Chelsea's first league game of 2063/64 is in the match reports; the save's season rows are still empty."""
    with engine.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", level=1))
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.__table__.c.fm_uid == MYERS).values(team_uid=CHELSEA))
        fixture = c.execute(world.WorldFixture.__table__.insert(), _fixture("2063-08-05", CHELSEA, ARSENAL, (2, 0), LEAGUE, 162)).inserted_primary_key[0]
        c.execute(world.WorldMatchPlayer.__table__.insert(), [_match_player(fixture, 0, 0, MYERS, 7.6, goals=2, assists=0)])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [{
            "person_uid": MYERS, "team_uid": CHELSEA, "season_start": 2063, "kind": 1, "seq": 0, "league_uid": LEAGUE, "starts": 0,
            "substitute_appearances": 0, "rated_appearances": 0, "goals": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0,
            "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": AS_OF}])


def test_the_season_being_played_takes_its_numbers_from_the_match_reports(small_world):
    _season_under_way(small_world)
    with small_world.connect() as c:
        data = q.club_season(c, CHELSEA, 2063)
        (myers,) = data["players"]
        assert (myers["apps"], myers["goals"], myers["league_apps"], myers["rating"]) == (1, 2, 1, 7.6)
        overview = q.season_overview(c, CHELSEA, 2063, data)
    assert overview["current"] and overview["form"] == ["W"]
    midfield = dict(overview["squad_lines"])["Midfield"]  # no position known: midfield
    assert [p["name"] for p in midfield] == ["Ken Myers"]


def test_the_front_page_shows_the_results_the_squad_and_the_papers(small_world, pages):
    _season_under_way(small_world)
    page = pages.get(f"/clubs/{CHELSEA}/2063").data.decode()
    assert "Results so far" in page and "The squad now" in page and "In the papers" in page
    assert "<b>1</b> game ·" in page and "<b>2</b> goals" in page and "7.60" in page
    assert "The game keeps no team of the season" not in page  # no empty best eleven


def test_an_import_fills_the_season_being_played_from_the_reports_and_keeps_the_saves_own_numbers(small_world):
    from datetime import date

    from sqlalchemy import text

    from app.services import season_from_reports

    _season_under_way(small_world)
    with small_world.begin() as c:
        old = c.execute(world.WorldFixture.__table__.insert(), _fixture("2063-04-02", CHELSEA, ARSENAL, (1, 0), LEAGUE, 162)).inserted_primary_key[0]
        c.execute(world.WorldMatchPlayer.__table__.insert(), [_match_player(old, 0, 0, MYERS, 9.0, goals=1)])  # last season: left alone
        assert season_from_reports.fill_current_seasons(c, date(2063, 8, 20)) == 1
        row = c.execute(text("select * from player_seasons where person_uid = :p and season_start = 2063 and kind = 1"), {"p": MYERS}).mappings().one()
        assert (row["starts"], row["goals"], row["rated_appearances"], row["rating_total"], row["minutes"]) == (1, 2, 1, 76, 90)
        last = c.execute(text("select starts, goals from player_seasons where person_uid = :p and season_start = 2062 and kind = 1"), {"p": MYERS}).one()
        assert tuple(last) == (30, 12)  # the save's own season
        assert season_from_reports.fill_current_seasons(c, date(2063, 8, 20)) == 0  # nothing more in the reports
