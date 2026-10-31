"""Two features the papers run beside the news (25 Oct 2026): the travel column, one trip a cycle to a game somewhere in
the world, a journey with a memory; and the interviews, a retired player, a manager or a legend asked about his life on
a hook the archive gives. Both are written from the archive's facts alone; the words are the writers' craft.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta

from app.services import world_queries as q

COLUMN_NAME = "Away Days"
COLUMNIST = {"id": None, "name": "Teodor Albescu",
             "persona": "A Romanian travel writer in his fifties who has spent thirty years going to football matches around the world "
                        "and writing the Away Days column about what it took to get there, the food, the fans, the look of the ground and "
                        "the atmosphere in it. A fourth-division ground on a wet Tuesday or a full national stadium: either will do, as "
                        "long as the trip is worth telling. He goes by train and bus where he can, eats where the fans eat, talks to people "
                        "outside the ground, and never pretends a place is grander or poorer than it is.",
             "style": "Plain, exact, warm; a first-person travel log in the present tense of the trip, with dates, hours and distances; "
                      "the real geography and the real town, told the way a well-read traveller tells it, not a guidebook; particular "
                      "things, never lists of clichés; a wry eye for the small absurdities of away days; the match as a fan in the stand saw it.",
             "quirks": "Says what the journey took in hours and changes and what it cost in rough terms, what he ate by its proper name, "
                       "what the ground looks like from his seat, and how he got there from the last place."}
TRIPS_REMEMBERED = 6  # the earlier stops the column carries
NO_RETURN_TRIPS = 8  # the same country is not visited again within this many trips
NEAR_KM = 1_500  # a stop within this of the last is "near"; two of three trips stay near, the third may jump
SMALL_CROWD = 12_000  # a ground this small is one kind of day out; a full big ground (FULL_HOUSE) is the other
FULL_HOUSE = 40_000
RARE_DESTINATION = 2  # a country with this many games or fewer in the cycle's pool is a rarer trip, worth the journey
TRAVEL_SIZE = 0.8  # on the news scale (a derby 55, a league round 70): the column is its own page, so its score only orders
INTERVIEW_SIZE = 0.8  # ... and an interview's full score is 64, a page beside the news, not above it (160 beat every derby, 16 Dec 2063)
INTERVIEW_STARTS = 300  # a career of this many starts is worth an interview (the retired keep no reputation in the save)
ROUND_BIRTHDAYS = (40, 50, 60, 70, 75, 80)
RETIRED_WITHIN_YEARS = 2


# -- the travel column ------------------------------------------------------------------------------------------------------
def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (latitude, longitude) points."""
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def bearing_words(a: tuple[float, float], b: tuple[float, float]) -> str:
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (*a, *b))
    x = math.sin(lon2 - lon1) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    angle = (math.degrees(math.atan2(x, y)) + 360) % 360
    return ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")[int((angle + 22.5) // 45) % 8]


def published_trips() -> list[dict]:
    """The column's earlier trips, oldest first: each with its country, club, ground, date and place, from the stories printed."""
    from sqlalchemy.exc import OperationalError

    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue

    try:
        rows = (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerIssue.status == "published",
                                                                       WorldSoccerArticle.lead_key.like("travel_log:%"))
                .order_by(WorldSoccerIssue.publication_date, WorldSoccerArticle.id).all())
    except (RuntimeError, OperationalError):
        return []
    trips = []
    for article in rows:
        stop = (article.lead or {}).get("facts", {}).get("this_stop") or {}
        if stop:
            trips.append({**stop, "trip": len(trips) + 1, "headline": article.headline})
    return trips


def _place(n: int | None) -> str | None:
    if not n:
        return None
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def club_story(w, team_uid: int, club_uid: int | None, brief: bool = False) -> dict:
    """Who a club is in the archive's years (27 Oct 2026: the column arrived in Sutton and said nothing of Sutton): its
    league seasons and the climbs and falls between them, what it won, its legends, rivals and longest servants, its
    manager, and where it stands this season. The real club's history before our day is the writer's own knowledge."""
    overview = q.club_overview(w.c, team_uid)
    seasons = sorted(overview["seasons"], key=lambda r: r["season"])
    lines, last_level = [], None
    for r in seasons:
        comp = w.comp(r["league_uid"])
        level = comp.get("level")
        move = "" if last_level is None or level is None or level == last_level else (" (up)" if level < last_level else " (down)")
        last_level = level if level is not None else last_level
        place = f"{_place(r['position'])} of {r['teams_in_league']}" if r["position"] else "no table"
        lines.append(f"{q.season_label(r['season'])}: {comp.get('name') or 'the league'}{move}, "
                     + (f"{place} so far" if r["season"] == w.season else place)
                     + (f"; top scorer {r['top']['name']} ({r['top']['goals']})" if r.get("top") and r["top"]["goals"] and not brief else ""))
    levels = [w.comp(r["league_uid"]).get("level") for r in seasons if w.comp(r["league_uid"]).get("level")]
    honours = [f"{w.comp(h['competition_uid'])['name']}: {h['count']} ({', '.join(h['seasons'][-6:])})"
               for h in overview["honours"] if w.comp(h["competition_uid"]).get("name")]  # the game's oldest titles carry no name
    story: dict = {
        "club": w.name(team_uid),
        "seasons_in_the_archive": len(seasons),
        "highest_level_reached": min(levels) if levels else None,
        "lowest_level": max(levels) if levels else None,
        "league_seasons": (lines[-6:] if brief else lines[-16:]) or None,
        "honours": honours or None,
    }
    if club_uid:
        relations = q.club_relations(w.c, club_uid)
        story["legends_and_icons"] = [r["name"] + (f" ({r['reason_name']})" if r.get("reason_name") else "")
                                      for r in (relations["Legend"] + relations["Icon"])[:6]] or None
        story["rivals"] = [r["name"] for r in relations["Rival"][:4]] or None
    if club_uid and not brief:
        story["most_appearances"] = [f"{r['name']}: {r['apps']} games over {r['seasons']} seasons" for r in overview["most_apps"][:4]] or None
        story["top_scorers"] = [f"{r['name']}: {r['goals']} league goals" for r in overview["top_scorers"][:3] if r["goals"]] or None
        manager = next(iter(q.club_managers(w.c, team_uid)), None)
        if manager and manager.get("current"):
            story["manager"] = manager["name"] + (f", since {str(manager['started'])[:4]}" if manager.get("started") else "")
    return {k: v for k, v in story.items() if v is not None}


def _rivalry(w, f: dict) -> str | None:
    """How each club ranks the other as a rival, when the game says so."""
    home, away = f["home_club"], w.team(f["away_uid"]).get("club_uid")
    said = []
    for a, b, name_a, name_b in ((home, away, w.name(f["home_uid"]), w.name(f["away_uid"])), (away, home, w.name(f["away_uid"]), w.name(f["home_uid"]))):
        r = q.rivalry(w.c, a, b) if a and b else None
        if r:
            said.append(f"{name_a} count {name_b} a rival" + (f" ({r['reason']})" if r["reason"] else ""))
    return "; ".join(said) or None


def _meetings(w, f: dict) -> list[str]:
    """The two clubs' earlier games in the archive, newest first (at most six)."""
    rows = w.rows("select date, home_uid, away_uid, home_goals, away_goals from fixtures where home_goals is not null and date < :d "
                  "and ((home_uid = :h and away_uid = :a) or (home_uid = :a and away_uid = :h)) order by date desc limit 6",
                  d=str(f["date"]), h=f["home_uid"], a=f["away_uid"])
    if not rows:
        return []
    won = sum(1 for r in rows if (r["home_goals"] - r["away_goals"]) * (1 if r["home_uid"] == f["home_uid"] else -1) > 0)
    drawn = sum(1 for r in rows if r["home_goals"] == r["away_goals"])
    record = (f"{w.name(f['home_uid'])} in these {len(rows)} games: won {won}, drew {drawn}, lost {len(rows) - won - drawn}")
    return [record] + [f"{str(r['date'])[:10]}: {w.name(r['home_uid'])} {r['home_goals']}-{r['away_goals']} {w.name(r['away_uid'])}" for r in rows]


def travel_log(w) -> list:
    """One trip a cycle: a played game with a full report, in a country the column has not seen lately (and not the followed
    clubs' own, where every other page already is), near the last stop two trips in three; a rare destination, a small ground
    or a full house are each worth the journey."""
    from app.services.story_leads import Lead

    trips = published_trips()
    last = trips[-1] if trips else None
    visited = {t.get("nation_uid") for t in trips[-NO_RETURN_TRIPS:]}
    games = w.rows("select f.*, r.stadium, coalesce(h.nation_uid, cl.nation_uid) home_nation, h.stadium_uid, h.club_uid home_club, h.age_limit, "
                   "c.scope, c.level, c.name competition, c.reputation competition_reputation, s.latitude, s.longitude from fixtures f join match_reports r on r.fixture_id = f.id "
                   "join teams h on h.fm_uid = f.home_uid left join clubs cl on cl.fm_uid = h.club_uid "
                   "left join competitions c on c.fm_uid = f.competition_uid "
                   "left join stadiums s on s.fm_uid = h.stadium_uid where f.date > :s and f.date <= :u and h.age_limit is null "
                   "and r.stadium is not null and r.stadium != '' order by f.date", s=str(w.since), u=str(w.until))
    if not games:
        return []
    jump = len(trips) % 3 == 2  # the third trip may jump far
    here = (last["latitude"], last["longitude"]) if last and last.get("latitude") is not None else None
    home_nations = {g["home_nation"] for g in games if w.followed(g["home_uid"])} | {g["home_nation"] for g in games if w.followed(g["away_uid"])}
    pool = Counter(g["home_nation"] for g in games)

    def score(g: dict) -> float:
        value = 1.0
        if g["home_nation"] in visited:
            value -= 0.8
        if g["home_nation"] in home_nations:
            value -= 0.6
        if g["latitude"] is not None and here:
            km = distance_km(here, (g["latitude"], g["longitude"]))
            value += (0.6 if km <= NEAR_KM else -0.3) if not jump else (0.5 if km > NEAR_KM else 0)
        crowd = g["attendance"] or 0
        value += 0.4 if pool[g["home_nation"]] <= RARE_DESTINATION else 0
        value += 0.3 if crowd and (crowd <= SMALL_CROWD or crowd >= FULL_HOUSE) else 0  # a small ground or a full house: an atmosphere either way
        value += 0.3 if w.followed(g["home_uid"], g["away_uid"]) and len(trips) % 6 == 5 else 0
        value += min(0.2, 0.1 * ((g["home_goals"] or 0) + (g["away_goals"] or 0)))  # goals make a day, up to a point
        return value

    f = max(games, key=score)
    stadium_at = (f["latitude"], f["longitude"]) if f["latitude"] is not None else None
    goals = w.rows("select g.minute, g.added_time, g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                   "where g.fixture_id = :f and g.kind in (0, 1, 2) order by g.minute, g.seq", f=f["id"])
    nation = w.nations.get(f["home_nation"], {}).get("name")
    stop = {"nation_uid": f["home_nation"], "country": nation, "club": w.name(f["home_uid"]), "ground": f["stadium"], "date": str(f["date"]),
            "latitude": f["latitude"], "longitude": f["longitude"]}
    where = (f"the ground stands at latitude {f['latitude']:.3f}, longitude {f['longitude']:.3f}: the real town there, its real geography, "
             f"roads, railways and food" if f["latitude"] is not None else None)
    journey = None
    if last and here and stadium_at:
        km = distance_km(here, stadium_at)
        journey = f"{km:,.0f} km {bearing_words(here, stadium_at)} of the last stop, {last.get('ground')} in {last.get('club')}, {last.get('country')}"
    level = {1: "the top division", 2: "the second division", 3: "the third division", 4: "the fourth division"}.get(f["level"]) or (
        f"division {f['level']}" if f["level"] else None)
    adjective = w.nations.get(f["home_nation"], {}).get("adjective")
    facts = {"column": COLUMN_NAME, "trip_number": len(trips) + 1, "year": w.until.year, "this_stop": stop, "the_place": where,
             "the_people": f"{adjective}" if adjective else None,
             "the_day_out": ("a small ground" if f["attendance"] and f["attendance"] <= SMALL_CROWD else "a full house" if f["attendance"]
                             and f["attendance"] >= FULL_HOUSE else "a middling crowd") + (f", {f['attendance']:,} in the ground" if f["attendance"] else ""),
             "the_game": {"competition": f["competition"] or "a friendly", "level": level, "date": str(f["date"]),
                          "home": w.name(f["home_uid"]), "away": w.name(f["away_uid"]), "score": f"{f['home_goals']}-{f['away_goals']}",
                          "ground": f["stadium"], "attendance": f["attendance"] or None,
                          "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                                    f"({w.name(f['home_uid'] if g['side'] == 0 else f['away_uid'])})" for g in goals] or None},
             "the_home_club": club_story(w, f["home_uid"], f["home_club"]),
             "the_visitors": club_story(w, f["away_uid"], w.team(f["away_uid"]).get("club_uid"), brief=True),
             "their_rivalry": _rivalry(w, f),
             "meetings_before": _meetings(w, f) or None,
             "journey_from_the_last_stop": journey,
             "earlier_stops": [f"Trip {t['trip']}, {t['date']}: {t['club']} at {t['ground']}, {t['country']}" for t in trips[-TRIPS_REMEMBERED:]] or None,
             "the_world_it_is": f"It is {w.until.year}. The town, the ground and the country are the real ones, decades on from our day: no event, "
                                f"person or price of the 2020s; the clubs, players and results are the notebook's."}
    return [Lead("travel_log", f"{COLUMN_NAME}, trip {len(trips) + 1}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])} at {f['stadium']}, {nation}",
                 date.fromisoformat(str(f["date"])[:10]), 10 * 8 * TRAVEL_SIZE, {k: v for k, v in facts.items() if v is not None},
                 [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}", f"club:{f['home_uid']}", f"club:{f['away_uid']}"], {"world"},
                 slug=f"{f['id']}")]


# -- the interviews ------------------------------------------------------------------------------------------------------------
def interviews(w) -> list:
    """A retired player, a manager or a legend asked about his life, on a hook the cycle gives: a career that ended within two
    years, a round birthday in the cycle. The best known first, each person once."""
    from app.services.story_leads import Lead

    leads = []
    people = w.rows("select p.fm_uid, p.name, p.birth_date, p.staff_job, p.staff_team_uid, n.adjective, c.last_season, c.starts, c.titles "
                    "from people p join (select s.person_uid, max(s.season_start) last_season, sum(s.starts) starts, "
                    "sum(case when s.kind = 1 then 0 else 0 end) titles from player_seasons s group by s.person_uid having sum(s.starts) >= :m) c "
                    "on c.person_uid = p.fm_uid left join nations n on n.fm_uid = p.nation_uid "
                    "where p.team_uid is null and p.birth_date is not null and c.last_season < :y order by c.starts desc limit 300",
                    m=INTERVIEW_STARTS, y=w.season)
    for p in people:
        age = q.age_on(p["birth_date"], w.until)
        birth = date.fromisoformat(str(p["birth_date"])[:10])
        birthday = birth.replace(year=w.until.year) if not (birth.month == 2 and birth.day == 29) else birth.replace(year=w.until.year, day=28)
        hooks = []
        if w.since < birthday <= w.until and age in ROUND_BIRTHDAYS:
            hooks.append(f"his {age}th birthday on {birthday}")
        if w.season - p["last_season"] <= RETIRED_WITHIN_YEARS and p["last_season"] >= w.season - RETIRED_WITHIN_YEARS:
            hooks.append(f"his playing days ended after the {q.season_label(p['last_season'])} season")
        if not hooks:
            continue
        honours = q.player_honours(w.c, p["fm_uid"])
        career = q.player_career(w.c, p["fm_uid"], honours)
        clubs = {}
        for r in career.rows:
            c = clubs.setdefault(r["team_uid"], {"club": w.name(r["team_uid"]), "first": r["season"], "last": r["season"], "games": 0, "goals": 0})
            c["last"], c["games"], c["goals"] = r["season"], c["games"] + (r["all_apps"] or r["apps"] or 0), c["goals"] + (r["all_goals"] or r["goals"] or 0)
        main = max(clubs.values(), key=lambda c: c["games"]) if clubs else None
        won = [f"{w.comp(h['competition_uid']).get('name') or 'a competition'} {q.season_label(h['season'])} with {w.name(h['team_uid'])}" for h in honours if h["place"] == 1]
        awards = [f"{a['name']}: won {a['wins']} ({', '.join(str(y) for y in a['years'][:6])})" for a in q.player_awards(w.c, p["fm_uid"]) if a["wins"]][:8]
        now = f"{p['staff_job']} at {w.name(p['staff_team_uid'])}" if p["staff_job"] and p["staff_team_uid"] else "out of the game"
        facts = {"person": p["name"], "age": age, "nationality": p["adjective"], "hook": "; ".join(hooks), "what_he_does_now": now,
                 "career": [f"{c['club']}, {q.season_label(c['first'])}{' to ' + q.season_label(c['last']) if c['last'] != c['first'] else ''}: "
                            f"{c['games']} games, {c['goals']} goals" for c in sorted(clubs.values(), key=lambda c: c["first"])] or None,
                 "honours": won[:12] or None, "awards": awards or None,
                 "his_main_club": main["club"] if main else None, "year": w.until.year}
        leads.append(Lead("interview", f"The interview: {p['name']}, {age}, {main['club'] if main else ''}".rstrip(", "), w.until,
                          10 * 8 * INTERVIEW_SIZE * min(1.0, (p["starts"] + 60 * len(won)) / 1200),
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{p['fm_uid']}"] + ([f"club:{max(clubs, key=lambda t: clubs[t]['games'])}"] if clubs else []),
                          w.beats_for(max(clubs, key=lambda t: clubs[t]["games"])) | {"world"} if clubs else {"world"}, slug=str(p["fm_uid"])))
    return leads
