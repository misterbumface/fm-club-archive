"""The fans of the social feed (docs/social-plan.md, step 2): lasting fan accounts, and the threads they write under
the clubs' posts.

Each club that gets a thread has FANS_PER_CLUB fans, made once and kept: a first name from the club's own country (the
save's people), a handle and a temper (TEMPERS), which the writer plays. A thread is one AI call: the club's post and
the facts around it (both clubs' recent results, their last meetings, the rivalry the save records) in a notebook,
and fans of the club, of the other club and of a rival in the cast. The answer is checked as the articles are
(years, scores and large numbers must be in the notebook): the faulty replies are asked for again once, then dropped.

Threads are written on request (a button on the Feed), in the background; and, while the app runs and Ollama
answers, by the slow writer: one thread every few minutes with a local model, which costs nothing, up to
SLOW_LIMIT threads for the posts of the last RECENT_DAYS days (trickle_once, started by the Mac app's server).
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Connection

from app.extensions import db
from app.models import world as world_models
from app.models.social import FAN, PLAYER, SocialAccount, SocialPost
from app.services import moods, social_feed
from app.services import world_queries as q
from app.services.ai_settings import get_deepseek_model, resolve_ai_api_key
from app.services.story_writer import StoryError, hard_faults, request_json, text_faults

FANS_PER_CLUB = 6
THREADS = 8  # candidates looked at by default
REPLIES = (5, 8)  # replies in a thread, at least and at most
REPLY_LENGTH = 240  # characters
TEMPERATURE = 0.9
RECENT_DAYS = 10  # club posts newer than this, before the newest post, can get a thread
AGE_HALF_LIFE = 4  # a post's claim to a thread halves with every this many days of age: this week's news first
# The club posts the fans answer, and how much more than the likes alone say: a title or a new manager is worth a
# thread before a routine win; a loan of a young player rarely is.
THREAD_KINDS = {"title": 4.0, "manager_out": 3.0, "manager_in": 3.0, "result": 1.0, "signing": 1.5, "sale": 1.0,
                "loan_in": 0.3, "loan_out": 0.2, "player_signing": 1.5, "player_goals": 1.2, "scoop": 1.2, "injury": 1.6,
                "player_injury": 1.4}
HOT = 50  # a post this hot (social_feed heat) gets a longer thread and more rival fans
HOT_REPLIES = (8, 12)
RIVALS = 3  # a rival's fan comes from one of the club's fiercest rivals
FOLLOWED_BOOST = 5  # the followed clubs' posts count this many times their likes

TEMPERS: dict[str, tuple[str, str]] = {  # key: (label, how the fan writes)
    "optimist": ("Eternal optimist", "Every signing is world class, every draw a good point, and the title is coming. Hearts and "
                                     "exclamation marks."),
    "doomer": ("Doom-monger", "Sees the end of the club in every bad result and stays gloomy, sarcastic even after a win."),
    "nerd": ("Numbers nerd", "Talks shape, pressing and the facts in the notebook; dry humour, never shouts."),
    "oldtimer": ("Old-timer", "Has followed the club for decades, compares everything with the old days; grumpy but loving."),
    "banter": ("Wind-up merchant", "Lives to mock the other club's fans and rivals: laughing faces, caps lock, nicknames."),
    "ultra": ("Ultra", "Passion of the terraces: chants, flags, loyalty, ALL CAPS when excited; defends the club against all."),
    "young": ("Always online", "Young and chronically online: memes and slang ('he's HIM', 'cooked', 'no shot', 'ratio')."),
    "critic": ("The critic", "Blames the manager or the board for everything; '... OUT' is a favourite."),
}

_HANDLES = ("{club}Til1Die", "{first}{club}{yy}", "Proper{club}", "{club}Faithful", "{club}Ultra{n}", "{first}_{club}",
            "Old{club}Head", "{club}Mad{first}", "{first}{yy}{club}", "Mighty{club}{n}", "{club}Thru{first}")

PLAYER_VOICE = ("The player himself, media-trained: humble, thanks the fans and the team, speaks in footballers' "
                "clichés, never bites back hard; he writes one reply at most.")

PROMPT = (
    "You write a thread on a football social network in a world of a Football Manager save. The notebook (JSON) holds "
    "the post that starts the thread, the facts around it, and the cast: fans with a handle, a club and a temper. Write "
    "{low} to {high} replies from the cast, as those people would really post: short (at most {length} characters), in "
    "English, each in its own voice and temper, with emojis where that fan would use them. They answer the post or one "
    "another (reply_to is the handle answered, or null for the post): fans of other clubs and of rivals wind them up, "
    "and they bite back. Each result in the notebook says what it was for, how long before the post it was and whether "
    "it was last season: speak of it that way (a defeat of last season is not 'last week'). "
    "Opinions can be wild, unfair and funny, but every fact must be in the notebook: no other "
    "scores, results, years, numbers (in figures or in words), players, games or clubs; never invent who scored, what a "
    "player did or where he played: 'people' holds all that is known about the people in the post; standing_with_clubs says "
    "which clubs count a person a Legend, an Icon or Favoured, or dislike him, and the fans of those clubs feel it (a legend "
    "sold is heartbreak, worse to a rival; a disliked man arriving is an outrage). No insults about anyone's origin, "
    "religion or body, no threats, no slurs. A fan may add one of the notebook's hashtags. If the notebook has "
    "predictions (what fans said before the match, and how they did), they remember: the right ones gloat, the wrong "
    "ones get reminded of their score, and they bite back. If the notebook has the_match (the game's report: the player "
    "of the match, xG, shots, possession, woodwork, red cards, injuries, the best and worst rated, a late winner, a win "
    "against the run of play), argue about what happened in it: the referee, the robbery, the miss, the hero, the "
    "flop. Answer with JSON only: "
    '{{"replies": [{{"handle": "...", "reply_to": null, "text": "..."}}]}}.'
)


# -- the fans ---------------------------------------------------------------------------------------------
def _plain(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode())


def _club_word(short_name: str) -> str:
    """The club's word in a fan's handle: "Chelsea", "Boca", "ManCity", not "AthleticoMadrid"."""
    words = [w for w in (_plain(w) for w in short_name.split()) if len(w) > 1 and w.upper() not in ("FC", "AFC", "CF", "SC", "AC", "CA")]
    word = (words[0] if words else _plain(short_name)) or "Club"
    for more in words[1:]:  # "Man" alone is no club: "ManCity", "RealMadrid", "AstonVilla"
        if len(word) >= 6:
            break
        word += more
    return word if len(word) <= 14 else word[:14]


def _first_names(connection: Connection, club_uid: int) -> list[str]:
    """First names of the club's own country, from the save's people."""
    rows = connection.execute(text(
        "select p.first_name from people p join clubs c on c.fm_uid = :c where p.nation_uid = c.nation_uid "
        "and p.first_name is not null and length(p.first_name) between 3 and 10 order by random() limit 60"), {"c": club_uid}).all()
    names = list(dict.fromkeys(r[0] for r in rows if " " not in r[0]))
    return names or ["Sam", "Alex", "Chris", "Jo", "Danny", "Mo", "Ricky", "Lou"]


def fans_of(connection: Connection, club_uid: int) -> list[SocialAccount]:
    """The club's fans, made the first time they are needed and kept."""
    fans = SocialAccount.query.filter_by(kind=FAN, club_uid=club_uid).order_by(SocialAccount.id).all()
    if len(fans) >= FANS_PER_CLUB:
        return fans
    club = connection.execute(text("select coalesce(c.short_name, t.name), t.name from teams t left join clubs c "
                                   "on c.fm_uid = t.club_uid where t.fm_uid = :c"), {"c": club_uid}).first()
    if club is None:
        return fans
    rng = random.Random(club_uid)
    word, names = _club_word(club[1] if "." in club[0] else club[0]), _first_names(connection, club_uid)  # not "A. Madrid"
    tempers = rng.sample(list(TEMPERS), len(TEMPERS))
    taken = {f.persona.get("temper") for f in fans if f.persona}
    for temper in [t for t in tempers if t not in taken][:FANS_PER_CLUB - len(fans)]:
        first = _plain(rng.choice(names)) or "Fan"
        for _ in range(20):
            handle = rng.choice(_HANDLES).format(club=word, first=first, yy=f"{rng.randint(0, 99):02d}", n=rng.randint(1, 99))[:30]
            if not SocialAccount.query.filter_by(handle=handle).first():
                break
        else:
            handle = f"{word}Fan{rng.randint(100, 9999)}"
        fan = SocialAccount(handle=handle, name=f"{first} {rng.choice('ABCDEFGHJKLMNPRSTW')}.", kind=FAN, club_uid=club_uid,
                            persona={"temper": temper, "follows": rng.randint(40, 4000)})
        if not _saved(fan):
            # another writer made this club's fans at the same moment (the club's seed gives it the same handles):
            # use the fans it made, or this fan under a handle nobody has
            made = SocialAccount.query.filter_by(kind=FAN, club_uid=club_uid).order_by(SocialAccount.id).all()
            if len(made) >= FANS_PER_CLUB:
                return made
            fan.handle = f"{word}Fan{random.randint(10_000, 99_999)}"
            if not _saved(fan):
                continue
        fans.append(fan)
    return fans


def _saved(account: SocialAccount) -> bool:
    """Save a new account; False when its handle is taken (the session goes on as before)."""
    try:
        with db.session.begin_nested():
            db.session.add(account)
    except IntegrityError:
        return False
    return True


# -- the notebook -----------------------------------------------------------------------------------------
def _clubs_in(connection: Connection, post: SocialPost) -> list[int]:
    """The posting club, then the other clubs of the post: a reserve or youth team counts as its club (fans support
    Hertha, not Hertha's under-23s)."""
    clubs = [post.account.club_uid] if post.account.club_uid else []  # a paper's post: the clubs it names
    for _, kind, uid in post.links or []:
        if kind == "club":
            club = connection.execute(text("select coalesce(club_uid, fm_uid) from teams where fm_uid = :u"), {"u": uid}).scalar()
            if club and club not in clubs:
                clubs.append(club)
    return clubs


_RESULT_COLUMNS = ("select f.date, h.name, a.name, f.home_goals, f.away_goals, f.home_goals_after_extra_time, "
                   "f.away_goals_after_extra_time, f.home_penalties, f.away_penalties, c.name from fixtures f "
                   "join teams h on h.fm_uid = f.home_uid join teams a on a.fm_uid = f.away_uid ")


def _season(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


def _ago(day: date, post: date) -> str:
    days = (post - day).days
    if days <= 1:
        return "the day of the post" if days <= 0 else "the day before the post"
    if days < 14:
        return f"{days} days before the post"
    if days < 60:
        return f"{days // 7} weeks before the post"
    return f"{days // 30} months before the post"


def _result_line(row, post: date) -> str:
    """"16 May 2063, English Premier Division, 2 months before the post, last season: Fulham 2-0 Chelsea": the fans
    must know how old a result is and what it was for, or a defeat of May reads like last week's."""
    day = date.fromisoformat(str(row[0])[:10])
    home, away, hg, ag, het, aet, hp, ap, competition = row[1:]
    score = f"{het}-{aet} after extra time" if het is not None else f"{hg}-{ag}"
    if hp is not None:
        score += f", {hp}-{ap} on penalties"
    when = _ago(day, post) + (", last season" if _season(day) < _season(post) else "")
    return f"{day.day} {day:%B %Y}, {competition or 'a match'}, {when}: {home} {score} {away}"


def _recent(connection: Connection, club: int, before: date, limit: int = 5) -> list[str]:
    rows = connection.execute(text(
        _RESULT_COLUMNS + "join competitions c on c.fm_uid = f.competition_uid where (f.home_uid = :c or f.away_uid = :c) "
        "and f.home_goals is not null and f.date <= :d and coalesce(c.scope, '') != 'friendly' order by f.date desc limit :n"),
        {"c": club, "d": str(before), "n": limit}).all()
    return [_result_line(row, before) for row in rows]


def _meetings(connection: Connection, one: int, other: int, before: date) -> list[str]:
    rows = connection.execute(text(
        _RESULT_COLUMNS + "left join competitions c on c.fm_uid = f.competition_uid where ((f.home_uid = :a and f.away_uid = :b) "
        "or (f.home_uid = :b and f.away_uid = :a)) and f.home_goals is not null and f.date <= :d order by f.date desc limit 4"),
        {"a": one, "b": other, "d": str(before)}).all()
    return [_result_line(row, before) for row in rows]


_PLACES = {1: "won", 2: "runners-up in", 3: "third in"}


def _honours(connection: Connection, club: int, before: date) -> list[str]:
    """How the club's competitions of the last twelve months ended for it: won, runners-up, third."""
    rows = connection.execute(text(
        "select p.place, c.name, e.season_label, e.decided_on from edition_places p join competition_editions e "
        "on e.competition_uid = p.competition_uid and e.year = p.year join competitions c on c.fm_uid = p.competition_uid "
        "where p.team_uid = :c and p.place <= 3 and e.decided_on <= :d and e.decided_on > date(:d, '-365 days') "
        "order by e.decided_on desc"), {"c": club, "d": str(before)}).all()
    return [f"{_PLACES[place]} {competition} {season}" for place, competition, season, _ in rows]


def _rival(connection: Connection, club: int, besides: set[int], rng: random.Random) -> int | None:
    """One of the club's fiercest rivals in the save, other than the clubs already in the thread."""
    rivals = [uid for (uid,) in connection.execute(text("select subject_uid from club_relations where club_uid = :c "
                                                        "and relation = 'rival' order by level desc limit 8"), {"c": club})
              if uid not in besides]
    return rng.choice(rivals[:RIVALS]) if rivals else None


def _age(born, day: date) -> int | None:
    if not born:
        return None
    born = date.fromisoformat(str(born)[:10])
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def _person(connection: Connection, uid: int, day: date) -> dict | None:
    """What the fans may know of a person in the post: age, position, recent seasons, a fee, managerial spells."""
    row = connection.execute(text("select name, birth_date from people where fm_uid = :u"), {"u": uid}).first()
    if row is None:
        return None
    facts: dict = {"name": row[0]}
    if _age(row[1], day) is not None:
        facts["age"] = _age(row[1], day)
    positions = q.player_positions(q.latest_attributes(connection, uid))
    if positions and positions["label"]:
        facts["position"] = positions["label"]
    seasons = connection.execute(text(
        "select s.season_start, t.name, sum(s.starts + s.substitute_appearances) games, sum(s.goals) goals from player_seasons s "
        "join teams t on t.fm_uid = s.team_uid where s.person_uid = :u and s.kind in (1, 2, 3) group by s.season_start, s.team_uid "
        "order by s.season_start desc limit 3"), {"u": uid}).all()
    if seasons:
        facts["recent_seasons"] = [f"{start}/{(start + 1) % 100:02d}, {team}: {games} games, {goals} goals" for start, team, games, goals in seasons]
    fee = connection.execute(text("select fee from transfers where person_uid = :u and date = :d and fee > 0"),
                             {"u": uid, "d": str(day)}).scalar()
    if fee:
        facts["fee"] = f"£{fee / 1_000_000:.1f}m"
    feelings = q.person_club_relations(connection, uid)
    if feelings:  # a Legend leaving is a heartbreak, a Disliked man arriving an outrage
        facts["standing_with_clubs"] = [f"{r['group']} at {r['club_name']}" for r in feelings[:5]]
    injury = connection.execute(text("select injury, injured_on, days from injuries where person_uid = :u and current = 1 "
                                     "order by days desc limit 1"), {"u": uid}).first()
    if injury:
        facts["injured_now"] = f"{injury[0]} since {injury[1]}, expected out {injury[2]} days"
    talk = moods.public_talk(connection, uid, day)
    if talk:  # dressing-room talk the fans have heard of
        facts["dressing_room_talk"] = talk
    spells = connection.execute(text(
        "select t.name, s.started, s.ended, s.won, s.drawn, s.lost from manager_spells s join teams t on t.fm_uid = s.team_uid "
        "where s.person_uid = :u and s.ended <= :d order by s.ended desc limit 3"), {"u": uid, "d": str(day)}).all()
    if spells:
        facts["managed"] = [f"{team}, {started} to {ended}: won {won}, drawn {drawn}, lost {lost}"
                            for team, started, ended, won, drawn, lost in spells]
    return facts


def _name(connection: Connection, uid: int) -> str | None:
    row = connection.execute(text("select name from teams where fm_uid = :u"), {"u": uid}).first()
    return row[0] if row else None


def thread_notebook(connection: Connection, post: SocialPost) -> tuple[dict, list[SocialAccount]]:
    """What the thread's writer knows, and its cast: fans of the posting club, of the other club and of a rival."""
    clubs = _clubs_in(connection, post)
    if not clubs:
        raise StoryError("The post names no club for fans to support.")
    home = clubs[0]
    notebook: dict = {"post": {"by": post.account.name, "handle": post.account.handle, "date": str(post.posted_on),
                               "kind": post.kind, "text": post.text},
                      "clubs": {}}
    for club in clubs[:2]:
        facts = {"recent_results": _recent(connection, club, post.posted_on)}
        honours = _honours(connection, club, post.posted_on)
        if honours:
            facts["last_twelve_months"] = honours  # what the club won, or nearly won: no fan may say otherwise
        notebook["clubs"][_name(connection, club) or str(club)] = facts
    if len(clubs) > 1:
        other = clubs[1]
        notebook["last_meetings"] = _meetings(connection, home, other, post.posted_on)
        for a, b in ((home, other), (other, home)):
            rivalry = q.rivalry(connection, a, b)
            if rivalry:
                notebook.setdefault("rivalries", []).append(
                    f"{_name(connection, a)} count {_name(connection, b)} as a rival ({rivalry['level']}/100"
                    + (f", {rivalry['reason'].lower()}" if rivalry["reason"] else "") + ")")
    linked_people = [uid for _, kind, uid in post.links or [] if kind == "person"]
    if post.account.kind == PLAYER and post.account.person_uid not in linked_people:
        linked_people.insert(0, post.account.person_uid)  # a player's own post is about him
    people = [_person(connection, uid, post.posted_on) for uid in linked_people]
    if post.kind != "result" and any(people):  # a result's scorers are named in the post; the fans need no more
        notebook["people"] = [p for p in people if p]
    rng = random.Random(f"cast:{post.id}")

    def some(club: int, count: int) -> list[SocialAccount]:
        fans = fans_of(connection, club)
        return rng.sample(fans, min(count, len(fans)))

    cast = some(home, 3)
    if len(clubs) > 1:
        cast += some(clubs[1], 2)
    rival = _rival(connection, home, set(clubs), rng)
    if rival and _name(connection, rival):
        cast += some(rival, 2 if post.heat >= HOT else 1)
        notebook["rivals_in_the_thread"] = f"{_name(connection, rival)}, a rival of {_name(connection, home)}"
    if post.heat >= HOT and len(clubs) > 1:
        cast += [f for f in some(clubs[1], 3) if f not in cast][:1]  # a hot post draws more of the other side
    match = next((key for _, kind, key in post.links or [] if kind == "match"), None)
    if post.kind == "result" and match:
        day, home_uid, away_uid = match.split(":")
        fixture = connection.execute(text("select id from fixtures where date = :d and home_uid = :h and away_uid = :a"),
                                     {"d": day, "h": int(home_uid), "a": int(away_uid)}).scalar()
        the_match = q.match_summary(connection, fixture) if fixture else None
        if the_match:
            notebook["the_match"] = the_match
        from app.services import social_predictions

        predicted = social_predictions.predictions_notebook(connection, match)
        if predicted:
            notebook["predictions"] = predicted
            seen = {f.handle for f in cast}
            for p in sorted(predicted, key=lambda p: p["verdict"] != "exact score")[:3]:  # the predictors remember
                fan = SocialAccount.query.filter_by(handle=p["handle"]).first()
                if fan and fan.handle not in seen:
                    cast.append(fan)
                    seen.add(fan.handle)
    notebook["cast"] = [{"handle": f.handle, "name": f.name, "supports": _name(connection, f.club_uid),
                         "temper": TEMPERS[f.persona["temper"]][0] + ": " + TEMPERS[f.persona["temper"]][1]} for f in cast]
    linked = [uid for _, kind, uid in post.links or [] if kind == "person"]
    if post.account.kind == PLAYER:
        linked.insert(0, post.account.person_uid)
    player = (SocialAccount.query.filter(SocialAccount.kind == PLAYER, SocialAccount.person_uid.in_(linked)).first()
              if linked else None)
    if player and player.id != post.account_id:  # the player himself joins in, once
        cast.append(player)
        notebook["cast"].append({"handle": player.handle, "name": player.name, "plays_for": _name(connection, player.club_uid),
                                 "temper": PLAYER_VOICE})
    known = social_feed._clubs(connection, set(clubs[:2]))
    notebook["hashtags"] = [f"#{social_feed.club_tag(known[club])}" for club in clubs[:2] if club in known]
    return notebook, cast


# -- writing a thread -------------------------------------------------------------------------------------
def _replies(answer: dict, cast: dict[str, SocialAccount]) -> list[dict]:
    replies = []
    for reply in answer.get("replies") or []:
        if not isinstance(reply, dict):
            continue
        handle = str(reply.get("handle") or "").lstrip("@")
        body = str(reply.get("text") or "").strip()
        if handle in cast and body:
            to = str(reply.get("reply_to") or "").lstrip("@") or None
            replies.append({"handle": handle, "reply_to": to if to in cast else None, "text": body[:REPLY_LENGTH + 40]})
    return replies


def write_thread(post: SocialPost, model: str | None = None, drip: bool = False) -> int:
    """The fans' replies under one club post, checked, by the given model (the one chosen in Settings when none);
    returns how many were kept. With ``drip`` the replies are shown one by one, REPLY_GAP apart, after those already
    waiting (pending_replies): one AI call writes the thread, the feed shows it as it might have happened."""
    with db.engines[world_models.BIND].connect() as connection:
        notebook, cast = thread_notebook(connection, post)
    model = model or get_deepseek_model()
    key = resolve_ai_api_key(None, model=model)
    if not key:
        raise StoryError("No API key is set for the AI model chosen in Settings.")
    by_handle = {f.handle: f for f in cast}
    low, high = HOT_REPLIES if post.heat >= HOT else REPLIES
    messages = [{"role": "system", "content": PROMPT.format(low=low, high=high, length=REPLY_LENGTH)},
                {"role": "user", "content": json.dumps(notebook, ensure_ascii=False, default=str)}]
    replies = _replies(request_json(model, key, messages, temperature=TEMPERATURE), by_handle)
    faulty = {i: hard_faults(text_faults(r["text"], notebook)) for i, r in enumerate(replies)}  # a stock phrase suits a fan
    faulty = {i: f for i, f in faulty.items() if f}
    if faulty:
        messages += [{"role": "assistant", "content": json.dumps({"replies": replies}, ensure_ascii=False)},
                     {"role": "user", "content": "Write the thread again; these replies had facts that are not in the notebook: "
                                                 + "; ".join(f"{replies[i]['handle']}: {', '.join(f)}" for i, f in faulty.items())}]
        replies = _replies(request_json(model, key, messages, temperature=TEMPERATURE), by_handle)
        replies = [r for r in replies if not hard_faults(text_faults(r["text"], notebook))]
    return _save(post, replies, by_handle, drip)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def pending_replies() -> int:
    """Replies written but not yet shown."""
    return SocialPost.query.filter(SocialPost.shown_at > _now()).count()


def _release_times(count: int, rng: random.Random) -> list[datetime]:
    """When each of ``count`` new replies is shown: REPLY_GAP apart, after the last reply already waiting."""
    last = db.session.query(db.func.max(SocialPost.shown_at)).scalar()
    at = max(last or _now(), _now())
    times = []
    for _ in range(count):
        at += timedelta(seconds=rng.uniform(*REPLY_GAP))
        times.append(at)
    return times


def _save(post: SocialPost, replies: list[dict], cast: dict[str, SocialAccount], drip: bool = False) -> int:
    if SocialPost.query.filter(SocialPost.event_key.like(f"reply:{post.id}:%")).first():
        db.session.rollback()  # the other writer (the button, or the slow writer) answered this post meanwhile
        return 0
    rng = random.Random(f"likes:{post.id}")
    shown = _release_times(len(replies), random.Random()) if drip else [None] * len(replies)
    latest: dict[str, SocialPost] = {}
    for number, reply in enumerate(replies):
        parent = latest.get(reply["reply_to"]) or post
        fan = cast[reply["handle"]]
        likes = max(0, int(post.likes * rng.lognormvariate(-4.5, 1.1)))
        saved = SocialPost(account_id=fan.id, posted_on=post.posted_on, kind="reply", event_key=f"reply:{post.id}:{number}",
                           text=reply["text"], links=[], reply_to_id=parent.id, likes=likes, reposts=int(likes * rng.uniform(0, 0.1)),
                           shown_at=shown[number])
        db.session.add(saved)
        db.session.flush()
        latest[reply["handle"]] = saved
    db.session.commit()
    return len(replies)


def thread_candidates(limit: int = THREADS) -> list[SocialPost]:
    """The club posts without replies of the last RECENT_DAYS, by likes, kind and heat, the followed clubs' boosted and
    older posts fading: a well-liked post from three imports ago once took the threads, far down the feed."""
    from app.services.story_leads import followed_clubs

    newest = db.session.query(db.func.max(SocialPost.posted_on)).scalar()
    if newest is None:
        return []
    answered = db.session.query(SocialPost.reply_to_id).filter(SocialPost.reply_to_id.isnot(None))
    posts = (SocialPost.query.join(SocialAccount).filter(SocialAccount.kind != FAN, SocialPost.kind.in_(list(THREAD_KINDS)),
                                                         SocialPost.reply_to_id.is_(None), SocialPost.id.notin_(answered),
                                                         SocialPost.posted_on >= newest - timedelta(days=RECENT_DAYS))
             .all())
    followed = set(followed_clubs())
    posts.sort(key=lambda p: -p.likes * THREAD_KINDS[p.kind] * (1 + p.heat / 40) * (FOLLOWED_BOOST if p.account.club_uid in followed else 1)
               * 0.5 ** ((newest - p.posted_on).days / AGE_HALF_LIFE))
    return posts[:limit]


# -- the slow writer --------------------------------------------------------------------------------------
SLOW_KEY = "social_fans_slow"  # "off" stops the slow writer
SLOW_LIMIT = 24  # threads the slow writer writes for each import (each newest post date); the button's do not count
SLOW_COUNT_KEY = "social_fans_slow_count"  # {"newest": the newest post's date, "written": threads since}
SLOW_PAUSE = 20  # seconds after writing a thread before the next look
SLOW_CHECK = 30  # seconds between two looks while replies wait to be shown
SLOW_BUFFER = 12  # replies waiting to be shown: the slow writer writes ahead no further (about two threads)
REPLY_GAP = (10, 40)  # seconds between two replies shown, at random: a feed that moves, not a dump
SLOW_IDLE = 600  # seconds between two looks when there is nothing to do


def slow_enabled() -> bool:
    from app.models import AppSetting

    setting = AppSetting.query.filter_by(key=SLOW_KEY).first()
    return setting is None or setting.value != "off"


def set_slow(on: bool) -> None:
    from app.models import AppSetting

    setting = AppSetting.query.filter_by(key=SLOW_KEY).first()
    if setting is None:
        db.session.add(AppSetting(key=SLOW_KEY, value="on" if on else "off"))
    else:
        setting.value = "on" if on else "off"


def slow_model() -> str | None:
    """The local model the slow writer uses: the one chosen in Settings if it is local, else an installed one (a
    Gemma first, which writes these well, else the smallest); none when Ollama does not answer."""
    from app.services.ai_settings import OLLAMA_PREFIX, ollama_models

    installed = ollama_models()
    if not installed:
        return None
    chosen = get_deepseek_model()
    if chosen.startswith(OLLAMA_PREFIX) and any(OLLAMA_PREFIX + m["name"] == chosen for m in installed):
        return chosen
    gemma = [m for m in installed if m["name"].startswith("gemma")]
    return OLLAMA_PREFIX + min(gemma or installed, key=lambda m: m["size_gb"])["name"]


def answered_recently() -> int:
    """Threads under the club posts of the last RECENT_DAYS days."""
    newest = db.session.query(db.func.max(SocialPost.posted_on)).scalar()
    if newest is None:
        return 0
    parents = db.session.query(SocialPost.reply_to_id).filter(SocialPost.reply_to_id.isnot(None))
    return (SocialPost.query.join(SocialAccount).filter(SocialAccount.kind != FAN, SocialPost.id.in_(parents),
                                                        SocialPost.posted_on >= newest - timedelta(days=RECENT_DAYS)).count())


def _slow_count() -> tuple[str | None, int]:
    """The newest post's date and how many threads the slow writer has written since it."""
    import json as json_module

    from app.models import AppSetting

    newest = db.session.query(db.func.max(SocialPost.posted_on)).filter(SocialPost.kind != "reply").scalar()
    setting = AppSetting.query.filter_by(key=SLOW_COUNT_KEY).first()
    stored = json_module.loads(setting.value) if setting else {}
    newest = str(newest) if newest else None
    return newest, stored.get("written", 0) if stored.get("newest") == newest else 0


def _count_slow_thread(newest: str | None, written: int) -> None:
    import json as json_module

    from app.models import AppSetting

    setting = AppSetting.query.filter_by(key=SLOW_COUNT_KEY).first()
    value = json_module.dumps({"newest": newest, "written": written})
    if setting is None:
        db.session.add(AppSetting(key=SLOW_COUNT_KEY, value=value))
    else:
        setting.value = value
    db.session.commit()


def slow_state() -> dict:
    """For the Feed: whether the slow writer works, with which model, and how far it is since the last import."""
    on = social_feed.enabled() and slow_enabled()
    model = slow_model() if on else None
    return {"on": on, "model": model.split(":", 1)[1] if model else None, "done": _slow_count()[1], "limit": SLOW_LIMIT,
            "waiting": pending_replies()}


def trickle_once(log: Callable[[str], None] = lambda text: None) -> bool:
    """One thread by the slow writer, when the feed and the slow writer are on, Ollama answers, no import or other
    job runs and it has written fewer than SLOW_LIMIT threads since the newest posts; True when it wrote one (the next
    comes after SLOW_PAUSE)."""
    from flask import current_app

    from app.services import save_jobs

    if not (social_feed.enabled() and slow_enabled()) or save_jobs.busy(current_app._get_current_object()):
        return False
    newest, written = _slow_count()
    if written >= SLOW_LIMIT or pending_replies() >= SLOW_BUFFER:  # the quota is filled, or enough is waiting
        return False
    model = slow_model()
    posts = thread_candidates(1) if model else []
    if not posts:
        return False
    try:
        replies = write_thread(posts[0], model=model, drip=True)
    except StoryError as exc:
        db.session.rollback()
        log(f"The slow writer wrote no thread under {posts[0].account.name}'s post: {exc}")
        return False
    log(f"The slow writer wrote {replies} replies under {posts[0].account.name}'s post; they show one by one")
    if replies:
        _count_slow_thread(newest, written + 1)
    return replies > 0


def slow_pause(wrote: bool) -> int:
    """Seconds until the slow writer looks again: soon while it has work, long when it has none."""
    if wrote:
        return SLOW_PAUSE
    if not (social_feed.enabled() and slow_enabled()) or _slow_count()[1] >= SLOW_LIMIT:
        return SLOW_IDLE
    return SLOW_CHECK if pending_replies() or thread_candidates(1) else SLOW_IDLE
