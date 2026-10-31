# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Team sheets from the match reports: formations, a team's profile, and the talking points of a selection."""

from datetime import date

from app.models import world
from app.services import story_notebooks, tactics
from app.services import world_queries as q
from tests.test_story_leads import _match_player
from tests.test_world_pages import AS_OF, ARSENAL, CHELSEA, LEAGUE, _fixture, app, pages, small_world  # noqa: F401

# Chelsea's usual 4-2-3-1: person uid -> (slot, shirt)
USUAL = {101: ("GK", 1), 102: ("D (R)", 2), 103: ("D (C)", 5), 104: ("D (C)", 6), 105: ("D (L)", 3), 106: ("DM", 8),
         107: ("DM", 4), 108: ("AM (R)", 7), 109: ("AM (C)", 10), 110: ("AM (L)", 11), 111: ("ST (C)", 9)}
YOUNGSTER, SIGNING, STAR, ARSENAL_XI = 120, 121, 122, range(201, 212)
ARSENAL_SLOTS = ("GK", "D (R)", "D (C)", "D (C)", "D (L)", "M (R)", "M (C)", "M (C)", "M (L)", "ST (C)", "ST (C)")


def _people(engine) -> None:
    with engine.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": uid, "name": f"Chelsea {uid}", "as_of": AS_OF, "team_uid": CHELSEA, "birth_date": date(2035, 1, 1),
             "reputation_world": 5000} for uid in USUAL] + [
            {"fm_uid": YOUNGSTER, "name": "Young Kid", "as_of": AS_OF, "team_uid": CHELSEA, "birth_date": date(2045, 3, 1), "reputation_world": 800},
            {"fm_uid": SIGNING, "name": "New Man", "as_of": AS_OF, "team_uid": CHELSEA, "birth_date": date(2038, 1, 1), "reputation_world": 6000},
            {"fm_uid": STAR, "name": "Big Star", "as_of": AS_OF, "team_uid": CHELSEA, "birth_date": date(2036, 1, 1), "reputation_world": 9500}] + [
            {"fm_uid": uid, "name": f"Arsenal {uid}", "as_of": AS_OF, "team_uid": ARSENAL, "birth_date": None, "reputation_world": None}
            for uid in ARSENAL_XI])


def _match(engine, day: str, chelsea: dict, score=(2, 0), bench=(), subs=(), opponent: int = ARSENAL) -> int:
    """A league match Chelsea v Arsenal (or another opponent, with Arsenal's eleven) with a report: chelsea maps
    person -> (slot, shirt); subs are (person, minute, slot, person off)."""
    with engine.begin() as c:
        fixture = c.execute(world.WorldFixture.__table__.insert(), _fixture(day, CHELSEA, opponent, score, LEAGUE, 162)).inserted_primary_key[0]
        rows, seq = [], 0
        off = {gone: minute for _, minute, _, gone in subs}
        for person, (slot, shirt) in chelsea.items():
            rows.append(_match_player(fixture, 0, seq, person, 7.0, shirt_number=shirt, start_position=slot, end_position=slot,
                                      minute_off=off.get(person, 0)))
            seq += 1
        for person, minute, slot, _ in subs:
            rows.append(_match_player(fixture, 0, seq, person, 6.8, shirt_number=30 + seq, start_position=None, end_position=slot,
                                      minute_on=minute))
            seq += 1
        for person in bench:
            rows.append(_match_player(fixture, 0, seq, person, None, played=0, shirt_number=40 + seq, start_position=None, end_position=None))
            seq += 1
        rows += [_match_player(fixture, 1, i, person, 6.5, shirt_number=i + 1, start_position=slot, end_position=slot)
                 for i, (person, slot) in enumerate(zip(ARSENAL_XI, ARSENAL_SLOTS))]
        c.execute(world.WorldMatchPlayer.__table__.insert(), rows)
        c.execute(world.WorldMatchReport.__table__.insert(), {"fixture_id": fixture, "stadium": "Stamford Bridge", "referee_name": "Wes Noble",
                                                             "minutes": 90, "as_of": AS_OF})
        c.execute(world.WorldMatchTeam.__table__.insert(), [{"fixture_id": fixture, "side": side, "team_uid": team, "squad": 1, "as_of": AS_OF}
                                                           for side, team in ((0, CHELSEA), (1, opponent))])
    return fixture


def _season(engine) -> int:
    """Four matches of the usual eleven, then one where everything changes."""
    with engine.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", level=1))
        c.execute(world.WorldInjury.__table__.insert(), [{"person_uid": 111, "seq": 0, "injury": "torn hamstring", "injured_on": date(2063, 9, 1),
                                                          "days": 40, "severity": "Major", "team_uid": CHELSEA, "current": 1, "as_of": AS_OF}])
        c.execute(world.WorldTransfer.__table__.insert(), [{"person_uid": SIGNING, "date": date(2063, 8, 20), "kind": 1, "from_uid": ARSENAL,
                                                            "to_uid": CHELSEA, "fee": 30_000_000, "fee_with_add_ons": 30_000_000}])
    _people(engine)
    for day in ("2063-08-10", "2063-08-17", "2063-08-24", "2063-08-31"):
        _match(engine, day, USUAL, bench=(STAR,), score=(1, 1) if day == "2063-08-31" else (2, 0))
    changed = {uid: USUAL[uid] for uid in USUAL if uid not in (111, 107, 108, 110)}  # the striker injured, the shape changed
    changed |= {YOUNGSTER: ("ST (C)", 45), SIGNING: ("ST (C)", 19), 108: ("M (R)", 7), 110: ("M (L)", 11)}
    return _match(engine, "2063-09-07", changed, bench=(STAR,), subs=((107, 70, "DM", 106),))


def test_a_formation_has_the_games_name():
    assert tactics.formation(["D (R)", "D (C)", "D (C)", "D (L)", "DM", "M (C)", "M (C)", "AM (R)", "AM (L)", "ST (C)"]) == "4-3-3 DM Wide"
    assert tactics.formation(iter(["GK", "D (R)", "D (C)", "D (C)", "D (L)", "DM", "DM", "AM (R)", "AM (C)", "AM (L)", "ST (C)"])) == "4-2-3-1 DM AM Wide"
    assert tactics.formation(["D (R)", "D (C)", "D (C)", "D (L)", "M (R)", "M (C)", "M (C)", "M (L)", "ST (C)", "ST (C)"]) == "4-4-2"
    # a combination the table does not have: named by the game's pattern
    assert tactics.formation(["D (C)", "D (C)", "D (L)", "D (R)", "M (C)", "M (C)", "M (C)", "ST (C)", "ST (C)", "ST (C)"]) == "4-3-3 Narrow"
    assert tactics.formation(["D (C)", "D (C)", "D (C)", "WB (R)", "WB (L)", "DM", "M (C)", "M (C)", "ST (C)", "ST (C)"]) == "5-3-2 DM WB"
    assert tactics.formation(["D (C)", "D (C)"]) is None


def test_a_selection_tells_the_change_of_shape_the_injured_regular_and_who_took_his_place(small_world):
    fixture = _season(small_world)
    with small_world.connect() as c:
        notes = tactics.selection_notes(c, fixture)[CHELSEA]
        profile = tactics.team_profile(c, CHELSEA, date(2063, 9, 7))
    assert profile["games"] == 4 and profile["usual_shape"] == "4-2-3-1 DM AM Wide" and 111 in profile["regulars"]
    assert notes["line_up"].startswith("4-1-2-1-2 DM AM Wide: GK #1 Chelsea 101; DR #2 Chelsea 102")
    text = "\n".join(notes["notes"])
    assert "Changed shape: 4-1-2-1-2 DM AM Wide, not their usual 4-2-3-1 DM AM Wide (4 of their last 4 games)" in text
    assert "Chelsea 111, a regular (4 of the last 4 starts, usually striker), injured (torn hamstring)" in text
    assert "Chelsea 107, a regular (4 of the last 4 starts, usually defensive midfielder), on the bench, came on in the 70th minute" in text
    assert "New Man, a new signing (transfer from Arsenal, 2063-08-20, £30.0m), started as striker; his first start in the reports" in text
    assert "Young Kid, aged 18, started as striker for the first time in the reports" in text
    assert "Big Star, one of the squad's best-known players, was an unused substitute" in text
    assert "Chelsea 108 started as right midfielder, not his usual right winger" in text
    assert notes["substitutions"] == ["70': Chelsea 107 on as defensive midfielder for Chelsea 106 (defensive midfielder)"]


def test_a_partnership_is_judged_by_its_own_job(small_world):
    together = {"games": 4, "won": 4, "drawn": 0, "lost": 0, "scored": 1.0, "conceded": 0.2, "points": 3.0}
    apart = {"games": 4, "won": 1, "drawn": 1, "lost": 2, "scored": 2.0, "conceded": 1.5, "points": 1.0}
    assert tactics.partnership_verdict({"label": "centre-backs", "together": together, "apart": apart}) == "a strong partnership"
    assert tactics.partnership_verdict({"label": "strike partners", "together": together, "apart": apart}) == "a struggling partnership"
    assert tactics.partnership_verdict({"label": "left flank", "together": together, "apart": apart}) == "a strong partnership"
    assert tactics.partnership_verdict({"label": "centre-backs", "together": together, "apart": None}) is None


def test_the_writers_and_the_fans_get_the_team_sheets(small_world):
    fixture = _season(small_world)
    with small_world.connect() as c:
        facts = story_notebooks.match_report_facts(c, fixture)
        summary = q.match_summary(c, fixture)
    assert facts["line_ups"]["Chelsea"].startswith("4-1-2-1-2 DM AM Wide: GK #1")
    assert any("injured (torn hamstring)" in note for note in facts["team_selection"]["Chelsea"])
    assert "never invent roles" in facts["note"]
    assert summary["sides"][0]["shape"].startswith("4-1-2-1-2") and summary["sides"][0]["talking_points"]


def test_older_reports_get_their_slots_from_a_save(small_world, monkeypatch, tmp_path):
    from app.services import fm_save, save_import

    fixture = _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldMatchPlayer.__table__.update().where(world.WorldMatchPlayer.__table__.c.fixture_id == fixture)
                  .values(shirt_number=None, start_position=None, end_position=None))
        rows = c.execute(world.WorldMatchPlayer.__table__.select().where(world.WorldMatchPlayer.__table__.c.fixture_id == fixture)
                         .order_by("side", "seq")).mappings().all()

    def player(row, bits):
        return fm_save.MatchPlayer(row["person_uid"], row["rating"] or 0.0, 0.0, 0.0, **{name: row[name] for name in (
            "goals", "assists", "shots", "shots_on_target", "passes", "passes_completed", "key_passes", "clear_cut_chances",
            "crosses", "crosses_completed", "dribbles", "tackles", "tackles_won", "key_tackles", "interceptions", "clearances",
            "headers", "headers_won", "key_headers", "fouls", "yellow_cards", "booked_minute", "minute_on", "minute_off",
            "added_time_off")}, injury_minute=0, red_cards=0, sent_off_minute=0, shirt=row["seq"] + 1, start_slot=bits, end_slot=bits)

    sides = [fm_save.MatchSide(team, 1, tuple(player(r, 1 if r["seq"] == 0 else 16384) for r in rows if r["side"] == side))
             for side, team in ((0, 1), (1, 2))]
    report = fm_save.MatchReport(0, 1, "", None, None, None, 90, *sides)

    class Save:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, name):
            return b""

    monkeypatch.setattr(fm_save, "FmSave", Save)
    monkeypatch.setattr(fm_save, "read_match_reports", lambda data: [report])
    with small_world.begin() as c:
        assert save_import.backfill_match_positions(c, tmp_path / "old.fm") == len(rows)
        first = c.execute(world.WorldMatchPlayer.__table__.select().where(world.WorldMatchPlayer.__table__.c.fixture_id == fixture)
                          .order_by("side", "seq")).mappings().first()
    assert (first["shirt_number"], first["start_position"], first["end_position"]) == (1, "GK", "GK")


def test_the_match_page_shows_the_team_sheets_and_the_season_page_how_they_play(small_world, pages):
    fixture = _season(small_world)
    page = pages.get(f"/matches/{fixture}").data.decode()
    assert "Team sheets" in page and "pitch-sheet" in page and "C. 101" in page  # Chelsea 101 as on the game's screen
    assert "70&#39;: Chelsea 107 on as defensive midfielder for Chelsea 106" in page and "Talking points" in page
    season = pages.get(f"/clubs/{CHELSEA}/2063").data.decode()
    assert "How they play" in season and "The usual eleven" in season and "League average" in season


def test_a_teams_record_by_the_formation_it_used_and_faced(small_world):
    _season(small_world)
    with small_world.connect() as c:
        records = tactics.formation_records(c, CHELSEA, "2063-07-01", "2063-09-30")
    used = {row["shape"]: row for row in records["used"]}
    assert (used["4-2-3-1 DM AM Wide"]["games"], used["4-2-3-1 DM AM Wide"]["won"], used["4-2-3-1 DM AM Wide"]["points"]) == (4, 3, 2.5)
    assert used["4-1-2-1-2 DM AM Wide"]["games"] == 1
    (faced,) = records["faced"]
    assert (faced["shape"], faced["games"], faced["scored"], faced["conceded"]) == ("4-4-2", 5, 9, 1)
    assert tactics.record_words(faced).startswith("5 games (4-1-0), 2.6 points a game, goals 9-1")


def test_the_club_page_shows_how_they_line_up_now_and_what_each_player_really_plays(small_world, pages):
    _season(small_world)
    with small_world.begin() as c:  # the game date: after these matches
        c.execute(world.WorldSave.__table__.update().values(game_date=date(2063, 9, 20)))
    page = pages.get(f"/clubs/{CHELSEA}").data.decode()
    assert "2063/64 so far" in page and "How they line up" in page and "Likely eleven" in page and "Last team sheet" in page
    assert "4-2-3-1 DM AM Wide (4 of 5)" in page
    squad = page[page.index('id="squad"'):]
    assert "<th>Plays</th>" in squad.replace(' title="The slot he has started in most this season"', "") and "rating-chip" in squad


def test_the_manager_and_the_games_partnerships_show_on_the_pages(small_world, pages):
    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldSave.__table__.update().values(game_date=date(2063, 9, 20)))
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 900, "name": "Boss Man", "as_of": AS_OF, "staff_job": "Manager",
                                                         "staff_team_uid": CHELSEA, "staff_since": date(2060, 7, 1)}])
        c.execute(world.WorldStaffProfile.__table__.insert(), [{
            "person_uid": 900, "attacking": 17, "defending": 10, "fitness": 6, "mental": 16, "technical": 14, "tactical": 15, "set_pieces": 2,
            "working_with_youngsters": 11, "formation": 29, "formation_second": 29, "formation_other": 4, "formation_other_2": 4,
            "tactical_style": 4, "mentality": 7, "pressing": 15, "as_of": AS_OF}])
        c.execute(world.WorldPartnership.__table__.insert(), [
            {"team_uid": CHELSEA, "person_a": 103, "person_b": 104, "role_a": 1, "role_b": 2, "matches": 4, "rating_sum": 296,
             "goals_for": 9, "goals_against": 1, "departed": 0, "as_of": AS_OF}])
    with small_world.connect() as c:
        from app.services import world_queries as q
        profile = q.manager_profile(c, 900)
        notes = tactics.selection_notes(c, (c.execute(world.WorldFixture.__table__.select().order_by(world.WorldFixture.__table__.c.date.desc())).first()).id)
    assert profile["tactical_style"] == "Wing Play" and profile["preferred_formation"] == "4-2-3-1 DM AM Wide"
    assert profile["other_formations"] == ["4-3-3 DM Wide"] and profile["coaching_style"] == "Attacking"
    assert any("Chelsea 103 and Chelsea 104: 4 games together (the game's record), average rating 7.4, goals 9-1" in n for n in notes[CHELSEA]["notes"])
    page = pages.get(f"/clubs/{CHELSEA}").data.decode()
    assert "The manager:" in page and "Wing Play" in page and "the game's own record" in page
    assert "As a coach" in pages.get("/people/900").data.decode()
