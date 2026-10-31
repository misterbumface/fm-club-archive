"""The social feed (docs/social-plan.md): the clubs' own accounts post what happened in the save.

Step 1 has only the clubs' accounts, and their posts are made from templates, without AI: instant, so every import
makes them, and always right. Each post comes from one event of the save (a result, a signing or a sale, a title, a
manager's leaving or arrival) and is named by it, so an import of the same days again posts nothing twice.

Clubs with an account: the followed clubs and the FEED_CLUBS best-known clubs; a big transfer (BIG_FEE) brings in
both its clubs. The whole feed can be turned off in Settings.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from flask import has_app_context
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import AppSetting
from app.models import world as world_models
from app.models.social import CLUB, JOURNALIST, PLAYER, SocialAccount, SocialPost

ENABLED_KEY = "social_feed"  # "off" turns the feed off
UNTIL_KEY = "social_feed_until"  # the last game date the feed has posted up to
FEED_CLUBS = 40
FIRST_DAYS = 14  # the first run posts this many days before the game date
BIG_FEE = 25_000_000  # pounds
PLAYER_FEE = 10_000_000  # a signing this big: the player posts too
SCOOP_FEE = 15_000_000  # a move this big: The Transfer Wire posts it
SCOOP_OUTLET = "transfer-wire"
SIGNED, SOLD = (1, 2, 4, 6), (1, 2, 4)  # transfer kinds: transfer, part-exchange, free transfer, compensation
LOAN = 3
SACKED, RESIGNED, RETIRED = 2, 0, 6  # manager leaving reasons (fm_save.MANAGER_LEAVING)


def enabled() -> bool:
    if not has_app_context():
        return True
    try:
        setting = AppSetting.query.filter_by(key=ENABLED_KEY).first()
    except SQLAlchemyError:  # no settings table yet (a fresh archive, an error page): the default
        db.session.rollback()
        return True
    return setting is None or setting.value != "off"


def set_enabled(on: bool) -> None:
    setting = AppSetting.query.filter_by(key=ENABLED_KEY).first()
    if setting is None:
        db.session.add(AppSetting(key=ENABLED_KEY, value="on" if on else "off"))
    else:
        setting.value = "on" if on else "off"


@dataclass
class Event:
    key: str  # one event, the same in every import
    kind: str
    day: date
    club: int  # the club that posts
    text: str
    links: list = field(default_factory=list)  # [name, "club"|"person"|"match", id]
    size: float = 1.0  # how much the world cares, for likes and reposts
    author: tuple | None = None  # ("player", person uid, name) or ("outlet", slug); None: the club
    heat: int = 0  # 0-100: how much it stirs people (a derby, an upset, a sale to a rival); hot posts draw more replies


# -- the clubs --------------------------------------------------------------------------------------------
def feed_clubs(connection: Connection, followed: tuple[int, ...] = ()) -> list[int]:
    """The followed clubs, then the best-known ones."""
    known = [r[0] for r in connection.execute(text(
        "select fm_uid from teams where club_uid = fm_uid and age_limit is null and reputation is not null "
        "order by reputation desc limit :n"), {"n": FEED_CLUBS})]
    return list(dict.fromkeys([*followed, *known]))


def _clubs(connection: Connection, uids: set[int]) -> dict[int, dict]:
    if not uids:
        return {}
    marks = ",".join(str(int(u)) for u in uids)
    return {r["fm_uid"]: dict(r) for r in connection.execute(text(
        f"select t.fm_uid, t.name, coalesce(c.short_name, t.name) short_name, coalesce(t.reputation, 3000) reputation "
        f"from teams t left join clubs c on c.fm_uid = t.club_uid where t.fm_uid in ({marks})")).mappings()}


DERBY_LEVEL = 50  # a rivalry this strong (0-100, either way) makes a derby
UPSET_GAP = 1500  # a club this much better known that loses has been upset


def rivalry_level(connection: Connection, one: int, other: int) -> int:
    """How fierce the rivalry between two clubs is, in either direction (0 when none)."""
    row = connection.execute(text("select max(level) from club_relations where relation = 'rival' and "
                                  "((club_uid = :a and subject_uid = :b) or (club_uid = :b and subject_uid = :a))"),
                             {"a": one, "b": other}).scalar()
    return row or 0


def standing(connection: Connection, person: int, team: int) -> str | None:
    """How a club regards a person, as the save has it: "Legend", "Icon", "Favoured" or "Disliked" (none: nothing)."""
    from app.services import world_queries as q

    club = connection.execute(text("select coalesce(club_uid, fm_uid) from teams where fm_uid = :t"), {"t": team}).scalar() or team
    row = connection.execute(text("select relation, level, permanent from club_relations where club_uid = :c and subject_uid = :p "
                                  "and relation != 'rival' order by relation = 'disliked' desc"), {"c": club, "p": person}).first()
    return q.relation_group(row[0], row[1], bool(row[2])) if row else None


_STANDING_HEAT = {"Legend": 40, "Icon": 40, "Favoured": 20}  # a club's favourite leaving
DISLIKED_HEAT = 35  # a person the buying club's fans cannot stand arriving


def _hot(value: float) -> int:
    return max(0, min(100, round(value)))


def handle_for(name: str) -> str:
    """"Chelsea" -> "ChelseaFC", "Real Madrid FC" -> "RealMadridFC": the name's letters, in ASCII."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", plain)
    handle = "".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words)[:30] or "Club"
    return handle if re.search(r"(FC|CF|SC|AC|Club|United|City)$", handle) else handle + "FC"


def tag_for(name: str) -> str:
    """A hashtag's word: "Premier Division" -> "PremierDivision", "Borussia Mönchengladbach" -> "BorussiaMonchengladbach"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(w if w.isupper() else w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", plain))[:30]


def club_tag(club: dict) -> str:
    return tag_for(club["name"] if "." in club["short_name"] else club["short_name"])


def _free_handle(handle: str) -> str:
    number = 1
    while SocialAccount.query.filter_by(handle=handle if number == 1 else f"{handle}{number}").first():
        number += 1
    return handle if number == 1 else f"{handle}{number}"


def player_account(uid: int, name: str, club_uid: int | None) -> SocialAccount:
    """A player's own account, made when he first posts; it follows him to his new clubs."""
    account = SocialAccount.query.filter_by(kind=PLAYER, person_uid=uid).first()
    if account is None:
        account = SocialAccount(handle=_free_handle(tag_for(name) or "Player"), name=name, kind=PLAYER, person_uid=uid, club_uid=club_uid)
        db.session.add(account)
        db.session.flush()
    elif club_uid and account.club_uid != club_uid:
        account.club_uid = club_uid
    return account


def outlet_account(slug: str) -> SocialAccount | None:
    """A paper's own account (its desk), made when it first posts."""
    from app.models import Outlet
    from app.services.outlets import ensure_outlets

    outlet = Outlet.query.filter_by(slug=slug).first() or next((o for o in ensure_outlets() if o.slug == slug), None)
    if outlet is None or not outlet.active:
        return None
    account = SocialAccount.query.filter(SocialAccount.kind == JOURNALIST, SocialAccount.person_uid.is_(None),
                                         SocialAccount.name == outlet.name).first()
    if account is None:
        account = SocialAccount(handle=_free_handle(tag_for(outlet.name)), name=outlet.name, kind=JOURNALIST,
                                persona={"outlet": slug})
        db.session.add(account)
        db.session.flush()
    return account


def writer_account(writer) -> SocialAccount:
    """A paper's writer's own account."""
    account = next((a for a in SocialAccount.query.filter_by(kind=JOURNALIST).all()
                    if a.persona and a.persona.get("writer_id") == writer.id), None)
    if account is None:
        account = SocialAccount(handle=_free_handle(tag_for(writer.name) or "Writer"), name=writer.name, kind=JOURNALIST,
                                persona={"outlet": writer.outlet.slug, "writer_id": writer.id})
        db.session.add(account)
        db.session.flush()
    return account


def club_account(uid: int, club: dict) -> SocialAccount:
    account = SocialAccount.query.filter_by(kind=CLUB, club_uid=uid).first()
    if account is None:
        handle = _free_handle(handle_for(club["name"] if "." in club["short_name"] else club["short_name"]))  # not "A. Madrid"
        account = SocialAccount(handle=handle, name=club["name"], kind=CLUB, club_uid=uid)
        db.session.add(account)
        db.session.flush()
    return account


# -- the events -------------------------------------------------------------------------------------------
_WIN = ("Three points! 💪", "Job done. ✅", "Get in! 🙌", "Another win in the bag.", "What a performance! 🔥", "Onwards. ⬆️")
_BIG_WIN = ("Statement made. 🔥", "Total control from start to finish. 👏", "Goals, goals, goals! ⚽⚽⚽", "Ruthless. 😤")
_DRAW = ("A point apiece.", "Honours even.", "We go again. 👊", "Spoils shared.", "A hard-earned point.")
_LOSS = ("Not our day.", "Disappointing. We go again.", "Heads up. We'll bounce back. 👊", "Not good enough today.",
         "We move on to the next one.")
_PENS_WON = ("Through on penalties! 😅", "Nerves of steel from the spot! 🎯")
_PENS_LOST = ("Out on penalties. Heartbreaking. 💔", "Penalties are cruel. Proud of the effort.")
_TITLE = ("🏆 CHAMPIONS! 🏆", "🏆 WINNERS! 🏆", "WE ARE THE CHAMPIONS! 🏆", "Silverware! 🏆✨")
_SIGNED = ("✍️ SIGNED | We are delighted to announce the signing of {player} from {other}. Welcome to {club}!",
           "✍️ Here he is! {player} joins from {other}. Welcome, {first}! 🤝",
           "✍️ DONE DEAL | {player} completes his move from {other}.")
_SIGNED_FREE = ("✍️ SIGNED | {player} joins us on a free transfer. Welcome to {club}!",
                "✍️ Welcome, {first}! {player} joins on a free transfer. 🤝")
_LOAN_IN = ("✍️ {player} joins on loan from {other} for the season. Welcome, {first}! 🤝",
            "✍️ LOAN SIGNING | {player} arrives from {other}.")
_SOLD = ("👋 {player} has joined {other}. Thank you for everything, {first}, and good luck!",
         "👋 Farewell, {first}. {player} completes his move to {other}. Thank you for your service.",
         "{player} has joined {other}. All the best, {first}! 💙")
_LOAN_OUT = ("{player} has joined {other} on loan. Good luck, {first}! 👍", "📋 {player} heads to {other} on loan.")
_SACKED = ("{club} can confirm that {person} has left the club. Thank you, {first}, and all the best for the future.",
           "📋 CLUB STATEMENT | {person} has been relieved of duties as manager.")
_RESIGNED = ("{person} has resigned as manager. Thank you, {first}, and good luck for the future.",
             "📋 CLUB STATEMENT | {person} has stepped down as manager.")
_LEFT = ("{person} has left the role of manager. Thank you, {first}.",)
_BRACE_WON = ("Two goals, three points. What a night! ⚽⚽", "Brace ✌️⚽ Big team performance, thanks for the support!",
              "Happy to help the team with two today 🙏⚽⚽")
_BRACE_NOT = ("Two goals but not the result we wanted. We go again. 👊", "Happy to score, gutted with the result. Onto the next one.")
_HAT_TRICK = ("Match ball is mine! ⚽⚽⚽ Thank you all 🙏", "Hat-trick! Dreams do come true ⚽⚽⚽",
              "Three goals, one team. Couldn't do it without the lads 💪⚽⚽⚽")
_PLAYER_SIGNED = ("Dream come true! Proud to sign for {club} ✍️ Can't wait to get started.",
                  "New chapter. 💪 Thank you to everyone at {other} for everything. Let's go, {club}!",
                  "Home. ✍️ Can't wait to meet the fans.", "Buzzing to be here! Thank you for the welcome, {club} 🙏")
_LEGEND_SOLD = ("💔 Some goodbyes are harder than others. {player} has joined {other}. Forever one of us, {first}.",
                "👋 A legend moves on. {player} joins {other}. Thank you for everything, {first}. You'll always be one of us.")
_WELCOME_HOME = ("🏠 Welcome home, {first}! {player} is back where he belongs.", "🏠 HE'S BACK. {player} returns home. Welcome back, {first}!")
_SCOOP = ("🚨 DONE DEAL: {player} joins {buyer} from {seller}. Fee: {fee}.", "✅ CONFIRMED: {player} to {buyer}, {fee} paid to {seller}.",
          "🚨 {buyer} complete the signing of {player} from {seller} for {fee}.")
# A club posts an injury only when it matters: one of its best-known players (KEY_PLAYERS, by world reputation) out
# for KEY_INJURY_DAYS or more, anyone out for LONG_INJURY_DAYS or more, or for the followed clubs FOLLOWED_INJURY_DAYS;
# at most INJURY_POSTS_A_DAY in the whole feed, the biggest first. A feed of every blister read like an obituary page.
KEY_PLAYERS = 5
KEY_INJURY_DAYS, LONG_INJURY_DAYS, FOLLOWED_INJURY_DAYS = 21, 42, 14
INJURY_POSTS_A_DAY = 3
PLAYER_INJURY_DAYS = 28  # and from the player himself
_INJURY = ("🏥 Injury update: {player} has suffered {a_injury} and is expected to be out for {time}.",
           "🏥 {player} picked up {a_injury} and faces around {time} on the sidelines. Get well soon, {first}! 💙",
           "Medical update | {player} ({injury}) is expected to miss {time}.")
_PLAYER_INJURED = ("Gutted to be out for a while, but I'll be back stronger. Thanks for all the messages 🙏💪",
                   "Not the news I wanted. Time to work hard and come back better than before. 💪",
                   "Tough one to take. The road back starts today. See you soon! 🙏")
_APPOINTED = ("🤝 We are delighted to confirm the appointment of {person} as our new manager. Welcome, {first}!",
              "📋 OFFICIAL | {person} is our new manager. Welcome to {club}!",
              "🤝 Welcome, gaffer! {person} takes charge.")


def _rng(key: str) -> random.Random:
    return random.Random(key)


def _first(name: str) -> str:
    return name.split()[0] if name else name


def _minute(goal: dict) -> str:
    return f"{goal['minute']}+{goal['added_time']}'" if goal["added_time"] else f"{goal['minute']}'"


def _scorers(goals: list[dict]) -> tuple[str, list]:
    """"Mutiu 12', 67' · Jones 80' (pen)" and its links."""
    by_scorer: dict = defaultdict(list)
    names: dict = {}
    for goal in goals:
        scorer = goal["scorer_uid"] if goal["kind"] != 1 else "og"
        by_scorer[scorer].append(_minute(goal) + (" (pen)" if goal["kind"] == 2 else ""))
        names[scorer] = "Own goal" if scorer == "og" else (goal["name"] or "Unknown").split()[-1]
    parts, links = [], []
    for scorer, minutes in by_scorer.items():
        parts.append(f"{names[scorer]} {', '.join(minutes)}")
        if scorer not in ("og", None) and names[scorer] != "Unknown":
            links.append([names[scorer], "person", scorer])
    return " · ".join(parts), links


def result_events(connection: Connection, since: date, until: date, clubs: list[int], names: dict[int, dict]) -> list[Event]:
    """The clubs' competitive results: no friendlies, nor games of a competition the save does not list (a pre-season
    tournament)."""
    wanted = set(clubs)
    marks = ",".join(str(int(u)) for u in wanted) or "0"
    games = connection.execute(text(
        f"select f.*, coalesce(c.short_name, c.name) competition from fixtures f join competitions c on c.fm_uid = f.competition_uid "
        f"where f.date > :s and f.date <= :u and f.home_goals is not null and c.fm_uid is not null and coalesce(c.scope, '') != 'friendly' "
        f"and (f.home_uid in ({marks}) or f.away_uid in ({marks})) order by f.date, f.id"), {"s": str(since), "u": str(until)}).mappings().all()
    events = []
    for game in games:
        goals = [dict(g) for g in connection.execute(text(
            "select g.*, p.name from goals g left join people p on p.fm_uid = g.scorer_uid where g.fixture_id = :f and g.kind in (0, 1, 2) "
            "order by g.seq"), {"f": game["id"]}).mappings()]
        home, away = names.get(game["home_uid"]), names.get(game["away_uid"])
        if not home or not away:
            continue
        extra = game["home_goals_after_extra_time"] is not None
        hg, ag = (game["home_goals_after_extra_time"], game["away_goals_after_extra_time"]) if extra else (game["home_goals"], game["away_goals"])
        line = f"FT | {home['short_name']} {hg}–{ag} {away['short_name']}" + (" (AET)" if extra else "")
        competition_tag = tag_for(game["competition"] or "")
        if game["home_penalties"] is not None:
            line += f", {game['home_penalties']}–{game['away_penalties']} on penalties"
        day = date.fromisoformat(str(game["date"])[:10])
        match = f"{day}:{game['home_uid']}:{game['away_uid']}"
        from app.services import world_queries as q

        report = q.match_summary(connection, game["id"])  # the game's report, when the save brought one
        for side, club in ((0, game["home_uid"]), (1, game["away_uid"])):
            if club not in wanted:
                continue
            ours, theirs = (hg, ag) if side == 0 else (ag, hg)
            rng = _rng(f"result:{match}:{club}")
            other_club = game["away_uid"] if side == 0 else game["home_uid"]
            gap = names[club]["reputation"] - names[other_club]["reputation"]
            heat = 10 + (35 if rivalry_level(connection, club, other_club) >= DERBY_LEVEL else 0)
            heat += 30 if ours < theirs and gap >= UPSET_GAP else 20 if ours > theirs and -gap >= UPSET_GAP else 0
            heat += 10 if abs(ours - theirs) >= 4 else 0
            if report:  # what happened in it: red cards, a late winner, a robbery, injuries
                mine, others = report["sides"][side], report["sides"][1 - side]
                heat += min(20, 10 * (len(mine.get("red_cards", [])) + len(others.get("red_cards", []))))
                heat += 20 if "late_winner" in report else 0
                robbed = report.get("won_against_the_run_of_play")
                heat += (10 if robbed == mine.get("team") else 25) if robbed else 0
                heat += min(10, 5 * len(mine.get("injured_in_the_match", [])))
            if game["home_penalties"] is not None:
                heat += 15
                won = (game["home_penalties"] > game["away_penalties"]) == (side == 0)
                mood, size = rng.choice(_PENS_WON if won else _PENS_LOST), 1.3 if won else 0.6
            elif ours > theirs:
                mood, size = rng.choice(_BIG_WIN if ours - theirs >= 3 else _WIN), 1.3 if ours - theirs >= 3 else 1.0
            elif ours == theirs:
                mood, size = rng.choice(_DRAW), 0.6
            else:
                mood, size = rng.choice(_LOSS), 0.45
            ours_goals = [g for g in goals if g["side"] == side]
            scored, links = _scorers(ours_goals)
            tags = " ".join(f"#{t}" for t in (club_tag(names[club]), competition_tag) if t)
            body = "\n".join(part for part in (f"{line} · {game['competition']}" if game["competition"] else line,
                                               f"⚽ {scored}" if scored else "", f"{mood} {tags}") if part)
            other = away if side == 0 else home
            events.append(Event(f"result:{match}", "result", day, club, body,
                                [[other["short_name"], "club", other["fm_uid"]], *links, ["", "match", match]], size, heat=_hot(heat)))
            for scorer, count in Counter(g["scorer_uid"] for g in ours_goals if g["kind"] != 1 and g["scorer_uid"]).items():
                if count < 2:
                    continue
                name = next(g["name"] for g in ours_goals if g["scorer_uid"] == scorer)
                if not name:
                    continue
                key = f"goals:{match}:{scorer}"
                choices = _HAT_TRICK if count >= 3 else _BRACE_WON if ours > theirs else _BRACE_NOT
                events.append(Event(key, "player_goals", day, club, f"{_rng(key).choice(choices)} #{club_tag(names[club])}",
                                    [["", "match", match]], 1.2 if count >= 3 else 0.8, ("player", scorer, name), _hot(heat)))
    return events


def transfer_events(connection: Connection, since: date, until: date, clubs: list[int]) -> list[Event]:
    wanted = set(clubs)
    moves = connection.execute(text(
        "select t.*, p.name from transfers t join people p on p.fm_uid = t.person_uid "
        "where t.date > :s and t.date <= :u and t.kind in (1, 2, 3, 4, 6) order by t.date, t.fee desc"),
        {"s": str(since), "u": str(until)}).mappings().all()
    moves = [m for m in moves if m["from_uid"] in wanted or m["to_uid"] in wanted or m["fee"] >= BIG_FEE]
    names = _clubs(connection, {m["from_uid"] for m in moves} | {m["to_uid"] for m in moves})
    events = []
    for move in moves:
        day = date.fromisoformat(str(move["date"])[:10])
        key = f"transfer:{move['person_uid']}:{day}"
        size = 1.0 + min(move["fee"] / 20_000_000, 4.0)
        player, first = move["name"], _first(move["name"])
        to_rival = rivalry_level(connection, move["to_uid"], move["from_uid"]) >= DERBY_LEVEL  # sold to the enemy
        heat = _hot(min(move["fee"] / 1_000_000, 40) + (45 if to_rival else 0)) if move["kind"] != LOAN else _hot(5 + (30 if to_rival else 0))
        old_love = standing(connection, move["person_uid"], move["from_uid"]) if move["from_uid"] else None
        new_feeling = standing(connection, move["person_uid"], move["to_uid"])
        heat = _hot(heat + _STANDING_HEAT.get(old_love, 0) + (DISLIKED_HEAT if new_feeling == "Disliked" else 0)
                    + (30 if new_feeling in ("Legend", "Icon") else 0))  # a legend leaving, a hated man arriving, a hero coming home
        buyer, seller = names.get(move["to_uid"]), names.get(move["from_uid"])
        if buyer and (move["to_uid"] in wanted or move["fee"] >= BIG_FEE):
            choices = _LOAN_IN if move["kind"] == LOAN else _SIGNED_FREE if move["kind"] == 4 or not seller else _SIGNED
            if new_feeling in ("Legend", "Icon") and move["kind"] != LOAN:
                choices = _WELCOME_HOME
            body = _rng(key + ":in").choice(choices).format(player=player, first=first, club=buyer["short_name"],
                                                            other=seller["short_name"] if seller else "")
            links = [[player, "person", move["person_uid"]]] + ([[seller["short_name"], "club", seller["fm_uid"]]] if seller else [])
            body += f" #{club_tag(buyer)}"
            events.append(Event(key, "loan_in" if move["kind"] == LOAN else "signing", day, buyer["fm_uid"], body, links,
                                size * (0.6 if move["kind"] == LOAN else 1.0), heat=heat))
            if move["kind"] != LOAN and move["fee"] >= PLAYER_FEE:
                said = _rng(key + ":player").choice(_PLAYER_SIGNED).format(club=buyer["short_name"],
                                                                          other=seller["short_name"] if seller else "my old club")
                events.append(Event(key, "player_signing", day, buyer["fm_uid"], f"{said} #{club_tag(buyer)}",
                                    [[buyer["short_name"], "club", buyer["fm_uid"]]], size, ("player", move["person_uid"], player), heat))
        if seller and buyer and (move["from_uid"] in wanted or move["fee"] >= BIG_FEE):
            farewell = _LOAN_OUT if move["kind"] == LOAN else _LEGEND_SOLD if old_love in ("Legend", "Icon") else _SOLD
            body = _rng(key + ":out").choice(farewell).format(
                player=player, first=first, club=seller["short_name"], other=buyer["short_name"])
            events.append(Event(key, "loan_out" if move["kind"] == LOAN else "sale", day, seller["fm_uid"], body,
                                [[player, "person", move["person_uid"]], [buyer["short_name"], "club", buyer["fm_uid"]]], size * 0.5,
                                heat=_hot(heat * 1.3)))  # the selling side's fans feel it most
        if seller and buyer and move["kind"] != LOAN and move["fee"] >= SCOOP_FEE:
            fee = f"£{move['fee'] / 1_000_000:.1f}m"
            said = _rng(key + ":scoop").choice(_SCOOP).format(player=player, buyer=buyer["short_name"], seller=seller["short_name"], fee=fee)
            events.append(Event(key, "scoop", day, buyer["fm_uid"], f"{said} #{club_tag(buyer)} #TransferNews",
                                [[player, "person", move["person_uid"]], [buyer["short_name"], "club", buyer["fm_uid"]],
                                 [seller["short_name"], "club", seller["fm_uid"]]], size, ("outlet", SCOOP_OUTLET), heat))
    return events


def title_events(connection: Connection, since: date, until: date, clubs: list[int], names: dict[int, dict]) -> list[Event]:
    rows = connection.execute(text(
        "select e.competition_uid, e.season_label, e.decided_on, p.team_uid, c.name competition from competition_editions e "
        "join edition_places p on p.competition_uid = e.competition_uid and p.year = e.year and p.place = 1 "
        "join competitions c on c.fm_uid = e.competition_uid where e.decided_on > :s and e.decided_on <= :u"),
        {"s": str(since), "u": str(until)}).mappings().all()
    events = []
    for row in rows:
        club = names.get(row["team_uid"])
        if row["team_uid"] not in clubs or not club:
            continue
        key = f"title:{row['competition_uid']}:{row['season_label']}"
        body = f"{_rng(key).choice(_TITLE)}\n{row['competition']} winners {row['season_label']}! 🎉 #{club_tag(club)} #Champions"
        events.append(Event(key, "title", date.fromisoformat(str(row["decided_on"])[:10]), row["team_uid"], body, [], 4.0, heat=40))
    return events


def manager_events(connection: Connection, since: date, until: date, clubs: list[int], names: dict[int, dict]) -> list[Event]:
    marks = ",".join(str(int(u)) for u in clubs) or "0"
    events = []
    for row in connection.execute(text(
            f"select s.team_uid, s.person_uid, s.ended, s.reason, p.name from manager_spells s join people p on p.fm_uid = s.person_uid "
            f"where s.ended > :s and s.ended <= :u and s.team_uid in ({marks}) and s.reason in (0, 2, 3, 6)"),
            {"s": str(since), "u": str(until)}).mappings():
        club = names.get(row["team_uid"])
        if not club:
            continue
        key = f"manager_out:{row['person_uid']}:{row['ended']}"
        choices = _SACKED if row["reason"] == SACKED else _RESIGNED if row["reason"] == RESIGNED else _LEFT
        body = _rng(key).choice(choices).format(person=row["name"], first=_first(row["name"]), club=club["short_name"])
        events.append(Event(key, "manager_out", date.fromisoformat(str(row["ended"])[:10]), row["team_uid"], body,
                            [[row["name"], "person", row["person_uid"]]], 1.6, heat=60 if row["reason"] == SACKED else 35))
    for row in connection.execute(text(
            f"select fm_uid, name, staff_team_uid, staff_since from people where staff_job = 'Manager' and staff_since > :s "
            f"and staff_since <= :u and staff_team_uid in ({marks})"), {"s": str(since), "u": str(until)}).mappings():
        club = names.get(row["staff_team_uid"])
        if not club:
            continue
        key = f"manager_in:{row['fm_uid']}:{row['staff_since']}"
        body = _rng(key).choice(_APPOINTED).format(person=row["name"], first=_first(row["name"]), club=club["short_name"])
        events.append(Event(key, "manager_in", date.fromisoformat(str(row["staff_since"])[:10]), row["staff_team_uid"], body,
                            [[row["name"], "person", row["fm_uid"]]], 1.8, heat=30))
    return events


def _time(days: int) -> str:
    return f"{days} days" if days < 14 else f"{round(days / 7)} weeks" if days < 90 else f"{round(days / 30)} months"


def _a(injury: str) -> str:
    """'a twisted ankle', 'an ankle injury', but 'broken ribs' and 'sprained ankle ligaments'."""
    last = injury.split()[-1] if injury else ""
    if last.endswith("s") and not last.endswith(("ss", "is", "us")):
        return injury
    return ("an " if injury[:1] in "aeiou" else "a ") + injury


def injury_events(connection: Connection, since: date, until: date, clubs: list[int], names: dict[int, dict],
                  followed: tuple[int, ...] = ()) -> list[Event]:
    """The clubs' injury updates for the injuries that matter, begun in the window (see KEY_PLAYERS); after a serious
    one the player posts too."""
    marks = ",".join(str(int(u)) for u in clubs) or "0"
    found = []
    for row in connection.execute(text(
            f"select * from (select i.person_uid, i.injury, i.injured_on, i.days, p.name, coalesce(t.club_uid, p.team_uid) club, "
            f"rank() over (partition by p.team_uid order by coalesce(p.reputation_world, 0) desc) standing from people p "
            f"left join teams t on t.fm_uid = p.team_uid left join injuries i on i.person_uid = p.fm_uid and i.current = 1 "
            f"and i.injured_on > :s and i.injured_on <= :u where p.staff_job is null and t.age_limit is null "
            f"and coalesce(t.club_uid, p.team_uid) in ({marks})) where person_uid is not null"),
            {"s": str(since), "u": str(until)}).mappings():
        key_player = row["standing"] <= KEY_PLAYERS
        mine = row["club"] in followed
        if not (row["days"] >= LONG_INJURY_DAYS or (key_player and row["days"] >= KEY_INJURY_DAYS)
                or (mine and row["days"] >= FOLLOWED_INJURY_DAYS)):
            continue
        if names.get(row["club"]):
            found.append((mine, key_player, row))
    by_day: dict[date, list] = {}
    for mine, key_player, row in found:
        by_day.setdefault(date.fromisoformat(str(row["injured_on"])[:10]), []).append((mine, key_player, row))
    events = []
    for day, rows in by_day.items():
        for _, _, row in sorted(rows, key=lambda x: (not x[0], not x[1], -x[2]["days"]))[:INJURY_POSTS_A_DAY]:
            club = names[row["club"]]
            key = f"injury:{row['person_uid']}:{day}"
            said = _rng(key).choice(_INJURY).format(player=row["name"], first=_first(row["name"]), injury=row["injury"],
                                                    a_injury=_a(row["injury"]), time=_time(row["days"]))
            size = 0.8 + min(row["days"] / 30, 3.0)
            heat = _hot(min(row["days"], 90) / 3)
            events.append(Event(key, "injury", day, row["club"], f"{said} #{club_tag(club)}", [[row["name"], "person", row["person_uid"]]],
                                size, heat=heat))
            if row["days"] >= PLAYER_INJURY_DAYS:
                events.append(Event(key, "player_injury", day, row["club"], _rng(key + ":player").choice(_PLAYER_INJURED), [], size,
                                    ("player", row["person_uid"], row["name"]), heat))
    return events


def events(connection: Connection, since: date, until: date, followed: tuple[int, ...] = ()) -> list[Event]:
    clubs = feed_clubs(connection, followed)
    names = _clubs(connection, set(clubs) | {u for r in connection.execute(text(
        "select home_uid, away_uid from fixtures where date > :s and date <= :u"), {"s": str(since), "u": str(until)}) for u in r})
    return (result_events(connection, since, until, clubs, names) + transfer_events(connection, since, until, clubs)
            + title_events(connection, since, until, clubs, names) + manager_events(connection, since, until, clubs, names)
            + injury_events(connection, since, until, clubs, names, followed))


# -- posting ----------------------------------------------------------------------------------------------
def _reach(reputation: int, size: float, key: str) -> tuple[int, int]:
    """Likes and reposts: the club's fame squared, the event's size, and chance."""
    rng = _rng(key + ":reach")
    likes = int((reputation / 10_000) ** 3 * 30_000 * size * rng.lognormvariate(0, 0.35)) + rng.randint(3, 40)
    return likes, int(likes * rng.uniform(0.06, 0.18))


def post_events(found: list[Event], names: dict[int, dict]) -> int:
    made = 0
    for event in found:
        club = names.get(event.club)
        if club is None:
            continue
        reputation = club["reputation"]
        if event.author and event.author[0] == "player":
            account = player_account(event.author[1], event.author[2], event.club)
            reputation = int(reputation * 0.9)
        elif event.author and event.author[0] == "outlet":
            account = outlet_account(event.author[1])
            if account is None:
                continue
            reputation = 8000
        else:
            account = club_account(event.club, club)
        if SocialPost.query.filter_by(account_id=account.id, event_key=event.key).first():
            continue
        likes, reposts = _reach(reputation, event.size * (1 + event.heat / 100), f"{event.key}:{account.handle}")
        db.session.add(SocialPost(account_id=account.id, posted_on=event.day, kind=event.kind, event_key=event.key,
                                  text=event.text, links=event.links, likes=likes, reposts=reposts, heat=event.heat))
        made += 1
    return made


def make_posts(until: date | None = None, progress: Callable[[str], None] = lambda text: None) -> int:
    """The clubs' posts since the feed last posted (or the last FIRST_DAYS days), up to the game date. Nothing when
    the feed is turned off."""
    from app.services import world_queries as q
    from app.services.story_leads import followed_clubs

    if not enabled():
        return 0
    with db.engines[world_models.BIND].connect() as connection:
        until = until or q.game_date(connection)
        if until is None:
            return 0
        stored = AppSetting.query.filter_by(key=UNTIL_KEY).first()
        since = date.fromisoformat(stored.value) if stored else until - timedelta(days=FIRST_DAYS)
        found = events(connection, since, until, followed_clubs()) if since < until else []
        names = _clubs(connection, {e.club for e in found})
    made = post_events(found, names)
    if stored is None:
        db.session.add(AppSetting(key=UNTIL_KEY, value=until.isoformat()))
    elif since < until:
        stored.value = until.isoformat()
    db.session.commit()
    # The matchday posts look ahead, and the predictions are checked against results: both even with no new days.
    from app.services import social_predictions  # it uses the fans, which use this module

    with db.engines[world_models.BIND].connect() as connection:
        made += social_predictions.called_it(connection) + social_predictions.post_matchdays(connection, until, followed_clubs())
    progress(f"The clubs posted {made} times")
    return made


def post_articles(issue_ids: list[int]) -> int:
    """Each new edition's lead story, posted by its writer (the paper's own account for an article without one)."""
    from app.models import WorldSoccerIssue
    from app.services import editions

    if not enabled():
        return 0
    made = 0
    for issue in (db.session.get(WorldSoccerIssue, i) for i in issue_ids):
        outlet = editions.outlet_of(issue) if issue else None
        if outlet is None or issue.status != "published" or not issue.articles:
            continue
        article = min(issue.articles, key=lambda a: (a.sort_order, a.id))
        account = writer_account(article.writer) if article.writer else outlet_account(outlet.slug)
        if account is None or SocialPost.query.filter_by(account_id=account.id, event_key=f"article:{article.id}").first():
            continue
        rng = _rng(f"article:{article.id}")
        opener = rng.choice(("NEW 📰", "My latest 👇", "Out now in " + outlet.name + " 📰", "Today's lead 🗞️"))
        text_ = f"{opener} {article.headline}" + (f"\n{article.standfirst}" if article.standfirst else "")
        likes = int(rng.lognormvariate(7.5, 0.6))
        db.session.add(SocialPost(account_id=account.id, posted_on=issue.publication_date, kind="article",
                                  event_key=f"article:{article.id}", text=text_,
                                  links=[["", "article", f"{outlet.slug}:{issue.id}:{article.id}"]],
                                  likes=likes, reposts=int(likes * rng.uniform(0.05, 0.15))))
        made += 1
    db.session.commit()
    return made
