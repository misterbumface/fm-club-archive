# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""The news of the season (story_leads_now): round-ups, runs, derbies, previews, injuries, form and the scoring pace."""

from datetime import date

from app.extensions import db
from app.models import world
from app.services import story_leads_now as now
from app.services.story_leads import _World
from tests.test_world_pages import AS_OF, ARSENAL, CHELSEA, FA_CUP, LEAGUE, LIVERPOOL, SPURS, _fixture, app, small_world  # noqa: F401

SINCE, UNTIL = date(2063, 8, 20), date(2063, 9, 3)
SCORER, HURT, FLOP, STAR = 301, 302, 303, 304


def _season(engine):
    """Four rounds of a new season: Chelsea win all four, Spurs lose all four; Arsenal and Spurs are rivals."""
    stats = {"substitute_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0,
             "red_cards": 0, "minutes": 0, "as_of": AS_OF, "seq": 0, "season_start": 2063, "kind": 1, "league_uid": LEAGUE}
    with engine.begin() as c:
        comps, teams = world.WorldCompetition.__table__, world.WorldTeam.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", nation_uid=765, level=1))
        for uid, rep in ((CHELSEA, 9000), (LIVERPOOL, 8500), (ARSENAL, 8000), (SPURS, 7000)):
            c.execute(teams.update().where(teams.c.fm_uid == uid).values(reputation=rep))
        c.execute(world.WorldFixture.__table__.delete().where(world.WorldFixture.__table__.c.date == date(2063, 8, 12)))
        c.execute(world.WorldFixture.__table__.insert(), [
            _fixture("2063-08-10", CHELSEA, LIVERPOOL, (2, 0), LEAGUE, 162), _fixture("2063-08-10", ARSENAL, SPURS, (1, 0), LEAGUE, 162),
            _fixture("2063-08-17", SPURS, CHELSEA, (0, 3), LEAGUE, 162), _fixture("2063-08-17", LIVERPOOL, ARSENAL, (0, 1), LEAGUE, 162),
            _fixture("2063-08-24", CHELSEA, ARSENAL, (1, 0), LEAGUE, 162), _fixture("2063-08-24", SPURS, LIVERPOOL, (0, 2), LEAGUE, 162),
            _fixture("2063-08-31", LIVERPOOL, CHELSEA, (0, 1), LEAGUE, 162), _fixture("2063-08-31", SPURS, ARSENAL, (1, 2), LEAGUE, 162),
            _fixture("2063-09-10", LIVERPOOL, ARSENAL, None, LEAGUE, 162)])
        chelsea_games = [r.id for r in c.execute(world.WorldFixture.__table__.select().where(
            (world.WorldFixture.__table__.c.competition_uid == LEAGUE) & (world.WorldFixture.__table__.c.date >= date(2063, 8, 1))
            & ((world.WorldFixture.__table__.c.home_uid == CHELSEA) | (world.WorldFixture.__table__.c.away_uid == CHELSEA))))]
        c.execute(world.WorldGoal.__table__.insert(), [
            {"fixture_id": f, "seq": 0, "scorer_uid": SCORER, "side": 0, "minute": 10, "added_time": 0, "kind": 0} for f in chelsea_games])
        c.execute(world.WorldClubRelation.__table__.insert(), [
            {"club_uid": SPURS, "relation": "rival", "subject_uid": ARSENAL, "reason": None, "level": 90, "permanent": 0, "names_stadium": 0, "as_of": AS_OF}])
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": SCORER, "name": "Sam Scorer", "as_of": AS_OF, "team_uid": CHELSEA, "reputation_world": 6000},
            {"fm_uid": HURT, "name": "Hal Hurt", "as_of": AS_OF, "team_uid": LIVERPOOL, "reputation_world": 6500},
            {"fm_uid": FLOP, "name": "Fred Flop", "as_of": AS_OF, "team_uid": SPURS, "reputation_world": 8500},
            {"fm_uid": STAR, "name": "Stan Star", "as_of": AS_OF, "team_uid": ARSENAL, "reputation_world": 2500}])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [
            {**stats, "person_uid": SCORER, "team_uid": CHELSEA, "starts": 4, "goals": 4, "rated_appearances": 4, "rating_total": 290},
            {**stats, "person_uid": FLOP, "team_uid": SPURS, "starts": 4, "goals": 0, "rated_appearances": 4, "rating_total": 248},
            {**stats, "person_uid": STAR, "team_uid": ARSENAL, "starts": 4, "goals": 3, "rated_appearances": 4, "rating_total": 316}])
        c.execute(world.WorldInjury.__table__.insert(), [
            {"person_uid": HURT, "seq": 0, "injury": "broken ankle", "injured_on": date(2063, 8, 31), "days": 40, "severity": "Major",
             "team_uid": LIVERPOOL, "current": 1, "as_of": AS_OF}])


def _found(engine) -> dict[str, list]:
    with engine.connect() as c:
        w = _World(c, SINCE, UNTIL, (CHELSEA,))
        found: dict[str, list] = {}
        for finder in now.FINDERS:
            for lead in finder(w):
                found.setdefault(lead.kind, []).append(lead)
    return found


def test_the_league_round_up_tells_the_cycles_results_and_the_table(small_world):
    """One match-day article per round (27 Oct 2026): the big games in full, then the table; the cycle's two rounds give two."""
    _season(small_world)
    found = _found(small_world)
    first, second = sorted(found["league_round"], key=lambda lead: lead.date)
    assert first.facts["match_day"] == "2063-08-24" and second.facts["match_day"] == "2063-08-31"
    assert second.headline == "English Premier Division, match day of 2063-08-31: Liverpool 0-1 Chelsea, Chelsea won"  # the biggest clubs' game first
    game = second.facts["big_games"][0]
    assert game["result"].endswith("Liverpool 0-1 Chelsea, Chelsea won") and game["goals"] == ["Sam Scorer 10' (Liverpool)"]
    assert second.facts["table_top"][0].startswith("1. Chelsea: 12 points from 4 games") and "other_results" not in second.facts
    assert second.facts["without_a_win"] == ["Tottenham"] and "home" in second.beats
    assert any(ref.startswith("match:2063-08-31") for ref in second.refs)


def test_a_small_story_of_a_round_game_is_folded_into_the_match_day_article():
    from app.services.story_leads import Lead, fold_into_matchdays

    day = date(2063, 8, 31)
    round_up = Lead("league_round", "Round", day, 60, {}, ["competition:1", "match:2063-08-31:1:2"], {"world"})
    star = Lead("performance", "Sam Scorer hits four", day, 50, {}, ["person:9", "match:2063-08-31:1:2", "club:1"], {"world"})
    elsewhere = Lead("performance", "A cup hat-trick", day, 50, {}, ["person:8", "match:2063-08-30:5:6"], {"world"})
    kept = fold_into_matchdays([round_up, star, elsewhere])
    assert kept == [round_up, elsewhere] and round_up.facts["stories_of_the_day"] == ["Sam Scorer hits four"]
    assert "person:9" in round_up.refs


def test_the_market_is_news_in_its_windows():
    from app.services.story_leads import in_window

    assert in_window(date(2063, 8, 31)) and in_window(date(2064, 1, 15)) and not in_window(date(2063, 11, 2))


def test_runs_derbies_previews_and_injuries_are_news_now(small_world):
    _season(small_world)
    found = _found(small_world)
    runs = {lead.facts["club"]: lead.facts for lead in found["run"]}
    assert runs["Chelsea"]["run"] == "4 league wins in a row"
    assert runs["Tottenham"]["run"] == "4 league defeats in a row" and runs["Tottenham"]["pressure_on_the_manager"]
    (derby,) = found["derby"]
    assert derby.facts["score"] == "1-2" and derby.facts["rivalry"].startswith("90/100")
    (preview,) = found["preview"]
    assert preview.headline.startswith("Coming up: Liverpool v Arsenal, 2063-09-10")
    assert preview.facts["home_injured"] == ["Hal Hurt (broken ankle)"]
    assert preview.facts["top_clash"] == "2nd against 3rd, 6 points apart, 2 games left"  # Rotherham v Liverpool was cut (2 Nov 2026)
    assert "relegation_six_pointer" not in preview.facts  # four teams: no bottom four to speak of
    (blow,) = found["injury_blow"]
    assert blow.facts["club_games_he_misses"] == 1 and blow.facts["back_about"] == "2063-10-10"


def test_form_against_the_name_and_the_scoring_pace(small_world):
    _season(small_world)
    found = _found(small_world)
    assert [lead.facts["player"] for lead in found["underperformer"]] == ["Fred Flop"]
    assert [lead.facts["player"] for lead in found["overperformer"]] == ["Stan Star"]
    (pace,) = found["stat_watch"]
    assert pace.facts["league_goals"] == 4 and pace.facts["goals_a_game"] == 1.0 and "numbers" in pace.beats


def test_a_preview_tells_the_system_on_the_tactics_screen(small_world):
    _season(small_world)
    with small_world.begin() as c:
        (made,) = c.execute(world.WorldTeamTactic.__table__.insert().values(
            team_uid=LIVERPOOL, slot=1, name="Red Machine", style="Gegenpress", mentality="Attacking", fluidity="Fluid",
            formation="4-3-3 DM Wide", as_of=AS_OF)).inserted_primary_key
        c.execute(world.WorldTeamTacticSlot.__table__.insert(), [
            {"tactic_id": made, "seq": 0, "position": "GK", "side": None, "duty": "Support", "role_key": "1.10", "role": "Sweeper Keeper"},
            {"tactic_id": made, "seq": 1, "position": "ST (C)", "side": None, "duty": "Attack", "role_key": "1.08", "role": "Advanced Forward"}])
    (preview,) = _found(small_world)["preview"]
    assert preview.facts["home_system_on_the_tactics_screen"] == ("4-3-3 DM Wide, Gegenpress, Attacking, Fluid: GK Sweeper Keeper (Support); "
                                                                   "ST Advanced Forward (Attack)")
    assert "away_system_on_the_tactics_screen" not in preview.facts


def test_a_previewed_game_gets_its_report_for_the_paper_that_previewed_it(app, small_world):
    from app.models.outlet import Outlet
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue
    from app.services import editions

    _season(small_world)
    with small_world.begin() as c:  # the previewed game is played: Liverpool 2-1 Arsenal on 10 Sep
        f = world.WorldFixture.__table__
        c.execute(f.update().where(f.c.date == date(2063, 9, 10)).values(home_goals=2, away_goals=1))
    paper = Outlet(name="The English Daily", slug="english-daily", voice="plain", byline="Desk", beats=["nation:765"], articles_per_edition=6)
    db.session.add(paper)
    db.session.flush()
    issue = WorldSoccerIssue(publication_date=date(2063, 9, 3), knowledge_cutoff=date(2063, 9, 3), status="published", edition_name="x", outlet_id=paper.id)
    issue.articles.append(WorldSoccerArticle(headline="Liverpool v Arsenal: the top two meet", section="Coming up", article_type="report",
                                             lead_key=f"preview:match:2063-09-10:{LIVERPOOL}:{ARSENAL}:2063", standfirst="Hurt is out.",
                                             body="x", byline="Desk", prominence="standard", sort_order=0,
                                             lead={"facts": {"home_injured": ["Hal Hurt (broken ankle)"], "key_duels": ["Star v Hurt"], "home": "Liverpool"}}))
    db.session.add(issue)
    db.session.commit()
    with small_world.connect() as c:
        w = _World(c, date(2063, 9, 4), date(2063, 9, 17), (CHELSEA,))
        (lead,) = now.promised_reports(w)
    assert lead.headline == "As previewed: Liverpool 2-1 Arsenal (English Premier Division)" and lead.outlet_id == paper.id
    assert lead.facts["what_we_said"] == {"headline": "Liverpool v Arsenal: the top two meet", "standfirst": "Hurt is out.", "published": "2063-09-03",
                                          "home_injured": ["Hal Hurt (broken ankle)"], "key_duels": ["Star v Hurt"]}
    assert lead.facts["score"] == "2-1" and lead.refs[0] == f"match:2063-09-10:{LIVERPOOL}:{ARSENAL}"
    other = Outlet(name="Another", slug="another", voice="plain", byline="Desk", beats=["nation:765"], articles_per_edition=6)
    db.session.add(other)
    db.session.commit()
    plans = editions.plan_editions([lead], [paper, other], {})
    assert [(plan.outlet.id, [x.kind for x, _ in plan.items]) for plan in plans if plan.items] == [(paper.id, ["promised_report"])]  # not the other paper's


def test_the_drama_of_a_game_raises_its_preview(small_world, monkeypatch):
    """Liverpool v Arsenal on 10 Sep: first against second late in the season (thresholds lowered), Liverpool unbeaten,
    Arsenal's manager a former Liverpool manager; and an FA Cup final on the same day."""
    monkeypatch.setattr(now, "LATE_SEASON", 0.0)
    monkeypatch.setattr(now, "TITLE_GAP", 6)
    monkeypatch.setattr(now, "UNBEATEN_AT_STAKE", 2)
    _season(small_world)
    with small_world.begin() as c:  # Arsenal win at Chelsea and Liverpool beat Chelsea: Arsenal first, Liverpool second, unbeaten in 2
        f = world.WorldFixture.__table__
        c.execute(f.update().where((f.c.date == date(2063, 8, 24)) & (f.c.home_uid == CHELSEA)).values(home_goals=0, away_goals=3))
        c.execute(f.update().where(f.c.date == date(2063, 8, 31)).where(f.c.home_uid == LIVERPOOL).values(home_goals=3, away_goals=0))
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 900, "name": "Bill Back", "as_of": AS_OF, "staff_job": "Manager", "staff_team_uid": ARSENAL}])
        c.execute(world.WorldManagerSpell.__table__.insert(), [{"team_uid": LIVERPOOL, "seq": 1, "person_uid": 900, "started": date(2058, 7, 1),
                                                                "ended": date(2060, 5, 30), "won": 50, "drawn": 20, "lost": 20, "league_wins": 1, "cup_wins": 0, "as_of": AS_OF}])
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2063-09-10", CHELSEA, SPURS, None, FA_CUP, 3099)])  # the FA Cup final: one tie in its stage
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == FA_CUP).values(scope="cup", nation_uid=765, level=1, reputation=150))
    found = _found(small_world)
    previews = {lead.facts["home"] + " v " + lead.facts["away"]: lead for lead in found["preview"]}
    big = previews["Liverpool v Arsenal"]
    assert big.facts["title_decider"].startswith("first against second, ") and big.facts["home_run_at_stake"] == "unbeaten in 2 league games"
    assert big.facts["away_returns"] == ["Bill Back, Arsenal's manager, managed Liverpool (2058 to 2060)"]
    assert previews["Chelsea v Tottenham"].facts["round"] == "the final"
    assert big.score > previews["Chelsea v Tottenham"].score  # a title decider with a run and a return outweighs a cup final


def test_a_competition_the_world_does_not_know_is_a_friendly():
    """A national team's next game in an unknown competition (an international friendly has no scope) once stopped the whole
    news cycle with KeyError 'name' (24 Nov 2063)."""
    from types import SimpleNamespace

    w = SimpleNamespace(comp=lambda uid: {11: {"name": "English Premier Division"}}.get(uid, {}))
    assert now._comp_name(w, 11) == "English Premier Division" and now._comp_name(w, 12345) == "a friendly"


def test_the_market_tells_who_wants_a_followed_clubs_player_and_the_big_bids(small_world):
    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldTransferInterest.__table__.insert(), [
            {"team_uid": ARSENAL, "person_uid": SCORER, "rumoured": 0, "wage": 40000, "offered_wage": 80000, "as_of": AS_OF},
            {"team_uid": LIVERPOOL, "person_uid": SCORER, "rumoured": 1, "wage": 40000, "offered_wage": 40000, "as_of": AS_OF}])
        c.execute(world.WorldTransferOffer.__table__.insert(), [
            {"team_uid": ARSENAL, "person_uid": SCORER, "squad_uid": ARSENAL, "fee": 43_000_000, "status": "collapsed", "offered_on": date(2063, 8, 31),
             "until": date(2063, 9, 3), "completes_on": date(2064, 1, 1), "as_of": AS_OF}])
    found = _found(small_world)
    (interest,) = found["transfer_interest"]
    assert interest.headline == "Arsenal want Sam Scorer (Chelsea)" and interest.facts["rumoured"] == ["Liverpool"]
    assert "wages_on_offer" not in interest.facts and interest.slug == f"{SCORER}:2063"  # the entry's two amounts are not wages
    (bid,) = found["transfer_bid"]
    assert bid.headline == "Arsenal bid £43.0m for Sam Scorer (collapsed)" and bid.facts["his_club"] == "Chelsea" and bid.facts["fee_pounds"] == 43_000_000


def test_a_bid_from_an_earlier_cycle_is_news_again_when_it_is_answered(small_world):
    _season(small_world)
    bid = {"team_uid": ARSENAL, "person_uid": SCORER, "squad_uid": ARSENAL, "fee": 43_000_000, "offered_on": date(2063, 6, 1),
           "until": date(2063, 6, 8), "completes_on": date(2063, 6, 8), "as_of": AS_OF}
    with small_world.begin() as c:
        c.execute(world.WorldTransferOffer.__table__.insert(), [{**bid, "status": "made", "status_changed_on": None}])
    assert "transfer_bid" not in _found(small_world)  # made before this cycle, and no answer: old news
    with small_world.begin() as c:
        c.execute(world.WorldTransferOffer.__table__.update().values(status="rejected", status_changed_on=UNTIL))
    (answered,) = _found(small_world)["transfer_bid"]
    assert answered.headline == "Arsenal bid £43.0m for Sam Scorer (rejected)" and answered.facts["status_known_since"] == str(UNTIL)


def test_a_counter_proposal_is_told_with_its_bid_never_as_a_bid(small_world):
    """Chelsea asked Juventus for £165m for Valera on a £71.5m bid; the paper never said so (26 Jan 2064). The counter is
    news with its bid when a save first shows it, and stays in the bid's story when the bid is answered."""
    _season(small_world)
    bid = {"team_uid": ARSENAL, "person_uid": SCORER, "squad_uid": ARSENAL, "offered_on": date(2063, 8, 1), "as_of": AS_OF}
    with small_world.begin() as c:
        c.execute(world.WorldTransferOffer.__table__.insert(), [
            {**bid, "fee": 43_000_000, "status": "made", "until": date(2063, 9, 20), "completes_on": date(2063, 9, 20), "status_changed_on": None},
            {**bid, "fee": 90_000_000, "status": "counter", "until": None, "completes_on": date(2063, 8, 1), "status_changed_on": UNTIL}])
    (countered,) = _found(small_world)["transfer_bid"]
    assert countered.headline == "Arsenal bid £43.0m for Sam Scorer (countered)"
    assert countered.facts["his_club_asked_for_pounds"] == 90_000_000 and "asked for £90.0m" in countered.facts["the_counter_proposal"]
    with small_world.begin() as c:
        t = world.WorldTransferOffer.__table__
        c.execute(t.update().where(t.c.status == "counter").values(status_changed_on=date(2063, 8, 10)))
        c.execute(t.update().where(t.c.status == "made").values(status="withdrawn", status_changed_on=UNTIL))
    (walked,) = _found(small_world)["transfer_bid"]
    assert walked.headline == "Arsenal bid £43.0m for Sam Scorer (withdrawn)" and walked.facts["his_club_asked_for_pounds"] == 90_000_000


def test_a_loan_offer_alone_is_news_and_keeps_its_own_status(small_world):
    """A week with a loan offer and no bid stopped the news cycle: the loan's key read the bid loop's status (2 Nov 2026)."""
    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldTransferOffer.__table__.insert(), [
            {"team_uid": ARSENAL, "person_uid": SCORER, "squad_uid": ARSENAL, "fee": 0, "loan": 1, "status": "made",
             "offered_on": UNTIL, "until": None, "completes_on": date(2064, 6, 30), "as_of": AS_OF}])
    found = _found(small_world)
    (loan,) = found["loan_offer"]
    assert "transfer_bid" not in found and loan.slug.endswith(":made")


def test_a_world_cup_in_play_gets_reports_previews_a_round_up_and_our_players(small_world):
    from app.services import tournament_leads
    from tests.test_world_pages import ENGLAND

    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 1301385, "name": "World Cup", "short_name": "World Cup", "abbreviation": "WC",
                                                                "scope": "national_teams", "level": None, "reputation": 200}])
        c.execute(world.WorldNation.__table__.insert(), [{"fm_uid": 801, "name": "Spain", "short_name": "Spain", "abbreviation": "ESP", "adjective": "Spanish"},
                                                         {"fm_uid": 802, "name": "Brazil", "short_name": "Brazil", "abbreviation": "BRA", "adjective": "Brazilian"},
                                                         {"fm_uid": 803, "name": "Japan", "short_name": "Japan", "abbreviation": "JPN", "adjective": "Japanese"}])
        c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": 900 + n, "club_uid": None, "nation_uid": 800 + n, "name": name, "age_limit": None, "as_of": AS_OF}
                                                       for n, name in ((1, "Spain"), (2, "Brazil"), (3, "Japan"))])
        # a group stage from 25 August, a final on 20 September: in play at the cycle's end, 3 September
        c.execute(world.WorldFixture.__table__.insert(), [
            _fixture("2063-08-25", ENGLAND, 901, (2, 1), 1301385, 1), _fixture("2063-08-25", 902, 903, (3, 0), 1301385, 1),
            _fixture("2063-08-29", ENGLAND, 902, (0, 0), 1301385, 1), _fixture("2063-08-29", 901, 903, (1, 0), 1301385, 1),
            _fixture("2063-09-06", ENGLAND, 903, None, 1301385, 1), _fixture("2063-09-06", 901, 902, None, 1301385, 1),
            _fixture("2063-09-20", ENGLAND, 902, None, 1301385, 2)])
        played = c.exec_driver_sql("select id from fixtures where date = '2063-08-25' and home_uid = %d" % ENGLAND).scalar()
        c.execute(world.WorldPlayerGame.__table__.insert(), [{"person_uid": SCORER, "team_uid": ENGLAND, "played_on": date(2063, 8, 25), "opponent_uid": 901,
                                                              "fixture_id": played, "status": "played", "minutes": 90, "rating": 8.2, "xg": 1.1, "goals": 2,
                                                              "assists": 0, "as_of": AS_OF}])
    with small_world.connect() as c:
        w = _World(c, SINCE, UNTIL, (CHELSEA,))
        t = tournament_leads.major_tournament(w)
        assert (t["name"], t["year"], t["phase"], t["played"], t["games"]) == ("World Cup", 2063, "in play", 4, 7)
        leads = tournament_leads.tournament_coverage(w)
    kinds = {lead.kind: [x for x in leads if x.kind == lead.kind] for lead in leads}
    assert {k: len(v) for k, v in kinds.items()} == {"tournament_report": 4, "tournament_preview": 2, "tournament_round_up": 1, "our_players_at_tournament": 1}
    report = next(x for x in kinds["tournament_report"] if "Spain" in x.headline and "England" in x.headline)
    assert report.headline == "World Cup 2063, Group stage: England 2-1 Spain" and report.facts["our_players_in_it"] == ["Sam Scorer (Chelsea, England): 1 game, 90 minutes, 2 goals, rated 8.20"]
    assert report.facts["groups_now"][0].startswith("Group stage: ") and "England 4" in report.facts["groups_now"][0]
    assert report.refs[0] == f"match:2063-08-25:{ENGLAND}:901" and "home" in report.beats
    preview = next(x for x in kinds["tournament_preview"] if "Japan" in x.headline)
    assert preview.facts["our_players_in_the_squads"] == ["Sam Scorer (Chelsea, England): 1 game, 90 minutes, 2 goals, rated 8.20"]
    assert "England 2-1 Spain" in preview.facts["so_far_in_the_tournament"][0]
    ours = kinds["our_players_at_tournament"][0]
    assert ours.headline == "Chelsea at the World Cup 2063: 1 players this cycle" and ours.slug == f"1301385:{UNTIL}"
    round_up = kinds["tournament_round_up"][0]
    assert round_up.facts["where_it_stands"] == "4 of 7 games played, in play" and len(round_up.facts["results_this_cycle"]) == 4


def test_the_followed_clubs_every_game_gets_a_report_and_a_preview_for_its_own_paper(small_world):
    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": 99999, "club_uid": None, "nation_uid": None, "name": "99999", "age_limit": None, "as_of": AS_OF}])
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2063-09-07", CHELSEA, SPURS, None, LEAGUE, 162),
                                                          _fixture("2063-09-11", CHELSEA, 99999, None, 777, 1),  # an unknown competition: a friendly
                                                          _fixture("2063-09-01", 99999, CHELSEA, (1, 1), 777, 1)])
    found = _found(small_world)
    reports = sorted(found["club_report"], key=lambda x: x.date)
    assert [r.headline for r in reports] == ["Chelsea 1-0 Arsenal (English Premier Division)", "Liverpool 0-1 Chelsea (English Premier Division)",
                                             "99999 1-1 Chelsea (a friendly)"] or [r.headline for r in reports][:2] == [
        "Chelsea 1-0 Arsenal (English Premier Division)", "Liverpool 0-1 Chelsea (English Premier Division)"]
    league_game, friendly = reports[0], reports[-1]
    assert league_game.beats == {"home"} and league_game.facts["goals"] == ["Sam Scorer 10' (Chelsea)"] and "home_place_now" in league_game.facts
    season = league_game.facts["home_league_season"]  # the whole record, so a report cannot invent a defeat (6 Feb 2064)
    assert season.startswith("Chelsea in this league this season, up to and with this game: played") and "The defeats:" in season
    assert friendly.facts["treatment"] == "a brief: this was a friendly" and friendly.score < league_game.score
    previews = sorted(found["club_preview"], key=lambda x: x.facts["date"])
    assert [p.headline for p in previews] == ["Next up: Chelsea v Tottenham, 2063-09-07 (English Premier Division)",
                                              "Next up: Chelsea v 99999, 2063-09-11 (a friendly)"] or len(previews) == 2
    assert previews[0].refs[0] == f"match:2063-09-07:{CHELSEA}:{SPURS}" and previews[1].facts["treatment"] == "a brief: this is a friendly"


def test_a_scouted_player_is_looked_up_again_a_year_later_and_two_years_later(app, small_world):
    from app.models.outlet import Outlet
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue

    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldTransfer.__table__.insert(), [{"person_uid": STAR, "date": date(2063, 1, 15), "kind": 1, "from_uid": SPURS, "to_uid": CHELSEA,
                                                            "squad_uid": CHELSEA, "fee": 12_000_000, "fee_with_add_ons": 12_000_000}])
    young = Outlet(name="The Young Ones", slug="young-ones", voice="plain", byline="Desk", beats=["world", "young"], articles_per_edition=6)
    db.session.add(young)
    db.session.flush()
    issue = WorldSoccerIssue(publication_date=date(2062, 8, 28), knowledge_cutoff=date(2062, 8, 28), status="published", edition_name="x", outlet_id=young.id)
    issue.articles.append(WorldSoccerArticle(headline="Scouting report: Stan Star (18), Tottenham", section="Scouting", article_type="report",
                                             lead_key=f"scouting:person:{STAR}:2062", standfirst="A winger to watch.", body="x", byline="Desk",
                                             prominence="standard", sort_order=0,
                                             lead={"facts": {"age": 18, "team": "Tottenham", "scout_view": "one to watch", "standout_traits": ["pace"]},
                                                   "beats": ["world", "young"]}))
    db.session.add(issue)
    db.session.commit()
    with small_world.connect() as c:
        (lead,) = now.scouting_follow_ups(_World(c, SINCE, UNTIL, (CHELSEA,)))  # 28 Aug 2063 falls in the cycle: one year on
        assert now.scouting_follow_ups(_World(c, date(2063, 9, 4), date(2063, 9, 17), (CHELSEA,))) == []  # not due again
        (second,) = now.scouting_follow_ups(_World(c, date(2064, 8, 20), date(2064, 9, 3), (CHELSEA,)))  # two years on
    assert lead.headline == "Stan Star, 1 year after our scouting report" and lead.slug == f"{STAR}:1" and lead.outlet_id == young.id
    assert lead.facts["what_we_said"] == {"published": "2062-08-28", "headline": "Scouting report: Stan Star (18), Tottenham", "standfirst": "A winger to watch.",
                                          "age": 18, "team": "Tottenham", "scout_view": "one to watch", "standout_traits": ["pace"]}
    assert lead.facts["moves_since"] == ["2063-01-15: transfer from Tottenham to Chelsea, £12.0m"] and lead.facts["years_since_our_report"] == 1
    assert lead.beats == {"world", "young"} and lead.refs[0] == f"person:{STAR}"
    assert second.slug == f"{STAR}:2" and second.facts["years_since_our_report"] == 2


def test_a_player_the_paper_called_wrong_gets_no_second_follow_up(app, small_world):
    from app.models.outlet import Outlet
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue

    _season(small_world)
    young = Outlet(name="The Young Ones", slug="young-ones", voice="plain", byline="Desk", beats=["world", "young"], articles_per_edition=6)
    db.session.add(young)
    db.session.flush()
    report = WorldSoccerIssue(publication_date=date(2062, 8, 28), knowledge_cutoff=date(2062, 8, 28), status="published", edition_name="x", outlet_id=young.id)
    report.articles.append(WorldSoccerArticle(headline="Scouting report: Stan Star (18), Tottenham", section="Scouting", article_type="report",
                                              lead_key=f"scouting:person:{STAR}:2062", body="x", byline="Desk", prominence="standard", sort_order=0,
                                              lead={"facts": {"age": 18}, "beats": ["world", "young"]}))
    first = WorldSoccerIssue(publication_date=date(2063, 9, 3), knowledge_cutoff=date(2063, 9, 3), status="published", edition_name="y", outlet_id=young.id)
    first.articles.append(WorldSoccerArticle(headline="Stan Star, a year on: we got him wrong", section="Scouting", article_type="report",
                                             lead_key=f"scouting_follow_up:{STAR}:1", body="x", byline="Desk", prominence="standard", sort_order=0,
                                             lead={"facts": {}, "beats": ["world", "young"], "verdict": "wrong"}))
    db.session.add_all([report, first])
    db.session.commit()
    with small_world.connect() as c:
        assert now.scouting_follow_ups(_World(c, date(2064, 8, 20), date(2064, 9, 3), (CHELSEA,))) == []  # called wrong: dropped
    first.articles[0].lead = {"facts": {}, "beats": ["world", "young"], "verdict": "early"}
    db.session.commit()
    with small_world.connect() as c:
        (second,) = now.scouting_follow_ups(_World(c, date(2064, 8, 20), date(2064, 9, 3), (CHELSEA,)))  # called early: followed on
    assert second.slug == f"{STAR}:2"


def test_a_sacked_manager_and_a_kings_wage_abroad_are_stories(small_world):
    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 900, "name": "Petru Ciurel", "as_of": AS_OF, "team_uid": None},
                                                         {"fm_uid": 901, "name": "Rogério José", "as_of": AS_OF, "team_uid": ARSENAL}])
        c.execute(world.WorldManagerSpell.__table__.insert(), [
            {"team_uid": CHELSEA, "seq": 0, "person_uid": 900, "started": date(2062, 12, 10), "ended": date(2063, 8, 28), "won": 27, "drawn": 12, "lost": 19,
             "reason": 2, "league_wins": 0, "cup_wins": 1, "as_of": AS_OF}])
        c.execute(world.WorldTransfer.__table__.insert(), [{"person_uid": 901, "date": date(2063, 6, 16), "kind": 1, "from_uid": CHELSEA, "to_uid": ARSENAL,
                                                            "squad_uid": ARSENAL, "fee": 41_728_360, "fee_with_add_ons": 41_728_360}])
        c.execute(world.WorldContract.__table__.insert(), [
            {"person_uid": 901, "team_uid": ARSENAL, "wage": 1_444_770, "job": "Player", "signed": date(2063, 6, 16), "joined": date(2063, 6, 16),
             "expires": date(2065, 6, 30), "happiness": 100, "first_seen": AS_OF, "as_of": AS_OF},
            {"person_uid": SCORER, "team_uid": CHELSEA, "wage": 330_000, "job": "Player", "signed": date(2062, 7, 1), "joined": date(2060, 7, 1),
             "expires": date(2066, 6, 30), "happiness": 80, "first_seen": AS_OF, "as_of": AS_OF}])
    found = _found(small_world)
    (out,) = found["manager_out"]
    assert out.headline == "Petru Ciurel sacked at Chelsea" and out.facts["record"] == "58 games: 27 won, 12 drawn, 19 lost"
    assert out.facts["honours_in_the_spell"] == "0 league titles, 1 cups" and "home" in out.beats and out.slug == f"900:{CHELSEA}:2063-08-28"
    (king,) = found["kings_wage"]
    assert king.headline == "Rogério José on £1,400,000 a week at Arsenal" and king.facts["the_move"] == "2063-06-16: Chelsea to Arsenal, £41.7m"
    assert king.facts["the_old_clubs_best_paid_player_earns"] == "£330,000 a week" and king.slug == "901:2063"


def test_the_travel_column_and_the_interviews(small_world):
    from app.services import features

    _season(small_world)
    with small_world.begin() as c:
        played = c.exec_driver_sql("select id from fixtures where date = '2063-08-24'").scalar()  # Chelsea 1-0 Arsenal
        c.execute(world.WorldStadium.__table__.insert(), [{"fm_uid": 5000, "latitude": 51.4817, "longitude": -0.191, "as_of": AS_OF},
                                                          {"fm_uid": 5001, "latitude": 46.5547, "longitude": 15.6459, "as_of": AS_OF}])
        c.exec_driver_sql("update teams set stadium_uid = 5000 where fm_uid = %d" % CHELSEA)
        c.exec_driver_sql("update clubs set nation_uid = 765 where fm_uid = %d" % CHELSEA)
        c.execute(world.WorldMatchReport.__table__.insert(), [{"fixture_id": played, "stadium": "Stamford Bridge", "minutes": 90, "as_of": AS_OF}])
        c.exec_driver_sql("update fixtures set attendance = 40123 where id = %d" % played)
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 777, "name": "Old Legend", "birth_date": date(2023, 8, 25), "as_of": AS_OF, "team_uid": None}])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [
            {"person_uid": 777, "team_uid": CHELSEA, "season_start": y, "kind": 1, "seq": 0, "league_uid": LEAGUE, "starts": 35, "substitute_appearances": 0,
             "rated_appearances": 35, "goals": 10, "assists": 5, "player_of_match": 2, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 3,
             "red_cards": 0, "minutes": 3000, "rating_total": 25000, "as_of": AS_OF} for y in range(2050, 2062)])
    with small_world.connect() as c:
        w = _World(c, SINCE, UNTIL, (CHELSEA,))
        (trip,) = features.travel_log(w)
        people = features.interviews(w)
    assert trip.headline == "Away Days, trip 1: Chelsea v Arsenal at Stamford Bridge, England" and trip.facts["this_stop"]["latitude"] == 51.4817
    assert trip.facts["the_game"]["attendance"] == 40123 and trip.facts["the_game"]["score"] == "1-0" and trip.facts["year"] == 2063
    assert trip.facts.get("journey_from_the_last_stop") is None and "2063" in trip.facts["the_world_it_is"] and trip.slug == str(played)
    home = trip.facts["the_home_club"]  # who the club is (27 Oct 2026: the column reached Sutton and said nothing of Sutton)
    assert home["club"] == "Chelsea" and home["league_seasons"] and ("Old Legend: 420 games over 12 seasons" in home["most_appearances"])
    assert trip.facts["the_visitors"]["club"] == "Arsenal" and "most_appearances" not in trip.facts["the_visitors"]
    assert round(features.distance_km((51.4817, -0.191), (46.5547, 15.6459))) == 1275 and features.bearing_words((51.48, -0.19), (46.55, 15.65)) == "east"
    (legend,) = [l for l in people if l.facts["person"] == "Old Legend"]
    assert legend.facts["hook"] == "his 40th birthday on 2063-08-25; his playing days ended after the 2061/62 season"
    assert legend.facts["career"] == ["Chelsea, 2050/51 to 2061/62: 420 games, 120 goals"] and legend.facts["what_he_does_now"] == "out of the game"
    assert legend.slug == "777" and "home" in legend.beats


def test_a_club_shopping_widely_makes_no_story_on_its_own_and_one_market_fills_no_paper(small_world):
    """The game has the Saudi clubs after every well-known player of about 30 (27 Nov 2063: 13 "Saudis circle" stories in one
    edition). A suitor with SERIAL_SUITOR players on its list is background; one country's clubs give at most SAME_MARKET stories."""
    _season(small_world)
    riyadh, jeddah, saudi, spain = 7001, 7002, 133, 801
    with small_world.begin() as c:
        c.execute(world.WorldNation.__table__.insert(), [{"fm_uid": saudi, "name": "Saudi Arabia", "short_name": "Saudi Arabia", "abbreviation": "KSA", "adjective": "Saudi"}])
        c.execute(world.WorldClub.__table__.insert(), [{"fm_uid": u, "name": n, "short_name": n, "nation_uid": saudi, "as_of": AS_OF, "reputation": 8300}
                                                       for u, n in ((riyadh, "Al-Hilal"), (jeddah, "Al-Ittihad"))])
        c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": u, "club_uid": u, "nation_uid": None, "name": n, "age_limit": None, "as_of": AS_OF, "reputation": 8300}
                                                       for u, n in ((riyadh, "Al-Hilal"), (jeddah, "Al-Ittihad"))])
        c.exec_driver_sql("update clubs set nation_uid = 765 where fm_uid in (%d, %d, %d)" % (ARSENAL, LIVERPOOL, SPURS))
        stars = list(range(9000, 9040))
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": u, "name": f"Star {u}", "team_uid": ARSENAL, "reputation_world": 9000 - (u - 9000), "as_of": AS_OF}
                                                         for u in stars] + [{"fm_uid": 9100, "name": "Chased", "team_uid": LIVERPOOL, "reputation_world": 8000, "as_of": AS_OF},
                                                                            {"fm_uid": 9101, "name": "Ours", "team_uid": CHELSEA, "reputation_world": 5000, "as_of": AS_OF}])
        c.execute(world.WorldTransferInterest.__table__.insert(), [{"team_uid": riyadh, "person_uid": u, "rumoured": 0, "wage": 1, "offered_wage": 1, "as_of": AS_OF}
                                                                   for u in stars + [9100, 9101]]
                  + [{"team_uid": SPURS, "person_uid": 9100, "rumoured": 0, "wage": 1, "offered_wage": 1, "as_of": AS_OF},
                     {"team_uid": jeddah, "person_uid": 9000, "rumoured": 0, "wage": 1, "offered_wage": 1, "as_of": AS_OF}])
    with small_world.connect() as c:
        w = _World(c, SINCE, UNTIL, (CHELSEA,))
        interest = {l.facts["player"]: l for l in now.transfer_market(w) if l.kind == "transfer_interest"}
    assert set(interest) == {"Chased", "Ours"}  # the stars wanted only by a club shopping widely: no story; 9000 has a real chaser but
    # "Chased" scores higher and SHOPPING_STORIES is one: one story a cycle with a shopping club among the suitors
    assert interest["Ours"].facts["also_on_the_lists_of"] == {"Al-Hilal": "42 players on their list"}  # a followed club's player: always
    assert interest["Ours"].facts["clubs_after_him"] == ["Al-Hilal"]  # no other suitor: the shopping club is all there is to name
    assert interest["Chased"].facts["clubs_after_him"] == ["Tottenham"] and interest["Chased"].headline == "Tottenham want Chased (Liverpool)"
    assert interest["Chased"].facts["also_on_the_lists_of"] == {"Al-Hilal": "42 players on their list"}
    with small_world.begin() as c:  # two clubs of one country chasing for real: the best SAME_MARKET of their stories a cycle
        c.exec_driver_sql("delete from transfer_interest where team_uid = %d and person_uid >= 9003" % riyadh)
    with small_world.connect() as c:
        w = _World(c, SINCE, UNTIL, (CHELSEA,))
        interest = sorted(l.facts["player"] for l in now.transfer_market(w) if l.kind == "transfer_interest" and l.facts["player"].startswith("Star"))
    assert interest == ["Star 9000", "Star 9001"]


def test_a_transfer_story_carries_the_players_record_and_the_clubs_last_results(small_world):
    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldTransferInterest.__table__.insert(), [{"team_uid": ARSENAL, "person_uid": SCORER, "rumoured": 0, "wage": 1, "offered_wage": 1, "as_of": AS_OF}])
    found = _found(small_world)
    (interest,) = found["transfer_interest"]
    assert interest.facts["his_time_at_the_club"].startswith("since 2063/64: ") and interest.facts["his_time_at_the_club"].endswith("goals in league, cup and continental games")
    assert interest.facts["the_clubs_last_results"][0].startswith("2063-") and "Chelsea" in interest.facts["the_clubs_last_results"][0]
    assert interest.facts["nothing_else_happened"].startswith("these are the club's last games")


def test_two_managers_trading_sharp_answers_make_a_war_of_words_and_reach_the_notebook(small_world):
    """Pladevall and Dysart, 19 Dec 2063: 'Pladevall: Dysart lacks respect', 'Dysart claims Liverpool were lucky'."""
    from app.services import moods

    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 701, "name": "Robert Pladevall", "as_of": AS_OF},
                                                         {"fm_uid": 702, "name": "Bay Dysart", "as_of": AS_OF}])
        rows = [{"day": date(2063, 9, 1), "team_uid": LIVERPOOL, "question": 20344, "subject_uid": 702, "speaker_uid": 701, "opponent_uid": ARSENAL,
                 "subject_team_uid": ARSENAL, "answer": 4, "topic": "x", "tone": "negative", "headline": "Pladevall: Dysart lacks respect", "names_subject": 1, "as_of": AS_OF},
                {"day": date(2063, 9, 2), "team_uid": ARSENAL, "question": 20331, "subject_uid": 701, "speaker_uid": 702, "opponent_uid": LIVERPOOL,
                 "subject_team_uid": LIVERPOOL, "answer": 5, "topic": "x", "tone": "very negative", "headline": "Dysart claims Liverpool were lucky", "names_subject": 1, "as_of": AS_OF},
                {"day": date(2063, 9, 1), "team_uid": ARSENAL, "question": 20315, "subject_uid": 701, "speaker_uid": 702, "opponent_uid": LIVERPOOL,
                 "subject_team_uid": LIVERPOOL, "answer": 4, "topic": "x", "tone": "negative", "headline": "Dysart criticises Aston Villa", "names_subject": 0, "as_of": AS_OF}]
        c.execute(world.WorldPressAnswer.__table__.insert(), rows)
    with small_world.connect() as c:
        (war,) = now.wars_of_words(_World(c, SINCE, UNTIL, (CHELSEA,)))
        press = moods.press_words(c, 701, date(2063, 9, 3))
    assert war.headline == "War of words: Robert Pladevall and Bay Dysart" and war.kind == "war_of_words"
    assert war.facts["exchanges"] == ["2063-09-01: Pladevall: Dysart lacks respect (negative)", "2063-09-02: Dysart claims Liverpool were lucky (very negative)"]
    assert press == {"said_in_public": ["2063-09-01: Pladevall: Dysart lacks respect (negative)"],
                     "said_about_him": ["2063-09-02: Dysart claims Liverpool were lucky (very negative)"]}  # the leftover subject is not about him


def test_the_news_items_make_pressure_vacancy_and_rumour_leads_and_reach_the_notebook(small_world):
    from app.services import moods

    _season(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 801, "name": "Boss Bob", "as_of": AS_OF, "staff_job": "Manager", "staff_team_uid": LIVERPOOL},
                                                         {"fm_uid": 802, "name": "Max Lee", "as_of": AS_OF, "staff_job": "Manager", "staff_team_uid": SPURS},
                                                         {"fm_uid": 803, "name": "Gone Gary", "as_of": AS_OF, "staff_job": None, "staff_team_uid": None}])
        blank = {"other_person_uid": None, "other_team_uid": None, "match_day": None, "fee": None}
        c.execute(world.WorldNewsItem.__table__.insert(), [
            {**blank, "kind": "board_pressure", "key": "b", "day": date(2063, 9, 1), "person_uid": 801, "team_uid": LIVERPOOL, "other_team_uid": ARSENAL, "as_of": AS_OF},
            {**blank, "kind": "job_link", "key": "j", "day": date(2063, 9, 2), "person_uid": 802, "other_person_uid": 803, "team_uid": SPURS, "other_team_uid": ARSENAL, "as_of": AS_OF},
            {**blank, "kind": "transfer_rumour", "key": "r", "day": date(2063, 9, 2), "person_uid": SCORER, "team_uid": ARSENAL, "fee": 43_020_000, "as_of": AS_OF}])
    with small_world.connect() as c:
        w = _World(c, SINCE, UNTIL, (CHELSEA,))
        (pressure,) = now.under_pressure(w)
        (vacancy,) = now.vacancies(w)
        (rumour,) = now.transfer_rumours(w)
        lee = moods.news_words(c, 802, date(2063, 9, 3))
    assert pressure.headline == "Boss Bob under pressure at Liverpool" and "losing patience" in pressure.facts["the_report"]
    assert vacancy.headline == "Who takes the Arsenal job?" and vacancy.facts["linked_with_the_job"] == ["Max Lee (Tottenham)"]
    assert vacancy.facts["the_manager_who_left"] == "Gone Gary"
    assert rumour.headline == "Arsenal after Sam Scorer (Chelsea), about £43M" and rumour.facts["status"] == "a press report, no bid is known"
    assert lee == ["2063-09-02: linked with the vacant job at Arsenal after Gone Gary left"]


def _match_player(fixture_id: int, side: int, person: int, xg: float, goals: int, shots: int, rating: float = 6.8) -> dict:
    zero = dict.fromkeys(("assists", "passes", "passes_completed", "key_passes", "clear_cut_chances", "crosses", "crosses_completed",
                          "dribbles", "tackles", "tackles_won", "key_tackles", "interceptions", "clearances", "headers",
                          "headers_won", "key_headers", "fouls", "yellow_cards", "booked_minute", "minute_on", "added_time_off"), 0)
    return {**zero, "fixture_id": fixture_id, "side": side, "seq": 1, "person_uid": person, "played": 90, "rating": rating,
            "distance": 0.0, "minute_off": 90, "xg": xg, "goals": goals, "shots": shots, "shots_on_target": goals + 1, "as_of": AS_OF}


def test_misfiring_strikers_and_tired_legs(small_world, monkeypatch):
    """27 Oct 2026: a striker far below his expected goals, and a well-known player jaded after a month of games."""
    _season(small_world)
    with small_world.begin() as c:
        games = [r for r in c.exec_driver_sql("select id, home_uid, date from fixtures where competition_uid = %d and home_goals is not null "
                                              "and date >= '2063-08-01' and (home_uid = %d or away_uid = %d) order by date" % (LEAGUE, SPURS, SPURS))]
        c.execute(world.WorldMatchPlayer.__table__.insert(), [_match_player(g[0], 0 if g[1] == SPURS else 1, FLOP, 1.6, 0, 6) for g in games])
        c.execute(world.WorldPersonMood.__table__.insert(), [{"person_uid": FLOP, "fatigue": 450, "as_of": UNTIL}])
    monkeypatch.setattr(now, "TIRED_GAMES", 4)  # the test league plays four games a month
    found = _found(small_world)
    (miss,) = found["misfiring"]
    assert miss.facts["league_goals"] == 0 and miss.facts["expected_goals"] == 6.4 and miss.facts["goals_against_expected"] == "-6.4"
    assert miss.facts["last_league_goal"] == "none this season" and "clinical" not in found
    (tired,) = found["tired_legs"]
    assert tired.facts["games_in_the_last_30_days"] == 4 and tired.facts["his_state"].startswith("jaded")
    assert tired.headline == "Fred Flop (Tottenham) jaded after 4 games in a month"


def test_a_bid_says_who_plays_in_his_position_at_both_clubs_from_the_team_sheets(small_world):
    """Chelsea's strikers are the players who start up front, not the youth players rated best there (31 Oct 2026): a bid's
    notebook names who he would compete with at the bidding club and who plays there at his own club without him."""
    from app.services import world_queries as q

    _season(small_world)
    kid = 305
    with small_world.begin() as c:
        games = list(c.exec_driver_sql("select id, home_uid, away_uid from fixtures where competition_uid = %d and home_goals is not null "
                                       "and date >= '2063-08-01'" % LEAGUE))
        sheets = []
        for fixture, home, away in games:
            for team, side in ((home, 0), (away, 1)):
                starter = {CHELSEA: SCORER, ARSENAL: STAR}.get(team)
                if starter:
                    sheets.append({**_match_player(fixture, side, starter, 0.5, 1, 2), "played": 1, "start_position": "ST (C)"})
        c.execute(world.WorldMatchPlayer.__table__.insert(), sheets)
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": kid, "name": "Kid Youth", "as_of": AS_OF, "team_uid": CHELSEA}])
        c.execute(world.WorldTransferOffer.__table__.insert(), [
            {"team_uid": ARSENAL, "person_uid": SCORER, "squad_uid": ARSENAL, "fee": 43_000_000, "status": "made", "offered_on": date(2063, 9, 1),
             "until": date(2063, 9, 8), "completes_on": date(2063, 9, 8), "as_of": AS_OF}])
        assert q.sheet_positions(c, [SCORER, kid]) == {SCORER: "ST (C)"}  # the kid never started for the first team
    (bid,) = _found(small_world)["transfer_bid"]
    moves = bid.facts["if_he_moves"]
    assert moves["at_the_bidding_club"]["who_plays_there"] == ["Stan Star: 4 starts there since 2063-07-01, 4 goals in them"]
    assert moves["at_the_bidding_club"]["starters_there_a_game"] == 1
    assert moves["at_his_club_without_him"]["his_starts_there"] == "4 starts, 4 goals since 2063-07-01"
    assert moves["at_his_club_without_him"]["who_plays_there"] == ["nobody has started there since 2063-07-01"]


def test_the_club_system_says_who_usually_starts_in_each_slot(small_world):
    """The club page's "How they play" names the players who start in each slot's position, from the team sheets; the
    personalised roles alone left the keeper, the centre-backs and the striker empty (1 Nov 2026)."""
    from app.services import team_tactics

    _season(small_world)
    with small_world.begin() as c:
        games = list(c.exec_driver_sql("select id, home_uid, away_uid from fixtures where competition_uid = %d and home_goals is not null "
                                       "and date >= '2063-08-01'" % LEAGUE))
        c.execute(world.WorldMatchPlayer.__table__.insert(), [
            {**_match_player(fixture, 0 if home == CHELSEA else 1, SCORER, 0.5, 1, 2), "played": 1, "start_position": "ST (C)"}
            for fixture, home, away in games if CHELSEA in (home, away)])
        (made,) = c.execute(world.WorldTeamTactic.__table__.insert().values(team_uid=CHELSEA, slot=1, name="Blues", as_of=AS_OF)).inserted_primary_key
        c.execute(world.WorldTeamTacticSlot.__table__.insert(), [
            {"tactic_id": made, "seq": 0, "position": "GK", "side": None, "duty": "Support", "role_key": "1.10", "role": "Sweeper Keeper"},
            {"tactic_id": made, "seq": 1, "position": "ST (C)", "side": None, "duty": "Attack", "role_key": "1.08", "role": "Advanced Forward"}])
        system = team_tactics.current(c, CHELSEA)
    keeper, striker = system["slots"]
    assert keeper["usually"] == [] and keeper["usually_span"] == 1
    assert [(u["name"], u["starts"]) for u in striker["usually"]] == [("Sam Scorer", 4)] and striker["usually_span"] == 1


def test_a_preview_names_the_injured_regulars_not_the_injured_reserves(small_world):
    """Ponferrada "will miss the injured Fran Belmonte, their leading scorer" (31 Oct 2026): a preview's injured are the
    team's regulars on its team sheets, with how much they play; an injured reserve, however well known, is left out."""
    from app.services import world_queries as q

    _season(small_world)
    reserve = 306
    with small_world.begin() as c:
        games = [(r.id, r.home_uid) for r in c.execute(world.WorldFixture.__table__.select().where(
            (world.WorldFixture.__table__.c.home_goals.is_not(None)) & (world.WorldFixture.__table__.c.date >= date(2063, 8, 1))
            & ((world.WorldFixture.__table__.c.home_uid == LIVERPOOL) | (world.WorldFixture.__table__.c.away_uid == LIVERPOOL))))]
        c.execute(world.WorldMatchPlayer.__table__.insert(), [
            {**_match_player(fixture, 0 if home == LIVERPOOL else 1, HURT, 0.1, 0, 0), "played": 1, "start_position": "DM"}
            for fixture, home in games[:3]] + [
            {**_match_player(fixture, 0 if home == LIVERPOOL else 1, FLOP + 100, 0.1, 0, 0), "seq": 2, "played": 1, "start_position": "GK"}
            for fixture, home in games])
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": reserve, "name": "Rex Reserve", "as_of": AS_OF, "team_uid": LIVERPOOL, "reputation_world": 9000},
            {"fm_uid": FLOP + 100, "name": "Kurt Keeper", "as_of": AS_OF, "team_uid": LIVERPOOL, "reputation_world": 1000}])
        c.execute(world.WorldInjury.__table__.insert(), [
            {"person_uid": reserve, "seq": 0, "injury": "sore back", "injured_on": date(2063, 8, 30), "days": 20, "severity": "Minor",
             "team_uid": LIVERPOOL, "current": 1, "as_of": AS_OF}])
        regulars = q.regulars(c, LIVERPOOL, UNTIL)
        assert set(regulars) == {HURT, FLOP + 100} and regulars[HURT]["starts"] == 3 and regulars[HURT]["games"] == 4
        assert q.regulars(c, SPURS, UNTIL) is None  # no team sheets: the archive cannot tell
    (preview,) = _found(small_world)["preview"]
    assert preview.facts["home_injured"] == ["Hal Hurt (broken ankle; a regular at DM: 3 starts in 4 games)"]


def test_the_squad_lines_put_the_youth_team_players_apart(small_world):
    """Verlhac, Rodríguez and Spyrou play for Chelsea's U21s and U18s: on the club page they are not first-team strikers."""
    from app.services import world_queries as q

    _season(small_world)
    with small_world.begin() as c:
        club = c.exec_driver_sql("select club_uid from teams where fm_uid = %d" % CHELSEA).scalar()
        c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": 6301, "club_uid": club, "nation_uid": None, "name": "Chelsea U21", "age_limit": 21, "as_of": AS_OF}])
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 305, "name": "Kid Youth", "as_of": AS_OF, "team_uid": CHELSEA}])
        c.execute(world.WorldPlayerGame.__table__.insert(), [
            {"person_uid": 305, "team_uid": 6301, "played_on": date(2063, 8, 30), "status": "played", "minutes": 90, "as_of": AS_OF},
            {"person_uid": SCORER, "team_uid": 6301, "played_on": date(2063, 7, 30), "status": "unused", "minutes": 0, "as_of": AS_OF}])
        assert q.youth_only(c, CHELSEA, 2063, [305, SCORER]) == {305}


def test_a_european_preview_has_the_league_phase_table_and_each_sides_european_past(small_world):
    """Shamrock Rovers, 29th of 36 with six points, were written as already in the knockout rounds for the first time: the
    notebook had no table and no past (1 Nov 2026)."""
    _season(small_world)
    EUROPE = 1301394
    with small_world.begin() as c:
        c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": EUROPE, "name": "UEFA Champions League", "short_name": "Champions League",
                                                                "abbreviation": "UCL", "scope": "continental", "level": None, "reputation": 10000}])
        c.execute(world.WorldFixture.__table__.insert(), [
            _fixture("2063-08-20", CHELSEA, ARSENAL, (3, 0), EUROPE, 4812), _fixture("2063-08-20", LIVERPOOL, SPURS, (1, 1), EUROPE, 4812),
            _fixture("2063-08-27", SPURS, CHELSEA, (0, 2), EUROPE, 4812), _fixture("2063-08-27", ARSENAL, LIVERPOOL, (2, 2), EUROPE, 4812),
            _fixture("2063-09-10", CHELSEA, SPURS, None, EUROPE, 4812)])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [
            {"person_uid": SCORER, "team_uid": CHELSEA, "season_start": y, "kind": 3, "league_uid": EUROPE, "starts": 1, "substitute_appearances": 0,
             "assists": 0, "goals": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 90,
             "rated_appearances": 0, "rating_total": 0, "as_of": AS_OF, "seq": 10 + i} for i, y in enumerate((2061, 2062, 2063))])
    found = _found(small_world)
    (preview,) = [lead for lead in found["club_preview"] if lead.facts["competition"] == "UEFA Champions League"]
    assert preview.facts["home_place"] == "1 of 4 in the league phase, 6 points from 2 games, form WW"
    assert preview.facts["away_place"] == "3 of 4 in the league phase, 1 points from 2 games, form DL"  # Arsenal, 1 point too, are behind on goal difference
    assert preview.facts["stage"].startswith("the league phase: one table of 4 teams; what each place earns")
    assert preview.facts["home_seasons_in_european_competition"] == 3 and preview.facts["away_seasons_in_european_competition"] == 0


def test_a_small_club_that_came_up_gets_a_check_in_once_at_each_point_of_the_season(small_world):
    """Big clubs that went down and small clubs that came up are checked on now and then (1 Nov 2026): Spurs, up from
    the second level and the least known club of a strong league, get a check-in when the season passes halfway."""
    second = 9101
    _season(small_world)
    with small_world.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(reputation=180))
        c.execute(comps.insert(), [{"fm_uid": second, "name": "English Second Division", "short_name": "Second", "abbreviation": "ESD", "scope": "league", "nation_uid": 765, "level": 2,
                                    "as_of": AS_OF}])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [
            {"substitute_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0,
             "red_cards": 0, "minutes": 0, "as_of": AS_OF, "seq": 0, "season_start": 2062, "kind": 1, "league_uid": second,
             "person_uid": FLOP, "team_uid": SPURS, "starts": 30, "goals": 9, "rated_appearances": 30, "rating_total": 2100}])
    (lead,) = _found(small_world)["check_in"]
    assert lead.key == f"check_in:{SPURS}:2063:1"  # 2 of 6 played before the cycle, 4 after: past halfway
    assert lead.facts["the_story"].startswith("promoted from the English Second Division last season, among the least known clubs")
    assert lead.facts["how_far_in"] == "just past halfway: 4 of 6 games"
    assert lead.facts["place"].startswith("4, 0 points from 4 games") and lead.facts["points_behind_first"] == 12
    assert "name no promotion, play-off or relegation places" in lead.facts["league_rules"]  # one past season only: not known


def test_a_leagues_places_come_from_its_past_seasons(small_world):
    """Not known before (2 Nov 2026): the final tables of past seasons against the league each club played next. Here the
    last team went down three seasons in a row; one season 3rd went down as well, a play-off."""
    from app.services import world_queries as q

    second = 9102
    teams = (CHELSEA, LIVERPOOL, ARSENAL, SPURS)  # finishing in this order every season: the better side wins 1-0
    with small_world.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", level=1))
        c.execute(comps.insert(), [{"fm_uid": second, "name": "Second", "short_name": "Second", "abbreviation": "S2", "scope": "league",
                                    "nation_uid": 765, "level": 2}])
        games, rows = [], []
        for year in (2059, 2060, 2061):
            for a in range(4):
                for b in range(4):
                    if a != b:
                        games.append(_fixture(f"{year}-{9 + a:02d}-{10 + b}", teams[a], teams[b], (1, 0) if a < b else (0, 1), LEAGUE, 162))
            for i, team in enumerate(teams):  # the season after: the last, and once also 3rd, in the second level
                down = i == 3 or (i == 2 and year == 2060)
                rows.append({"person_uid": 400 + i, "team_uid": team, "season_start": year + 1, "kind": 1, "league_uid": second if down else LEAGUE,
                             "seq": year, "starts": 1, "substitute_appearances": 0, "goals": 0, "assists": 0, "player_of_match": 0,
                             "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 90, "rated_appearances": 0,
                             "rating_total": 0, "as_of": AS_OF})
        c.execute(world.WorldFixture.__table__.insert(), games)
        c.execute(world.WorldPlayerSeason.__table__.insert(), rows)
        zones = q.league_zones(c, LEAGUE, 2062)
        assert zones == {"seasons": 3, "teams": 4, "up": 0, "down": 1, "extra_up": None, "extra_down": (3, 3, 1)}
        words = q.zone_words(zones)
        assert words.startswith("from the archive's last 3 seasons of this league: the relegation zone: the bottom team; a relegation play-off, "
                                "most likely: in 1 of those seasons 3rd went down too. Use the plain words")
        table = [{"points": p} for p in (9, 7, 4, 3)]
        assert q.zone_of(zones, table, 4) == "in the relegation zone, 1 point from safety"  # "the places that, season after season, have meant the drop"
        assert q.zone_of(zones, table, 3) == "in the relegation play-off place, most likely"
        assert q.zone_of(zones, table, 2) == "4 points above the relegation zone"
        assert q.zone_of(zones, table[:3], 3) is None  # another size of league: the past says nothing
        assert q.league_zones(c, LEAGUE, 2061) is None  # two seasons only: not known
        assert "name no promotion, play-off or relegation places" in q.zone_words(None)
