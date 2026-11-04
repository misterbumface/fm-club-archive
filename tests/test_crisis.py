# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Crisis mode (story_leads_crisis) and the firepower model under it (firepower)."""

from datetime import date

from app.models import world
from app.services import firepower
from app.services import story_leads_crisis as crisis
from app.services.story_leads import _World
from tests.test_story_leads_now import SINCE, UNTIL, _season
from tests.test_world_pages import AS_OF, ARSENAL, CHELSEA, LEAGUE, LIVERPOOL, SPURS, _fixture, app, small_world  # noqa: F401


def _squads(engine, strong: int = SPURS) -> None:
    """Eleven rated players at each club: the strong club's rate 180, the others' 140."""
    rows, uid = [], 9000
    for team in (CHELSEA, LIVERPOOL, ARSENAL, SPURS):
        for _ in range(11):
            uid += 1
            rows.append({"fm_uid": uid, "name": f"Player {uid}", "as_of": AS_OF, "team_uid": team, "nation_uid": 765,
                         "current_ability": 180 if team == strong else 140})
    with engine.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), rows)


def test_the_strength_and_the_points_it_should_take(small_world):
    _squads(small_world)
    with small_world.connect() as c:
        spurs = firepower.strength(c, SPURS)
    assert spurs["overall"] == 180 and len(spurs["stars"]) == firepower.STARS
    assert firepower.words(spurs).startswith("the best eleven rates 180 of 200")
    home, away = firepower.expected_points(180, 140, True), firepower.expected_points(180, 140, False)
    assert 3 > home > away > firepower.expected_points(160, 160, None) == firepower.MIDDLE


def test_a_strong_squad_losing_is_a_crisis_and_the_other_stories_say_so(small_world):
    """Spurs, the best squad by far, lose all four: a crisis; their derby's story carries it."""
    from app.services import story_leads_now as now

    _season(small_world)
    _squads(small_world)
    with small_world.connect() as c:
        w = _World(c, SINCE, UNTIL, (CHELSEA,))
        (lead,) = crisis.crises(w)
        assert lead.kind == "crisis" and lead.headline == "Tottenham: a crisis (0 points from 5 games)"  # May's game counts, 2062's not
        assert lead.facts["points_from_them"] == "0 of 15" and lead.facts["place"] == 4 and lead.facts["place_its_squad_suggests"] == 1
        assert lead.facts["the_crisis_began_with"] == "2063-08-24" and lead.facts["games_since_the_last_win"] == "no win in the last 5"
        derby = next(found for found in now.derbies(w))
        crisis.mark(w, [derby])
        assert derby.facts["in_crisis"][0].startswith("Tottenham are in crisis: 0 points from their last 5 games")


def test_an_equal_squad_losing_is_no_crisis_and_two_wins_end_one(small_world):
    _season(small_world)
    _squads(small_world, strong=CHELSEA)  # Spurs' players are no better than the others': a bad run, not a crisis
    with small_world.connect() as c:
        assert crisis.crises(_World(c, SINCE, UNTIL, (CHELSEA,))) == []
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.__table__.c.team_uid == SPURS).values(current_ability=180))
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.__table__.c.team_uid == CHELSEA).values(current_ability=140))
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2063-09-07", SPURS, LIVERPOOL, (2, 0), LEAGUE, 162),
                                                          _fixture("2063-09-14", ARSENAL, SPURS, (0, 1), LEAGUE, 162)])
    with small_world.connect() as c:
        (over,) = crisis.crises(_World(c, UNTIL, date(2063, 9, 15), (CHELSEA,)))
    assert over.kind == "crisis_over" and len(over.facts["the_games_that_ended_it"]) == 2
