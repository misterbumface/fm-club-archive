"""Fill the archive's ``world`` database from a saved game (``flask import-save``).

The save is read once with app/services/fm_save.py; every save number (team,
person, stage, competition key) is translated to an FM uid here and never
stored. Rows are upserted by their natural keys, so importing the same save
twice changes nothing, and saves add up: a row with ``as_of`` keeps its values
unless the save being imported has the same or a later game date.
"""

from __future__ import annotations

import re

import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from pathlib import Path

from sqlalchemy import Table, delete, func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from app.extensions import db
from app.models import world
from app.services import fm_save, fm_tactics, manager_history, save_reading, season_from_reports
from config import ARCHIVE

log = logging.getLogger(__name__)

_CHUNK = 5000
_NONE = 0  # team uid for "no team" (a youth intake, a release, a season without a club)


@dataclass
class ImportResult:
    save_id: int
    game_date: date
    counts: Counter = field(default_factory=Counter)  # rows written, and rows skipped as "<table> skipped"


class SaveImportError(Exception):
    """The save or the game database cannot be read."""


def import_save(path: str | Path, lang_db: str | Path | None = None, progress: Callable[[str], None] = log.info) -> ImportResult:
    """Read the save at ``path`` and upsert everything into the ``world`` database."""
    path = Path(path)
    lang_db = Path(lang_db) if lang_db else fm_save.find_language_database(ARCHIVE.game_database_dir)
    if lang_db is None or not lang_db.exists():
        raise SaveImportError("The game's lang_db.dat was not found; competition and award names come from it.")
    engine = db.engines[world.BIND]
    create_tables(engine)
    reader = _SaveReader(path, lang_db, progress)
    reader.read()  # on several cores, before the database is locked for writing
    with engine.connect() as connection:
        check_same_game(connection, reader.parts["people"])
    with engine.begin() as connection:
        return reader.write(connection, path.name)


OTHER_GAME_SHARE = 0.10  # of the people a save shares with the archive (by uid), the share that are someone else
OTHER_GAME_MINIMUM = 500  # ... and at least this many: in the same game a few names change (renames, junk names)


def check_same_game(connection: Connection, people: dict) -> None:
    """Refuse a save from another game. Two games share the real players (same uid, same person),
    but each game makes its own new players under the same uids. Measured: a save of the same game
    has 0-1.5% of its shared uids on a different person (other name and birth date), another
    game's about 20%."""
    uids = sorted(people)
    shared = other = 0
    try:
        for start in range(0, len(uids), 20_000):
            chunk = ",".join(str(int(uid)) for uid in uids[start:start + 20_000])
            for uid, name, born in connection.execute(text(f"select fm_uid, name, birth_date from people where fm_uid in ({chunk})")):
                shared += 1
                person = people[uid]
                if name != person.name and str(born) != str(person.birth_date):
                    other += 1
    except Exception:  # noqa: BLE001 - a new archive has no people table yet
        return
    if other >= OTHER_GAME_MINIMUM and other > OTHER_GAME_SHARE * shared:
        raise SaveImportError(f"This save is from another game: {other:,} of the {shared:,} people it shares with this archive "
                              f"are someone else here ({other / shared:.0%}). Make a new archive for it.")


def create_tables(engine) -> None:
    """Make the missing tables, and the columns and indexes added to tables that already exist."""
    metadata = db.metadatas[world.BIND]
    metadata.create_all(engine)
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            have = {row[1] for row in connection.exec_driver_sql(f"pragma table_info({table.name})")}
            for column in table.columns:
                if column.name not in have and column.nullable:
                    kind = column.type.compile(dialect=engine.dialect)
                    connection.exec_driver_sql(f"alter table {table.name} add column {column.name} {kind}")
    for table in metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)


class _SaveReader:
    """Reads one save and writes it; the save's numbers stay inside this object."""

    def __init__(self, path: Path, lang_db: Path, progress: Callable[[str], None]) -> None:
        self.path = path
        self.lang_db = lang_db
        self.progress = progress
        self.counts: Counter = Counter()

    # -- reading ------------------------------------------------------------------------------
    def read(self) -> None:
        parts = save_reading.read_save(self.path, self.lang_db, self.progress)
        self.parts = parts
        self.fixtures = parts["fixtures"]
        played = [f.date for f in self.fixtures if f.played]
        if not played:
            raise SaveImportError("The save has no played matches, so its game date is not known.")
        self.game_date = max(played)
        self.clubs, self.teams = parts["clubs"], parts["teams"]
        self.nation_names = parts["nation_names"]
        self.team_nations = fm_save.national_team_nations(self.teams, self.nation_names)
        self.team_uid = {number: team.fm_uid for number, team in self.teams.items()}
        self.person_uid, self.people = parts["person_uid"], parts["people"]
        self.competition_names, self.competition_keys = parts["competition_names"], parts["competition_keys"]
        self.stages, self.competition_reputations = parts["stages"], parts["competition_reputations"]

    # -- writing ------------------------------------------------------------------------------
    def write(self, connection: Connection, file_name: str) -> ImportResult:
        self._write_competitions(connection)
        self._write_clubs_and_teams(connection)
        self._write_editions(connection)
        self._write_fixtures(connection)  # before this save's row: it compares with the newest earlier save
        self._write_match_reports(connection)
        save_id = self._write_save(connection, file_name)
        self._write_people(connection, save_id)
        self._write_club_relations(connection)
        self._write_club_finances(connection)
        self._write_manager_history(connection)
        self._write_injuries(connection)
        self._write_staff_profiles(connection)
        self._write_partnerships(connection)
        self._write_moods(connection)
        self._write_tactics(connection)
        self._write_transfer_market(connection)
        self._write_player_games(connection)
        self._write_contracts(connection)
        self._write_stadiums(connection)
        self._write_press_answers(connection)
        self._write_news_items(connection)
        self._write_player_seasons(connection)
        self._write_awards(connection)
        self._write_transfers(connection)
        self._clear_retired_teams(connection, save_id)
        self._drop_unconfirmed_names(connection)
        self._write_competition_facts(connection)
        self._write_competition_reputations(connection)
        self.counts["player_seasons from match reports"] = season_from_reports.fill_current_seasons(connection, self.game_date)
        counts = dict(self.counts)
        connection.execute(world.WorldSave.__table__.update().where(world.WorldSave.id == save_id).values(counts=counts))
        return ImportResult(save_id, self.game_date, self.counts)

    def _write_competition_facts(self, connection: Connection) -> None:
        """Each competition's scope, country and (for a senior league) level, from the world itself."""
        facts = competition_facts(connection, self.game_date)
        table = world.WorldCompetition.__table__
        connection.execute(table.update().values(scope=None, nation_uid=None, level=None))
        for uid, row in facts.items():
            connection.execute(table.update().where(table.c.fm_uid == uid).values(**row))
        self.counts["competitions with a scope"] += len(facts)
        self.counts["leagues with a level"] += sum(1 for row in facts.values() if row["level"])

    def _write_competition_reputations(self, connection: Connection) -> None:
        """The game's reputation of each competition, when this save is the newest."""
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        if newest is not None and newest > self.game_date:
            return
        table = world.WorldCompetition.__table__
        connection.execute(table.update().values(reputation=None))
        for uid, value in self.competition_reputations.items():
            connection.execute(table.update().where(table.c.fm_uid == uid).values(reputation=value))
        self.counts["competitions with a reputation"] += len(self.competition_reputations)

    def _write_club_relations(self, connection: Connection) -> None:
        """Every club's rivals and favourite and disliked people, when this save is the newest:
        they are the clubs as they are now, so a newer save replaces them all."""
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        found = self.parts.get("club_relations")
        if not found or (newest is not None and newest > self.game_date):
            return  # an older save, or none read: the lists stay as they are
        # a person the save gives no name (a few old database people) would show an older, wrong one
        named = set(self.people) | {player.uid for player in self.parts.get("players", ())}
        rows = {}
        for index, relations in found.items():
            club = self.club_uid.get(index)
            if club is None:
                continue
            for r in relations:
                subject = self.club_uid.get(r.subject) if r.relation == fm_save.RIVAL else self.person_uid.get(r.subject)
                if subject is None or (r.relation != fm_save.RIVAL and subject not in named):
                    self.counts["club_relations skipped"] += 1
                    continue
                rows[(club, r.relation, subject)] = {
                    "club_uid": club, "relation": r.relation, "subject_uid": subject, "reason": r.reason, "level": r.level,
                    "permanent": int(r.permanent), "names_stadium": int(r.names_stadium), "as_of": self.game_date}
        connection.execute(delete(world.WorldClubRelation.__table__))
        self._put(connection, world.WorldClubRelation, list(rows.values()), ["club_uid", "relation", "subject_uid"])

    def _write_injuries(self, connection: Connection) -> None:
        """Every linked player's injuries, past and current, when this save is the newest: each save holds the whole
        history, so a newer save replaces them all."""
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        history, groups = self.parts.get("injury_history"), self.parts.get("injury_groups")
        if not history or not groups or (newest is not None and newest > self.game_date):
            return
        current = self.parts.get("current_injuries", {})
        rows = []
        for uid in set(groups) | set(current):
            past = history[groups[uid]] if uid in groups and groups[uid] < len(history) else []
            for seq, (injury, now) in enumerate([(i, False) for i in past] + [(i, True) for i in current.get(uid, ())]):
                rows.append({"person_uid": uid, "seq": seq, "injury": injury.injury, "injured_on": injury.injured_on, "days": injury.days,
                             "severity": injury.severity, "place": injury.place, "cause": fm_save.INJURY_CAUSES.get(injury.cause),
                             "treatment": fm_save.INJURY_TREATMENTS.get(injury.treatment),
                             "team_uid": self.team_uid.get(injury.team) if injury.team is not None else None,
                             "current": int(now), "as_of": self.game_date})
        connection.execute(delete(world.WorldInjury.__table__))
        self._put(connection, world.WorldInjury, rows, ["person_uid", "seq"])

    def _write_club_finances(self, connection: Connection) -> None:
        """Each club's bank balance and budgets for this save's date (the archive keeps them all), and, when this
        save is the newest, its income streams and debts as they are now."""
        found = self.parts.get("club_finances")
        if not found:
            return
        snapshots, incomes, debts = [], [], []
        for index, f in found.items():
            club = self.club_uid.get(index)
            if club is None or f.bank is None:  # only the clubs the game runs in detail have finances
                continue
            snapshots.append({"club_uid": club, "game_date": self.game_date, "bank": f.bank, "transfer_budget": f.transfer_budget,
                              "wage_budget": f.wage_budget, "yearly_income": sum(i.yearly for i in f.incomes) if f.incomes else None,
                              "debt": sum(d.amount for d in f.debts) if f.debts else None})
            incomes += [{"club_uid": club, "seq": n, "kind": i.kind, "total": i.total, "yearly": i.yearly, "starts": i.start, "ends": i.end,
                         "renews": int(i.renews), "as_of": self.game_date} for n, i in enumerate(f.incomes)]
            debts += [{"club_uid": club, "seq": n, "source": d.source, "amount": d.amount, "monthly_repayment": d.monthly_repayment,
                       "monthly_interest": d.monthly_interest, "conditional_repayment": d.conditional_repayment, "starts": d.start,
                       "ends": d.end, "in_fpp": int(d.in_fpp), "as_of": self.game_date} for n, d in enumerate(f.debts)]
        self._put(connection, world.WorldClubFinance, snapshots, ["club_uid", "game_date"])
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        if newest is not None and newest > self.game_date:
            return
        connection.execute(delete(world.WorldClubIncome.__table__))
        connection.execute(delete(world.WorldClubDebt.__table__))
        self._put(connection, world.WorldClubIncome, incomes, ["club_uid", "seq"])
        self._put(connection, world.WorldClubDebt, debts, ["club_uid", "seq"])

    def _write_manager_history(self, connection: Connection) -> None:
        """The managers' spells of every team whose list the results single out (manager_history), when this
        save is the newest: the save keeps them all, so the newest replaces them."""
        lists = self.parts.get("manager_history")
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        if not lists or (newest is not None and newest > self.game_date):
            return
        self.progress("Writing manager history")
        links = manager_history.link_lists(connection, lists)
        rows = []
        for number, team_uid in links.items():
            for seq, spell in enumerate(lists[number]):
                rows.append({"team_uid": team_uid, "seq": seq, "person_uid": self.person_uid.get(spell.person), "started": spell.started,
                             "ended": spell.ended, "won": spell.won, "drawn": spell.drawn, "lost": spell.lost, "reason": spell.reason,
                             "league_wins": spell.league_wins, "cup_wins": spell.cup_wins, "as_of": self.game_date})
        self.counts["manager history lists linked"] += len(links)
        self.counts["manager history lists not linked"] += len(lists) - len(links)
        connection.execute(delete(world.WorldManagerSpell.__table__))
        self._put(connection, world.WorldManagerSpell, rows, ["team_uid", "seq"])

    def _drop_unconfirmed_names(self, connection: Connection) -> None:
        """Older readers gave some people a name from another person's block in their record
        ("DJ Buffonge" for 653 people). When a save knows a person but no name for them, nothing
        in the archive refers to them and no newer save wrote their row, the row goes."""
        named = set(self.people) | {player.uid for player in self.parts["players"]}
        nameless = [{"uid": uid} for uid in set(self.person_uid.values()) - named]
        connection.exec_driver_sql("create temp table if not exists nameless (uid integer primary key)")
        connection.exec_driver_sql("delete from nameless")
        if nameless:
            connection.execute(text("insert or ignore into nameless (uid) values (:uid)"), nameless)
        used = " ".join(f"and fm_uid not in (select {column} from {table} where {column} is not null)" for table, column in (
            ("player_snapshots", "person_uid"), ("player_seasons", "person_uid"), ("match_players", "person_uid"),
            ("match_reports", "player_of_the_match_uid"), ("match_reports", "referee_uid"), ("goals", "scorer_uid"),
            ("award_places", "person_uid"), ("best_elevens", "person_uid"), ("transfers", "person_uid")))
        result = connection.execute(text(f"delete from people where as_of <= :d and fm_uid in (select uid from nameless) {used}"),
                                    {"d": self.game_date})
        self.counts["people without a confirmed name (removed)"] += result.rowcount

    def _clear_retired_teams(self, connection: Connection, save_id: int) -> None:
        """A retired player's record keeps the last club. A person still plays when this save
        has their attributes, or league games in the last two seasons, or a move in the last
        two years; the others lose their current team."""
        season = self.game_date.year if self.game_date.month >= 7 else self.game_date.year - 1
        cutoff = self.game_date - timedelta(days=730)
        result = connection.execute(text(
            "update people set team_uid = null where team_uid is not null and as_of = :today "
            "and fm_uid not in (select person_uid from player_snapshots where save_id = :save) "
            "and fm_uid not in (select person_uid from player_seasons where season_start >= :season and kind = 1) "
            "and fm_uid not in (select person_uid from transfers where date >= :cutoff)"),
            {"today": self.game_date, "save": save_id, "season": season - 1, "cutoff": cutoff})
        self.counts["people without a team (retired)"] += result.rowcount

    def _write_save(self, connection: Connection, file_name: str) -> int:
        table = world.WorldSave.__table__
        _upsert(connection, table, [{"file_name": file_name, "game_date": self.game_date, "imported_at": datetime.now()}], ["file_name"])
        return connection.execute(select(table.c.id).where(table.c.file_name == file_name)).scalar_one()

    def _write_competitions(self, connection: Connection) -> None:
        rows = [
            {"fm_uid": uid, "name": c.name, "short_name": c.short_name, "abbreviation": c.abbreviation}
            for uid, c in self.competition_names.items()
        ]
        self._put(connection, world.WorldCompetition, rows, ["fm_uid"])

    def _write_clubs_and_teams(self, connection: Connection) -> None:
        nations = [
            {"fm_uid": n.fm_uid, "name": n.name, "short_name": n.short_name, "abbreviation": n.abbreviation, "adjective": n.adjective}
            for n in self.nation_names.values()
        ]
        self._put(connection, world.WorldNation, nations, ["fm_uid"])
        first_team: dict[int, fm_save.Team] = {}
        for team in sorted(self.teams.values(), key=lambda t: (t.age_limit is not None, bool(t.own_name), t.fm_uid)):
            if not team.national:
                first_team.setdefault(team.club_index, team)
        self.club_uid = {index: team.fm_uid for index, team in first_team.items()}
        nation_uid = {index: nation.fm_uid for index, nation in self.team_nations.items()}
        clubs = [
            {"fm_uid": self.club_uid[index], "name": club.name, "short_name": club.short_name,
             "nation_uid": nation_uid.get(club.nation_index), "league_nation_uid": nation_uid.get(club.league_nation_index),
             "as_of": self.game_date}
            for index, club in self.clubs.items()
            if index in self.club_uid
        ]
        self._put(connection, world.WorldClub, clubs, ["fm_uid"], guarded=True)
        nation_names = {index: nation.name for index, nation in self.team_nations.items()}
        stadiums = self.parts["stadiums"]
        teams = []
        for team in _unique(self.teams.values(), key=lambda t: t.fm_uid):
            nation = self.team_nations.get(team.club_index) if team.national else None
            if team.national and nation is None:
                self.counts["teams skipped"] += 1  # a national team whose nation has no senior team record
                continue
            teams.append(
                {
                    "fm_uid": team.fm_uid,
                    "club_uid": None if team.national else self.club_uid[team.club_index],
                    "nation_uid": nation.fm_uid if nation else None,
                    "name": fm_save.team_name(team, self.clubs, nation_names),
                    "age_limit": team.age_limit,
                    "reputation": team.reputation,
                    "stadium_uid": stadiums[team.stadium].uid if team.stadium in stadiums else None,
                    "as_of": self.game_date,
                }
            )
        self._put(connection, world.WorldTeam, teams, ["fm_uid"], guarded=True)
        self.known_teams = {row["fm_uid"] for row in teams}

    def _write_people(self, connection: Connection, save_id: int) -> None:
        nation_uid = {index: nation.fm_uid for index, nation in self.team_nations.items()}
        looks = self.parts["appearances"]
        no_look = fm_save.Appearance(None, None, None, None, None)
        people = {}
        for uid, person in self.people.items():
            look = looks.get(uid, no_look)
            people[uid] = {
                "fm_uid": uid,
                "name": person.name,
                "first_name": person.first_name,
                "last_name": person.last_name,
                "birth_date": person.birth_date,
                "nation_uid": nation_uid.get(look.nation_index),
                "ethnicity": look.ethnicity,
                "skin_tone": look.skin_tone,
                "hair_colour": look.hair_colour,
                "hair_length": look.hair_length,
                "as_of": self.game_date,
            }
        snapshots = []
        for player in self.parts["players"]:
            if player.uid not in people:
                look = looks.get(player.uid, no_look)
                people[player.uid] = {
                    "fm_uid": player.uid,
                    "name": f"{player.first_name or ''} {player.last_name or ''}".strip() or str(player.uid),
                    "first_name": player.first_name,
                    "last_name": player.last_name,
                    "birth_date": player.birth_date,
                    "nation_uid": nation_uid.get(look.nation_index),
                    "ethnicity": look.ethnicity,
                    "skin_tone": look.skin_tone,
                    "hair_colour": look.hair_colour,
                    "hair_length": look.hair_length,
                    "as_of": self.game_date,
                }
            snapshots.append(
                {
                    "person_uid": player.uid,
                    "save_id": save_id,
                    "height_cm": player.height_cm,
                    "weight_kg": player.weight_kg,
                    "attributes": player.attributes,
                    "positions": player.positions,
                    "traits": player.traits,
                    "left_foot": player.left_foot,
                    "right_foot": player.right_foot,
                    "preferred_moves": list(player.preferred_moves) if player.preferred_moves is not None else None,
                }
            )
        histories = self.parts["team_histories"]
        reputations = self.parts["person_reputations"]
        contracts = self.parts["staff_contracts"]
        for uid, row in people.items():  # _clear_retired_teams drops the teams of people who no longer play
            team = self.team_uid.get(fm_save.current_team(histories.get(uid, ()), self.teams))
            row["team_uid"] = team if team in self.known_teams else None
            reputation = reputations.get(uid)
            row["reputation_home"] = reputation.home if reputation else None
            row["reputation_current"] = reputation.current if reputation else None
            row["reputation_world"] = reputation.world if reputation else None
            row["current_ability"] = reputation.ability if reputation else None
            row["potential_ability"] = reputation.potential if reputation else None
            contract = contracts.get(uid)
            staff = contract if contract and contract.job != fm_save.PLAYER_JOB else None
            team_uid = self.team_uid.get(staff.team) if staff else None
            row["staff_job"] = staff.job_name if staff and team_uid in self.known_teams else None
            row["staff_team_uid"] = team_uid if row["staff_job"] else None
            row["staff_since"] = staff.started if row["staff_job"] else None
            row["staff_wage"] = staff.wage if row["staff_job"] else None
            row["staff_contract_ends"] = staff.expires if row["staff_job"] else None
        self.counts["people with a reputation"] += len(reputations)
        self.counts["staff with a job"] += sum(1 for row in people.values() if row["staff_job"])
        self._put(connection, world.WorldPerson, list(people.values()), ["fm_uid"], guarded=True)
        # The save file can be imported again at a later game date (the same name, saved over): its
        # snapshots are what it holds now, not players who have retired since.
        connection.execute(text("delete from player_snapshots where save_id = :save"), {"save": save_id})
        self._put(connection, world.WorldPlayerSnapshot, list(_unique(snapshots, key=lambda r: r["person_uid"])), ["person_uid", "save_id"])

    def _write_editions(self, connection: Connection) -> None:
        seasons = self.parts["competition_seasons"]
        editions, places = {}, {}
        for season in seasons:
            uid = self.competition_keys.get(season.competition_key)
            if uid not in self.competition_names:
                self.counts["competition_editions skipped"] += 1
                continue
            editions[(uid, season.year)] = {
                "competition_uid": uid,
                "year": season.year,
                "season_label": season.season_label,
                "decided_on": season.decided_on,
            }
            for place, number in enumerate(season.slots, 1):
                team = self.team_uid.get(number) if number is not None else None
                if team in self.known_teams:
                    places[(uid, season.year, place)] = {"competition_uid": uid, "year": season.year, "place": place, "team_uid": team}
                elif number is not None:
                    self.counts["edition_places skipped"] += 1
        self._put(connection, world.WorldEdition, list(editions.values()), ["competition_uid", "year"])
        self._put(connection, world.WorldEditionPlace, list(places.values()), ["competition_uid", "year", "place"])

    def _write_fixtures(self, connection: Connection) -> None:
        """Played fixtures are facts and add up; unplayed ones are only the newest save's schedule.

        A save older than the newest imported one writes no unplayed fixtures, and the
        newest save removes unplayed fixtures that older saves wrote (moved or cancelled).
        """
        self.progress("Writing fixtures")
        table = world.WorldFixture.__table__
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        is_newest = newest is None or self.game_date >= newest
        if is_newest:
            stale = (table.c.home_goals.is_(None)) & (table.c.as_of < self.game_date)
            self.counts["fixtures unscheduled"] += connection.execute(delete(table).where(stale)).rowcount
        # A played fixture already stored with this score and competition is written again only if something differs:
        # a result does not change, and the save holds every one since the game began (1.4 million in 40 seasons).
        stored = {(row.date, row.home_uid, row.away_uid): (row.home_goals, row.away_goals, row.competition_uid)
                  for row in connection.execute(select(table.c.date, table.c.home_uid, table.c.away_uid, table.c.home_goals,
                                                       table.c.away_goals, table.c.competition_uid).where(table.c.home_goals.isnot(None)))}
        rows, goals = {}, {}
        for fixture in self.fixtures:
            if not fixture.played and not is_newest:
                continue
            home, away = self.team_uid.get(fixture.home_id), self.team_uid.get(fixture.away_id)
            if home not in self.known_teams or away not in self.known_teams:
                self.counts["fixtures skipped"] += 1
                continue
            key = (fixture.date, home, away)
            if fixture.played and stored.get(key) == (fixture.home_goals, fixture.away_goals, self.stages.get(fixture.competition_id)):
                self.counts["fixtures already stored"] += 1
                continue
            rows[key] = {
                "date": fixture.date,
                "home_uid": home,
                "away_uid": away,
                "competition_uid": self.stages.get(fixture.competition_id),
                "stage": fixture.competition_id,
                "home_goals": fixture.home_goals,
                "away_goals": fixture.away_goals,
                "home_goals_after_extra_time": fixture.home_goals_after_extra_time,
                "away_goals_after_extra_time": fixture.away_goals_after_extra_time,
                "home_penalties": fixture.home_penalties,
                "away_penalties": fixture.away_penalties,
                "attendance": fixture.attendance,
                "away_fans": fixture.away_fans,
                "as_of": self.game_date,
            }
            if fixture.goals:
                goals[key] = fixture.goals
        self._put(connection, world.WorldFixture, list(rows.values()), ["date", "home_uid", "away_uid"], guarded=True)
        ids = {
            (row.date, row.home_uid, row.away_uid): row.id
            for row in connection.execute(select(table.c.id, table.c.date, table.c.home_uid, table.c.away_uid))
        }
        goal_rows = []
        for key, fixture_goals in goals.items():
            for seq, goal in enumerate(fixture_goals):
                goal_rows.append(
                    {
                        "fixture_id": ids[key],
                        "seq": seq,
                        "scorer_uid": self.person_uid.get(goal.player_id),
                        "side": goal.side,
                        "minute": goal.minute,
                        "added_time": goal.added_time,
                        "kind": goal.kind,
                    }
                )
        self._put(connection, world.WorldGoal, goal_rows, ["fixture_id", "seq"])

    def _write_match_reports(self, connection: Connection) -> None:
        """The game's detailed match reports. A save keeps them only for recent matches, so
        each save adds its own; a report never changes once the match is played."""
        self.progress("Writing match reports")
        reports = self.parts["match_reports"]
        played: dict[tuple[int, int, int], list[fm_save.Fixture]] = {}
        for fixture in self.fixtures:
            if fixture.played and (self.game_date - fixture.date).days <= MATCH_REPORT_DAYS:
                played.setdefault((fixture.fixture_id, fixture.home_id, fixture.away_id), []).append(fixture)
        table = world.WorldFixture.__table__
        ids = {(row.date, row.home_uid, row.away_uid): row.id
               for row in connection.execute(select(table.c.id, table.c.date, table.c.home_uid, table.c.away_uid)
                                             .where(table.c.date >= self.game_date - timedelta(days=MATCH_REPORT_DAYS)))}
        report_rows, team_rows, player_rows, shot_rows = {}, [], [], []
        # a match is often stored twice, and one copy can lack the shots: the copies with shots first
        reports = sorted(reports, key=lambda r: not any(p.shot_list for side in (r.home, r.away) for p in side.players))
        for report in reports:
            fixture = _report_fixture(report, played.get((report.fixture_id, report.home.team, report.away.team), []))
            if fixture is None:
                self.counts["match reports unlinked"] += 1
                continue
            fixture_id = ids.get((fixture.date, self.team_uid.get(fixture.home_id), self.team_uid.get(fixture.away_id)))
            if fixture_id is None or fixture_id in report_rows:
                continue
            report_rows[fixture_id] = {
                "fixture_id": fixture_id, "stadium": report.stadium, "referee_uid": self.person_uid.get(report.referee),
                "referee_name": report.referee_name, "player_of_the_match_uid": self.person_uid.get(report.player_of_the_match),
                "minutes": report.minutes, "as_of": self.game_date,
            }
            for side_number, side in enumerate((report.home, report.away)):
                team_rows.append({
                    "fixture_id": fixture_id, "side": side_number, "team_uid": self.team_uid.get(side.team), "squad": side.squad,
                    "passes": side.passes, "passes_completed": side.passes_completed, "corners": side.corners,
                    "woodwork": side.woodwork, "clear_cut_chances": side.clear_cut_chances, "fouls": side.fouls,
                    "yellow_cards": side.yellow_cards, "red_cards": side.red_cards, "as_of": self.game_date,
                })
                shots = sorted(((shot, player) for player in side.players for shot in player.shot_list),
                               key=lambda item: (item[0].minute, item[0].second))
                for seq, (shot, player) in enumerate(shots):
                    shot_rows.append({"fixture_id": fixture_id, "side": side_number, "seq": seq, "person_uid": self.person_uid.get(player.person),
                                      "minute": shot.minute, "second": shot.second, "outcome": fm_save.SHOT_OUTCOMES[shot.outcome],
                                      "across": shot.across, "height": shot.height, "outside_box": _outside_box(shot, player),
                                      "kind": shot.kind, "as_of": self.game_date})
                for seq, player in enumerate(side.players):
                    row = {name: getattr(player, name) for name in world.MATCH_PLAYER_FIGURES}
                    row.update(fixture_id=fixture_id, side=side_number, seq=seq, person_uid=self.person_uid.get(player.person),
                               played=int(player.played), rating=player.rating or None, distance=player.distance or None,
                               xg=round(player.xg, 3), shirt_number=player.shirt or None,
                               start_position=fm_save.position_name(player.start_slot), end_position=fm_save.position_name(player.end_slot),
                               as_of=self.game_date)
                    player_rows.append(row)
        self._put(connection, world.WorldMatchReport, list(report_rows.values()), ["fixture_id"], guarded=True)
        self._put(connection, world.WorldMatchTeam, team_rows, ["fixture_id", "side"], guarded=True)
        self._put(connection, world.WorldMatchPlayer, player_rows, ["fixture_id", "side", "seq"], guarded=True)
        self._put(connection, world.WorldMatchShot, shot_rows, ["fixture_id", "side", "seq"], guarded=True)

    def _write_staff_profiles(self, connection: Connection) -> None:
        profiles = self.parts.get("staff_profiles") or {}
        rows = [{"person_uid": uid, **p.coaching, "working_with_youngsters": p.working_with_youngsters, "formation": p.formations[0],
                 "formation_second": p.formations[1], "formation_other": p.formations[2], "formation_other_2": p.formations[3],
                 "tactical_style": p.tactical_style, "mentality": p.mentality, "pressing": p.pressing, "as_of": self.game_date}
                for uid, p in profiles.items()]
        self._put(connection, world.WorldStaffProfile, rows, ["person_uid"], guarded=True)

    def _write_partnerships(self, connection: Connection) -> None:
        """The human club's partnership table, replaced by this save's (the game prunes it). Its club: the one most of the
        pairs' players who have not left are at."""
        found = self.parts.get("partnerships") or []
        contracts = self.parts["staff_contracts"]
        teams = Counter(self.team_uid.get(contracts[uid].team) for a, b, p in found if not p.departed for uid in (a, b) if uid in contracts)
        teams.pop(None, None)
        if not teams:
            return
        team = teams.most_common(1)[0][0]
        table = world.WorldPartnership.__table__
        connection.execute(table.delete().where(table.c.team_uid == team))
        rows = {(a, b, p.role_a, p.role_b): {"team_uid": team, "person_a": a, "person_b": b, "role_a": p.role_a, "role_b": p.role_b,
                                              "matches": p.matches, "rating_sum": p.rating_sum, "goals_for": p.goals_for,
                                              "goals_against": p.goals_against, "departed": int(p.departed), "as_of": self.game_date}
                for a, b, p in found}
        self._put(connection, world.WorldPartnership, list(rows.values()), ["team_uid", "person_a", "person_b", "role_a", "role_b"])

    def _write_moods(self, connection: Connection) -> None:
        """Morale, happiness, concerns, feelings and the human club's social groups: what the newest save says now (an
        older save leaves them as they are)."""
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        if newest is not None and self.game_date < newest:
            return
        reputations, contracts = self.parts["person_reputations"], self.parts["staff_contracts"]
        known = set(self.people)
        moods, concerns, feelings = [], [], []
        for uid in known:
            reputation, contract = reputations.get(uid), contracts.get(uid)
            morale = reputation.morale if reputation else None
            happiness = contract.happiness if contract else None
            if morale is not None or happiness is not None:
                moods.append({"person_uid": uid, "morale": morale, "happiness": happiness, "sharpness": reputation and reputation.sharpness,
                              "fatigue": reputation and reputation.fatigue, "condition": reputation and reputation.condition,
                              "as_of": self.game_date})
            if not contract:
                continue
            for seq, concern in enumerate(contract.concerns):
                code = (concern.kind, concern.code)
                key = getattr(concern, fm_save.CONCERN_COMPETITION[code]) if code in fm_save.CONCERN_COMPETITION else None
                nation = self.team_nations.get(concern.ref - 1) if code == fm_save.RETURN_HOME and concern.ref else None
                concerns.append({"person_uid": uid, "seq": seq, "kind": concern.kind, "code": concern.code, "stage": concern.stage,
                                 "ref": concern.ref, "ref2": concern.ref2,
                                 "about_person_uid": concern.person if concern.person in known else None,
                                 "competition_uid": self.competition_keys.get(key) if key is not None else None,
                                 "nation_uid": nation.fm_uid if nation else None, "started": concern.started, "ended": concern.ended,
                                 "as_of": self.game_date})
            for feeling in contract.feelings:
                if feeling.person in known:
                    feelings.append({"person_uid": uid, "other_uid": feeling.person, "likes": int(feeling.likes), "reason": feeling.reason,
                                     "level": feeling.level, "as_of": self.game_date})
        for model in (world.WorldPersonMood, world.WorldPersonConcern, world.WorldPersonFeeling):
            connection.execute(model.__table__.delete())
        self._put(connection, world.WorldPersonMood, moods, ["person_uid"])
        self._put(connection, world.WorldPersonConcern, concerns, ["person_uid", "seq"])
        self._put(connection, world.WorldPersonFeeling, list(_unique(feelings, key=lambda r: (r["person_uid"], r["other_uid"], r["likes"]))),
                  ["person_uid", "other_uid", "likes"])
        team, groups = self.parts.get("social_groups") or (None, [])
        team_uid = self.team_uid.get(team) if team is not None else None
        if team_uid in self.known_teams and groups:
            table = world.WorldSocialGroup.__table__
            connection.execute(table.delete().where(table.c.team_uid == team_uid))
            rows = [{"team_uid": team_uid, "person_uid": uid, "group_number": number, "seq": seq, "as_of": self.game_date}
                    for number, group in enumerate(groups, 1) for seq, uid in enumerate(group) if uid in known]
            self._put(connection, world.WorldSocialGroup, list(_unique(rows, key=lambda r: r["person_uid"])), ["team_uid", "person_uid"])

    def _write_tactics(self, connection: Connection) -> None:
        """The human club's tactic slots (the newest save only) and the tactic it used in each match its match files
        keep. A match file holds its season and crowd, not its date: its fixture is the one between its two teams in
        that season with that crowd; among several, the one whose season squads (player_seasons, both clubs) hold the
        most of the people in the file, then the latest."""
        slots, matches = self.parts.get("tactic_slots") or [], self.parts.get("match_tactics") or []
        team_uid = self.team_uid.get(self.parts.get("human_team"))
        if team_uid not in self.known_teams or not (slots or matches):
            return
        self.progress("Writing tactics")
        table, slot_table = world.WorldTeamTactic.__table__, world.WorldTeamTacticSlot.__table__
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        rows: list[tuple[dict, fm_tactics.Tactic]] = []
        if newest is None or self.game_date >= newest:
            for number, tactic in enumerate(slots, 1):
                rows.append(({"team_uid": team_uid, "slot": number, "fixture_id": None, "played_on": None}, tactic))
        squads: dict[tuple[int, int], set[int]] = {}
        for r in connection.execute(text("select team_uid, season_start, person_uid from player_seasons")).mappings():
            squads.setdefault((r["team_uid"], r["season_start"]), set()).add(r["person_uid"])
        for _, match, uids in matches:
            home, away = match.home_uid, match.away_uid
            if team_uid not in (home, away) or match.season is None:
                continue
            found = connection.execute(text("select id, date, attendance from fixtures where home_uid = :h and away_uid = :a "
                                            "and home_goals is not null and date >= :s and date < :e order by date"),
                                       {"h": home, "a": away, "s": f"{match.season}-07-01", "e": f"{match.season + 1}-07-01"}).mappings().all()
            known = squads.get((home, match.season), set()) | squads.get((away, match.season), set())
            best = max(found, key=lambda f: (f["attendance"] == match.attendance, len(uids & known), str(f["date"])), default=None)
            if best is not None:
                rows.append(({"team_uid": team_uid, "slot": None, "fixture_id": best["id"],
                              "played_on": date.fromisoformat(str(best["date"])[:10])}, match.tactics[0]))
        numbers = self.parts.get("tactic_people") or {}
        if rows:  # the club's rows are replaced: the save keeps the same match files from one import to the next
            gone = select(table.c.id).where(table.c.team_uid == team_uid).where(
                table.c.slot.is_not(None) if not matches else table.c.slot.is_not(None) | table.c.fixture_id.is_not(None))
            connection.execute(delete(slot_table).where(slot_table.c.tactic_id.in_(gone)))
            connection.execute(delete(table).where(table.c.id.in_(gone)))
        for row, tactic in rows:
            made = connection.execute(table.insert().values(
                **row, name=tactic.name, style=tactic.style, mentality=tactic.mentality, fluidity=tactic.fluidity,
                formation=tactic.formation, as_of=self.game_date)).inserted_primary_key[0]
            slot_rows = [{"tactic_id": made, "seq": seq, "position": s.position, "side": s.side, "duty": s.duty, "role_key": s.role_key,
                          "role": s.role, "person_uid": None} for seq, s in enumerate(tactic.slots)]
            seen = set()
            for p in tactic.player_roles:
                uid = numbers.get(p.person_number)
                key = (uid, p.slot.position, p.slot.side)
                if uid in self.people and key not in seen:
                    seen.add(key)
                    slot_rows.append({"tactic_id": made, "seq": 100 + len(seen), "position": p.slot.position, "side": p.slot.side,
                                      "duty": p.slot.duty, "role_key": p.slot.role_key, "role": p.slot.role, "person_uid": uid})
            connection.execute(slot_table.insert(), slot_rows)
            self.counts["team_tactics"] += 1
            self.counts["team_tactic_slots"] += len(slot_rows)

    def _write_transfer_market(self, connection: Connection) -> None:
        """Who wants whom (the newest save's lists replace the table) and the bids (kept, by club, player, fee and date)."""
        interest, offers = self.parts.get("transfer_interest") or [], self.parts.get("transfer_offers") or []
        if not (interest or offers):
            return
        newest = connection.execute(select(func.max(world.WorldSave.game_date))).scalar_one()
        if newest is None or self.game_date >= newest:
            rows = [{"team_uid": self.team_uid.get(i.team), "person_uid": self.person_uid.get(i.person), "rumoured": int(i.rumoured),
                     "wage": i.wage or None, "offered_wage": i.offered_wage or None, "as_of": self.game_date} for i in interest]
            rows = [r for r in rows if r["team_uid"] in self.known_teams and r["person_uid"] in self.people]
            connection.execute(world.WorldTransferInterest.__table__.delete())
            self._put(connection, world.WorldTransferInterest, list(_unique(rows, key=lambda r: (r["team_uid"], r["person_uid"]))),
                      ["team_uid", "person_uid"])
        bids = [{"team_uid": self.team_uid.get(o.team), "person_uid": self.person_uid.get(o.person), "squad_uid": self.team_uid.get(o.squad),
                 "fee": o.fee, "status": o.status_word, "offered_on": o.offered_on, "until": o.until, "completes_on": o.completes_on,
                 "loan": int(o.loan), "loan_wage": o.loan_wage, "fee_guaranteed": o.fee_guaranteed, "fee_max": o.fee_max,
                 "as_of": self.game_date} for o in offers]
        bids = [b for b in bids if b["team_uid"] in self.known_teams and b["person_uid"] in self.people and b["offered_on"]]
        # a bid seen before with another status changed in this save (Juventus's open bid for Valera, rejected later: 18 Jan 2064)
        table = world.WorldTransferOffer.__table__
        stored = {(r.team_uid, r.person_uid, r.fee, r.offered_on): r for r in connection.execute(
            select(table.c.team_uid, table.c.person_uid, table.c.fee, table.c.offered_on, table.c.status, table.c.status_changed_on, table.c.as_of))}
        for b in bids:
            before = stored.get((b["team_uid"], b["person_uid"], b["fee"], b["offered_on"]))
            changed = before is not None and before.status != b["status"] and before.as_of < self.game_date
            b["status_changed_on"] = self.game_date if changed else before.status_changed_on if before is not None else None
            if before is None and b["status"] == "counter":  # a counter-proposal is news the day a save first shows it
                b["status_changed_on"] = self.game_date
        # guarded: an older save read later does not turn a rejected bid back into an open one
        self._put(connection, world.WorldTransferOffer, list(_unique(bids, key=lambda b: (b["team_uid"], b["person_uid"], b["fee"], b["offered_on"]))),
                  ["team_uid", "person_uid", "fee", "offered_on"], guarded=True)

    def _write_stadiums(self, connection: Connection) -> None:
        """Every stadium's place on the map, by its FM uid."""
        stadiums = self.parts.get("stadiums") or {}
        rows = [{"fm_uid": st.uid, "latitude": round(st.latitude, 5), "longitude": round(st.longitude, 5), "as_of": self.game_date}
                for st in stadiums.values()]
        self._put(connection, world.WorldStadium, list(_unique(rows, key=lambda r: r["fm_uid"])), ["fm_uid"], guarded=True)

    def _write_press_answers(self, connection: Connection) -> None:
        """The season's press conference answers: the speaker is the conference team's manager on the day (manager_spells,
        written before), the headline the game's own for that answer, with the names (fm_save.render_headline)."""
        answers = self.parts.get("press_answers") or []
        questions = self.parts.get("media_questions") or {}
        if not answers:
            return
        spells = {}
        for r in connection.execute(text("select team_uid, person_uid, started, ended from manager_spells where person_uid is not null")).mappings():
            spells.setdefault(r["team_uid"], []).append(r)
        people = {r["fm_uid"]: (r["name"], r["last_name"]) for r in connection.execute(text("select fm_uid, name, last_name from people")).mappings()}
        teams = {r["fm_uid"]: r["name"] for r in connection.execute(text("select fm_uid, name from teams")).mappings()}

        current = {r["staff_team_uid"]: (r["fm_uid"], r["staff_since"]) for r in connection.execute(text(
            "select fm_uid, staff_team_uid, staff_since from people where staff_job = 'Manager' and staff_team_uid is not null")).mappings()}

        def manager(team: int | None, day) -> int | None:
            for spell in spells.get(team, []):
                if (spell["started"] is None or str(spell["started"]) <= str(day)) and (spell["ended"] is None or str(spell["ended"]) >= str(day)):
                    return spell["person_uid"]
            now = current.get(team)  # the save keeps the spells of a few teams only: else today's manager, if he was there then
            if now and (now[1] is None or str(now[1]) <= str(day)):
                return now[0]
            return None

        rows = []
        for a in answers:
            team, opponent = self.team_uid.get(a.team), self.team_uid.get(a.opponent)
            subject, subject_team = self.person_uid.get(a.subject), self.team_uid.get(a.subject_team)
            if team is None or subject is None:
                continue
            speaker = manager(team, a.day)
            question = questions.get(a.question) or {}
            said = (question.get("answers") or {}).get(a.answer) or {}
            names_subject = "person#2" in (said.get("headline") or "") + (question.get("question") or "")  # asked about him, or answered about him
            # the conference head's opponent is the nearest head's, not always this answer's: a headline about the subject
            # takes his team as the other side
            other = subject_team if names_subject and subject_team else opponent
            headline = fm_save.render_headline(said.get("headline"), {1: people.get(speaker), 2: people.get(subject)},
                                               {1: teams.get(team), 2: teams.get(other)}) if speaker else None
            topic = question.get("topic")
            rows.append({"day": a.day, "team_uid": team, "question": a.question, "subject_uid": subject, "speaker_uid": speaker,
                         "opponent_uid": other, "subject_team_uid": subject_team, "answer": a.answer, "names_subject": int(names_subject),
                         "topic": topic.removeprefix("QID_").replace("_", " ").lower() if topic else None,
                         "tone": (said.get("tone") or "").replace("_", " ") or None, "headline": headline, "as_of": self.game_date})
        self._put(connection, world.WorldPressAnswer, list(_unique(rows, key=lambda r: (r["day"], r["team_uid"], r["question"], r["subject_uid"]))),
                  ["day", "team_uid", "question", "subject_uid"])
        self.counts["press answers"] = len(rows)

    def _write_news_items(self, connection: Connection) -> None:
        """The news items of the decoded kinds, each kept only when its fields agree with the world: the singled-out player
        plays for the team given, the linked manager is a manager, the pressured manager manages that team. A rumour keeps
        the day it was first seen."""
        items = self.parts.get("news_items") or []
        if not items:
            return
        people = {r["fm_uid"]: r for r in connection.execute(text("select fm_uid, team_uid, staff_job, staff_team_uid from people")).mappings()}
        seen = {r["key"]: date.fromisoformat(r["day"]) if isinstance(r["day"], str) else r["day"]  # raw SQL gives text
                for r in connection.execute(text("select key, day from news_items")).mappings()}
        rows = []
        for i in items:
            person, other = self.person_uid.get(i.person), self.person_uid.get(i.other_person)
            team, other_team = self.team_uid.get(i.team), self.team_uid.get(i.other_team)
            p, o = people.get(person) or {}, people.get(other) or {}
            if i.kind == "danger_man":
                good = person and other and team and o.get("team_uid") == team and person != other
                key = f"danger_man:{person}:{other}:{i.match_day}"
            elif i.kind == "job_link":
                good = person and team and other_team and team != other_team and p.get("staff_job") == "Manager"
                key = f"job_link:{person}:{other_team}:{i.day}"
            elif i.kind == "national_scouting":
                nation = connection.execute(text("select club_uid from teams where fm_uid = :t"), {"t": team}).scalar() if team else 1
                good = person and other and team and nation is None and p.get("staff_job") == "Manager" and p.get("staff_team_uid") == team and o
                key = f"national_scouting:{person}:{other}:{i.day}"
            elif i.kind == "board_pressure":
                good = person and team and p.get("staff_team_uid") == team
                key = f"board_pressure:{person}:{other_team}:{i.match_day}"
            else:
                good = person and team and p.get("team_uid") != team
                key = f"transfer_rumour:{person}:{team}:{i.fee}"
            if not good:
                continue
            day = i.day or seen.get(key) or self.game_date
            rows.append({"kind": i.kind, "key": key, "day": day, "person_uid": person, "other_person_uid": other, "team_uid": team,
                         "other_team_uid": other_team, "match_day": i.match_day, "fee": i.fee, "as_of": self.game_date})
        self._put(connection, world.WorldNewsItem, list(_unique(rows, key=lambda r: r["key"])), ["key"])

    def _write_contracts(self, connection: Connection) -> None:
        """Everyone's contract from this save, kept as history: a deal is a row by person, club and wage; a newer save
        updates its end, its happiness and its dates, an older one only fills the first-seen date."""
        contracts = self.parts.get("staff_contracts") or {}
        if not contracts:
            return
        self.progress("Writing contracts")
        rows = []
        for uid, c in contracts.items():
            team = self.team_uid.get(c.team)
            if uid not in self.people or team not in self.known_teams or not c.wage:
                continue
            rows.append({"person_uid": uid, "team_uid": team, "wage": c.wage, "job": c.job_name, "signed": c.signed, "joined": c.started,
                         "expires": c.expires, "happiness": c.happiness, "first_seen": self.game_date, "as_of": self.game_date})
        self._put(connection, world.WorldContract, list(_unique(rows, key=lambda r: (r["person_uid"], r["team_uid"], r["wage"]))),
                  ["person_uid", "team_uid", "wage"], guarded=True)
        if rows:  # an older save: the deals it holds were there already
            connection.execute(text("update contracts set first_seen = :d where person_uid = :p and team_uid = :t and wage = :w and first_seen > :d"),
                               [{"d": self.game_date, "p": r["person_uid"], "t": r["team_uid"], "w": r["wage"]} for r in rows])

    def _write_player_games(self, connection: Connection) -> None:
        """The tracked players' recent games, team by team (kept: a row never changes once the game is played). A list is
        taken as the player's only when the team is his nation's, or a club's he has seasons with, since a list is tied to
        its owner only by what follows it in the save."""
        games = self.parts.get("player_games") or []
        if not games:
            return
        self.progress("Writing players' recent games")
        teams = {row["fm_uid"]: row for row in connection.execute(text("select fm_uid, club_uid, nation_uid from teams")).mappings()}
        clubs: dict[int, set[int]] = {}
        for row in connection.execute(text("select distinct s.person_uid, t.club_uid from player_seasons s join teams t on t.fm_uid = s.team_uid "
                                           "where t.club_uid is not null")).mappings():
            clubs.setdefault(row["person_uid"], set()).add(row["club_uid"])
        nations = {row["fm_uid"]: row["nation_uid"] for row in connection.execute(text("select fm_uid, nation_uid from people where nation_uid is not null")).mappings()}
        table = world.WorldFixture.__table__
        fixtures = {(row.date, row.home_uid, row.away_uid): row.id
                    for row in connection.execute(select(table.c.id, table.c.date, table.c.home_uid, table.c.away_uid))}
        rows, skipped = [], 0
        for g in games:
            person, team, opponent = self.person_uid.get(g.person), self.team_uid.get(g.team), self.team_uid.get(g.opponent)
            if person not in self.people or team not in teams or g.date is None:
                continue
            t = teams[team]
            if not ((t["nation_uid"] is not None and t["nation_uid"] == nations.get(person)) or (t["club_uid"] in clubs.get(person, ()))):
                skipped += 1
                continue
            f = g.figures or {}
            rows.append({"person_uid": person, "team_uid": team, "played_on": g.date, "opponent_uid": opponent,
                         "fixture_id": fixtures.get((g.date, team, opponent)) or fixtures.get((g.date, opponent, team)),
                         "competition_uid": self.competition_keys.get(g.competition), "status": g.status_word,
                         "position": g.position_name,
                         "minutes": f.get("minutes"), "minute_off": f.get("minute_off") or None,
                         "rating": f["rating"] / 10 if f.get("rating") else None, "xg": f["xg"] / 100 if f else None,
                         "goals": f.get("goals"), "assists": f.get("assists"), "conceded": f.get("conceded"), "shots": f.get("shots"),
                         "shots_on_target": f.get("shots_on_target"), "passes": f.get("passes"), "passes_completed": f.get("passes_completed"),
                         "key_passes": f.get("key_passes"), "clear_cut_chances": f.get("clear_cut_chances"), "tackles": f.get("tackles"),
                         "tackles_won": f.get("tackles_won"), "key_tackles": f.get("key_tackles"), "headers": f.get("headers"),
                         "headers_won": f.get("headers_won"), "key_headers": f.get("key_headers"), "interceptions": f.get("interceptions"),
                         "dribbles": f.get("dribbles"), "fouls": f.get("fouls"), "fouled": f.get("fouled"), "offsides": f.get("offsides"),
                         "yellow_cards": f.get("yellow_cards"), "distance": f["distance"] / 10 if f else None, "as_of": self.game_date})
        self.counts["player_games skipped (team not the player's)"] = skipped
        self._put(connection, world.WorldPlayerGame, list(_unique(rows, key=lambda r: (r["person_uid"], r["team_uid"], r["played_on"]))),
                  ["person_uid", "team_uid", "played_on"], guarded=True)

    def _write_player_seasons(self, connection: Connection) -> None:
        self.progress("Writing season statistics")
        stats, groups = self.parts["season_stats"], self.parts["stat_groups"]
        rows = []
        for person, group in groups.items():
            seq: Counter = Counter()
            for record in stats[group]:
                team = _NONE if record.team == fm_save.NO_TEAM else self.team_uid.get(record.team)
                if team is None:
                    self.counts["player_seasons skipped"] += 1
                    continue
                key = (person, team, record.season_start, record.kind)
                rows.append(
                    {
                        "person_uid": person,
                        "team_uid": team,
                        "season_start": record.season_start,
                        "kind": record.kind,
                        "seq": seq[key],
                        "league_uid": self.competition_keys.get(record.league_key),
                        "starts": record.starts,
                        "substitute_appearances": record.substitute_appearances,
                        "rated_appearances": record.rated_appearances,
                        "goals": record.goals,
                        "assists": record.assists,
                        "player_of_match": record.player_of_match,
                        "goals_conceded": record.goals_conceded,
                        "clean_sheets": record.clean_sheets,
                        "yellow_cards": record.yellow_cards,
                        "red_cards": record.red_cards,
                        "minutes": record.minutes,
                        "rating_total": record.rating_total,
                        "as_of": self.game_date,
                    }
                )
                seq[key] += 1
        self._put(connection, world.WorldPlayerSeason, rows, ["person_uid", "team_uid", "season_start", "kind", "seq"], guarded=True)
        self.stats, self.stat_groups = stats, groups

    def _write_awards(self, connection: Connection) -> None:
        self.progress("Writing awards and best elevens")
        names = self.parts["award_names"]
        self._put(
            connection,
            world.WorldAward,
            [{"fm_uid": uid, "name": a.name, "short_name": a.short_name} for uid, a in names.items()],
            ["fm_uid"],
        )
        keys = self.parts["award_keys"]
        places = {}
        for year in self.parts["award_years"]:
            uid = keys.get(year.award_key)
            if uid not in names:
                self.counts["award_places skipped"] += len(year.places)
                continue
            for place, entry in enumerate(year.places, 1):
                places[(uid, year.year, place)] = {
                    "award_uid": uid,
                    "year": year.year,
                    "place": place,
                    "person_uid": self.person_uid.get(entry.person),
                    "club_uid": entry.club,
                    "age": entry.age,
                    "appearances": entry.appearances,
                    "goals": entry.goals,
                    "assists": entry.assists,
                    "rating_total": entry.rating_total,
                    "votes": entry.votes,
                }
        self._put(connection, world.WorldAwardPlace, list(places.values()), ["award_uid", "year", "place"])

        elevens = self.parts["best_elevens"]
        owners = fm_save.guess_best_eleven_clubs(elevens, self.stats, self.stat_groups, self.person_uid)
        rows = {}
        for group, seasons in enumerate(elevens):
            team = self.team_uid.get(owners[group], _NONE) if group in owners else _NONE
            save_group = 0 if team else group
            for season in seasons:
                for slot, player in enumerate(season.players):
                    rows[(team, save_group, season.year, slot)] = {
                        "team_uid": team,
                        "save_group": save_group,
                        "season_start": season.year,
                        "slot": slot,
                        "competition_uid": self.competition_keys.get(season.competition_key),
                        "person_uid": self.person_uid.get(player.person),
                        "position": fm_save.position_name(player.position),
                        "appearances": player.appearances,
                        "goals": player.goals,
                        "rating_total": player.rating_total,
                        "as_of": self.game_date,
                    }
        self._put(connection, world.WorldBestEleven, list(rows.values()), ["team_uid", "save_group", "season_start", "slot"], guarded=True)

    def _write_transfers(self, connection: Connection) -> None:
        self.progress("Writing transfers")
        rows = {}
        for move in self.parts["transfers"]:
            person = self.person_uid.get(move.person)
            teams = [_NONE if number is None else self.team_uid.get(number) for number in (move.from_team, move.to_team, move.squad)]
            if person is None or teams[0] is None or teams[1] is None:
                self.counts["transfers skipped"] += 1
                continue
            key = (person, move.date, move.kind, teams[0], teams[1])
            rows[key] = {
                "person_uid": person,
                "date": move.date,
                "kind": move.kind,
                "from_uid": teams[0],
                "to_uid": teams[1],
                "squad_uid": teams[2] or None,
                "fee": move.fee,
                "fee_with_add_ons": move.fee_with_add_ons,
            }
        self._put(connection, world.WorldTransfer, list(rows.values()), ["person_uid", "date", "kind", "from_uid", "to_uid"])

    def _put(self, connection: Connection, model, rows: list[dict], keys: list[str], guarded: bool = False) -> None:
        _upsert(connection, model.__table__, rows, keys, guarded)
        self.counts[model.__tablename__] += len(rows)


def _outside_box(shot, player) -> int | None:
    """Whether a goal came from outside the box: the report counts the scorer's such goals in the match, not which. All of
    his goals from outside: 1; none: 0; some: unknown (null). Not a goal: null."""
    if shot.outcome != fm_save.SHOT_GOAL or not player.goals:
        return None
    outside = min(player.goals_outside_box, player.goals)
    return 1 if outside == player.goals else 0 if outside == 0 else None


def _side_print(players) -> tuple:
    """A side of a match report told apart from every other by its players' figures, in squad order."""
    return tuple((round(p["rating"] or 0, 2), p["passes"], p["passes_completed"], p["minute_on"], p["minute_off"], p["goals"])
                 for p in players)


def backfill_match_positions(connection: Connection, save_path: Path) -> int:
    """Fill the shirt numbers and slots of match rows written before they were read, from one save's match reports (only
    that part of the save is read). A report is found among the stored ones by its players' figures; the rows updated."""
    with fm_save.FmSave(save_path) as save:
        reports = fm_save.read_match_reports(save.read("match_stats_cfg.dat"))
    stored: dict[tuple, list] = {}
    sides: dict[tuple[int, int], list[dict]] = {}
    for row in connection.execute(text("select fixture_id, side, seq, rating, passes, passes_completed, minute_on, minute_off, goals "
                                       "from match_players where shirt_number is null order by fixture_id, side, seq")).mappings():
        sides.setdefault((row["fixture_id"], row["side"]), []).append(row)
    for key, players in sides.items():
        stored.setdefault(_side_print(players), []).append(key)
    updates = []
    for report in reports:
        for side in (report.home, report.away):
            found = stored.get(_side_print([{"rating": p.rating or None, "passes": p.passes, "passes_completed": p.passes_completed,
                                              "minute_on": p.minute_on, "minute_off": p.minute_off, "goals": p.goals}
                                             for p in side.players]))
            if not found or len(found) != 1:
                continue
            fixture_id, side_number = found[0]
            updates += [{"f": fixture_id, "s": side_number, "q": seq, "shirt": p.shirt or None,
                         "start": fm_save.position_name(p.start_slot), "end": fm_save.position_name(p.end_slot)}
                        for seq, p in enumerate(side.players)]
    if updates:
        connection.execute(text("update match_players set shirt_number = :shirt, start_position = :start, end_position = :end "
                                "where fixture_id = :f and side = :s and seq = :q and shirt_number is null"), updates)
    return len(updates)


MATCH_REPORT_DAYS = 400  # saves keep reports for a few months; fixture ids and pairings repeat each season


def _report_fixture(report: fm_save.MatchReport, candidates: list[fm_save.Fixture]) -> fm_save.Fixture | None:
    """The played fixture a report belongs to: the one whose score fits the players' goals
    (own goals count for the other side, so a score may be higher), else the newest."""
    if not candidates:
        return None
    goals = (report.home.total("goals"), report.away.total("goals"))

    def fits(fixture: fm_save.Fixture) -> int:
        score = (fixture.home_goals_after_extra_time if fixture.home_goals_after_extra_time is not None else fixture.home_goals,
                 fixture.away_goals_after_extra_time if fixture.away_goals_after_extra_time is not None else fixture.away_goals)
        return 2 if score == goals else 1 if score[0] >= goals[0] and score[1] >= goals[1] else 0

    return max(candidates, key=lambda fixture: (fits(fixture), fixture.date))


def _upsert(connection: Connection, table: Table, rows: list[dict], keys: list[str], guarded: bool = False) -> None:
    """Insert ``rows``, updating rows with the same key (only from a save as new or newer when ``guarded``)."""
    if not rows:
        return
    statement = sqlite_insert(table)
    columns = [column.name for column in table.columns if column.name not in keys and column.name in rows[0]]
    if columns:
        statement = statement.on_conflict_do_update(
            index_elements=keys,
            set_={name: statement.excluded[name] for name in columns},
            where=(table.c.as_of <= statement.excluded.as_of) if guarded else None,
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=keys)
    for start in range(0, len(rows), _CHUNK):
        connection.execute(statement, rows[start : start + _CHUNK])


def _unique(items: Iterable, key):
    seen = set()
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            yield item


FACT_YEARS = 3  # seasons of fixtures that decide a competition's scope and country
CONTINENTAL_ONE_COUNTRY = 0.75  # a competition where one country has this share of the clubs is national
LEVEL_SEASONS = 10  # seasons of club moves that link a country's leagues
YOUTH_NAMES = re.compile(r"\b(U ?1\d|U ?2[0-3]|Under ?\d+|Primavera|Youth|Reserves?|Academy|Juniors?)\b", re.IGNORECASE)


def competition_facts(connection: Connection, game_date: date) -> dict[int, dict]:
    """Scope, country and level of every competition.

    Leagues come from the season statistics (FM keeps no fixtures for leagues it does not
    simulate in detail, but every club's league is in its statistics): a league's clubs are
    the first teams whose statistics name it, its country is the league country of most of
    them, and it is a youth league when most of its teams are second or youth teams. Other
    competitions come from their recent fixtures: national teams, youth, continental (clubs
    from three or more countries) or cup; names such as Friendly are friendlies.

    A senior league's level: in each country the league whose clubs draw the biggest home
    crowds (in any game) is 1, and clubs only move between leagues one level apart, so a
    league's level is one more than its distance from the top in moves of clubs.
    """
    from collections import Counter, defaultdict, deque

    from app.services.world_queries import is_friendly

    season = game_date.year if game_date.month >= 7 else game_date.year - 1
    since = date(season - FACT_YEARS, 7, 1)
    teams = {uid: (club, nation, age) for uid, club, nation, age in connection.execute(
        text("select fm_uid, club_uid, nation_uid, age_limit from teams"))}
    club_country = dict(connection.execute(text("select fm_uid, league_nation_uid from clubs")).all())
    names = dict(connection.execute(text("select fm_uid, name from competitions")).all())
    home_crowd: dict[int, list[int]] = defaultdict(list)
    sides: dict[int, Counter] = defaultdict(Counter)
    for competition, home, away, crowd in connection.execute(text(
            "select competition_uid, home_uid, away_uid, attendance from fixtures where home_goals is not null and date >= :d"),
            {"d": str(since)}):
        if crowd:
            home_crowd[home].append(crowd)
        if competition is None:
            continue
        for team in (home, away):
            club, nation, age = teams.get(team, (None, None, None))
            if nation and not club:
                sides[competition]["national"] += 1
            elif club:
                sides[competition]["youth" if (age or team != club) else "first"] += 1
                if club_country.get(club):
                    sides[competition][("country", club_country[club])] += 1

    # leagues, from the season statistics of the last seasons
    members: dict[int, Counter] = defaultdict(Counter)
    club_league: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for team, year, league in connection.execute(text(
            "select team_uid, season_start, league_uid from player_seasons where kind = 1 and league_uid is not null "
            "and season_start >= :s"), {"s": season - LEVEL_SEASONS}):
        club_league[(team, year)][league] += 1
    by_club: dict[int, dict[int, int]] = defaultdict(dict)
    for (team, year), count in club_league.items():
        league = count.most_common(1)[0][0]
        by_club[team][year] = league
        if year >= season - FACT_YEARS:
            members[league][team] += 1
    facts: dict[int, dict] = {}
    for league, count in members.items():
        first = [t for t in count if teams.get(t, (None,))[0] == t]
        countries = Counter(club_country.get(t) for t in first if club_country.get(t))
        youth = len(count) - len(first)
        is_youth = youth > len(first) or bool(YOUTH_NAMES.search(names.get(league, "")))
        facts[league] = {"scope": "youth" if is_youth else "league",
                         "nation_uid": countries.most_common(1)[0][0] if countries else None, "level": None}

    for uid, count in sides.items():
        if uid in facts:
            continue
        total = count["national"] + count["youth"] + count["first"]
        countries = Counter({key[1]: n for key, n in count.items() if isinstance(key, tuple)})
        country = countries.most_common(1)[0][0] if countries else None
        if is_friendly(names.get(uid, "")):
            scope = "friendly"
        elif YOUTH_NAMES.search(names.get(uid, "")):
            scope = "youth"  # also national youth tournaments: European U21 Championship, Under 20 World Cup
        elif count["national"] * 2 > total:
            scope, country = "national_teams", None
        elif count["youth"] * 2 > count["first"] + count["youth"]:
            scope = "youth"
        elif sum(1 for n in countries.values() if n >= 2) >= 3 and countries.most_common(1)[0][1] < CONTINENTAL_ONE_COUNTRY * sum(countries.values()):
            scope, country = "continental", None  # not a national cup with overseas clubs (the French Cup: Réunion, Martinique...)
        else:
            scope = "cup"
        facts[uid] = {"scope": scope, "nation_uid": country, "level": None}

    senior = {uid for uid, row in facts.items() if row["scope"] == "league" and row["nation_uid"]}
    links: dict[int, set[int]] = defaultdict(set)
    for seasons in by_club.values():
        for year, league in seasons.items():
            following = seasons.get(year + 1)
            if (following and following != league and league in senior and following in senior
                    and facts[league]["nation_uid"] == facts[following]["nation_uid"]):
                links[league].add(following)
                links[following].add(league)

    def crowd(league: int) -> float:
        values = [c for team in members[league] for c in home_crowd.get(team, [])]
        return sum(values) / len(values) if values else 0.0

    by_country: dict[int, list[int]] = defaultdict(list)
    for uid in senior:
        by_country[facts[uid]["nation_uid"]].append(uid)
    for leagues in by_country.values():
        top = max(leagues, key=crowd)
        level = {top: 1}
        queue = deque([top])
        while queue:
            league = queue.popleft()
            for other in links[league]:
                if other not in level:
                    level[other] = level[league] + 1
                    queue.append(other)
        for uid, value in level.items():
            facts[uid]["level"] = value
    return facts
