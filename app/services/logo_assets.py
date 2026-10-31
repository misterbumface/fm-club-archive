"""Read FM UID-keyed logo assets directly from the user-supplied Logos pack."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from config import ARCHIVE

# Both packs are too large for git. They come from the game's graphics folder (the archive's
# settings; FM_LOGO_DIR and FM_FLAGS_DIR override), else from beside the project (Logos/ inside
# it, Flags/ one level up), where the first archive found them.
LOGO_ROOT = ARCHIVE.logos_dir if ARCHIVE.logos_dir.is_dir() else Path(__file__).resolve().parents[2] / "Logos"
FLAG_ROOT = ARCHIVE.flags_dir if ARCHIVE.flags_dir.is_dir() else Path(__file__).resolve().parents[3] / "Flags"
_PICTURE_KINDS = {"club": "club", "competition": "comp", "nation": "nation"}
_CONFIG_MAP = re.compile(r'<record from="([^"]+)" to="graphics/pictures/(club|comp|nation)/(\d+)/(?:logo|flag)"')


@lru_cache(maxsize=1)
def pack_index() -> dict[tuple[str, str], Path]:
    """(club|comp|nation, uid) -> picture, from every config.xml of the flag and logo packs (flags first), the way the game
    finds them: a pack works whatever its folders are called (Clubs, Club, Competitions, Competiciones...). The small
    folders are left out; a pack's normal picture serves both sizes."""
    found: dict[tuple[str, str], Path] = {}
    for root in (FLAG_ROOT, LOGO_ROOT):
        if not root.is_dir():
            continue
        for config in sorted(root.rglob("config.xml")):
            if any(part.lower() == "small" for part in config.relative_to(root).parts):
                continue
            for name, kind, uid in _CONFIG_MAP.findall(config.read_text(encoding="utf-8", errors="replace")):
                for extension in (".png", ".jpg"):
                    image = config.parent / f"{name}{extension}"
                    if image.is_file():
                        found.setdefault((kind, uid), image)
                        break
    return found


_TROPHY_MAP = re.compile(r'<record from="([^"]+)" to="graphics/pictures/comp/(\d+)/trophy"')
_SHIPPED_TROPHIES = Path(__file__).resolve().parents[1] / "static" / "img" / "trophies"


def trophy_roots() -> list[Path]:
    """Where trophy packs are: any folder of the game's graphics folder with "troph" in its name (sortitoutsi's Trophies
    Pack, however it was unpacked), and trophies_graphics_pack beside the project."""
    graphics = Path(ARCHIVE.fm_user_dir).expanduser() / "graphics"
    roots = sorted(p for p in graphics.iterdir() if p.is_dir() and "troph" in p.name.lower()) if graphics.is_dir() else []
    beside = Path(__file__).resolve().parents[2] / "trophies_graphics_pack"
    return roots + ([beside] if beside.is_dir() else [])


@lru_cache(maxsize=1)
def trophy_index() -> dict[str, Path]:
    """Competition uid -> trophy picture, from the trophy packs' config.xml. The pictures are the user's own download:
    the repository ships none (sortitoutsi's terms allow personal use only, 31 Oct 2026)."""
    found: dict[str, Path] = {}
    for root in trophy_roots():
        for config in sorted(root.rglob("config.xml")):
            for name, uid in _TROPHY_MAP.findall(config.read_text(encoding="utf-8", errors="replace")):
                for extension in (".png", ".jpg"):
                    image = config.parent / f"{name}{extension}"
                    if image.is_file():
                        found.setdefault(uid, image)
                        break
    return found


def trophy_path(competition_uid: str | int | None) -> Path | None:
    """A competition's trophy picture: from the user's trophy pack, else one shipped in app/static (a private build's)."""
    uid = str(competition_uid or "")
    if not re.fullmatch(r"\d+", uid):
        return None
    shipped = _SHIPPED_TROPHIES / f"{uid}.png"
    return trophy_index().get(uid) or (shipped if shipped.is_file() else None)


_ENTITY_DIRECTORIES = {
    "club": "Club",
    "competition": "Competiciones",
    "nation": "Naciones",
}

# FM's HTML exports contain club names but usually omit their numeric UID.
# These curated identities connect the archive clubs already present in this
# save to the user's UID-keyed graphics pack. New clubs still work without a
# mapping and receive the normal monogram fallback in the UI.
KNOWN_CLUB_LOGO_UIDS: dict[str, str] = {
    "arsenal": "602",
    "boreham wood": "5103712",
    "chelsea": "630",
    "fc bayern münchen": "915",
    "fc internazionale milano": "1135",
    "liverpool": "676",
    "manchester city": "679",
    "manchester united": "680",
    "real madrid": "1736",
    "tottenham hotspur": "728",
}


def known_club_logo_uid(club_name: str | None) -> str | None:
    """Return a curated FM club UID when the export supplied only a name."""
    normalized = re.sub(r"\s+", " ", (club_name or "").casefold()).strip()
    return KNOWN_CLUB_LOGO_UIDS.get(normalized)


@lru_cache(maxsize=4096)
def resolve_logo_path(
    entity_type: str, fm_uid: str | int | None, size: str = "small"
) -> Path | None:
    """Return a logo path only for a safe, known entity/size/UID combination."""
    uid = str(fm_uid or "")
    directory = _ENTITY_DIRECTORIES.get(entity_type)
    size_directory = {"small": "Small", "normal": "Normal"}.get(size)
    if directory is None or size_directory is None or not re.fullmatch(r"\d+", uid):
        return None

    base = (
        FLAG_ROOT / size_directory
        if entity_type == "nation"
        else LOGO_ROOT / directory / size_directory
    )
    direct = base / f"{uid}.png"
    if direct.is_file():
        return direct
    # A few nation packs split files into named subdirectories.
    nested = next(base.glob(f"*/{uid}.png"), None) if base.is_dir() else None
    if nested and nested.is_file():
        return nested
    # Any other pack, whatever its folders are called: the game's own map of it (31 Oct 2026).
    mapped = pack_index().get((_PICTURE_KINDS[entity_type], uid))
    if mapped:
        return mapped
    # Many club packs provide only the normal asset. It is still preferable
    # to a missing crest when a compact UI asks for the small variant.
    if size == "small":
        return resolve_logo_path(entity_type, uid, "normal")
    return None
