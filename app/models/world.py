"""The archive rebuilt from saved games (see docs/rewrite-plan.md, step 2).

These tables live in their own database (the ``world`` bind,
``instance/fm_world.sqlite3``) and are filled only by ``flask import-save``
(app/services/save_import.py). Every key is an FM unique id or a natural key
made of them; the save's own team, person and stage numbers never reach these
tables. Rows that a later save can change carry ``as_of``, the game date of the
save that wrote them: an older save never overwrites a newer one.

The ``World`` prefix keeps the class names apart from the old archive's models
until those are removed (step 7).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

BIND = "world"


class WorldSave(db.Model):
    __bind_key__ = BIND
    __tablename__ = "saves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)  # the last played match
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    counts: Mapped[dict | None] = mapped_column(JSON)  # rows read and skipped, per table


class WorldCompetition(db.Model):
    __bind_key__ = BIND
    __tablename__ = "competitions"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str] = mapped_column(String(200), nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(20), nullable=False)
    # Worked out from the world after each import (save_import._write_competition_facts):
    scope: Mapped[str | None] = mapped_column(String(20))  # league, cup, continental, national_teams, youth, friendly
    nation_uid: Mapped[int | None] = mapped_column(Integer, index=True)  # the country of a domestic competition
    level: Mapped[int | None] = mapped_column(Integer)  # a senior league's level in its country: 1 is the top
    reputation: Mapped[int | None] = mapped_column(Integer)  # the game's, 0-200 (World Cup 200), from the newest save


class WorldClub(db.Model):
    __bind_key__ = BIND
    __tablename__ = "clubs"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # its first team's uid
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str] = mapped_column(String(200), nullable=False)
    nation_uid: Mapped[int | None] = mapped_column(Integer, index=True)  # the club's own country (Cardiff: Wales)
    league_nation_uid: Mapped[int | None] = mapped_column(Integer)  # the country whose leagues it plays in (Cardiff: England)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldNation(db.Model):
    """A nation; its senior national team has the same FM uid (England 765)."""

    __bind_key__ = BIND
    __tablename__ = "nations"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    short_name: Mapped[str] = mapped_column(String(100), nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(10), nullable=False)
    adjective: Mapped[str] = mapped_column(String(100), nullable=False)


class WorldTeam(db.Model):
    """A club team (first team, B team, Chelsea U21) or a national team (England U21).

    A club team has ``club_uid`` (clubs.fm_uid is its first team); a national team has
    ``nation_uid`` instead.
    """

    __bind_key__ = BIND
    __tablename__ = "teams"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    club_uid: Mapped[int | None] = mapped_column(ForeignKey("clubs.fm_uid"), index=True)
    nation_uid: Mapped[int | None] = mapped_column(ForeignKey("nations.fm_uid"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    age_limit: Mapped[int | None] = mapped_column(Integer)
    # The game's, 0-10 000 (Chelsea 9983). A club's is its first team's; a nation's, its senior team's.
    reputation: Mapped[int | None] = mapped_column(Integer)
    stadium_uid: Mapped[int | None] = mapped_column(Integer)  # its home stadium's FM uid (the game's stadium packs use it)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPerson(db.Model):
    __bind_key__ = BIND
    __tablename__ = "people"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # as the game shows it
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    birth_date: Mapped[date | None] = mapped_column(Date)
    nation_uid: Mapped[int | None] = mapped_column(Integer, index=True)  # nationality (players only)
    ethnicity: Mapped[int | None] = mapped_column(Integer)  # fm_save.ETHNICITIES
    skin_tone: Mapped[int | None] = mapped_column(Integer)  # 1-20, when the game sets it
    hair_colour: Mapped[int | None] = mapped_column(Integer)  # the game's own code
    hair_length: Mapped[int | None] = mapped_column(Integer)  # fm_save.HAIR_LENGTHS
    team_uid: Mapped[int | None] = mapped_column(Integer, index=True)  # the team a playing player is in now (loan club during a loan)
    # The game's, 0-10 000, for playing players (null once retired): in the home country, now, worldwide
    reputation_home: Mapped[int | None] = mapped_column(Integer)
    reputation_current: Mapped[int | None] = mapped_column(Integer)
    reputation_world: Mapped[int | None] = mapped_column(Integer)
    # The game's hidden abilities (1-200; a negative potential is a range), for scouting stories only:
    # writers never see the numbers.
    current_ability: Mapped[int | None] = mapped_column(Integer)
    potential_ability: Mapped[int | None] = mapped_column(Integer)
    # A staff member's job now (fm_save.JOB_NAMES: "Head of Youth Development"), the team it is with, and since when.
    staff_job: Mapped[str | None] = mapped_column(String(40))
    staff_team_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    staff_since: Mapped[date | None] = mapped_column(Date)
    staff_wage: Mapped[int | None] = mapped_column(Integer)  # pounds a week, exact (the game rounds it on screen)
    staff_contract_ends: Mapped[date | None] = mapped_column(Date)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldClubRelation(db.Model):
    """A club's rival club, or a person it favours or dislikes, as the newest save has them
    (fm_save.read_club_relations). Its Legends are favourites at 100 kept permanently, its Icons the
    other permanent favourites, its Favoured personnel the favourites not kept permanently."""

    __bind_key__ = BIND
    __tablename__ = "club_relations"
    __table_args__ = (Index("ix_club_relations_subject", "subject_uid", "relation"),)

    club_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    relation: Mapped[str] = mapped_column(String(10), primary_key=True)  # rival, favourite, disliked
    subject_uid: Mapped[int] = mapped_column(Integer, primary_key=True)  # a club's uid for a rival, else a person's
    reason: Mapped[int | None] = mapped_column(Integer)  # the game's reason code (fm_save.RIVAL_REASONS, PERSON_REASONS)
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-100
    permanent: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    names_stadium: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldClubFinance(db.Model):
    """A club's money as one save has it (fm_save.read_club_finances), in pounds: one row per club and save date,
    so the archive keeps its history."""

    __bind_key__ = BIND
    __tablename__ = "club_finances"

    club_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_date: Mapped[date] = mapped_column(Date, primary_key=True)
    bank: Mapped[int | None] = mapped_column(Integer)
    transfer_budget: Mapped[int | None] = mapped_column(Integer)
    wage_budget: Mapped[int | None] = mapped_column(Integer)
    yearly_income: Mapped[int | None] = mapped_column(Integer)  # the income streams' yearly payments together
    debt: Mapped[int | None] = mapped_column(Integer)  # the debts' original amounts together


class WorldClubIncome(db.Model):
    """One income stream of a club (a sponsor, a grant, a parachute payment), as the newest save has it."""

    __bind_key__ = BIND
    __tablename__ = "club_incomes"

    club_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[int] = mapped_column(Integer, nullable=False)  # fm_save.INCOME_TYPES
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    yearly: Mapped[int] = mapped_column(Integer, nullable=False)
    starts: Mapped[date | None] = mapped_column(Date)
    ends: Mapped[date | None] = mapped_column(Date)
    renews: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldClubDebt(db.Model):
    """One debt or loan of a club, as the newest save has it."""

    __bind_key__ = BIND
    __tablename__ = "club_debts"

    club_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[int] = mapped_column(Integer, nullable=False)  # fm_save.DEBT_SOURCES
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_repayment: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_interest: Mapped[int] = mapped_column(Integer, nullable=False)
    conditional_repayment: Mapped[int] = mapped_column(Integer, nullable=False)
    starts: Mapped[date | None] = mapped_column(Date)
    ends: Mapped[date | None] = mapped_column(Date)
    in_fpp: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldManagerSpell(db.Model):
    """One manager's spell at a team since the game began (and the spell that ended then), as the newest save
    has it: the dates, the games won, drawn and lost, why it ended and the trophies (manager_history)."""

    __bind_key__ = BIND
    __tablename__ = "manager_spells"
    __table_args__ = (Index("ix_manager_spells_person", "person_uid"),)

    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # oldest first
    person_uid: Mapped[int | None] = mapped_column(Integer)
    started: Mapped[date | None] = mapped_column(Date)
    ended: Mapped[date | None] = mapped_column(Date)
    won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drawn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[int | None] = mapped_column(Integer)  # fm_save.MANAGER_LEAVING
    league_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cup_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldInjury(db.Model):
    """A player's injury, as the newest save has it (fm_save.read_injury_history, read_current_injuries): what, when, the
    time out (for a current one, the expected time out from the day it happened), the cause and treatment as the game
    names them, and whether it came in a match (the player's team then), in training, or was an illness."""

    __bind_key__ = BIND
    __tablename__ = "injuries"
    __table_args__ = (Index("ix_injuries_current", "current", "injured_on"),)

    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # oldest first; the current ones last
    injury: Mapped[str] = mapped_column(String(60), nullable=False)  # the game's name, lower case: "torn hamstring"
    injured_on: Mapped[date | None] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # Slight, Minor, Moderate, Major
    place: Mapped[str | None] = mapped_column(String(20))  # In match, In training; none for an illness
    cause: Mapped[str | None] = mapped_column(String(30))
    treatment: Mapped[str | None] = mapped_column(String(20))
    team_uid: Mapped[int | None] = mapped_column(Integer)  # the player's team in the match
    current: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPlayerSnapshot(db.Model):
    """A playing player's attributes in one save."""

    __bind_key__ = BIND
    __tablename__ = "player_snapshots"

    person_uid: Mapped[int] = mapped_column(ForeignKey("people.fm_uid"), primary_key=True)
    save_id: Mapped[int] = mapped_column(ForeignKey("saves.id"), primary_key=True)
    height_cm: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_kg: Mapped[int] = mapped_column(Integer, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False)
    positions: Mapped[dict | None] = mapped_column(JSON)  # fm_save.POSITION_CODES -> 1-20 (20 natural)
    traits: Mapped[dict | None] = mapped_column(JSON)  # the hidden attributes: fm_save.TRAIT_NAMES and the five in the block, 1-20
    left_foot: Mapped[int | None] = mapped_column(Integer)  # 1-20
    right_foot: Mapped[int | None] = mapped_column(Integer)
    preferred_moves: Mapped[list | None] = mapped_column(JSON)  # fm_save.PREFERRED_MOVES: the editor's "Player Traits"


class WorldEdition(db.Model):
    """One edition of a competition: a season or a one-year tournament."""

    __bind_key__ = BIND
    __tablename__ = "competition_editions"

    competition_uid: Mapped[int] = mapped_column(ForeignKey("competitions.fm_uid"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)  # the year it was won
    season_label: Mapped[str] = mapped_column(String(20), nullable=False)  # "2061/62" or "2061"
    decided_on: Mapped[date | None] = mapped_column(Date)


class WorldEditionPlace(db.Model):
    """Final order of an edition: place 1 is the winner, 2 the runner-up."""

    __bind_key__ = BIND
    __tablename__ = "edition_places"

    competition_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    place: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_uid: Mapped[int] = mapped_column(ForeignKey("teams.fm_uid"), nullable=False, index=True)


class WorldFixture(db.Model):
    __bind_key__ = BIND
    __tablename__ = "fixtures"
    __table_args__ = (UniqueConstraint("date", "home_uid", "away_uid"), Index("ix_fixtures_competition_date", "competition_uid", "date"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    home_uid: Mapped[int] = mapped_column(ForeignKey("teams.fm_uid"), nullable=False, index=True)
    away_uid: Mapped[int] = mapped_column(ForeignKey("teams.fm_uid"), nullable=False, index=True)
    competition_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)  # the save's stage number (stable between saves)
    home_goals: Mapped[int | None] = mapped_column(Integer)  # None until played
    away_goals: Mapped[int | None] = mapped_column(Integer)
    home_goals_after_extra_time: Mapped[int | None] = mapped_column(Integer)
    away_goals_after_extra_time: Mapped[int | None] = mapped_column(Integer)
    home_penalties: Mapped[int | None] = mapped_column(Integer)
    away_penalties: Mapped[int | None] = mapped_column(Integer)
    attendance: Mapped[int | None] = mapped_column(Integer)
    away_fans: Mapped[int | None] = mapped_column(Integer)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldGoal(db.Model):
    __bind_key__ = BIND
    __tablename__ = "goals"

    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # order in the match
    scorer_uid: Mapped[int | None] = mapped_column(Integer, index=True)  # None when the person is not known
    side: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 home, 1 away
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    added_time: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[int] = mapped_column(Integer, nullable=False)  # see fm_save.Goal


class WorldMatchReport(db.Model):
    """The game's detailed report of a match (fm_save.MatchReport). Saves keep these only for a
    few months, so the archive keeps every report a save brings."""

    __bind_key__ = BIND
    __tablename__ = "match_reports"

    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    stadium: Mapped[str | None] = mapped_column(String(120))
    referee_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    referee_name: Mapped[str | None] = mapped_column(String(120))
    player_of_the_match_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldMatchTeam(db.Model):
    """One side of a match report: the team block's own figures."""

    __bind_key__ = BIND
    __tablename__ = "match_teams"

    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    side: Mapped[int] = mapped_column(Integer, primary_key=True)  # 0 home, 1 away
    team_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    squad: Mapped[int] = mapped_column(Integer, nullable=False)
    passes: Mapped[int | None] = mapped_column(Integer)
    passes_completed: Mapped[int | None] = mapped_column(Integer)
    corners: Mapped[int | None] = mapped_column(Integer)
    woodwork: Mapped[int | None] = mapped_column(Integer)
    clear_cut_chances: Mapped[int | None] = mapped_column(Integer)
    fouls: Mapped[int | None] = mapped_column(Integer)
    yellow_cards: Mapped[int | None] = mapped_column(Integer)
    red_cards: Mapped[int | None] = mapped_column(Integer)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


MATCH_PLAYER_FIGURES = (
    "goals", "assists", "shots", "shots_on_target", "passes", "passes_completed", "key_passes", "clear_cut_chances",
    "crosses", "crosses_completed", "dribbles", "tackles", "tackles_won", "key_tackles", "interceptions", "clearances",
    "headers", "headers_won", "key_headers", "fouls", "yellow_cards", "booked_minute", "minute_on", "minute_off",
    "added_time_off", "injury_minute", "red_cards", "sent_off_minute", "goals_outside_box", "mistakes_leading_to_goal",
    "goals_conceded", "blocked_shots", "long_shots", "half_chances", "saves_held", "saves_parried", "saves_tipped", "shots_outside_box",
)


class WorldMatchShot(db.Model):
    """One shot of a match with a full report (fm_save.Shot): who, when on the match clock, how it ended (goal, off target,
    woodwork, saved, blocked) and where the ball crossed the goal line. The game keeps shots for some matches only."""

    __bind_key__ = BIND
    __tablename__ = "match_shots"
    __table_args__ = (Index("ix_match_shots_person", "person_uid"),)

    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    side: Mapped[int] = mapped_column(Integer, primary_key=True)  # 0 home, 1 away
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # in match order
    person_uid: Mapped[int | None] = mapped_column(Integer)
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    second: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    across: Mapped[float] = mapped_column(Float, nullable=False)  # metres from the goal's centre (posts at +-3.66)
    height: Mapped[float] = mapped_column(Float, nullable=False)  # metres (the bar at 2.44)
    outside_box: Mapped[int | None] = mapped_column(Integer)  # a goal from outside the box: 1 yes, 0 no, null unknown (the scorer's goals mixed)
    kind: Mapped[str | None] = mapped_column(String(8))  # header, volley, drive, finish (fm_save.SHOT_KINDS); null when the game kept no event
    range: Mapped[str | None] = mapped_column(String(8))  # close, outside (fm_save.SHOT_RANGES); null in between or no event
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldMatchPlayer(db.Model):
    """One squad member's line in a match report (fm_save.MatchPlayer)."""

    __bind_key__ = BIND
    __tablename__ = "match_players"

    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    side: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # order in the squad list
    person_uid: Mapped[int | None] = mapped_column(Integer, index=True)  # None when the person is not known
    played: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 for an unused substitute
    rating: Mapped[float | None] = mapped_column(Float)
    distance: Mapped[float | None] = mapped_column(Float)  # metres; 0 when the game did not measure it
    xg: Mapped[float] = mapped_column(Float, nullable=False)
    goals: Mapped[int] = mapped_column(Integer, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, nullable=False)
    shots: Mapped[int] = mapped_column(Integer, nullable=False)
    shots_on_target: Mapped[int] = mapped_column(Integer, nullable=False)
    passes: Mapped[int] = mapped_column(Integer, nullable=False)
    passes_completed: Mapped[int] = mapped_column(Integer, nullable=False)
    key_passes: Mapped[int] = mapped_column(Integer, nullable=False)
    clear_cut_chances: Mapped[int] = mapped_column(Integer, nullable=False)
    crosses: Mapped[int] = mapped_column(Integer, nullable=False)
    crosses_completed: Mapped[int] = mapped_column(Integer, nullable=False)
    dribbles: Mapped[int] = mapped_column(Integer, nullable=False)
    tackles: Mapped[int] = mapped_column(Integer, nullable=False)
    tackles_won: Mapped[int] = mapped_column(Integer, nullable=False)
    key_tackles: Mapped[int] = mapped_column(Integer, nullable=False)
    interceptions: Mapped[int] = mapped_column(Integer, nullable=False)
    clearances: Mapped[int] = mapped_column(Integer, nullable=False)
    headers: Mapped[int] = mapped_column(Integer, nullable=False)
    headers_won: Mapped[int] = mapped_column(Integer, nullable=False)
    key_headers: Mapped[int] = mapped_column(Integer, nullable=False)
    goals_outside_box: Mapped[int | None] = mapped_column(Integer)  # null in rows written before it was read
    mistakes_leading_to_goal: Mapped[int | None] = mapped_column(Integer)  # null in rows written before it was read
    goals_conceded: Mapped[int | None] = mapped_column(Integer)  # the keeper's; these eight are null in rows written before 24 Oct 2026
    blocked_shots: Mapped[int | None] = mapped_column(Integer)
    long_shots: Mapped[int | None] = mapped_column(Integer)
    half_chances: Mapped[int | None] = mapped_column(Integer)
    saves_held: Mapped[int | None] = mapped_column(Integer)
    saves_parried: Mapped[int | None] = mapped_column(Integer)
    saves_tipped: Mapped[int | None] = mapped_column(Integer)
    shots_outside_box: Mapped[int | None] = mapped_column(Integer)
    fouls: Mapped[int] = mapped_column(Integer, nullable=False)
    yellow_cards: Mapped[int] = mapped_column(Integer, nullable=False)
    booked_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    minute_on: Mapped[int] = mapped_column(Integer, nullable=False)
    minute_off: Mapped[int] = mapped_column(Integer, nullable=False)
    added_time_off: Mapped[int] = mapped_column(Integer, nullable=False)
    injury_minute: Mapped[int | None] = mapped_column(Integer)  # 0 when not injured; None in rows from before it was read
    red_cards: Mapped[int | None] = mapped_column(Integer)  # None in rows from before it was read
    sent_off_minute: Mapped[int | None] = mapped_column(Integer)
    shirt_number: Mapped[int | None] = mapped_column(Integer)  # None in rows from before it was read
    start_position: Mapped[str | None] = mapped_column(String(10))  # the slot at kick-off ("D (C)"); None when he did not start
    end_position: Mapped[str | None] = mapped_column(String(10))  # the slot at the end or when he went off; None when he did not play
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldStaffProfile(db.Model):
    """A manager's coaching profile from the newest save (fm_save.StaffProfile): coaching attributes (1-20), the game's
    numbers of his preferred formations and his tactical style (fm_save.FORMATION_NAMES, TACTICAL_STYLES), and the
    probable mentality and pressing preferences (1-20)."""

    __bind_key__ = BIND
    __tablename__ = "staff_profiles"

    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    attacking: Mapped[int] = mapped_column(Integer, nullable=False)
    defending: Mapped[int] = mapped_column(Integer, nullable=False)
    fitness: Mapped[int] = mapped_column(Integer, nullable=False)
    mental: Mapped[int] = mapped_column(Integer, nullable=False)
    technical: Mapped[int] = mapped_column(Integer, nullable=False)
    tactical: Mapped[int] = mapped_column(Integer, nullable=False)
    set_pieces: Mapped[int] = mapped_column(Integer, nullable=False)
    working_with_youngsters: Mapped[int] = mapped_column(Integer, nullable=False)
    formation: Mapped[int] = mapped_column(Integer, nullable=False)  # preferred; 0 for none
    formation_second: Mapped[int] = mapped_column(Integer, nullable=False)  # probably the second preferred
    formation_other: Mapped[int] = mapped_column(Integer, nullable=False)  # the defensive and attacking ones, order unknown
    formation_other_2: Mapped[int] = mapped_column(Integer, nullable=False)
    tactical_style: Mapped[int | None] = mapped_column(Integer)  # None when the save keeps none
    mentality: Mapped[int] = mapped_column(Integer, nullable=False)
    pressing: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPartnership(db.Model):
    """The game's record of two players who played together for the human club, in the roles they played (only the
    human club has one; fm_save.Partnership)."""

    __bind_key__ = BIND
    __tablename__ = "partnerships"

    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_a: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_b: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_a: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_b: Mapped[int] = mapped_column(Integer, primary_key=True)
    matches: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_sum: Mapped[int] = mapped_column(Integer, nullable=False)
    goals_for: Mapped[int] = mapped_column(Integer, nullable=False)
    goals_against: Mapped[int] = mapped_column(Integer, nullable=False)
    departed: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPersonMood(db.Model):
    """A person's state in the newest save: morale (1-20, players only) and overall happiness (-100..100) (fm_save.MORALE_WORDS,
    HAPPINESS_WORDS), and a player's match sharpness and condition (0-10 000) and fatigue (-500 fresh .. +500 jaded)."""

    __bind_key__ = BIND
    __tablename__ = "person_moods"

    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    morale: Mapped[int | None] = mapped_column(Integer)
    happiness: Mapped[int | None] = mapped_column(Integer)
    sharpness: Mapped[int | None] = mapped_column(Integer)
    fatigue: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[int | None] = mapped_column(Integer)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPersonConcern(db.Model):
    """A dated concern of a person, open or ended, as the newest save keeps them (fm_save.Concern, CONCERNS)."""

    __bind_key__ = BIND
    __tablename__ = "person_concerns"

    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # the save's order, oldest first
    kind: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    ref: Mapped[int | None] = mapped_column(Integer)
    ref2: Mapped[int | None] = mapped_column(Integer)
    about_person_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    competition_uid: Mapped[int | None] = mapped_column(Integer)
    nation_uid: Mapped[int | None] = mapped_column(Integer)
    started: Mapped[date | None] = mapped_column(Date)
    ended: Mapped[date | None] = mapped_column(Date)  # None while it lasts
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPersonFeeling(db.Model):
    """How a person feels about another, in the newest save (fm_save.Feeling, RELATION_REASONS)."""

    __bind_key__ = BIND
    __tablename__ = "person_feelings"

    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    other_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, index=True)
    likes: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # 1 likes, 0 dislikes
    reason: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-100
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldSocialGroup(db.Model):
    """The human club's social groups (the game's Dynamics screen) in the newest save: group 1 is the game's first."""

    __bind_key__ = BIND
    __tablename__ = "social_groups"

    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldTeamTactic(db.Model):
    """A team's tactic (fm_tactics.Tactic): the human club's slots as of the newest save (slot 1-3, no fixture), and the
    tactic its match files show it used in a match (the fixture, no slot). The slots are in team_tactic_slots."""

    __bind_key__ = BIND
    __tablename__ = "team_tactics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    slot: Mapped[int | None] = mapped_column(Integer)
    fixture_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fixtures.id"), index=True)
    played_on: Mapped[date | None] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    style: Mapped[str | None] = mapped_column(String(80))
    mentality: Mapped[str | None] = mapped_column(String(40))
    fluidity: Mapped[str | None] = mapped_column(String(40))
    formation: Mapped[str | None] = mapped_column(String(40))
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldTeamTacticSlot(db.Model):
    """One slot of a team tactic: the 11 formation slots (seq 0-10, no person), then the players' personalised roles."""

    __bind_key__ = BIND
    __tablename__ = "team_tactic_slots"

    tactic_id: Mapped[int] = mapped_column(Integer, ForeignKey("team_tactics.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str | None] = mapped_column(String(5))
    duty: Mapped[str | None] = mapped_column(String(10))
    role_key: Mapped[str] = mapped_column(String(20), nullable=False)
    role: Mapped[str | None] = mapped_column(String(60))
    person_uid: Mapped[int | None] = mapped_column(Integer, ForeignKey("people.fm_uid"))


class WorldContract(db.Model):
    """A person's contract as a save had it (fm_save.StaffContract): the club, the deal's start, the wage to the pound, the
    end, his happiness. Saves add up: a new deal (another wage) is another row, so the wage history stays."""

    __bind_key__ = BIND
    __tablename__ = "contracts"

    person_uid: Mapped[int] = mapped_column(Integer, ForeignKey("people.fm_uid"), primary_key=True)
    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    wage: Mapped[int] = mapped_column(Integer, primary_key=True)  # pounds a week, exact
    job: Mapped[str] = mapped_column(String(40), nullable=False)
    signed: Mapped[date | None] = mapped_column(Date)  # the deal's start
    joined: Mapped[date | None] = mapped_column(Date)  # the day he joined the club
    expires: Mapped[date | None] = mapped_column(Date)
    happiness: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[date] = mapped_column(Date, nullable=False)  # the oldest save with this deal
    as_of: Mapped[date] = mapped_column(Date, nullable=False)  # the newest


class WorldPressAnswer(db.Model):
    """An answer given at a press conference this season (fm_save.read_press_answers): who spoke (the conference team's
    manager on the day), about whom, the topic and tone of the game's question and answer, and the game's own headline for
    it, rendered with the names. The answer's words are not kept: the papers take its spirit (26 Oct 2026)."""

    __bind_key__ = BIND
    __tablename__ = "press_answers"
    __table_args__ = (Index("ix_press_answers_speaker", "speaker_uid"), Index("ix_press_answers_subject", "subject_uid"))

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    speaker_uid: Mapped[int | None] = mapped_column(Integer)
    opponent_uid: Mapped[int | None] = mapped_column(Integer)
    subject_team_uid: Mapped[int | None] = mapped_column(Integer)
    answer: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(200))
    tone: Mapped[str | None] = mapped_column(String(20))
    headline: Mapped[str | None] = mapped_column(String(300))
    # whether the answer's headline names the subject: only then is it about him (the subject field of a record whose
    # question names nobody is a leftover, Dysart 'criticises Aston Villa' carried Pladevall)
    names_subject: Mapped[int | None] = mapped_column(Integer, default=0)  # nullable: create_tables adds only nullable columns
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldNewsItem(db.Model):
    """A news item of a decoded kind (fm_save.read_news_items): who and what, never the game's words. danger_man: person
    (pundit or ex-player) singles out other_person of team before match_day; job_link: person (manager of team) linked with
    the vacancy at other_team, left by other_person; board_pressure: the board of team losing patience with person, the
    match v other_team decisive; transfer_rumour: team (the bidder) after person, fee in pounds (26 Oct 2026)."""

    __bind_key__ = BIND
    __tablename__ = "news_items"
    __table_args__ = (Index("ix_news_items_person", "person_uid"), Index("ix_news_items_other", "other_person_uid"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    day: Mapped[date | None] = mapped_column(Date)  # the news date; a rumour's first save
    person_uid: Mapped[int | None] = mapped_column(Integer)
    other_person_uid: Mapped[int | None] = mapped_column(Integer)
    team_uid: Mapped[int | None] = mapped_column(Integer)
    other_team_uid: Mapped[int | None] = mapped_column(Integer)
    match_day: Mapped[date | None] = mapped_column(Date)
    fee: Mapped[int | None] = mapped_column(Integer)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldStadium(db.Model):
    """A stadium's place on the map (fm_save.Stadium): teams point at it by stadium_uid. For the travel column's route."""

    __bind_key__ = BIND
    __tablename__ = "stadiums"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldTransferInterest(db.Model):
    """A club's interest in a player as of the newest save (fm_save.TransferInterest): active, or only rumoured."""

    __bind_key__ = BIND
    __tablename__ = "transfer_interest"

    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_uid: Mapped[int] = mapped_column(Integer, ForeignKey("people.fm_uid"), primary_key=True)
    rumoured: Mapped[int] = mapped_column(Integer, nullable=False)
    wage: Mapped[int | None] = mapped_column(Integer)  # NOT a wage: the entry's first amount, unnamed (no wage exists before a bid is accepted)
    offered_wage: Mapped[int | None] = mapped_column(Integer)  # NOT a wage: the entry's second amount, twice the first for an active interest
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPlayerGame(db.Model):
    """One of a tracked player's recent games for a team (fm_save.PlayerGame): the game's "Recent games for <team>" rows,
    kept after the full report is gone. Figures are the game's columns; for a keeper, dribbles hold his saves and the
    headers columns his saves held, parried and tipped."""

    __bind_key__ = BIND
    __tablename__ = "player_games"

    person_uid: Mapped[int] = mapped_column(Integer, ForeignKey("people.fm_uid"), primary_key=True)
    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    played_on: Mapped[date] = mapped_column(Date, primary_key=True)
    opponent_uid: Mapped[int | None] = mapped_column(Integer)
    fixture_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fixtures.id"), index=True)
    competition_uid: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # played, unused, injured, suspended, "status n"
    position: Mapped[str | None] = mapped_column(String(8))  # the slot he played: "GK", "D (C)", "AM (L)" (fm_save.POSITION_NAMES)
    minutes: Mapped[int | None] = mapped_column(Integer)
    minute_off: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(Float)
    xg: Mapped[float | None] = mapped_column(Float)
    goals: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    conceded: Mapped[int | None] = mapped_column(Integer)  # while he was on
    shots: Mapped[int | None] = mapped_column(Integer)
    shots_on_target: Mapped[int | None] = mapped_column(Integer)
    passes: Mapped[int | None] = mapped_column(Integer)
    passes_completed: Mapped[int | None] = mapped_column(Integer)
    key_passes: Mapped[int | None] = mapped_column(Integer)
    clear_cut_chances: Mapped[int | None] = mapped_column(Integer)
    tackles: Mapped[int | None] = mapped_column(Integer)
    tackles_won: Mapped[int | None] = mapped_column(Integer)
    key_tackles: Mapped[int | None] = mapped_column(Integer)
    headers: Mapped[int | None] = mapped_column(Integer)
    headers_won: Mapped[int | None] = mapped_column(Integer)
    key_headers: Mapped[int | None] = mapped_column(Integer)
    interceptions: Mapped[int | None] = mapped_column(Integer)
    dribbles: Mapped[int | None] = mapped_column(Integer)
    fouls: Mapped[int | None] = mapped_column(Integer)
    fouled: Mapped[int | None] = mapped_column(Integer)
    offsides: Mapped[int | None] = mapped_column(Integer)
    yellow_cards: Mapped[int | None] = mapped_column(Integer)
    distance: Mapped[float | None] = mapped_column(Float)  # km
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldTransferOffer(db.Model):
    """A bid for a player (fm_save.TransferOffer): the bidding club, the fee in pounds, when, and until when."""

    __bind_key__ = BIND
    __tablename__ = "transfer_offers"
    __table_args__ = (UniqueConstraint("team_uid", "person_uid", "fee", "offered_on", name="uq_transfer_offer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    person_uid: Mapped[int] = mapped_column(Integer, ForeignKey("people.fm_uid"), nullable=False, index=True)
    squad_uid: Mapped[int | None] = mapped_column(Integer)  # the team he would join (the club's first team, U21 or U18)
    fee: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # fm_save.BID_STATUS: made, agreed, completed, collapsed, withdrawn
    offered_on: Mapped[date | None] = mapped_column(Date)
    until: Mapped[date | None] = mapped_column(Date)
    completes_on: Mapped[date | None] = mapped_column(Date)
    loan: Mapped[int | None] = mapped_column(Integer, default=0)  # a loan offer (fee 0); nullable: added to old archives
    loan_wage: Mapped[int | None] = mapped_column(Integer)  # the record's sum for a loan: most likely the weekly wage share (unverified)
    fee_guaranteed: Mapped[int | None] = mapped_column(Integer)  # the offer as the game states it (fee + its 01 01 clauses)
    fee_max: Mapped[int | None] = mapped_column(Integer)  # with every conditional add-on
    status_changed_on: Mapped[date | None] = mapped_column(Date)  # the save that first showed a new status (a bid rejected after it was seen open)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldPlayerSeason(db.Model):
    """A player's statistics for one club, season and kind of competition (fm_save.SeasonStats)."""

    __bind_key__ = BIND
    __tablename__ = "player_seasons"
    __table_args__ = (Index("ix_player_seasons_team_season", "team_uid", "season_start"),)

    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)  # 0 when without a club
    season_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[int] = mapped_column(Integer, primary_key=True)  # fm_save.FRIENDLY ... INTERNATIONAL
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # more than one record of a kind: 0, 1, ...
    league_uid: Mapped[int | None] = mapped_column(Integer)  # the club's league that season
    starts: Mapped[int] = mapped_column(Integer, nullable=False)
    substitute_appearances: Mapped[int] = mapped_column(Integer, nullable=False)
    rated_appearances: Mapped[int] = mapped_column(Integer, nullable=False)
    goals: Mapped[int] = mapped_column(Integer, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, nullable=False)
    player_of_match: Mapped[int] = mapped_column(Integer, nullable=False)
    goals_conceded: Mapped[int] = mapped_column(Integer, nullable=False)
    clean_sheets: Mapped[int] = mapped_column(Integer, nullable=False)
    yellow_cards: Mapped[int] = mapped_column(Integer, nullable=False)
    red_cards: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tenths
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldAward(db.Model):
    __bind_key__ = BIND
    __tablename__ = "awards"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str] = mapped_column(String(200), nullable=False)


class WorldAwardPlace(db.Model):
    __bind_key__ = BIND
    __tablename__ = "award_places"

    award_uid: Mapped[int] = mapped_column(ForeignKey("awards.fm_uid"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    place: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1 = winner
    person_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    club_uid: Mapped[int | None] = mapped_column(Integer)  # club or national team FM uid
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    appearances: Mapped[int] = mapped_column(Integer, nullable=False)
    goals: Mapped[int] = mapped_column(Integer, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_total: Mapped[int] = mapped_column(Integer, nullable=False)
    votes: Mapped[int] = mapped_column(Integer, nullable=False)


class WorldBestEleven(db.Model):
    """A seasonal best eleven: slots 0-10 start, 11-17 are the substitutes.

    ``team_uid`` is 0 when the owning team is not known (national teams; see
    fm_save.guess_best_eleven_clubs); such lists are told apart by ``save_group``,
    the save's own list number (0 when the team is known).
    """

    __bind_key__ = BIND
    __tablename__ = "best_elevens"

    team_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    save_group: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_uid: Mapped[int | None] = mapped_column(Integer)  # the team's league or tournament
    person_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    position: Mapped[str | None] = mapped_column(String(10))
    appearances: Mapped[int] = mapped_column(Integer, nullable=False)
    goals: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_total: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class WorldTransfer(db.Model):
    """A move between teams. Team uid 0 means none (a youth intake, a free agent, a release)."""

    __bind_key__ = BIND
    __tablename__ = "transfers"

    person_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[int] = mapped_column(Integer, primary_key=True)  # fm_save.TRANSFER_KINDS
    from_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    to_uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    squad_uid: Mapped[int | None] = mapped_column(Integer)
    fee: Mapped[int] = mapped_column(Integer, nullable=False)  # pounds
    fee_with_add_ons: Mapped[int] = mapped_column(Integer, nullable=False)
