"""Inspect a Football Manager 24 saved game (.fm). Read-only.

    python tools/fm_save_reader.py files SAVE [--grep TEXT]
    python tools/fm_save_reader.py extract SAVE NAME OUTPUT
    python tools/fm_save_reader.py diff SAVE_A SAVE_B [--limit N]
    python tools/fm_save_reader.py uid SAVE UID
    python tools/fm_save_reader.py attributes SAVE POSITION [--file game_db.dat]
    python tools/fm_save_reader.py blocks SAVE [--limit N]
    python tools/fm_save_reader.py players SAVE [--uid UID] [--limit N] [--csv OUTPUT]
    python tools/fm_save_reader.py fixtures SAVE [--competition N] [--uid UID] [--club N] [--played] [--limit N] [--archived]
    python tools/fm_save_reader.py teams SAVE [--grep TEXT] [--limit N]
    python tools/fm_save_reader.py table SAVE COMPETITION_UID
    python tools/fm_save_reader.py winners SAVE COMPETITION_UID [--limit N]
    python tools/fm_save_reader.py career SAVE PLAYER_UID [--all]
    python tools/fm_save_reader.py awards SAVE PLAYER_UID
    python tools/fm_save_reader.py transfers SAVE PLAYER_UID

Needs ``pip install -r requirements-fmsave.txt``. See app/services/fm_save.py for
what is decoded and what is not.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import fm_save  # noqa: E402
from app.services.fm_save import FmSave, FmSaveError  # noqa: E402

DB = "game_db.dat"


def cmd_files(args) -> int:
    with FmSave(args.save) as save:
        entries = [e for e in save.directory.values() if not args.grep or args.grep.lower() in e.name.lower()]
        for entry in sorted(entries, key=lambda e: -e.size):
            print(f"{entry.name:48s} {entry.size:>14,} bytes  (compressed {entry.compressed:>12,})")
        print(f"{len(entries)} of {len(save.directory)} files", file=sys.stderr)
    return 0


def cmd_extract(args) -> int:
    with FmSave(args.save) as save:
        data = save.read(args.name)
    Path(args.output).write_bytes(data)
    print(f"Wrote {len(data):,} bytes to {args.output}")
    return 0


def cmd_diff(args) -> int:
    with FmSave(args.save_a) as a, FmSave(args.save_b) as b:
        only_a = sorted(set(a.directory) - set(b.directory))
        only_b = sorted(set(b.directory) - set(a.directory))
        if only_a or only_b:
            print(f"Files only in A: {only_a[:10]}  only in B: {only_b[:10]}")
        changed = 0
        for name in sorted(set(a.directory) & set(b.directory)):
            first, second = a.read(name), b.read(name)
            if first == second:
                continue
            changed += 1
            if len(first) != len(second):
                print(f"{name}: size changed {len(first):,} -> {len(second):,}")
                continue
            count, sample = fm_save.diff_files(first, second, args.limit)
            shown = "  ".join(f"@{p}: {x}->{y}" for p, x, y in sample[:8])
            print(f"{name}: {count:,} bytes differ  {shown}")
        print(f"{changed} files differ", file=sys.stderr)
    return 0


def cmd_uid(args) -> int:
    with FmSave(args.save) as save:
        for name in save.names():
            entry = save.directory[name]
            if entry.size < 4 or entry.size > 400_000_000:
                continue
            hits = fm_save.find_uid(save.read(name), args.uid, limit=6)
            if not hits:
                continue
            print(f"{name}: {len(hits)}+ hits at {hits}")
            if name == DB:
                data = save.read(name)
                for position in hits:
                    dates = fm_save.candidate_birth_dates(data, position)
                    if dates:
                        back, found = dates[0]
                        print(f"    nearest date-like bytes {back} bytes before @{position}: {found}  (heuristic)")
    return 0


def cmd_attributes(args) -> int:
    with FmSave(args.save) as save:
        data = save.read(args.file)
    position = args.position
    for name, value in fm_save.read_attributes(data, position).items():
        raw = data[position + fm_save.ATTRIBUTE_OFFSETS[name]]
        print(f"{name:18s} {value:>3}   (stored {raw})")
    body = fm_save.read_body(data, position)
    print(f"Height {body['height_cm']} cm, weight {body['weight_kg']} kg")
    return 0


def cmd_blocks(args) -> int:
    with FmSave(args.save) as save:
        data = save.read(DB)
        count = 0
        for position in fm_save.find_attribute_blocks(data):
            if count < args.limit:
                attrs = fm_save.read_attributes(data, position)
                body = fm_save.read_body(data, position)
                print(f"@{position:>11,}  Finishing {attrs['Finishing']:>2}  Pace {attrs['Pace']:>2}  "
                      f"{body['height_cm']} cm / {body['weight_kg']} kg")
            count += 1
        print(f"{count:,} attribute blocks in {DB}", file=sys.stderr)
    return 0


def cmd_players(args) -> int:
    import csv

    with FmSave(args.save) as save:
        data = save.read(DB)
    players = fm_save.iter_players(data, names=fm_save.read_name_tables(data))
    if args.uid:
        players = (p for p in players if p.uid == args.uid)
    names = list(fm_save.ATTRIBUTE_OFFSETS)
    writer = handle = None
    if args.csv:
        handle = open(args.csv, "w", newline="", encoding="utf-8")
        writer = csv.writer(handle)
        writer.writerow(["uid", "first_name", "last_name", "birth_date", "height_cm", "weight_kg", *names])
    count = 0
    try:
        for player in players:
            count += 1
            if writer:
                writer.writerow([player.uid, player.first_name, player.last_name, player.birth_date.isoformat(), player.height_cm, player.weight_kg,
                                 *(player.attributes[n] for n in names)])
            elif count <= args.limit or args.uid:
                print(f"{player.uid}  {player.first_name} {player.last_name}  born {player.birth_date}  {player.height_cm} cm {player.weight_kg} kg  "
                      f"Finishing {player.attributes['Finishing']:>2}  Pace {player.attributes['Pace']:>2}  (block @{player.block_position:,})")
    finally:
        if handle:
            handle.close()
    print(f"{count:,} players" + (f" written to {args.csv}" if args.csv else ""), file=sys.stderr)
    return 0


def _names(save: FmSave):
    """Club and team names; reading game_db.dat takes several seconds."""
    data = save.read(DB)
    clubs = fm_save.read_clubs(data)
    teams = fm_save.read_teams(data, clubs)
    lang_db = fm_save.find_language_database()
    nations = fm_save.national_team_nations(teams, fm_save.read_nation_names(lang_db)) if lang_db else {}
    nation_names = {index: nation.name for index, nation in nations.items()}

    def name(number: int) -> str:
        return fm_save.team_name(teams[number], clubs, nation_names) if number in teams else f"team {number}"

    return name


def _competition_names() -> dict:
    """Names from the installed game's database, or {} when the game is not found."""
    lang_db = fm_save.find_language_database()
    return fm_save.read_competition_names(lang_db) if lang_db else {}


def cmd_fixtures(args) -> int:
    with FmSave(args.save) as save:
        fix_man = save.read("fix_man.dat")
        fixtures = list(fm_save.iter_all_fixtures(fix_man) if args.archived else fm_save.iter_fixtures(fix_man))
        game_db = save.read(DB)
        lang_db = fm_save.find_language_database()
        order = fm_save.competition_uids_in_order(lang_db) if lang_db else None
        links = fm_save.read_stages(game_db, fm_save.read_competition_keys(game_db, order))
        name = _names(save)
    competitions = _competition_names()
    count = 0
    for fixture in sorted(fixtures, key=lambda f: f.date):
        if args.competition is not None and fixture.competition_id != args.competition:
            continue
        if args.uid is not None and links.get(fixture.competition_id) != args.uid:
            continue
        if args.club is not None and args.club not in (fixture.home_id, fixture.away_id):
            continue
        if args.played and not fixture.played:
            continue
        count += 1
        if count > args.limit:
            continue
        score = f"{fixture.home_goals}-{fixture.away_goals}" if fixture.played else "v"
        goals = "  ".join(
            f"{'H' if g.side == 0 else 'A'}{g.minute}{f'+{g.added_time}' if g.added_time else ''}"
            f"{'' if g.kind == 0 else f'(kind {g.kind})'}"
            for g in fixture.goals
        )
        uid = links.get(fixture.competition_id)
        if uid in competitions:
            competition = competitions[uid].short_name
        else:
            competition = f"comp {fixture.competition_id}" + (f" (uid {uid})" if uid is not None else "")
        print(f"{fixture.date}  {competition[:24]:<24} {name(fixture.home_id):>28} {score:^5} {name(fixture.away_id):<28}  {goals}")
    print(f"{count:,} fixtures", file=sys.stderr)
    return 0


def cmd_table(args) -> int:
    with FmSave(args.save) as save:
        rows = fm_save.read_league_table(save.read(f"comp_{args.competition}.dat"))
        name = _names(save) if rows else None
    if not rows:
        print(f"comp_{args.competition}.dat has no table.", file=sys.stderr)
        return 1
    title = _competition_names().get(args.competition)
    if title:
        print(title.name)
    print(" Pos  Club                            P   W   D   L   GF  GA   GD  Pts")
    for r in rows:
        print(f"{r.position:>4}  {name(r.club_id)[:28]:<28} {r.played:>4} {r.won:>3} {r.drawn:>3} {r.lost:>3} "
              f"{r.goals_for:>4} {r.goals_against:>3} {r.goal_difference:>+4} {r.points:>4}")
    return 0


def cmd_teams(args) -> int:
    with FmSave(args.save) as save:
        data = save.read(DB)
    clubs = fm_save.read_clubs(data)
    teams = fm_save.read_teams(data, clubs)
    needle = (args.grep or "").lower()
    shown = 0
    for team in teams.values():
        label = fm_save.team_name(team, clubs)
        if needle and needle not in label.lower():
            continue
        shown += 1
        if shown <= args.limit:
            print(f"team {team.number:>7}  club {team.club_index:>6}  FM uid {team.fm_uid:>10}  {label}")
    print(f"{shown:,} of {len(teams):,} teams ({len(clubs):,} clubs)", file=sys.stderr)
    return 0


def cmd_winners(args) -> int:
    lang_db = fm_save.find_language_database()
    if lang_db is None:
        print("The game's lang_db.dat was not found; competition keys cannot be named.", file=sys.stderr)
        return 1
    names = fm_save.read_competition_names(lang_db)
    if args.competition not in names:
        print(f"No competition with FM uid {args.competition} in the game database.", file=sys.stderr)
        return 1
    with FmSave(args.save) as save:
        game_db = save.read(DB)
        keys = fm_save.read_competition_keys(game_db, fm_save.competition_uids_in_order(lang_db))
        wanted = {key for key, uid in keys.items() if uid == args.competition}
        seasons = [s for s in fm_save.read_competition_history(save.read("comp_history_dt.cmt")) if s.competition_key in wanted]
        latest = fm_save.read_latest_editions(game_db, keys).get(args.competition)
        if latest is not None and all(s.year < latest.year for s in seasons):
            seasons.append(latest)
        name = _names(save)
    print(names[args.competition].name)
    for season in sorted(seasons, key=lambda s: s.year)[-args.limit:]:
        label = season.season_label
        winner = name(season.winner) if season.winner is not None else "-"
        runner_up = name(season.runner_up) if season.runner_up is not None else "-"
        print(f"{label:>9}  {winner:<30} {runner_up:<30} {season.decided_on or ''}")
    return 0


def cmd_career(args) -> int:
    with FmSave(args.save) as save:
        game_db = save.read(DB)
        stats = fm_save.read_season_stats(save.read("player_stats_hist_dt.cmt"), save.read("player_stats_hist_ls.dat"))
        group = fm_save.read_stat_groups(game_db, stats, retirement_manager=save.read("retirement_manager.dat")).get(args.player)
        if group is None:
            print(f"No statistics found for FM uid {args.player} (retired players have none).", file=sys.stderr)
            return 1
        seasons = stats[group]
        name = _names(save)
    print(" Season  Club                            Apps (sub)  Gls  Ast  PoM  Rating   Mins")
    for s in seasons:
        if not (s.league or args.all):
            continue
        rating = f"{s.average_rating:.2f}" if s.average_rating else "-"
        label = f"{s.season_start}/{str(s.season_start + 1)[-2:]}" + ("" if s.league else f" {s.kind}")
        print(f"{label:>9}  {name(s.team)[:30]:<30} {s.appearances:>4} ({s.substitute_appearances:>2}) {s.goals:>4} {s.assists:>4} "
              f"{s.player_of_match:>4}  {rating:>6} {s.minutes:>6}")
    if args.all:
        print("* other kinds: " + ", ".join(f"{k} {fm_save.KIND_NAMES[k]}" for k in sorted(fm_save.KIND_NAMES)))
    return 0


def cmd_awards(args) -> int:
    lang_db = fm_save.find_language_database()
    if lang_db is None:
        print("The game's lang_db.dat was not found; awards cannot be named.", file=sys.stderr)
        return 1
    award_names = fm_save.read_award_names(lang_db)
    with FmSave(args.save) as save:
        game_db = save.read(DB)
        numbers, _ = fm_save.read_person_names(game_db)
        person = next((number for number, uid in numbers.items() if uid == args.player), None)
        if person is None:
            print(f"No person with FM uid {args.player}.", file=sys.stderr)
            return 1
        keys = fm_save.read_award_keys(game_db, list(award_names))
        history = fm_save.read_award_history(save.read("award_year_hist_dt.cmt"))
        stats = fm_save.read_season_stats(save.read("player_stats_hist_dt.cmt"), save.read("player_stats_hist_ls.dat"))
        stat_groups = fm_save.read_stat_groups(game_db, stats, retirement_manager=save.read("retirement_manager.dat"))
        elevens = fm_save.read_best_elevens(save.read("tc_best_eleven_history_dt.cmt"), save.read("tc_best_eleven_history_ls.dat"))
        owners = fm_save.guess_best_eleven_clubs(elevens, stats, stat_groups, numbers)
        name = _names(save)
    for year in history:
        for place, entry in enumerate(year.places, 1):
            if entry.person == person:
                award = award_names.get(keys.get(year.award_key))
                print(f"{year.year}  {award.name if award else f'award key {year.award_key}'}  (place {place}, {entry.appearances} apps, {entry.goals} goals)")
    for group, seasons in enumerate(elevens):
        for season in seasons:
            for entry in season.players:
                if entry.person == person:
                    team = name(owners[group]) if group in owners else f"team group {group}"
                    role = "starting eleven" if entry.starting else "substitute"
                    print(f"{season.year}/{str(season.year + 1)[-2:]}  Seasonal Best Eleven, {team}: {fm_save.position_name(entry.position)} ({role})")
    return 0


def cmd_transfers(args) -> int:
    with FmSave(args.save) as save:
        numbers, _ = fm_save.read_person_names(save.read(DB))
        person = next((number for number, uid in numbers.items() if uid == args.player), None)
        if person is None:
            print(f"No person with FM uid {args.player}.", file=sys.stderr)
            return 1
        moves = [t for t in fm_save.read_transfers(save.read("transfer_man.dat")) if t.person == person]
        name = _names(save)
    for move in sorted(moves, key=lambda t: t.date):
        old = name(move.from_team) if move.from_team is not None else "-"
        new = name(move.to_team) if move.to_team is not None else "-"
        fee = f"  fee {move.fee:,}" + (f" (up to {move.fee_with_add_ons:,})" if move.fee_with_add_ons > move.fee else "") if move.fee else ""
        print(f"{move.date}  {fm_save.TRANSFER_KINDS.get(move.kind, f'kind {move.kind}'):<18} {old} -> {new}{fee}")
    return 0


def _command(sub, name: str, help_text: str, func, *arguments) -> None:
    parser = sub.add_parser(name, help=help_text)
    for flags, options in arguments:
        parser.add_argument(*flags, **options)
    parser.set_defaults(func=func)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    save = (("save",), {})
    limit = (("--limit",), {"type": int, "default": 10})

    _command(sub, "files", "List the named files inside a save.", cmd_files, save, (("--grep",), {}))
    _command(sub, "extract", "Write one decompressed file to disk.", cmd_extract,
             save, (("name",), {}), (("output",), {}))
    _command(sub, "diff", "Compare two saves file by file.", cmd_diff,
             (("save_a",), {}), (("save_b",), {}), (("--limit",), {"type": int, "default": 40}))
    _command(sub, "uid", "Find where a player's FM unique id appears.", cmd_uid, save, (("uid",), {"type": int}))
    _command(sub, "attributes", "Decode the attribute block at a Finishing-byte position.", cmd_attributes,
             save, (("position",), {"type": int}), (("--file",), {"default": DB}))
    _command(sub, "blocks", "Find every attribute block in game_db.dat.", cmd_blocks, save, limit)
    _command(sub, "players", "Read every player: id, birth date, 36 attributes, height, weight.", cmd_players,
             save, (("--uid",), {"type": int}), limit, (("--csv",), {}))
    _command(sub, "fixtures", "List fixtures and results with team names.", cmd_fixtures,
             save, (("--competition",), {"type": int}), (("--uid",), {"type": int}), (("--club",), {"type": int}),
             (("--played",), {"action": "store_true"}), (("--limit",), {"type": int, "default": 40}),
             (("--archived",), {"action": "store_true", "help": "include every archived fixture since the save began"}))
    _command(sub, "teams", "List teams with their club, FM uid and name.", cmd_teams,
             save, (("--grep",), {}), (("--limit",), {"type": int, "default": 40}))
    _command(sub, "winners", "Winners and runners-up of a competition, by its FM uid.", cmd_winners,
             save, (("competition",), {"type": int}), (("--limit",), {"type": int, "default": 20}))
    _command(sub, "career", "A player's league statistics per club and season, by FM uid.", cmd_career,
             save, (("player",), {"type": int}), (("--all",), {"action": "store_true", "help": "also cup and other records"}))
    _command(sub, "awards", "A player's awards and seasonal best elevens, by FM uid.", cmd_awards,
             save, (("player",), {"type": int}))
    _command(sub, "transfers", "A player's transfers, loans and releases, by FM uid (fees in pounds).", cmd_transfers,
             save, (("player",), {"type": int}))
    _command(sub, "table", "Show the current table of a league, by its FM uid (11 = Premier Division).", cmd_table,
             save, (("competition",), {"type": int}))

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FmSaveError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
