"""What each side of a group is sure of (21 Nov 2026: the groups covered as they stand)."""

from app.services.tournament_stories import group_places


def _game(home: int, away: int, score: tuple[int, int] | None) -> dict:
    return {"home_uid": home, "away_uid": away, "home_goals": score[0] if score else None, "away_goals": score[1] if score else None}


def test_two_wins_out_of_two_is_sure_of_the_top_two_and_two_defeats_is_out():
    games = [_game(1, 2, (2, 0)), _game(3, 4, (1, 0)), _game(1, 3, (1, 0)), _game(2, 4, (3, 3)),  # 1: 6, 3: 3, 2: 1, 4: 1
             _game(1, 4, None), _game(2, 3, None)]
    places = group_places(games)
    assert places[1] == "sure of a top-two place"  # 6 points; at most one side can reach 6
    assert places[3] == "a top-two place still open"
    assert places[4] == "a top-two place still open"  # 4 can reach 4 points, level with the others: open on goals


def test_a_side_that_cannot_catch_two_others_is_out():
    games = [_game(1, 4, (1, 0)), _game(2, 3, (2, 0)), _game(1, 3, (1, 0)), _game(2, 4, (1, 0)),  # 1: 6, 2: 6, 3: 0, 4: 0
             _game(1, 2, None), _game(3, 4, None)]
    places = group_places(games)
    assert places[3] == places[4] == "can no longer finish in the top two" and places[1] == places[2] == "sure of a top-two place"


def test_a_finished_group_gives_each_side_its_place():
    games = [_game(1, 2, (1, 0)), _game(3, 4, (0, 0)), _game(1, 3, (2, 2)), _game(2, 4, (1, 0)), _game(1, 4, (0, 1)), _game(2, 3, (1, 1))]
    places = group_places(games)
    assert places[1] == "finished first" and set(places.values()) == {"finished first", "finished second", "finished third", "finished fourth"}
