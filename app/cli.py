"""Custom Flask CLI commands.

    flask import-save PATH        read a saved game into the world database
    flask portraits generate UID  make a portrait with Draw Things
    flask portraits team UID      make the missing portraits of a squad
    flask backup                  back up the archive database
    flask leads                   the story finder's leads for a window
    flask editions                make the outlets' draft editions for the next news cycle
    flask archive                 this archive's folder and settings
    flask new-archive FOLDER      make a new archive folder for another game
    flask match-positions SAVE... fill the slots and shirt numbers of older match reports from saves
"""

from __future__ import annotations

from pathlib import Path

import click
from flask import Flask


def register_cli_commands(app: Flask) -> None:
    app.cli.add_command(import_save_command)
    app.cli.add_command(portraits_command)
    app.cli.add_command(backup_command)
    app.cli.add_command(leads_command)
    app.cli.add_command(editions_command)
    app.cli.add_command(archive_command)
    app.cli.add_command(new_archive_command)
    app.cli.add_command(match_positions_command)


def _settings_lines(settings) -> list[str]:
    save = settings.games_dir / settings.save_file if settings.save_file else None
    found = lambda path: "found" if path and path.exists() else "MISSING"  # noqa: E731
    return [f"  name          {settings.name}",
            f"  games         {settings.games_dir} ({found(settings.games_dir)})",
            f"  save          {settings.save_file or '(not chosen)'}{f' ({found(save)})' if save else ''}",
            f"  face pack     {settings.faces_dir} ({found(settings.faces_dir)}), own folder \"{settings.face_folder}\"",
            f"  stadiums      {settings.stadiums_dir} ({found(settings.stadiums_dir)})",
            f"  game          {settings.game_database_dir} ({found(settings.game_database_dir)})"]


@click.command("archive")
def archive_command() -> None:
    """This archive's folder and where it finds its game."""
    from config import ARCHIVE, ARCHIVE_DIR, SETTINGS_FILE

    click.echo(f"Archive folder: {ARCHIVE_DIR}" + ("" if (ARCHIVE_DIR / SETTINGS_FILE).is_file() else f" (no {SETTINGS_FILE}: defaults)"))
    click.echo("\n".join(_settings_lines(ARCHIVE)))


@click.command("new-archive")
@click.argument("folder", type=click.Path(file_okay=False, path_type=Path))
@click.option("--name", required=True, help="The archive's name (the game: \"Barcelona 2024\").")
@click.option("--fm-user-dir", type=click.Path(file_okay=False, path_type=Path),
              help="FM's user folder, with games/ and graphics/ (the game's preferences show it).")
@click.option("--game-dir", type=click.Path(file_okay=False, path_type=Path), help="The installed game (for its language file).")
@click.option("--save", "save_file", help="The save this archive follows: a file name in the games folder.")
def new_archive_command(folder: Path, name: str, fm_user_dir: Path | None, game_dir: Path | None, save_file: str | None) -> None:
    """Make a new archive folder for another game. Open it with FM_ARCHIVE_DIR=FOLDER: it makes its
    databases on its first start, empty until its first save is imported."""
    from dataclasses import replace

    from config import SETTINGS_FILE, ArchiveSettings, write_archive_settings

    folder = folder.expanduser().resolve()
    if (folder / SETTINGS_FILE).exists() or (folder / "fm_world.sqlite3").exists():
        raise click.ClickException(f"{folder} already holds an archive.")
    # Regens of two games share FM uids: each archive's portraits get their own face-pack folder.
    settings = replace(ArchiveSettings(), name=name, face_folder=f"Archive generated - {name}", save_file=save_file,
                       **({"fm_user_dir": str(fm_user_dir.expanduser())} if fm_user_dir else {}),
                       **({"game_dir": str(game_dir.expanduser())} if game_dir else {}))
    write_archive_settings(settings, folder)
    click.echo(f"New archive in {folder}:")
    click.echo("\n".join(_settings_lines(settings)))
    click.echo(f"Open it with: FM_ARCHIVE_DIR=\"{folder}\" flask --app app run --port 5051")


@click.group("portraits")
def portraits_command() -> None:
    """Pictures of people: the game's faces, and portraits generated with Draw Things."""


@portraits_command.command("generate")
@click.argument("uid", type=int)
@click.option("--replace", is_flag=True, help="Make a new portrait even when the person has a picture.")
def generate_portrait_command(uid: int, replace: bool) -> None:
    """Make one person's portrait with Draw Things (the player page's "Generate picture" button)."""
    from flask import current_app

    from app.services import world_portraits

    world_db = Path(current_app.config["SQLALCHEMY_BINDS"]["world"].removeprefix("sqlite:///"))
    try:
        path = world_portraits.generate_one(world_db, uid, replace=replace)
    except world_portraits.PortraitError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Picture: {path}")


@portraits_command.command("team")
@click.argument("team_uid", type=int)
@click.option("--season", type=int, help="First year of the season (2062 for 2062/63); the team's latest by default.")
def generate_team_portraits_command(team_uid: int, season: int | None) -> None:
    """Make portraits for every player of a team's season who has no picture yet."""
    from flask import current_app

    from app.services import world_portraits

    world_db = Path(current_app.config["SQLALCHEMY_BINDS"]["world"].removeprefix("sqlite:///"))
    try:
        made = world_portraits.generate_team(world_db, team_uid, season, progress=click.echo)
    except world_portraits.PortraitError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{made} portraits made.")


@click.command("import-save")
@click.argument("save_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--lang-db", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="The game's lang_db.dat (found automatically on macOS).")
@click.option("--editions/--no-editions", default=True, show_default=True, help="Make the outlets' draft editions after the import.")
def import_save_command(save_path: Path, lang_db: Path | None, editions: bool) -> None:
    """Read a saved game (.fm) into the archive's world database, then make the news cycle's draft editions."""
    from app.services.fm_save import FmSaveError
    from app.services.save_import import SaveImportError, import_save

    try:
        result = import_save(save_path, lang_db, progress=click.echo)
    except (SaveImportError, FmSaveError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Imported {save_path.name} (game date {result.game_date}):")
    for name, count in sorted(result.counts.items()):
        click.echo(f"  {name:28s} {count:>10,}")
    if editions:
        from app.services import editions as edition_service

        _make_editions(None, None, min_days=edition_service.CYCLE_DAYS)


def _make_editions(since, until, min_days: int = 0) -> None:
    from app.services import editions as edition_service

    result = edition_service.make_editions(since, until, progress=click.echo, min_days=min_days)
    if result:
        for refusal in result.refused:
            click.echo(f"  refused: {refusal}")
        click.echo("The drafts are on the desk: /desk")


@click.command("editions")
@click.option("--since", "since_text", help="Start of the news cycle (YYYY-MM-DD, not included); the end of the last cycle by default.")
@click.option("--until", "until_text", help="End of the news cycle (YYYY-MM-DD); the game date by default.")
def editions_command(since_text: str | None, until_text: str | None) -> None:
    """Make every active outlet's draft edition for a news cycle (docs/media-plan.md, step 4)."""
    from datetime import date

    _make_editions(date.fromisoformat(since_text) if since_text else None, date.fromisoformat(until_text) if until_text else None)


@click.command("backup")
@click.option(
    "--keep",
    type=click.IntRange(min=1),
    default=14,
    show_default=True,
    help="How many automatic backups to keep. Backups made by hand are never removed.",
)
@click.option(
    "--to",
    "backup_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Backup folder. Default: instance/backups.",
)
def backup_command(keep: int, backup_dir: Path | None) -> None:
    """Save a consistent copy of the SQLite database and remove old copies."""
    from flask import current_app

    from app.services.backup_service import create_backup, sqlite_path_from_uri

    try:
        database = sqlite_path_from_uri(current_app.config["SQLALCHEMY_DATABASE_URI"])
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    target_dir = backup_dir or Path(current_app.instance_path) / "backups"
    result = create_backup(database, target_dir, keep=keep)
    click.echo(f"Backup saved: {result.path} ({result.size_bytes / 1_048_576:.1f} MB)")
    for path in result.pruned:
        click.echo(f"Removed old backup: {path.name}")



@click.command("leads")
@click.option("--since", "since_text", help="Start of the window (YYYY-MM-DD, not included); 98 days before the end by default.")
@click.option("--until", "until_text", help="End of the window (YYYY-MM-DD); the game date by default.")
@click.option("--limit", default=40, show_default=True)
@click.option("--kind", default=None, help="Only this kind of lead (title, transfer, rising_star…).")
def leads_command(since_text: str | None, until_text: str | None, limit: int, kind: str | None) -> None:
    """Show the story finder's leads for a window, best first (docs/media-plan.md)."""
    from datetime import date, timedelta

    from app.extensions import db
    from app.models import world as world_models
    from app.services import world_queries
    from app.services.story_leads import find_leads

    with db.engines[world_models.BIND].connect() as connection:
        until = date.fromisoformat(until_text) if until_text else world_queries.game_date(connection)
        since = date.fromisoformat(since_text) if since_text else until - timedelta(days=98)
        found = [lead for lead in find_leads(connection, since, until) if not kind or lead.kind == kind]
    click.echo(f"{len(found)} leads from {since} to {until}")
    for lead in found[:limit]:
        click.echo(f"{lead.score:6.0f}  {lead.kind:13} {lead.headline}")


@click.command("match-positions")
@click.argument("saves", nargs=-1, type=click.Path(exists=True, dir_okay=False, path_type=Path))
def match_positions_command(saves: tuple[Path, ...]) -> None:
    """Fill the shirt numbers and slots of the match reports imported before they were read, from the saves given
    (only their match reports are read)."""
    from app.extensions import db
    from app.models import world
    from app.services.save_import import backfill_match_positions, create_tables

    engine = db.engines[world.BIND]
    create_tables(engine)
    for path in saves:
        with engine.begin() as connection:
            click.echo(f"{path.name}: {backfill_match_positions(connection, path)} rows")
