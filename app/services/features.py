"""Two features the papers run beside the news (25 Oct 2026): the travel column, one trip a cycle to a game somewhere in
the world, a journey with a memory; and the interviews, a retired player, a manager or a legend asked about his life on
a hook the archive gives. Both are written from the archive's facts alone; the words are the writers' craft.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from datetime import date, timedelta

from app.services import world_queries as q

COLUMN_NAME = "Away Days"
COLUMNIST = {"id": None, "name": "Teodor Albescu",
             "persona": "A Romanian travel writer in his fifties who has spent thirty years going to football matches around the world "
                        "and writing the Away Days column about what it took to get there, the food, the fans, the look of the ground and "
                        "the atmosphere in it. A fourth-division ground on a wet Tuesday or a full national stadium: either will do, as "
                        "long as the trip is worth telling. He goes by train and bus where he can, eats where the fans eat, talks to people "
                        "outside the ground, and never pretends a place is grander or poorer than it is. Things go wrong on the road and he "
                        "lets them: a missed train, a shut gate, a night in a bar instead of the stand. His wife, Ileana, is at home in Cluj "
                        "and thinks him a little mad.",
             "style": "Plain, exact, warm; a first-person travel log in the present tense of the trip, with dates, hours and distances; "
                      "the real geography and the real town, told the way a well-read traveller tells it, not a guidebook; particular "
                      "things, never lists of clichés; a wry eye for the small absurdities of away days; the match as a fan in the stand saw it.",
             "quirks": "Notices the one particular thing in a place: a sign, a smell, a habit of the crowd; tells a day as it went, "
                       "good or bad; never tells two trips the same way."}
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
            body = [line.strip() for line in (article.body or "").splitlines() if line.strip()]
            first = next((line for line in body if not _heading(line)), "")
            trips.append({**stop, "trip": len(trips) + 1, "headline": article.headline,
                          "this_trip": (article.lead or {}).get("facts", {}).get("this_trip") or {},
                          "opened_with": re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0][:240],
                          "headings": [line.strip("*# ") for line in body if _heading(line)]})
    return trips


def _heading(line: str) -> bool:
    """A section heading of a printed column: a short line of its own, not a sentence."""
    line = line.strip("*# ")
    return 0 < len(line) <= 60 and not line.endswith((".", "!", "?", "\u201d", '"')) and len(line.split()) <= 8


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


# Each trip is told its own way (3 Nov 2026: fifteen columns, every one opening on a bus with the fare, then the town, the
# club, the food with its price, the game, the walk back to the station). The angle leads the piece, the shape sets how it
# opens and runs, and the day may go its own way: life on the road. None of them repeats within the trips given.
ANGLES = {
    "a_person": "the day is told through one person met on the way, a steward, a kit man, a taxi driver, a fan who has not missed a home "
                "game in forty years: who they are, what the club is to them, what they say; the rest hangs off them",
    "one_player": "he watches one player for the whole game, the player_to_watch, from wherever he stands: what the player does off the "
                  "ball, how the crowd treats him, his numbers from the match report and nothing more",
    "the_ground": "the ground leads: its stands one by one, its history, what stood there before, what is seen beyond it, how it sounds full",
    "the_rivalry": "the rivalry leads: what lies between these two clubs and their towns, the meetings before, how this one felt",
    "the_town": "the town leads: he spends the day in it before the game, its real landmarks, the old quarter, the market, the river, the "
                "museum or the church that tells its story, and the game comes at the end of the day",
    "the_away_end": "he goes in with the visiting fans: their journey, their songs, their end of the ground, how the home town treats them",
    "the_past": "the club's past leads: its lost old ground, its golden years, its legends_and_icons, what is left of them in the town",
    "the_season": "the weather and the time of year lead: what this month is like in this place, the light, the cold or the heat, what "
                  "people do, what the season means for the club",
    "the_food": "he eats his way through the day: breakfast, the market, the stall by the ground, the bar after, each by its proper name, "
                "and the people who cook and sell it",
    "the_journey": "the journey itself is the story: the route, the changes, the people met in the carriages and stations, the country "
                   "seen from the window; the town and the game are where it ends",
}
SHAPES = {
    "the_road": "Open on the road or the arrival in the town, told as a scene; the score waits.",
    "the_whistle": "Open at the final whistle, in the stand, then go back to how the day got there; the score is known from the first line.",
    "the_night_before": "Open in the town the evening before the game: where he sleeps, what he eats, who he meets; the game is tomorrow.",
    "the_voice": "Open on a person he met, in their own words, then who they are and where; the score waits.",
    "the_diary": "Write it as a diary of the day: each section heading is a clock time (06:40, 11:15, 19:45), in order; open at the first.",
    "the_letter": "Write it as a letter to the editor of the paper: open 'Dear editor,' and sign off as Teodor at the end, the column "
                  "in between; the score waits.",
    "the_first_sight": "Open on the first sight of the ground, from a distance, a street or a hill; the score waits.",
    "the_morning_after": "Open the morning after, leaving the town, then look back on the day; the score is known from the first line.",
}
HAPPENINGS = {
    "travel_trouble": "the journey goes wrong: a strike, a cancelled or late train, a missed connection, a bus that breaks down, a long "
                      "border queue; he improvises, and makes it in time",
    "locked_out": "he cannot get in: the gates shut on a full ground, or his ticket is wrong, and he watches the game in a bar near the "
                  "ground among the home fans (or hears it from outside); the game as the screen and the room told it",
    "trouble": "trouble on the day: ultras fighting outside, riot police on the street, flares and a charge in the stands, a scuffle on "
               "the tram; he sees it from the edge and tells it plainly, no name and no death; play is not stopped, kick-off is on "
               "time, and the result stands as the notebook gives",
    "town_event": "the town has something else on: a festival, market day, a saint's procession, a wedding in his hotel, an election, "
                  "a race closing the roads; the game shares the day with it",
    "mishap": "a small mishap of his own, told wryly: a lost phone, a hotel that never heard of his booking, food that disagrees with "
              "him, a twisted ankle on the cobbles, rain through his only coat",
    "a_companion": "he falls in with someone for the whole day, a local who shows him the town, an away fan, a student, an old "
                   "groundhopper, and the day is theirs as much as his",
}
CALM_DAYS = 2  # in the deal of happenings, this many calm days (nothing goes wrong, nothing else on) to one of each kind
RUNNING_THREADS = (
    "the scarf: he buys a scarf at every ground; this one is number {trip}; the drawer at home and Ileana's view of it",
    "his table of the road's food: where today's food ranks among the earlier stops, and why",
    "a postcard home to Ileana in Cluj: what he writes on it, a few lines",
    "the ticket stub: he keeps every one in a shoebox; what this one looks like and what it cost",
    "his old notebook of grounds: what he writes in it tonight, and a line from an earlier page",
)
THREAD_EVERY = 3  # a running thread comes back on every third trip, never as a formula
KIND_POOL = 12  # the best games by place and crowd, among which the kind of game decides
KIND_NEW, KIND_AGAIN = 0.5, 0.3
GAME_KINDS = {"derby": "a derby", "cup": "a cup tie", "continental": "a continental night", "international": "an international",
              "friendly": "a friendly", "lower_league": "the lower leagues", "league": "a league game"}
REMEMBER = {"angle": 5, "shape": 4, "happening": 3, "game_kind": 3}  # a choice does not come back within this many trips
LAST_COLUMNS = 3  # the last columns' headlines, openings and headings the writer must not reuse


def game_kind(w, f: dict) -> str:
    """What kind of day out a game is: a derby, a cup tie, a continental night, an international, a friendly, the lower
    leagues or a plain league game."""
    if f.get("scope") == "national_teams" or f.get("home_club") is None:
        return "international"
    if f.get("scope") in ("continental", "cup", "friendly"):
        return f["scope"]
    if f.get("competition_uid") is None:
        return "friendly"
    if _rivalry(w, f):
        return "derby"
    return "lower_league" if (f.get("level") or 0) >= 3 else "league"


def _fresh(options, used: list, seed: int, weights: dict | None = None) -> str:
    """One of the options not used lately (any, when all were), chosen the same way for the same game."""
    left = [o for o in options if o not in used] or list(options)
    return random.Random(seed).choices(left, [(weights or {}).get(o, 1) for o in left])[0]


def this_trip(w, f: dict, trips: list[dict], kind: str) -> dict:
    """How this trip is told: its angle, shape, what happens on the day, and the running thread when it comes back."""
    def used(key: str) -> list:
        return [t.get("this_trip", {}).get(key) for t in trips[-REMEMBER[key]:]]

    angles = [a for a in ANGLES if a != "the_rivalry" or kind == "derby" or _rivalry(w, f)]
    if not f.get("attendance"):
        angles = [a for a in angles if a != "the_away_end"]
    happenings = list(HAPPENINGS) + ["calm"]
    if not f.get("attendance") or f["attendance"] < 5_000:
        happenings.remove("locked_out")  # a small crowd leaves room on the gate
    chosen = {"angle": _fresh(angles, used("angle"), f["id"]), "shape": _fresh(SHAPES, used("shape"), f["id"] + 1),
              "happening": _fresh(happenings, used("happening"), f["id"] + 2, {"calm": CALM_DAYS}), "game_kind": kind}
    told = {**chosen, "the_angle": ANGLES[chosen["angle"]], "the_shape": SHAPES[chosen["shape"]],
            "what_happens": HAPPENINGS.get(chosen["happening"], "a calm day: nothing goes wrong and nothing else is on; the people "
                                                                 "and the place carry it"),
            "the_game_is": GAME_KINDS[kind]}
    number = len(trips) + 1
    if number % THREAD_EVERY == 0:
        told["running_thread"] = RUNNING_THREADS[(number // THREAD_EVERY - 1) % len(RUNNING_THREADS)].format(trip=number)
    return told


def travel_log(w) -> list:
    """One trip a cycle: a played game with a full report, in a country the column has not seen lately (and not the followed
    clubs' own, where every other page already is), near the last stop two trips in three; a rare destination, a small ground
    or a full house are each worth the journey."""
    from app.services.story_leads import Lead
    from app.services.story_leads_now import _how_goals_went_in

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

    # the kind of game counts among the best: a kind the column has not seen lately is worth more, the last one's less
    recent = [t.get("this_trip", {}).get("game_kind") for t in trips[-REMEMBER["game_kind"]:]]
    best = [(g, game_kind(w, g)) for g in sorted(games, key=score, reverse=True)[:KIND_POOL]]
    f, kind = max(best, key=lambda gk: score(gk[0]) + (KIND_NEW if gk[1] not in recent else 0)
                  - (KIND_AGAIN if recent and gk[1] == recent[-1] else 0))
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
    watch = w.rows("select p.name from match_reports r join people p on p.fm_uid = r.player_of_the_match_uid where r.fixture_id = :f", f=f["id"])
    told = this_trip(w, f, trips, kind)
    if told["angle"] == "one_player" and watch:
        told["player_to_watch"] = watch[0]["name"]
    elif told["angle"] == "one_player":
        told["angle"], told["the_angle"] = "the_ground", ANGLES["the_ground"]
    facts = {"column": COLUMN_NAME, "trip_number": len(trips) + 1, "year": w.until.year, "this_stop": stop, "this_trip": told,
             "the_last_columns": [{"headline": t["headline"], "opened_with": t.get("opened_with"), "headings": t.get("headings") or None}
                                  for t in trips[-LAST_COLUMNS:]] or None,
             "the_place": where,
             "the_people": f"{adjective}" if adjective else None,
             "the_day_out": ("a small ground" if f["attendance"] and f["attendance"] <= SMALL_CROWD else "a full house" if f["attendance"]
                             and f["attendance"] >= FULL_HOUSE else "a middling crowd") + (f", {f['attendance']:,} in the ground" if f["attendance"] else ""),
             "the_game": {"competition": f["competition"] or "a friendly", "level": level, "date": str(f["date"]),
                          "home": w.name(f["home_uid"]), "away": w.name(f["away_uid"]), "score": f"{f['home_goals']}-{f['away_goals']}",
                          "ground": f["stadium"], "attendance": f["attendance"] or None,
                          "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                                    f"({w.name(f['home_uid'] if g['side'] == 0 else f['away_uid'])})" for g in goals] or None,
                          "how_the_goals_went_in": _how_goals_went_in(w, f)},
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
