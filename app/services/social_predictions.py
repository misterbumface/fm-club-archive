"""Matchday posts and the fans' predictions (docs/social-plan.md, step 4).

After each import the clubs post their big games of the next PREVIEW_DAYS days (their own followed clubs' games,
derbies, games between two of the best-known clubs), and a few fans of each side answer with a score, from
templates: each fan's temper bends the score (the optimist sees a rout, the doom-monger a defeat, the always-online
fan something absurd). The scores are kept, so once the result is in, a fan who called it exactly says so, and the
AI thread under the result knows every prediction (social_fans.thread_notebook): the right ones gloat, the wrong
ones get reminded.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.extensions import db
from app.models.social import SocialAccount, SocialPost
from app.services import social_fans, social_feed

PREVIEW_DAYS = 7
PREDICTORS = 3  # fans of each side who predict
_MATCHDAY = ("🗓️ MATCHDAY SOON | {home} v {away} · {competition} · {day}\nPredictions? 👇",
             "🗓️ COMING UP | {home} v {away} · {competition} · {day}\nWho's going? 🙌",
             "🗓️ {day}: {home} v {away} · {competition}\nYour scores below 👇")
_DERBY_DAY = "🔥 DERBY DAY | {home} v {away} · {competition} · {day}\nYou know what this one means. 👇"
_PREDICTIONS = {
    "optimist": ("Easy one. {score}. Get the bunting out! 🎉", "{score}. The march goes on! 🙌", "Feeling good: {score} ❤️"),
    "doomer": ("{score}. Prepare yourselves. 😩", "Gut feeling: {score}. Hope I'm wrong.", "{score}. It's what we do. 🙃"),
    "nerd": ("Calling it {score}: tight in midfield, settled by a set piece.", "{score}. The shapes favour it, just."),
    "oldtimer": ("{score}. Proper old-school grind.", "{score}, like the old days. Mark my words."),
    "banter": ("{score} and I'll be in your mentions all night 😂", "{score}. Enjoy the week, you lot 😂😂"),
    "ultra": ("{score}!!! NEVER IN DOUBT 🔥", "{score}. LOUDER THAN EVER, LADS 🔥"),
    "young": ("{score} light work fr 😤", "{score} and it's not even close no shot 💀"),
    "critic": ("{score}, and the manager will still blame the referee.", "{score}. Same old problems."),
}
_CALLED_IT = ("CALLED IT. {score} 🔮", "Told you. {score}. Exactly. 🔮", "{score}. Screenshot this. 🔮😎")


def _poisson(rng: random.Random, mean: float) -> int:
    limit, k, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        k += 1
        product *= rng.random()
    return k


def predict(rng: random.Random, temper: str, edge: float) -> tuple[int, int]:
    """A fan's score for his own side and the other: the clubs' standing (edge, in goals), chance and his temper."""
    ours, theirs = _poisson(rng, max(0.3, 1.4 + edge)), _poisson(rng, max(0.2, 1.1 - edge))
    if temper == "optimist":
        ours, theirs = ours + rng.randint(1, 2), max(0, theirs - 1)
    elif temper == "doomer":
        ours, theirs = max(0, ours - 1), theirs + rng.randint(1, 2)
    elif temper == "ultra":
        ours += 2
    elif temper == "young":
        ours, theirs = ours + rng.randint(2, 4), 0
    elif temper == "critic":
        ours = max(0, ours - 1)
    elif temper == "oldtimer":
        ours, theirs = min(ours, 2) or 1, min(theirs, 1)
    elif temper == "banter":
        ours, theirs = ours + 1, max(0, theirs - 1)
    elif temper == "nerd":
        ours, theirs = round(max(0.3, 1.4 + edge)), round(max(0.2, 1.1 - edge))
    return min(ours, 7), min(theirs, 7)


def _verdict(predicted: dict, home: int, away: int) -> str:
    if (predicted["home"], predicted["away"]) == (home, away):
        return "exact score"
    sign = (predicted["home"] > predicted["away"]) - (predicted["home"] < predicted["away"])
    return "right result" if sign == (home > away) - (home < away) else "wrong"


def _day_words(day: date) -> str:
    return f"{day:%a} {day.day} {day:%B}"


def upcoming(connection: Connection, until: date, clubs: list[int], followed: tuple[int, ...]) -> list[dict]:
    """The big games of the next PREVIEW_DAYS days: the followed clubs' games, derbies, and games between two of the
    best-known clubs (no friendlies)."""
    wanted, marks = set(clubs), ",".join(str(int(u)) for u in clubs) or "0"
    games = connection.execute(text(
        f"select f.date, f.home_uid, f.away_uid, coalesce(c.short_name, c.name) competition from fixtures f "
        f"join competitions c on c.fm_uid = f.competition_uid where f.home_goals is null and f.date > :u and f.date <= :e "
        f"and coalesce(c.scope, '') != 'friendly' and (f.home_uid in ({marks}) or f.away_uid in ({marks})) order by f.date"),
        {"u": str(until), "e": str(until + timedelta(days=PREVIEW_DAYS))}).mappings().all()
    found = []
    for game in games:
        home, away = game["home_uid"], game["away_uid"]
        derby = social_feed.rivalry_level(connection, home, away) >= social_feed.DERBY_LEVEL
        if not (home in followed or away in followed or derby or (home in wanted and away in wanted)):
            continue
        found.append({**game, "date": date.fromisoformat(str(game["date"])[:10]), "derby": derby,
                      "match": f"{str(game['date'])[:10]}:{home}:{away}"})
    return found


def post_matchdays(connection: Connection, until: date, followed: tuple[int, ...]) -> int:
    """The clubs' matchday posts for their big games ahead, each with a few fans' predictions; returns posts made."""
    clubs = social_feed.feed_clubs(connection, followed)
    games = upcoming(connection, until, clubs, followed)
    names = social_feed._clubs(connection, {g["home_uid"] for g in games} | {g["away_uid"] for g in games})
    made = 0
    for game in games:
        home, away = names.get(game["home_uid"]), names.get(game["away_uid"])
        if not home or not away:
            continue
        poster = home if game["home_uid"] in clubs else away
        account = social_feed.club_account(poster["fm_uid"], poster)
        key = f"matchday:{game['match']}"
        if SocialPost.query.filter_by(account_id=account.id, event_key=key).first():
            continue
        rng = random.Random(key)
        shape = _DERBY_DAY if game["derby"] else rng.choice(_MATCHDAY)
        body = shape.format(home=home["short_name"], away=away["short_name"], competition=game["competition"], day=_day_words(game["date"]))
        tags = " ".join(f"#{social_feed.club_tag(c)}" for c in (home, away))
        heat = 15 + (40 if game["derby"] else 0) + (20 if game["home_uid"] in clubs and game["away_uid"] in clubs else 0)
        heat += 10 if game["home_uid"] in followed or game["away_uid"] in followed else 0
        likes, reposts = social_feed._reach(poster["reputation"], 0.7 * (1 + heat / 100), key)
        post = SocialPost(account_id=account.id, posted_on=until, kind="matchday", event_key=key, text=f"{body} {tags}",
                          links=[[away["short_name"] if poster is home else home["short_name"], "club",
                                  game["away_uid"] if poster is home else game["home_uid"]], ["", "match", game["match"]]],
                          likes=likes, reposts=reposts, heat=min(heat, 100))
        db.session.add(post)
        db.session.flush()
        made += 1 + _predictions(connection, post, game, home, away, rng)
    db.session.commit()
    return made


def _predictions(connection: Connection, post: SocialPost, game: dict, home: dict, away: dict, rng: random.Random) -> int:
    made = 0
    for side, us, them in ((0, home, away), (1, away, home)):
        edge = (us["reputation"] - them["reputation"]) / 4000 + (0.25 if side == 0 else 0)  # standing, and home advantage
        for fan in rng.sample(social_fans.fans_of(connection, us["fm_uid"]), PREDICTORS):
            temper = (fan.persona or {}).get("temper", "nerd")
            ours, theirs = predict(random.Random(f"{game['match']}:{fan.handle}"), temper, edge)
            h, a = (ours, theirs) if side == 0 else (theirs, ours)
            score = f"{home['short_name']} {h}-{a} {away['short_name']}"
            said = rng.choice(_PREDICTIONS.get(temper, _PREDICTIONS["nerd"])).format(score=score.upper() if temper == "ultra" else score)
            likes = max(0, int(post.likes * rng.lognormvariate(-5, 1)))
            db.session.add(SocialPost(account_id=fan.id, posted_on=post.posted_on, kind="prediction", event_key=f"reply:{post.id}:p{fan.id}",
                                      text=f"{said} #{social_feed.club_tag(us)}", links=[], reply_to_id=post.id, likes=likes,
                                      reposts=int(likes * rng.uniform(0, 0.1)), prediction={"match": game["match"], "home": h, "away": a}))
            made += 1
    return made


def predictions_for(match: str) -> list[SocialPost]:
    return SocialPost.query.filter(SocialPost.kind == "prediction", db.func.json_extract(SocialPost.prediction, "$.match") == match).all()


def result_of(connection: Connection, match: str) -> tuple[int, int] | None:
    day, home, away = match.split(":")
    row = connection.execute(text("select coalesce(home_goals_after_extra_time, home_goals), coalesce(away_goals_after_extra_time, away_goals) "
                                  "from fixtures where date = :d and home_uid = :h and away_uid = :a and home_goals is not null"),
                             {"d": day, "h": int(home), "a": int(away)}).first()
    return (row[0], row[1]) if row else None


def predictions_notebook(connection: Connection, match: str) -> list[dict]:
    """What the fans predicted before a match, and how they did: for the AI thread under its result."""
    result = result_of(connection, match)
    if result is None:
        return []
    names = social_feed._clubs(connection, {int(match.split(":")[1]), int(match.split(":")[2])})
    home, away = (names.get(int(uid), {}).get("short_name", "") for uid in match.split(":")[1:])
    return [{"handle": p.account.handle, "predicted": f"{home} {p.prediction['home']}-{p.prediction['away']} {away}",
             "verdict": _verdict(p.prediction, *result)} for p in predictions_for(match)]


def called_it(connection: Connection) -> int:
    """Fans who predicted a played match's exact score say so, under their club's result post."""
    made = 0
    matches = {p.prediction["match"] for p in SocialPost.query.filter_by(kind="prediction")}
    for match in matches:
        result = result_of(connection, match)
        if result is None:
            continue
        for prediction in predictions_for(match):
            if _verdict(prediction.prediction, *result) != "exact score":
                continue
            fan = prediction.account
            club = SocialAccount.query.filter_by(kind="club", club_uid=fan.club_uid).first()
            parent = SocialPost.query.filter_by(account_id=club.id, event_key=f"result:{match}").first() if club else None
            key = f"reply:{parent.id}:c{fan.id}" if parent else None
            if parent is None or SocialPost.query.filter_by(account_id=fan.id, event_key=key).first():
                continue
            names = social_feed._clubs(connection, {int(match.split(":")[1]), int(match.split(":")[2])})
            home, away = (names[int(uid)]["short_name"] for uid in match.split(":")[1:])
            score = f"{home} {result[0]}-{result[1]} {away}"
            rng = random.Random(key)
            likes = max(1, int(parent.likes * rng.lognormvariate(-3.5, 0.6)))  # a correct call is well liked
            db.session.add(SocialPost(account_id=fan.id, posted_on=parent.posted_on, kind="called_it", event_key=key,
                                      text=rng.choice(_CALLED_IT).format(score=score), links=[], reply_to_id=parent.id,
                                      likes=likes, reposts=int(likes * 0.2)))
            made += 1
    db.session.commit()
    return made

