"""Which team each of the save's manager history lists belongs to (fm_save.read_manager_history).

The lists do not name their team, and the game keeps the link where it could not be found. But every spell
counts its games won, drawn and lost, and the archive has every result since the game began: a list belongs
to the senior team whose record in the dates of its longest spells comes closest. Checked on Arsenal,
England, Colombia, RB Leipzig, Burnley and Bristol City, and against the persons' own spells
(person_record_manager.dat): 190 of 199 lists agree, and in the others the results fit better.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services import fm_save

SPELLS_COMPARED = 5  # a list's longest spells
MIN_GAMES = 4  # a spell with fewer games says too little
CLOSE_ENOUGH = 0.12  # the best team's difference, per game compared
CLEAR_LEAD = 1.6  # the next team must be this many times further off
_JULIAN_ORDINAL = 1721424.5  # julianday() of date.fromordinal(0)


def link_lists(connection: Connection, lists: list[list[fm_save.ManagerSpell]]) -> dict[int, int]:
    """List number -> senior team uid, for the lists whose results single out one team; one list per team,
    the closest fits first. A penalty shoot-out counts as a win or a defeat in a spell but as a draw in the
    archive's results, and friendlies count in some spells only, so each spell is compared with and without
    them and the difference may not be quite zero."""
    rows = connection.execute(text(
        "select f.home_uid, f.away_uid, julianday(f.date), f.home_goals - f.away_goals, coalesce(c.scope, '') = 'friendly' "
        "from fixtures f left join competitions c on c.fm_uid = f.competition_uid where f.home_goals is not null")).all()
    if not rows:
        return {}
    home, away, day, margin, friendly = (np.array(column) for column in zip(*rows))
    team = np.concatenate([home, away]).astype(np.int64)
    days = np.concatenate([day, day]).astype(float)
    result = np.sign(np.concatenate([margin, -margin]))
    friendlies = np.concatenate([friendly, friendly]).astype(bool)
    uids, team_index = np.unique(team, return_inverse=True)
    order = np.argsort(days, kind="stable")
    days, team_index, result, friendlies = days[order], team_index[order], result[order], friendlies[order]
    senior = {uid for (uid,) in connection.execute(text("select fm_uid from teams where age_limit is null"))}
    youth = np.array([uid not in senior for uid in uids])
    teams = len(uids)
    # Each game as one code, team x 3 + outcome (0 won, 1 drawn, 2 lost): one bincount over a spell's games counts
    # every team's wins, draws and losses at once (it took six masked passes over the games and six bincounts a
    # spell, 48 s of an import). The games without friendlies are a second, shorter sequence of the same codes.
    outcome = np.where(result > 0, 0, np.where(result == 0, 1, 2))
    codes = team_index * 3 + outcome
    without = ~friendlies
    sequences = ((days, codes), (days[without], codes[without]))

    fits = []
    for number, spells in enumerate(lists):
        compared = sorted((s for s in spells if s.started and s.ended and s.won + s.drawn + s.lost >= MIN_GAMES),
                          key=lambda s: -(s.won + s.drawn + s.lost))[:SPELLS_COMPARED]
        if not compared:
            continue
        difference = np.zeros(teams)
        for spell in compared:
            wanted = np.array([spell.won, spell.drawn, spell.lost])
            best = None
            for spell_days, spell_codes in sequences:
                low = np.searchsorted(spell_days, spell.started.toordinal() + _JULIAN_ORDINAL)
                high = np.searchsorted(spell_days, spell.ended.toordinal() + _JULIAN_ORDINAL, side="right")
                counts = np.bincount(spell_codes[low:high], minlength=3 * teams).reshape(teams, 3)
                off = np.abs(counts - wanted).sum(axis=1)
                best = off if best is None else np.minimum(best, off)
            difference += best
        difference[youth] = np.inf
        games = sum(s.won + s.drawn + s.lost for s in compared)
        first, second = np.argsort(difference)[:2]
        fits.append((difference[first] / games, difference[second] / games, number, int(uids[first])))
    links, taken = {}, set()
    for closest, next_closest, number, uid in sorted(fits):
        if closest <= CLOSE_ENOUGH and next_closest >= CLEAR_LEAD * closest and uid not in taken:
            links[number] = uid
            taken.add(uid)
    return links
