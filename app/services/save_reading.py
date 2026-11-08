"""Reading a saved game on several cores, for the importer (app/services/save_import.py).

Reading a save is nearly all of an import's work, and most of it is independent: names,
appearances, team histories, contracts, statistics, fixtures... Each part below opens the
save in its own process (the 300 MB ``game_db.dat`` takes a third of a second to unpack)
and returns plain data. Writing stays in one process: SQLite has a single writer.
"""

from __future__ import annotations

import multiprocessing
import os
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from app.services import fm_save, fm_tactics


def _clubs(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    clubs = fm_save.read_clubs(game_db)
    records = fm_save._club_records(game_db)  # once: the relations and the finances both need it
    teams = fm_save.read_teams(game_db, clubs)
    return {"clubs": clubs, "teams": teams, "point_deductions": fm_save.read_point_deductions(save, teams), "stadiums": fm_save.read_stadiums(game_db),
            "club_relations": fm_save.read_club_relations(game_db, records),
            "club_finances": fm_save.read_club_finances(game_db, clubs, records), "manager_history": _manager_history(save)}


def _contracts(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    """Everyone's contract, the managers' profiles, the human club's partnerships and social groups: the person rows
    read once for all of them. Its own clubs and teams (a few seconds) rather than waiting for the clubs part."""
    teams = fm_save.read_teams(game_db, fm_save.read_clubs(game_db))
    rows = fm_save._person_rows(game_db)
    contracts = fm_save.read_staff_contracts(game_db, teams, rows)
    uid_of = {number: uid for number, (uid, _) in rows.items()}
    partnerships = [(uid_of.get(p.person_a), uid_of.get(p.person_b), p) for p in fm_save.read_partnerships(game_db)]
    # the human club: where most of its partnership players who have not left are
    human = Counter(contracts[uid].team for a, b, p in partnerships if not p.departed for uid in (a, b) if uid in contracts)
    human_team = human.most_common(1)[0][0] if human else None
    groups = fm_save.read_social_groups(game_db, human_team, rows) if human_team is not None else []
    return {"staff_contracts": contracts, "staff_profiles": fm_save.read_staff_profiles(game_db, contracts, person_rows=rows),
            "partnerships": [(a, b, p) for a, b, p in partnerships if a and b], "social_groups": (human_team, groups),
            "national_squads": fm_save.read_national_squads(game_db, teams, rows)}


def _manager_history(save: fm_save.FmSave) -> list[list[fm_save.ManagerSpell]]:
    try:
        return fm_save.read_manager_history(save.read("tc_manager_history_dt.cmt"), save.read("tc_manager_history_ls.dat"))
    except (KeyError, fm_save.FmSaveError):  # an older or a different save without it
        return []


def _names(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    person_uid, people = fm_save.read_person_names(game_db, fm_save.read_name_tables(game_db))
    return {"person_uid": person_uid, "people": people, "player_games": fm_save.read_player_games(game_db, person_uid)}


def _players(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    return {"players": list(fm_save.iter_players(game_db, names=fm_save.read_name_tables(game_db)))}


def _appearances(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    return {"appearances": fm_save.read_appearances(game_db)}


def _team_histories(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    return {"team_histories": fm_save.read_team_histories(game_db)}


def _statistics(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    stats = fm_save.read_season_stats(save.read("player_stats_hist_dt.cmt"), save.read("player_stats_hist_ls.dat"))
    groups = fm_save.read_stat_groups(game_db, stats, fm_save.read_name_tables(game_db),
                                      retirement_manager=save.read("retirement_manager.dat"))
    return {"season_stats": stats, "stat_groups": groups}


def _injuries(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    """Every player's injury history and current injuries."""
    if "injury_histories_dt.cmt" not in save.directory:  # an older or a different save without them
        return {}
    names = fm_save.read_injury_names(lang_db)
    anchors = fm_save._injury_anchors(game_db)
    return {"injury_history": fm_save.read_injury_history(save.read("injury_histories_dt.cmt"), save.read("injury_histories_ls.dat"), names),
            "injury_groups": fm_save.read_injury_groups(game_db, anchors),
            "current_injuries": fm_save.read_current_injuries(game_db, names, anchors)}


def _tactics(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    """The human manager's tactic slots and library (``tactics_man.dat``) and the tactic used in each of the human club's
    match files, with the FM uids of the players in the file (they date the match: see save_import._write_tactics). The
    human club is the one whose match files hold the slot tactics' names most often."""
    if "tactics_man.dat" not in save.directory:
        return {}
    library = save.read("tactics_man.dat")
    slots, saved = fm_tactics.read_tactic_slots(library), fm_tactics.read_tactic_library(library)
    names = {t.name for t in slots + saved}
    files = [n for n in save.names() if n.endswith((".apm", ".scm")) and "_" in n]
    by_team: dict[int, list[str]] = {}
    for name in files:
        by_team.setdefault(int(name.split("_")[0]), []).append(name)
    human = Counter()
    for team, owned in by_team.items():
        for name in owned:
            if name.endswith(".scm") and any(t.name in names for t in fm_tactics.iter_tactics(save.read(name))):
                human[team] += len(owned)
    rows = fm_save._person_rows(game_db)
    known = np.sort(np.fromiter((uid for uid, _ in rows.values()), dtype=np.uint32))
    team = human.most_common(1)[0][0] if human else None
    matches = []
    for name in sorted(by_team.get(team, [])):
        data = save.read(name)
        found = fm_tactics.read_match_tactics(data)
        if found and found.tactics:
            matches.append((name, found, fm_tactics.person_uids_in(data, known)))
    numbers = {p.person_number for t in slots + saved + [t for _, m, _ in matches for t in m.tactics] for p in t.player_roles}
    people = {number: rows[number][0] for number in numbers if number in rows}
    return {"tactic_slots": slots, "tactic_library": saved, "match_tactics": matches, "human_team": team, "tactic_people": people}


def _competitions(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    names = fm_save.read_competition_names(lang_db)
    keys = fm_save.read_competition_keys(game_db, list(names))
    seasons = fm_save.read_competition_history(save.read("comp_history_dt.cmt"))
    seasons += fm_save.read_latest_editions(game_db, keys).values()
    return {"competition_names": names, "competition_keys": keys, "stages": fm_save.read_stages(game_db, keys),
            "competition_reputations": fm_save.read_competition_reputations(game_db), "competition_seasons": seasons}


def _the_rest(save: fm_save.FmSave, game_db: bytes, lang_db: Path) -> dict[str, Any]:
    award_names = fm_save.read_award_names(lang_db)
    transfer_man = save.read("transfer_man.dat")
    try:
        reports = fm_save.read_match_reports(save.read("match_stats_cfg.dat"))
    except fm_save.FmSaveError:  # an old save without match reports
        reports = []
    return {"nation_names": fm_save.read_nation_names(lang_db), "award_names": award_names,
            "award_keys": fm_save.read_award_keys(game_db, list(award_names)),
            "award_years": list(fm_save.read_award_history(save.read("award_year_hist_dt.cmt"))),
            "goal_awards": fm_save.read_goal_awards(save.read("award_man.dat")) if "award_man.dat" in save.directory else [],
            # a new game has no best elevens until its first season's are picked
            "best_elevens": fm_save.read_best_elevens(save.read("tc_best_eleven_history_dt.cmt"), save.read("tc_best_eleven_history_ls.dat"))
            if "tc_best_eleven_history_dt.cmt" in save.directory else [],
            "transfers": list(fm_save.read_transfers(transfer_man)), "match_reports": reports,
            "transfer_interest": fm_save.read_transfer_interest(transfer_man), "transfer_offers": fm_save.read_transfer_offers(transfer_man),
            "press_answers": fm_save.read_press_answers(save.read("press_conference_manager.dat"))
            if "press_conference_manager.dat" in save.directory else [],
            "media_questions": fm_save.read_media_questions(),
            "news_items": fm_save.read_news_items(save.read("news.dat")) if "news.dat" in save.directory else []}


# The slowest first, so they start first.
PARTS: dict[str, Callable[[fm_save.FmSave, bytes, Path], dict[str, Any]]] = {
    "statistics": _statistics, "names": _names, "contracts": _contracts, "clubs": _clubs, "appearances": _appearances,
    "team histories": _team_histories, "injuries": _injuries, "competitions": _competitions, "players": _players, "tactics": _tactics,
    "the rest": _the_rest,
}


MAX_WORKERS = 9  # measured: 9 workers read a 450 MB save in 50 s, 10 in 70 s (each unpacks the 320 MB game_db)


def _read_part(name: str, path: str, lang_db: str) -> dict[str, Any]:
    with fm_save.FmSave(path) as save:
        return PARTS[name](save, save.read("game_db.dat"), Path(lang_db))


def read_save(path: Path, lang_db: Path, progress: Callable[[str], None], workers: int | None = None) -> dict[str, Any]:
    """Every part of the save the importer writes. The parts are read side by side, one process
    each, while this process reads the fixtures (over a million: too many to pass between
    processes quickly); the players' reputations come last, as they need the names and players.
    At most MAX_WORKERS: with ten workers beside this process the reading took 70 s instead of 50
    on a 15-core Mac (5 performance cores, 24 GB), the quick parts waiting on the slow ones."""
    workers = min(len(PARTS), MAX_WORKERS, workers or max(1, (os.cpu_count() or 2) - 1))
    found: dict[str, Any] = {}
    if workers <= 1:
        for number, name in enumerate(PARTS, 1):
            progress(f"Reading the save: {name} ({number} of {len(PARTS)})")
            found.update(_read_part(name, str(path), str(lang_db)))
        with fm_save.FmSave(path) as save:
            return _finish(save, found)
    # "spawn": the importer runs in a thread of the web server, and forking a threaded process is not safe
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        pending = {pool.submit(_read_part, name, str(path), str(lang_db)): name for name in PARTS}
        progress(f"Reading the save on {workers + 1} cores")
        with fm_save.FmSave(path) as save:
            found["fixtures"] = list(fm_save.iter_all_fixtures(save.read("fix_man.dat")))
            for number, future in enumerate(as_completed(pending), 1):
                found.update(future.result())
                progress(f"Reading the save: {pending[future]} done ({number} of {len(PARTS)})")
            return _finish(save, found)


def _finish(save: fm_save.FmSave, found: dict[str, Any]) -> dict[str, Any]:
    if "fixtures" not in found:
        found["fixtures"] = list(fm_save.iter_all_fixtures(save.read("fix_man.dat")))
    uids = set(found["people"]) | {player.uid for player in found["players"]}
    found["person_reputations"] = fm_save.read_person_reputations(save.read("game_db.dat"), uids)
    return found
