# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""The story finder (docs/media-plan.md), on the small world of the page tests."""

from datetime import date

from app.models import world
from app.services.story_leads import SCOUT_VIEWS, find_leads
from tests.test_world_pages import AS_OF, CHELSEA, FA_CUP, LEAGUE, LIVERPOOL, MYERS, SPURS, _fixture, app, small_world  # noqa: F401


def _prepare(engine):
    with engine.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", nation_uid=765, level=1))
        c.execute(comps.update().where(comps.c.fm_uid == FA_CUP).values(scope="cup", nation_uid=765))
        # five earlier FA Cups won by others: Liverpool's 2063 win is its first
        c.execute(world.WorldEdition.__table__.insert(), [
            {"competition_uid": FA_CUP, "year": y, "season_label": f"{y - 1}/{str(y)[-2:]}", "decided_on": date(y, 5, 20)} for y in range(2055, 2060)])
        c.execute(world.WorldEditionPlace.__table__.insert(), [
            {"competition_uid": FA_CUP, "year": y, "place": 1, "team_uid": CHELSEA} for y in range(2055, 2060)])
        transfer = {"kind": 1, "fee_with_add_ons": 0}
        c.execute(world.WorldTransfer.__table__.insert(), [
            {**transfer, "person_uid": 1, "date": date(2060, 1, 1), "from_uid": SPURS, "to_uid": LIVERPOOL, "fee": 50_000_000},
            {**transfer, "person_uid": MYERS, "date": date(2063, 6, 1), "from_uid": SPURS, "to_uid": CHELSEA, "fee": 90_000_000}])
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2063-04-02", CHELSEA, SPURS, (8, 0), LEAGUE, 162)])


def test_the_finder_ranks_a_first_title_a_record_fee_and_a_big_win(small_world):
    _prepare(small_world)
    with small_world.connect() as c:
        leads = find_leads(c, date(2063, 3, 24), AS_OF)
    by_kind = {}
    for lead in leads:
        by_kind.setdefault(lead.kind, []).append(lead)

    cup = next(lead for lead in by_kind["title"] if lead.facts["competition"] == "English FA Cup")
    assert cup.headline == "Liverpool won for the first time: English FA Cup 2062/63"
    assert cup.facts["final"]["score"] == "2-1" and f"edition:{FA_CUP}:2063" in cup.refs and "nation:765" in cup.beats
    league = next(lead for lead in by_kind["title"] if lead.facts["competition"] == "English Premier Division")
    assert cup.score > league.score  # a first title is bigger news than a title with no history

    fee = by_kind["transfer"][0]
    assert "world record fee" in fee.facts["angles"] and fee.refs[0] == f"person:{MYERS}" and "transfers" in fee.beats
    assert by_kind["big_win"][0].facts["score"] == "8-0"
    assert len({lead.key for lead in leads}) == len(leads)


def test_the_scouts_report_a_teenager_with_a_great_ceiling_in_words_not_numbers(small_world):
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": 77, "name": "Mark Webber", "birth_date": date(2046, 5, 26), "as_of": AS_OF, "team_uid": CHELSEA,
             "current_ability": 86, "potential_ability": 198},
            {"fm_uid": 78, "name": "Old Hand", "birth_date": date(2030, 1, 1), "as_of": AS_OF, "team_uid": CHELSEA,
             "current_ability": 120, "potential_ability": 190}])
    with small_world.connect() as c:
        leads = [lead for lead in find_leads(c, date(2063, 3, 24), AS_OF) if lead.kind == "scouting"]
    assert [lead.facts["player"] for lead in leads] == ["Mark Webber"]  # the 33-year-old is no prospect
    report = leads[0]
    assert report.facts["scout_view"] in SCOUT_VIEWS["behind, all promise"]  # his level in a scout's words, never the potential
    assert "age group" not in report.headline and "scouts_tags" not in report.facts
    assert "198" not in str(report.facts) and "86" not in str(report.facts)
    assert {"young", "home"} <= report.beats


def test_a_scout_rates_a_teenager_ahead_of_his_age_above_a_raw_one_with_a_higher_ceiling():
    from app.services.story_leads import scout_impression

    assert scout_impression(18, 130, 150) > scout_impression(17, 86, 198)
    assert scout_impression(15, 100, 198) > scout_impression(18, 130, 150)


def test_former_players_in_staff_jobs_make_appointments_and_where_now_features(small_world):
    stats = {"rated_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0,
             "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": AS_OF, "seq": 0, "substitute_appearances": 0}
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": 90, "name": "Joe Haigh", "as_of": AS_OF, "staff_job": "Head of Youth Development", "staff_team_uid": CHELSEA,
             "staff_since": date(2046, 5, 8)},
            {"fm_uid": 91, "name": "New Coach", "as_of": AS_OF, "staff_job": "Manager", "staff_team_uid": LIVERPOOL,
             "staff_since": date(2063, 6, 1)}])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [
            {**stats, "person_uid": 90, "team_uid": CHELSEA, "season_start": 2030 + i, "kind": 1, "league_uid": LEAGUE, "starts": 45,
             "goals": 45} for i in range(18)] + [
            {**stats, "person_uid": 91, "team_uid": CHELSEA, "season_start": 2040 + i, "kind": 1, "league_uid": LEAGUE, "starts": 30,
             "goals": 2} for i in range(10)])
    with small_world.connect() as c:
        leads = find_leads(c, date(2063, 3, 24), AS_OF)
    where = [lead for lead in leads if lead.kind == "where_now"]
    assert [lead.facts["person"] for lead in where] == ["Joe Haigh"]
    assert where[0].facts["now"] == "Head of Youth Development at Chelsea" and where[0].key == "where_now:person:90"
    appointed = [lead for lead in leads if lead.kind == "appointment"]
    assert [(lead.facts["person"], lead.facts["new_job"], lead.facts["at"]) for lead in appointed] == [("New Coach", "Manager", "Liverpool")]


def _match_player(fixture_id: int, side: int, seq: int, person: int | None, rating: float, **figures) -> dict:
    row = {name: 0 for name in world.MATCH_PLAYER_FIGURES}
    row.update(fixture_id=fixture_id, side=side, seq=seq, person_uid=person, played=1, rating=rating, distance=10_000.0, xg=0.0,
               as_of=AS_OF)
    row.update(figures)
    return row


def test_match_reports_make_performance_sending_off_and_smash_and_grab_stories(small_world):
    from app.services import story_notebooks

    with small_world.begin() as c:
        comps = world.WorldCompetition.__table__
        c.execute(comps.update().where(comps.c.fm_uid == LEAGUE).values(scope="league", nation_uid=765, level=1, reputation=184))
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": 95, "name": "Spurs Keeper", "as_of": AS_OF}, {"fm_uid": 96, "name": "Hot Head", "as_of": AS_OF}])
        fixture_id = c.execute(world.WorldFixture.__table__.insert(),
                               _fixture("2063-04-12", SPURS, CHELSEA, (1, 0), LEAGUE, 162)).inserted_primary_key[0]
        c.execute(world.WorldMatchReport.__table__.insert(), {"fixture_id": fixture_id, "stadium": "Spurs Ground", "referee_name": "Wes Noble",
                                                             "player_of_the_match_uid": 95, "minutes": 90, "as_of": AS_OF})
        c.execute(world.WorldMatchTeam.__table__.insert(), [
            {"fixture_id": fixture_id, "side": 0, "team_uid": SPURS, "squad": 2, "passes": 300, "passes_completed": 200, "corners": 1,
             "woodwork": 0, "clear_cut_chances": 0, "fouls": 14, "yellow_cards": 2, "red_cards": 1, "as_of": AS_OF},
            {"fixture_id": fixture_id, "side": 1, "team_uid": CHELSEA, "squad": 1, "passes": 600, "passes_completed": 540, "corners": 12,
             "woodwork": 2, "clear_cut_chances": 4, "fouls": 6, "yellow_cards": 0, "red_cards": 0, "as_of": AS_OF}])
        c.execute(world.WorldMatchPlayer.__table__.insert(), [
            _match_player(fixture_id, 0, 0, 95, 9.2, passes=20, passes_completed=12),
            _match_player(fixture_id, 0, 1, 96, 6.1, goals=1, shots=2, shots_on_target=1, xg=0.3, red_cards=1, sent_off_minute=45,
                          added_time_off=1),
            _match_player(fixture_id, 1, 0, MYERS, 7.4, shots=22, shots_on_target=9, xg=2.6, key_passes=5)])
    with small_world.connect() as c:
        leads = find_leads(c, date(2063, 3, 24), AS_OF)
        facts = story_notebooks.match_report_facts(c, fixture_id)
    by_kind = {lead.kind: lead for lead in leads if lead.kind in ("performance", "sending_off", "smash_and_grab")}

    assert by_kind["performance"].facts["player"] == "Spurs Keeper" and by_kind["performance"].facts["player_of_the_match"]
    assert by_kind["sending_off"].facts["minute"] == "45+1'" and by_kind["sending_off"].facts["result"] == "won"
    assert by_kind["smash_and_grab"].facts["winner"] == "Tottenham" and by_kind["smash_and_grab"].facts["beaten_shots"] == 22
    assert f"match:2063-04-12:{SPURS}:{CHELSEA}" in by_kind["smash_and_grab"].refs
    assert facts["player_of_the_match"] == {"name": "Spurs Keeper", "team": "Tottenham", "rating": 9.2}
    assert facts["sent_off"] == [{"name": "Hot Head", "team": "Tottenham", "did": "1 goal", "minutes": "sent off 45+1'"}]
    assert facts["teams"][1]["possession"] == "73%" and facts["teams"][1]["hit_the_woodwork"] == 2
    assert facts["chances_missed"] == [{"name": "Ken Myers", "team": "Chelsea", "shots": 22, "expected_goals": 2.6}]



def test_the_papers_are_told_the_overused_phrases(app):
    from app.models.outlet import Outlet
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue
    from app.services import article_writer
    from app.extensions import db

    with app.app_context():
        outlet = Outlet(slug="t", name="T", voice="v", beats=[], byline="b")
        db.session.add(outlet)
        db.session.flush()
        issue = WorldSoccerIssue(outlet_id=outlet.id, edition_name="T", publication_date=AS_OF, knowledge_cutoff=AS_OF, status="published")
        db.session.add(issue)
        db.session.flush()
        for n in range(6):
            db.session.add(WorldSoccerArticle(issue_id=issue.id, headline=f"h{n}", body=f"Story {n}. He was simply a joy to watch again.",
                                              sort_order=n))
        db.session.commit()
        article_writer._overused_cache.clear()
        phrases = article_writer.overused_phrases()
    assert "far ahead of his age" in phrases and "simply a joy to" in phrases


def test_a_new_signing_is_no_scouting_report_and_a_seasons_figures_name_their_club(small_world):
    from tests.test_world_pages import ARSENAL

    stats = {"rated_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0,
             "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": AS_OF, "seq": 0, "substitute_appearances": 0}
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": 77, "name": "Mark Webber", "birth_date": date(2046, 5, 26), "as_of": AS_OF, "team_uid": CHELSEA,
             "current_ability": 86, "potential_ability": 198},
            {"fm_uid": 79, "name": "Sascha Buechel", "birth_date": date(2046, 2, 10), "as_of": AS_OF, "team_uid": CHELSEA,
             "current_ability": 90, "potential_ability": 198}])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [
            {**stats, "person_uid": 77, "team_uid": ARSENAL, "season_start": 2062, "kind": 1, "league_uid": None, "starts": 30, "goals": 3}])
        c.execute(world.WorldTransfer.__table__.insert(), [
            {"person_uid": 79, "date": date(2063, 6, 20), "kind": 1, "from_uid": ARSENAL, "to_uid": CHELSEA, "squad_uid": None,
             "fee": 5_600_000, "fee_with_add_ons": 5_600_000}])
    with small_world.connect() as c:
        leads = [lead for lead in find_leads(c, date(2063, 3, 24), AS_OF) if lead.kind == "scouting"]
    assert [lead.facts["player"] for lead in leads] == ["Mark Webber"]  # signed eleven days ago: a transfer story
    assert leads[0].facts["team"] == "Chelsea"
    assert leads[0].facts["this_season"]["by_club"] == [{"club": "Arsenal", "games": 30, "goals": 3}]  # not Chelsea's figures


def test_a_signing_of_this_year_is_scouted_as_a_signing_with_his_old_clubs_career(small_world):
    from app.services import article_writer
    from tests.test_world_pages import ARSENAL

    stats = {"rated_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0,
             "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": AS_OF, "seq": 0, "substitute_appearances": 0}
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 79, "name": "Sascha Buechel", "birth_date": date(2046, 2, 10), "as_of": AS_OF,
                                                          "team_uid": CHELSEA, "current_ability": 90, "potential_ability": 198}])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [
            {**stats, "person_uid": 79, "team_uid": ARSENAL, "season_start": season, "kind": 1, "league_uid": None, "starts": games, "goals": goals}
            for season, games, goals in ((2061, 4, 0), (2062, 30, 3))])
        c.execute(world.WorldTransfer.__table__.insert(), [{"person_uid": 79, "date": date(2063, 3, 1), "kind": 1, "from_uid": ARSENAL,
                                                            "to_uid": CHELSEA, "squad_uid": None, "fee": 5_600_000, "fee_with_add_ons": 5_600_000}])
    with small_world.connect() as c:
        (lead,) = [lead for lead in find_leads(c, date(2063, 3, 24), AS_OF) if lead.kind == "scouting"]
        person = article_writer._person(c, 79, AS_OF)
    assert lead.facts["signed"] == "transfer from Arsenal on 2063-03-01, £5.6m"
    assert person["career_by_club"] == ["Arsenal 2061/62 to 2062/63: 34 games, 3 goals"]
    assert person["joined_club_now"] == "transfer from Arsenal on 2063-03-01, £5.6m"
