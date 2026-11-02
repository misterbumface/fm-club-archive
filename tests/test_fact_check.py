"""The paper's fact checker: hard facts checked in the archive, colour left alone (2 Nov 2026)."""

from datetime import date

from app.services import article_writer, fact_check, outlets
from tests.test_outlets import _lead, fake_ai  # noqa: F401
from tests.test_story_leads_now import _season
from tests.test_world_pages import AS_OF, app, small_world  # noqa: F401

UNTIL = date(2063, 9, 3)  # Chelsea have won all four league games; they won 1-0 at Liverpool on 31 August


def _check(small_world, claim):
    with small_world.connect() as c:
        return fact_check.Checker(c, UNTIL).check({"sentence": "S.", **claim})


def test_a_defeat_that_never_happened_is_wrong_and_the_truth_is_given(small_world):
    """The report of 6 Feb 2064 gave Chelsea a league defeat at Manchester City; they had won 4-0 there."""
    _season(small_world)
    wrong = _check(small_world, {"kind": "result", "team": "Chelsea", "opponent": "Liverpool", "outcome": "lost", "venue": "away",
                                 "this_season": True})
    assert wrong.verdict == "wrong" and "Liverpool 0-1 Chelsea, Chelsea won" in wrong.truth
    assert _check(small_world, {"kind": "result", "team": "Chelsea", "opponent": "Liverpool", "outcome": "won", "score": "1-0",
                                "venue": "away", "this_season": True}).verdict == "right"
    assert _check(small_world, {"kind": "result", "team": "Chelsea", "opponent": "Arsenal", "outcome": "won", "month": "2063-08"}).verdict == "right"
    assert _check(small_world, {"kind": "result", "team": "Chelsea", "opponent": "Arsenal", "outcome": "won", "month": "2063-07"}).verdict == "wrong"


def test_records_places_and_meetings_are_counted_from_the_archive(small_world):
    _season(small_world)
    assert _check(small_world, {"kind": "record", "team": "Chelsea", "won": 4, "lost": 0, "unbeaten_run": 4}).verdict == "right"
    record = _check(small_world, {"kind": "record", "team": "Chelsea", "lost": 1, "unbeaten_run": 25})
    assert record.verdict == "wrong" and "lost 0" in record.truth and "unbeaten_run 25 (the archive: 4)" in record.truth
    assert _check(small_world, {"kind": "place", "team": "Chelsea", "position": 1, "points": 12}).verdict == "right"
    assert _check(small_world, {"kind": "place", "team": "Chelsea", "position": 2}).verdict == "wrong"
    meetings = _check(small_world, {"kind": "meetings", "team": "Chelsea", "opponent": "Liverpool", "last": 3, "won": 3})
    assert meetings.verdict == "wrong" and "won 3 (the archive: 2)" in meetings.truth  # the cup final of May 2063 was 1-1


def test_a_fact_the_archive_cannot_check_goes_only_when_the_notebook_lacks_it(small_world):
    _season(small_world)
    assert _check(small_world, {"kind": "other", "in_notebook": False}).verdict == "unsupported"
    assert _check(small_world, {"kind": "other", "in_notebook": True}).verdict == "unknown"
    assert _check(small_world, {"kind": "player_game", "player": "Sam Scorer", "stat": "goals", "value": 2}).verdict == "unknown"  # no game
    assert _check(small_world, {"kind": "nonsense"}).verdict == "unknown"


def test_a_wrong_fact_goes_back_to_the_writer_and_what_stays_wrong_is_cut(small_world, fake_ai, monkeypatch):
    outlet = outlets.ensure_outlets()[0]
    lie = "Chelsea lost at Liverpool in 2063."
    first = {"headline": "Liverpool end the wait", "standfirst": "A first FA Cup.", "body": f"Liverpool beat Chelsea 2-1 in 2063. {lie}"}
    fixed = {"headline": "Liverpool end the wait", "standfirst": "A first FA Cup.", "body": "Liverpool beat Chelsea 2-1 in 2063."}
    claim = {"sentence": lie, "kind": "result", "team": "Chelsea", "opponent": "Liverpool", "outcome": "lost", "venue": "away", "this_season": True}
    rounds = [[claim], []]
    monkeypatch.setattr(fact_check, "claims", lambda model, key, text, notebook: rounds.pop(0))
    fake_ai += [first, fixed]
    article = article_writer.write_article(outlet, _lead(), UNTIL, "brief")
    assert article["body"] == fixed["body"] and not rounds

    rounds[:] = [[claim], [claim]]  # the writer kept it: the sentence is cut
    fake_ai += [first, first]
    article = article_writer.write_article(outlet, _lead(), UNTIL, "brief")
    assert article["body"] == "Liverpool beat Chelsea 2-1 in 2063." and not rounds


def test_form_fixtures_and_a_players_season(small_world):
    _season(small_world)
    assert _check(small_world, {"kind": "form", "team": "Chelsea", "last": 3, "won": 3}).verdict == "right"
    form = _check(small_world, {"kind": "form", "team": "Chelsea", "last": 3, "lost": 2})
    assert form.verdict == "wrong" and "lost 2 (the archive: 0)" in form.truth
    assert _check(small_world, {"kind": "fixture", "team": "Liverpool", "opponent": "Arsenal", "venue": "home"}).verdict == "right"
    assert _check(small_world, {"kind": "fixture", "team": "Liverpool", "opponent": "Arsenal", "venue": "away"}).verdict == "wrong"
    assert _check(small_world, {"kind": "player_season", "player": "Sam Scorer", "competition": "league", "goals": 4}).verdict == "right"
    assert _check(small_world, {"kind": "player_season", "player": "Sam Scorer", "goals": 9}).verdict == "wrong"


def test_a_cut_leaves_tidy_paragraphs():
    finding = fact_check.Finding("Wrong here.", {}, "wrong")
    article = {"headline": "H", "standfirst": "S.", "body": "One. Wrong here.\nWrong here. Two.\n\nThree."}
    assert fact_check.cut(article, [finding])["body"] == "One.\nTwo.\n\nThree."
