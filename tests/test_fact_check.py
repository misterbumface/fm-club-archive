"""The paper's fact checker: hard facts checked in the archive, colour left alone (2 Nov 2026)."""

from datetime import date

from app.services import article_writer, fact_check, outlets
from tests.test_outlets import _lead, fake_ai  # noqa: F401
from tests.test_story_leads_now import SCORER, _big_cup, _season
from tests.test_world_pages import AS_OF, CHELSEA, LIVERPOOL, app, small_world  # noqa: F401

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


def _shots(small_world, rows):
    from app.models import world

    with small_world.begin() as c:
        c.execute(world.WorldMatchShot.__table__.insert(), [
            {"fixture_id": 15, "side": 1, "person_uid": SCORER, "across": 0.5, "height": 0.1, "outside_box": None, "as_of": AS_OF, **r}
            for r in rows])


def test_the_kind_of_shot_is_read_from_the_events_type_bits():
    """Checked against the game's words for 25 goals (docs/goal-labels.md)."""
    from app.services.fm_save import shot_kind

    assert [shot_kind(code) for code in (0x03, 0x05, 0x85, 0xa1, 0x21, 0x01, 0x81, 0x00)] == \
        ["header", "volley", "volley", "drive", "drive", "finish", "finish", None]


def test_how_the_goals_went_in_tells_the_kind_the_height_and_a_rebound(small_world):
    """Rodrigão's shot was blocked and he headed the rebound in, in the same second (9 Feb 2064)."""
    from app.services import story_leads_now as now
    from app.services.story_leads import _World

    _season(small_world)
    _shots(small_world, [{"seq": 0, "minute": 80, "second": 8, "outcome": "blocked", "kind": None, "height": 1.4},
                         {"seq": 1, "minute": 80, "second": 9, "outcome": "goal", "kind": "header", "height": 0.4, "across": 3.2}])
    with small_world.connect() as c:
        w = _World(c, UNTIL, UNTIL, (CHELSEA,))
        lines = now._how_goals_went_in(w, {"id": 15, "home_uid": LIVERPOOL, "away_uid": CHELSEA})
    assert lines[0] == "Sam Scorer (Chelsea), 81' on the match clock: a header, low, near a post; from the rebound of his own blocked or saved shot"
    assert "never tell them" in lines[-1]


def test_the_range_of_a_goal_is_read_from_the_byte_after_the_type():
    """Measured on the game's analysis screen (3 Nov 2026): Pardal 5.8 m `40`, Messi 7.3 m `40`, Myers 12.3 m `10`,
    Robertson about 16 m `10`, Hilgers 24.1 m `90`; Diarra's free kick `88`."""
    from app.services.fm_save import shot_range

    assert [shot_range(code) for code in (0x40, 0x50, 0x44, 0x10, 0x00, 0x90, 0x80, 0x88)] == \
        ["close", "close", "close", None, None, "outside", "outside", "outside"]


def test_the_goal_lines_and_the_checker_tell_the_range(small_world):
    from app.services import story_leads_now as now
    from app.services.story_leads import _World

    _season(small_world)
    _shots(small_world, [{"seq": 0, "minute": 7, "second": 0, "outcome": "goal", "kind": "header", "range": "close", "outside_box": 0},
                         {"seq": 1, "minute": 42, "second": 0, "outcome": "goal", "kind": "drive", "range": "outside", "outside_box": 1}])
    with small_world.connect() as c:
        w = _World(c, UNTIL, UNTIL, (CHELSEA,))
        lines = now._how_goals_went_in(w, {"id": 15, "home_uid": LIVERPOOL, "away_uid": CHELSEA})
        assert lines[0].startswith("Sam Scorer (Chelsea), 8' on the match clock: a header from close range,")
        assert lines[1].startswith("Sam Scorer (Chelsea), 43' on the match clock: a drive (struck low and hard) from outside the box,")
        checker = fact_check.Checker(c, UNTIL, {"id": 15})
        claim = {"sentence": "S.", "kind": "goal_kind", "player": "Sam Scorer"}
        assert checker.check({**claim, "range": "close"}).verdict == "right"
        assert checker.check({**claim, "range": "outside", "shot": "drive"}).verdict == "right"
    with small_world.begin() as c:
        c.exec_driver_sql("delete from match_shots")
    _shots(small_world, [{"seq": 0, "minute": 7, "second": 0, "outcome": "goal", "kind": "finish", "range": "close", "outside_box": 0}])
    with small_world.connect() as c:
        wrong = fact_check.Checker(c, UNTIL, {"id": 15}).check({"sentence": "S.", "kind": "goal_kind", "player": "Sam Scorer", "range": "outside"})
        assert wrong.verdict == "wrong" and wrong.truth == "Sam Scorer's goal in this game: from close range"


def test_the_checker_knows_a_header_from_a_shot(small_world):
    _season(small_world)
    _shots(small_world, [{"seq": 0, "minute": 50, "second": 0, "outcome": "goal", "kind": "header"}])
    with small_world.connect() as c:
        checker = fact_check.Checker(c, UNTIL, {"id": 15})
        claim = {"sentence": "S.", "kind": "goal_kind", "player": "Sam Scorer"}
        assert checker.check({**claim, "shot": "header"}).verdict == "right"
        wrong = checker.check({**claim, "shot": "volley"})
        assert wrong.verdict == "wrong" and wrong.truth == "Sam Scorer's goal in this game: a header"
        assert checker.check({**claim, "shot": "shot"}).verdict == "wrong"  # a header is no strike with the foot
        assert fact_check.Checker(c, UNTIL, {"id": 9}).check({**claim, "shot": "volley"}).verdict == "unknown"  # no shot records


def test_a_title_run_is_counted_from_the_archives_champions(small_world):
    """The game said Chelsea were "on course to win their 42nd Premier Division title in a row"; the editor had cut it from
    a report as a fact it could not check (2 Nov 2026)."""
    from app.models import world

    _season(small_world)
    with small_world.begin() as c:  # the test world has Chelsea as 2063's champions; add 2062 and Liverpool in 2061
        c.execute(world.WorldEditionPlace.__table__.insert(), [{"competition_uid": 11, "year": 2062, "place": 1, "team_uid": CHELSEA},
                                                               {"competition_uid": 11, "year": 2061, "place": 1, "team_uid": LIVERPOOL}])
    assert _check(small_world, {"kind": "title_run", "team": "Chelsea", "won_in_a_row": 2}).verdict == "right"
    wrong = _check(small_world, {"kind": "title_run", "team": "Chelsea", "won_in_a_row": 41})
    assert wrong.verdict == "wrong" and wrong.truth.startswith("Chelsea have won 2 league titles in a row")


def test_a_player_trait_is_hinted_at_never_read_out():
    """The user: describe how players play, "gentle influence ... not HE LIKES TO CUT IN FROM THE LEFT AND HOLD UP PLAY"."""
    from app.services.story_writer import STYLE_FAULT, hard_faults, trait_copies

    notebook = {"people": [{"name": "Eric Geyer", "how_he_plays": "cuts inside from left wing, plays one-twos, comes deep to get ball"}]}
    assert trait_copies("Geyer, always looking to come inside, found space again.", notebook) == []
    copied = trait_copies("Geyer cuts inside from the left wing, plays one-twos and comes deep to get the ball.", notebook)
    assert len(copied) == 3 and all(f.startswith(STYLE_FAULT) for f in copied)
    assert hard_faults(copied) == []  # a style fault: the writer is asked again, the piece is not stopped


def test_through_or_out_is_checked_on_the_whole_tie(small_world):
    """25 Feb 2064: "for Napoli, the curtain has fallen" after a 1-0 defeat in Cluj; they went through 3-2 on aggregate."""
    _season(small_world)
    _big_cup(small_world, (1, 1))  # Chelsea 2-1 Liverpool, then Liverpool 1-1 Chelsea; Arsenal v Spurs still open
    out = _check(small_world, {"kind": "tie", "team": "Chelsea", "outcome": "out"})
    assert out.verdict == "wrong" and out.truth.startswith("Chelsea went through against Liverpool: 2063-08-20: Chelsea 2-1 Liverpool")
    assert _check(small_world, {"kind": "tie", "team": "Liverpool", "outcome": "out", "competition": "Champions League"}).verdict == "right"
    assert _check(small_world, {"kind": "tie", "team": "Chelsea", "outcome": "through", "opponent": "Liverpool"}).verdict == "right"
    assert _check(small_world, {"kind": "tie", "team": "Arsenal", "outcome": "through"}).verdict == "unknown"  # the second leg is to come


def test_a_goals_minute_and_a_games_weekday_are_checked(small_world):
    """25 Feb 2064: Cluj's goal after 37 minutes put "in the second half", and Milan's Wednesday second leg on a Thursday."""
    _season(small_world)  # Chelsea 1-0 Arsenal on Friday 24 August 2063, Sam Scorer after 10 minutes; Liverpool v Arsenal on Monday 10 September
    goal = {"kind": "goal_minute", "team": "Chelsea", "opponent": "Arsenal"}
    assert _check(small_world, {**goal, "player": "Sam Scorer", "minute": 10}).verdict == "right"
    assert _check(small_world, {**goal, "half": "first"}).verdict == "right"
    late = _check(small_world, {**goal, "half": "second"})
    assert late.verdict == "wrong" and late.truth.endswith("Sam Scorer 10' (Chelsea)")
    assert _check(small_world, {**goal, "minute": 10, "team": "Arsenal", "opponent": "Chelsea"}).verdict == "wrong"  # not theirs
    assert _check(small_world, {"kind": "goal_minute", "team": "Chelsea", "minute": 10}).verdict == "unknown"  # which game?
    day = {"kind": "game_day", "team": "Chelsea", "opponent": "Arsenal", "when": "past"}
    assert _check(small_world, {**day, "weekday": "Friday"}).verdict == "right"
    thursday = _check(small_world, {**day, "weekday": "Thursday"})
    assert thursday.verdict == "wrong" and thursday.truth == "Chelsea's games of these days: Friday 24 August: Chelsea v Arsenal"
    assert _check(small_world, {"kind": "game_day", "team": "Liverpool", "weekday": "Monday", "when": "future"}).verdict == "right"
    assert _check(small_world, {"kind": "game_day", "team": "Liverpool", "weekday": "Sunday", "when": "future"}).verdict == "wrong"
