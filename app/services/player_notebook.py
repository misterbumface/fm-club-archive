"""The facts a player bio is written from (docs/player-bios-plan.md).

``player_notebook`` reads the world database through ``world_queries`` -- the same
queries as the player page, so a bio and its page agree -- and returns one plain dict
that is sent to the writer as JSON. Names replace FM ids, and seasons are labels such as
"2061/62", so the writer never has to look anything up.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy.engine import Connection

from app.services import world_queries as q

EUROS_PER_POUND = 1.16  # the save keeps money in pounds; the game shows euros (1.1599 in the editor)
TEAMMATE_GAMES = 10  # league games each, for a shared season to count
GOAL_MILESTONES = (50, *range(100, 1501, 100))
GAME_MILESTONES = tuple(range(100, 1501, 100))
POSITION_WORDS = {
    "GK": "goalkeeper", "SW": "sweeper", "D (R)": "right-back", "D (L)": "left-back", "D (C)": "centre-back",
    "WB (R)": "right wing-back", "WB (L)": "left wing-back", "DM": "defensive midfielder", "M (R)": "right midfielder",
    "M (L)": "left midfielder", "M (C)": "central midfielder", "AM (R)": "right winger", "AM (L)": "left winger",
    "AM (C)": "attacking midfielder", "ST (C)": "striker",
}


def player_notebook(connection: Connection, uid: int, old_bio: str | None = None) -> dict | None:
    """Everything the save says about one player, or None when the person is unknown."""
    person = q.person(connection, uid)
    if person is None:
        return None
    today = q.game_date(connection)
    honours = q.player_honours(connection, uid)
    career = q.player_career(connection, uid, honours)
    finals = q.goals_in_finals(connection, uid, honours)
    elevens = q.player_best_elevens(connection, uid)
    transfers = q.player_transfers(connection, uid)
    teammates = _teammates(connection, uid, honours)
    awards = q._rows(connection, "select ap.*, aw.name award from award_places ap join awards aw on aw.fm_uid = ap.award_uid "
                                 "where ap.person_uid = :uid order by ap.year, ap.place", uid=uid)

    team_ids = {r["team_uid"] for r in career.rows} | {h["team_uid"] for h in honours} | {a["club_uid"] for a in awards}
    team_ids |= {t["from_uid"] for t in transfers} | {t["to_uid"] for t in transfers}
    team_ids |= {f["home_uid"] for f in finals} | {f["away_uid"] for f in finals}
    team_ids |= {(e["club"] or {}).get("team_uid") for e in elevens}
    teams = q.teams(connection, team_ids)
    competitions = q.competitions(connection, {h["competition_uid"] for h in honours} | {r["league_uid"] for r in career.rows}
                                  | {f["competition_uid"] for f in finals})

    def team(uid_: int | None) -> str | None:
        return teams.get(uid_, {}).get("name") if uid_ else None

    def competition(uid_: int | None) -> str | None:
        return competitions.get(uid_, {}).get("name") if uid_ else None

    main = q.main_positions(connection, [uid]).get(uid)  # the team sheets first, else the best rating
    seasons = _seasons(connection, uid, career, team, competition, keeper=main == "GK")
    attributes = q.latest_attributes(connection, uid)
    positions = Counter(_position(b["position"]) for row in elevens for b in (row["club"], row["national"]) if b and b["position"])
    last_season = max((r["season"] for r in career.rows), default=None)
    game_season = (today.year if today.month >= 7 else today.year - 1) if today else None
    if last_season is None:
        status = "no club games recorded"
    elif game_season is None or last_season >= game_season - 1:
        status = "active: playing at the game date; write in the present tense, the career is not over"
    else:
        status = f"no games since {q.season_label(last_season)}: write the career in the past tense"
    notebook = {
        "game_date": str(today) if today else None,
        "player": {
            "name": person["name"],
            "birth_date": str(person["birth_date"]) if person["birth_date"] else None,
            "age": q.age_on(person["birth_date"], today),
            "nationality": person.get("nationality"),
            "height_cm": attributes and attributes["height_cm"],
            "weight_kg": attributes and attributes["weight_kg"],
            "main_position": _position(main),  # without it a keeper with no attribute record was written as a defender (2 Nov 2026)
            "positions": _positions(attributes),
            "how_he_plays": ", ".join(attributes["preferred_moves"]).lower() if attributes and attributes.get("preferred_moves") else None,
            "positions_in_best_elevens": [p for p, _ in positions.most_common()],
            "last_season_with_games": q.season_label(last_season) if last_season else None,
            "career_status": status,
        },
        "career_totals": {"club_games": career.totals["all_apps"], "club_goals": career.totals["all_goals"],
                          "league_games": career.totals["apps"], "league_goals": career.totals["goals"],
                          "national_team_games": career.totals["national_apps"], "national_team_goals": career.totals["national_goals"]},
        "seasons": seasons,
        "honours": [{"competition": competition(h["competition_uid"]), "season": h["season_label"], "club": team(h["team_uid"]),
                     "result": "won" if h["place"] == 1 else "runner-up", "decided_on": str(h["decided_on"]) if h["decided_on"] else None}
                    for h in honours],
        "goals_in_finals": [_final(f, uid_side=f["team_uid"], team=team, competition=competition) for f in finals],
        "awards": [{"award": a["award"], "year": a["year"], "place": a["place"], "club": team(a["club_uid"]),
                    "games": a["appearances"], "goals": a["goals"], "votes": a["votes"]} for a in awards],
        "best_elevens": [{"season": q.season_label(e["season"]), "club_team": team((e["club"] or {}).get("team_uid")),
                          "club_position": _position((e["club"] or {}).get("position")),
                          "national_team_position": _position((e["national"] or {}).get("position"))}
                         for e in elevens],
        "transfers": [{"date": str(t["date"]), "kind": q.TRANSFER_KINDS.get(t["kind"], str(t["kind"])), "from": team(t["from_uid"]),
                       "to": team(t["to_uid"]), "fee_pounds": t["fee"] or None,
                       "fee_euros": round(t["fee"] * EUROS_PER_POUND) if t["fee"] else None} for t in transfers],
        "teammates": teammates,
        "milestones": _milestones(seasons, honours),
        "standing_with_clubs": _standing(connection, uid),
    }
    if old_bio:
        notebook["old_bio"] = {"text": old_bio, "use": "colour only; every fact above wins where the two disagree"}
    return notebook


FAVOURED_IN_NOTEBOOK = 50  # a lesser favourite is a passing fondness, not worth a writer's line


def _standing(connection: Connection, uid: int) -> list[dict] | None:
    """The game's own lists of how clubs see the player: a Legend or an Icon of a club, a favourite,
    or disliked by its fans."""
    found = [{"club": r["club_name"], "standing": {"Legend": "club legend", "Icon": "club icon", "Favoured": "favourite of the fans",
                                                    "Disliked": "disliked by the club and its fans"}[r["group"]],
              "level_of_100": r["level"], "for_their_time_as": r["reason_name"] or None}
             for r in q.person_club_relations(connection, uid) if r["group"] != "Favoured" or r["level"] >= FAVOURED_IN_NOTEBOOK]
    return found or None


def _positions(attributes: dict | None) -> dict | None:
    """The game's position ratings in words: natural and accomplished positions, and the feet."""
    found = q.player_positions(attributes)
    if found is None:
        return None
    by_level = {level: [POSITION_WORDS.get(s["code"], s["code"]) for s in sorted(found["spots"], key=lambda s: -s["rating"])
                        if s["level"] == level] for level in ("natural", "accomplished")}
    return {"natural": by_level["natural"], "accomplished": by_level["accomplished"] or None, "foot": found["foot"]}


def _position(code: str | None) -> str | None:
    return POSITION_WORDS.get(code, code) if code else None


def _seasons(connection: Connection, uid: int, career: q.Career, team, competition, keeper: bool = False) -> list[dict]:
    rows = []
    for r in career.rows:
        league = r["league_uid"]
        position = teams_in_league = None
        start, end = q.league_window(connection, league, r["season"]) if league else q.season_window(r["season"])
        label = str(r["season"]) if start.endswith("-01-01") else q.season_label(r["season"])  # calendar-year leagues (MLS)
        if league and r["apps"]:
            table = q.league_table(connection, league, start, end)
            row = next((t for t in table if t["team_uid"] == r["team_uid"]), None)
            position, teams_in_league = (row["position"] if row else None), len(table)
        rows.append({
            "season": label, "club": team(r["team_uid"]), "league": competition(league),
            "club_league_position": position, "teams_in_league": teams_in_league,
            "league_games": r["apps"], "league_games_as_sub": r["subs"], "league_goals": r["goals"], "league_assists": r["assists"],
            "player_of_the_match": r["pom"], "average_rating": r["rating"],
            "club_games_all_competitions": r["all_apps"], "club_goals_all_competitions": r["all_goals"],
            "national_team_games": r["national_apps"], "national_team_goals": r["national_goals"],
            "won": [competition(c) for c in r["won"]],
            **({"league_clean_sheets": r["clean_sheets"], "league_goals_conceded": r["conceded"]} if keeper and r["apps"] else {}),
        })
    return rows


def _final(fixture: dict, *, uid_side: int, team, competition) -> dict:
    scored, conceded, result = q.result_for(fixture, uid_side)
    other = fixture["away_uid"] if fixture["home_uid"] == uid_side else fixture["home_uid"]
    penalties = None
    if fixture["home_penalties"] is not None:
        mine, theirs = (fixture["home_penalties"], fixture["away_penalties"])
        penalties = f"{mine}-{theirs}" if fixture["home_uid"] == uid_side else f"{theirs}-{mine}"
    return {"competition": competition(fixture["competition_uid"]), "date": str(fixture["date"]), "club": team(uid_side),
            "opponent": team(other), "score": f"{scored}-{conceded}", "after_extra_time": fixture["home_goals_after_extra_time"] is not None,
            "penalties": penalties, "result": {"W": "won", "L": "lost", "D": "drawn"}[result], "goal_minutes": fixture["minutes"]}


def _teammates(connection: Connection, uid: int, honours: list[dict]) -> list[dict]:
    """The players who shared the most club seasons with this one (both with 10 or more league
    games), and the trophies they won together."""
    won = defaultdict(list)
    for h in honours:
        if h["place"] == 1:
            won[(h["team_uid"], h["season"])].append(h["competition_uid"])
    rows = q._rows(connection,
                   "select o.person_uid, p.name, group_concat(o.team_uid || ':' || o.season_start) shared "
                   "from player_seasons me join player_seasons o on o.team_uid = me.team_uid and o.season_start = me.season_start "
                   "and o.kind = 1 and o.person_uid != me.person_uid join people p on p.fm_uid = o.person_uid "
                   "where me.person_uid = :uid and me.kind = 1 and me.starts + me.substitute_appearances >= :n "
                   "and o.starts + o.substitute_appearances >= :n group by o.person_uid", uid=uid, n=TEAMMATE_GAMES)
    found = []
    for r in rows:
        shared = sorted({tuple(int(x) for x in pair.split(":")) for pair in r["shared"].split(",")}, key=lambda p: p[1])
        trophies = [c for pair in shared for c in won.get(pair, [])]
        found.append({"person_uid": r["person_uid"], "name": r["name"], "shared": shared, "trophies": trophies})
    found.sort(key=lambda f: (-len(f["shared"]), -len(f["trophies"]), f["name"]))
    top = found[:8]
    teams = q.teams(connection, {t for f in top for t, _ in f["shared"]})
    names = q.competitions(connection, {c for f in top for c in f["trophies"]})
    return [{"name": f["name"], "shared_seasons": len(f["shared"]),
             "clubs": sorted({teams.get(t, {}).get("name", "") for t, _ in f["shared"]}),
             "from": q.season_label(f["shared"][0][1]), "to": q.season_label(f["shared"][-1][1]),
             "trophies_together": dict(Counter(names.get(c, {}).get("name") for c in f["trophies"]).most_common())} for f in top]


def _milestones(seasons: list[dict], honours: list[dict]) -> list[dict]:
    """Firsts and round numbers, by the season they were reached (club competitions)."""
    found: list[dict] = []
    games = goals = 0
    for s in seasons:
        if s["club_games_all_competitions"] and not games:
            found.append({"season": s["season"], "milestone": f"first club games, for {s['club']}"})
        if s["club_goals_all_competitions"] and not goals:
            found.append({"season": s["season"], "milestone": f"first club goal, for {s['club']}"})
        before_games, before_goals = games, goals
        games += s["club_games_all_competitions"]
        goals += s["club_goals_all_competitions"]
        found += [{"season": s["season"], "milestone": f"{n}th club game, for {s['club']}"} for n in GAME_MILESTONES if before_games < n <= games]
        found += [{"season": s["season"], "milestone": f"{n}th club goal, for {s['club']}"} for n in GOAL_MILESTONES if before_goals < n <= goals]
    national = [s for s in seasons if s["national_team_games"]]
    if national:
        found.append({"season": national[0]["season"], "milestone": "first national-team games"})
    first_trophy = next((h for h in honours if h["place"] == 1), None)
    if first_trophy:
        found.append({"season": first_trophy["season_label"], "milestone": "first trophy"})
    if seasons:
        best = max(seasons, key=lambda s: (s["club_goals_all_competitions"], s["club_games_all_competitions"]))
        if best["club_goals_all_competitions"]:
            found.append({"season": best["season"], "milestone": f"most club goals in a season: {best['club_goals_all_competitions']}"})
    return found
