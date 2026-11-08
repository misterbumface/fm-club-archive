"""Club money in the papers (8 Nov 2026: "when a club is that deep in debt with that kind of salary, trouble is going to happen";
only the big names, only the big deals, and no paper knows a private club's books to the pound).

A paper knows what a club's published accounts say, once a year, and what people close to the club tell it. So: one story a
year at most for a club, on the day its accounts come out; only for the world's top clubs and the great names; only when
the figures are extreme, far in the red or sitting on a pile of cash; and every figure is rounded (world_queries.rough_money).

"points_docked" is the other story: the save shows it only as a league table with fewer points than the games earned.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.services import world_queries as q
from app.services.story_leads_now import _manager

TOP_CLUBS = 15  # by first-team reputation, whatever their name
GREAT_NAMES = (602, 630, 676, 679, 680, 868, 907, 915, 1099, 1135, 1139, 1687, 1708, 1736)  # Arsenal, Chelsea, Liverpool, both Manchester clubs, PSG,
# Dortmund, Bayern, Milan, Inter, Juventus, Atlético, Barcelona, Real Madrid: these stay names when their teams are poor for a while
ACCOUNTS_FROM, ACCOUNTS_SPAN = 30, 150  # the day of the year a club's accounts come out: after January, before July, its own day each year
DEEP_RED = -200_000_000
RED_WITH_DEBT, BIG_DEBT = -100_000_000, 250_000_000  # a smaller overdraft is a story only with a debt this big
BIG_CASH = 350_000_000
DEBT_DAYS = 90  # a debt read this long before the accounts is still the debt
YEAR_BEFORE_DAYS = 240  # the earlier balance must be at least this much older
DOCKED_REPUTATION, DOCKED_DAYS = 6000, 300
ACCOUNTS_SIZE, DOCKED_SIZE = 45, 70


def big_clubs(w) -> set[int]:
    top = w.rows("select t.fm_uid from teams t join clubs c on c.fm_uid = t.fm_uid where t.age_limit is null and t.reputation is not null "
                 "order by t.reputation desc limit :n", n=TOP_CLUBS)
    return {r["fm_uid"] for r in top} | set(GREAT_NAMES)


def accounts_day(club: int, year: int) -> date:
    return date(year, 1, 1) + timedelta(days=ACCOUNTS_FROM + club % ACCOUNTS_SPAN)


def _debt(w, club: int, day: str) -> int | None:
    start = str(date.fromisoformat(day[:10]) - timedelta(days=DEBT_DAYS))
    rows = w.rows("select debt from club_finances where club_uid = :c and game_date <= :d and game_date >= :s and debt is not null order by game_date desc limit 1",
                  c=club, d=day, s=start)
    return rows[0]["debt"] if rows else None


def _wages_a_year(w, club: int) -> int | None:
    row = w.rows("select sum(c.wage) weekly from contracts c join people p on p.fm_uid = c.person_uid and p.team_uid = c.team_uid "
                 "where c.team_uid = :c and c.job = 'Player' and c.as_of = (select max(as_of) from contracts where team_uid = :c)", c=club)
    return row[0]["weekly"] * 52 if row and row[0]["weekly"] else None


def _market(w, club: int, day: str) -> tuple[int, int]:
    """What the club paid for players and what it received, in the year up to the day."""
    start = str(date.fromisoformat(day[:10]) - timedelta(days=365))
    row = w.rows("select coalesce(sum(case when to_uid = :c then fee end), 0) paid, coalesce(sum(case when from_uid = :c then fee end), 0) received "
                 "from transfers where date > :s and date <= :d and (to_uid = :c or from_uid = :c)", c=club, s=start, d=day)
    return row[0]["paid"], row[0]["received"]


def _facts(w, club: int, money: dict) -> dict:
    """What a paper may print: never a figure to the pound."""
    day = str(money["game_date"])[:10]
    bank, debt = money["bank"], _debt(w, club, day)
    wages = _wages_a_year(w, club)
    paid, received = _market(w, club, day)
    older = w.rows("select bank, game_date from club_finances where club_uid = :c and game_date <= :d order by game_date desc limit 1",
                   c=club, d=str(date.fromisoformat(day) - timedelta(days=YEAR_BEFORE_DAYS)))
    facts = {"club": w.name(club), "manager": _manager(w, club),
             "bank_balance_reported": f"{q.rough_money(bank)} overdrawn" if bank < 0 else f"{q.rough_money(bank)} in the bank",
             "bank_debt_reported": q.rough_money(debt) if debt else None,
             "player_wages_a_year_reported": q.rough_money(wages) if wages else None,
             "spent_on_players_in_the_last_year": q.rough_money(paid) if paid else "nothing in fees",
             "received_for_players_in_the_last_year": q.rough_money(received) if received else "nothing in fees"}
    if older and older[0]["bank"] is not None:
        change = bank - older[0]["bank"]
        facts["since_earlier_this_season"] = f"the balance is {q.rough_money(abs(change))} {'better' if change > 0 else 'worse'} than at {str(older[0]['game_date'])[:7]}"
    return {k: v for k, v in facts.items() if v is not None}


def accounts(w) -> list:
    from app.services.story_leads import Lead

    leads = []
    for club in big_clubs(w):
        if not any(w.since < accounts_day(club, year) <= w.until for year in {w.since.year, w.until.year}):
            continue
        rows = w.rows("select bank, game_date from club_finances where club_uid = :c and game_date <= :u and bank is not null order by game_date desc limit 1",
                      c=club, u=str(w.until))
        if not rows:
            continue
        bank, debt = rows[0]["bank"], _debt(w, club, str(rows[0]["game_date"])[:10])
        red = bank <= DEEP_RED or (bank <= RED_WITH_DEBT and (debt or 0) >= BIG_DEBT)
        if not red and bank < BIG_CASH:
            continue
        facts = {"the_story": "a great club in serious money trouble" if red else "a great club sitting on a great pile of cash", **_facts(w, club, rows[0])}
        fame = (w.rep(club) or 8000) / 8000
        name = w.name(club)
        headline = f"{name}: the accounts, {q.rough_money(bank)} {'in the red' if red else 'in the bank'}"
        leads.append(Lead("club_accounts", headline, w.until, ACCOUNTS_SIZE * fame * (1.15 if red else 1.0), facts, [f"club:{club}"], w.beats_for(club),
                          slug=f"{club}:{w.until.year}"))
    return leads


def docked(w) -> list:
    from app.services.story_leads import Lead

    if not w.rows("select 1 from sqlite_master where name = 'point_deductions'"):  # the first import after this version makes the table
        return []
    leads, big = [], big_clubs(w)
    since = str(w.since)
    for r in w.rows("select competition_uid, team_uid, points, played, min(as_of) seen from point_deductions d where as_of > :s and as_of <= :u and not exists "
                    "(select 1 from point_deductions b where b.competition_uid = d.competition_uid and b.team_uid = d.team_uid and b.as_of <= :s and b.as_of >= :y) "
                    "group by competition_uid, team_uid", s=since, u=str(w.until), y=str(w.since - timedelta(days=DOCKED_DAYS))):
        team = r["team_uid"]
        if team not in big and team not in w.followed_teams and (w.rep(team) or 0) < DOCKED_REPUTATION:
            continue
        competition = w.rows("select name from competitions where fm_uid = :c", c=r["competition_uid"])
        name = w.name(team)
        money = w.rows("select bank, game_date from club_finances where club_uid = :c and game_date <= :u and bank is not null order by game_date desc limit 1", c=team, u=str(w.until))
        facts = {"club": name, "points_taken_off": r["points"], "league": competition[0]["name"] if competition else None, "games_played_when_seen": r["played"],
                 "manager": _manager(w, team), **({"their_money": _facts(w, team, money[0])} if money else {})}
        leads.append(Lead("points_docked", f"{name} docked {r['points']} points", w.until, DOCKED_SIZE * (w.rep(team) or 5000) / 8000,
                          {k: v for k, v in facts.items() if v is not None}, [f"club:{team}"], w.beats_for(team), slug=f"{team}:{r['competition_uid']}:{str(r['seen'])[:4]}"))
    return leads
