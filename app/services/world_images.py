"""The archive's own pictures on the world pages, by FM id (docs/images-plan.md).

Uploads are saved in the archive folder's ``generated/`` with the FM id in the file name, listed
in ``archive_images`` and tied to their subject by a ``world_ref`` link. Removing a picture
only hides it (``is_active``), so published World Soccer issues that show it keep working.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.archive_image import ArchiveImage, ArchiveImageLink
from app.services.image_library import images_of, register_archive_image

EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FOLDERS = {"portrait": "portraits", "photograph": "photos", "stadium": "stadiums", "matchday": "photos", "trophy": "trophies"}
KINDS_FOR = {"person": ("portrait", "photograph"), "club": ("stadium", "photograph", "matchday"),
             "competition": ("trophy", "photograph", "matchday")}


class ImageError(ValueError):
    """A wrong upload; the message is shown on the page."""


def add_image(ref: str, kind: str, upload: FileStorage | None, caption: str | None = None) -> ArchiveImage:
    """Save an uploaded picture and tie it to a world reference (``person:<uid>`` and so on)."""
    entity, uid = ref.split(":", 1)
    if kind not in KINDS_FOR.get(entity, ()):
        raise ImageError(f"A {entity} cannot have a {kind} picture.")
    if upload is None or not upload.filename:
        raise ImageError("Choose a picture to upload.")
    extension = Path(secure_filename(upload.filename)).suffix.lower()
    if extension not in EXTENSIONS:
        raise ImageError("The picture must be a PNG, JPG or WebP file.")
    folder = Path(current_app.config["GENERATED_DIR"]) / "world" / FOLDERS[kind]
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{entity}-{uid}-{secrets.token_hex(5)}{extension}"
    upload.save(folder / name)
    image = register_archive_image(f"/static/generated/world/{FOLDERS[kind]}/{name}", kind=kind, caption=caption or None, source="upload")
    image.links.append(ArchiveImageLink(entity_type=entity, entity_id=int(uid.split(":")[0]), relation="subject", world_ref=ref))
    db.session.commit()
    return image


def hide_image(image_id: int) -> ArchiveImage | None:
    image = db.session.get(ArchiveImage, image_id)
    if image is not None:
        image.is_active = False
        db.session.commit()
    return image


def delete_image(image_id: int) -> ArchiveImage | None:
    """Remove a picture for good: its row, its links, the uses that pointed at it, and its file when the archive made or
    keeps it (under the generated folder). A face-pack or logo address is left alone (not a file of the archive)."""
    from config import generated_path

    image = db.session.get(ArchiveImage, image_id)
    if image is None:
        return None
    for use in list(image.usages):
        use.image_id = None
    path = generated_path(image.url)
    db.session.delete(image)
    db.session.commit()
    if path is not None:
        path.unlink(missing_ok=True)
    return image


def library(limit: int = 400) -> list[ArchiveImage]:
    """Every picture in the library, newest first, hidden ones too (the gallery shows them greyed)."""
    return ArchiveImage.query.order_by(ArchiveImage.id.desc()).limit(limit).all()


def pictures(ref: str, kinds: tuple[str, ...] | None = None) -> list[ArchiveImage]:
    return images_of(ref, kinds)


def uploaded_trophy_url(competition_uid: int) -> str | None:
    found = images_of(f"competition:{competition_uid}", ("trophy",))
    return found[0].url if found else None


def find_images(entity_type: str | None = None, entity_id: int | None = None, year: int | None = None, query: str | None = None,
                limit: int = 30, issue_id: int | None = None, preferred_kinds: list[str] | None = None,
                exclude_urls: list[str] | None = None) -> dict:
    """Pictures for a World Soccer story: the archive's pictures tied to a world entity, the
    pictures known by FM id (face, crest, trophy, logo, flag), and a caption search."""

    from app.models import world as world_models
    from app.models.world_soccer_image_use import WorldSoccerImageUse
    from app.services import world_queries as q
    from app.services.logo_assets import resolve_logo_path, trophy_path
    from app.services.world_portraits import face_pack_index, old_portraits, picture_of
    from app.services.world_soccer import world_ref_for

    limit = max(1, min(int(limit), 100))
    excluded = set(exclude_urls or [])
    if issue_id:
        excluded |= {u.image_url for u in db.session.query(WorldSoccerImageUse).filter_by(issue_id=issue_id) if u.image_url}
    found: list[dict] = []

    def add(url: str | None, kind: str, reason: str, image: ArchiveImage | None = None) -> None:
        if url and url not in excluded and all(f["url"] != url for f in found):
            found.append({"url": url, "kind": kind, "reason": reason, "asset_id": image.id if image else None,
                          "caption": image.caption if image else None, "tags": image.tags_json if image else None})

    def archive(ref: str, why: str) -> None:
        for image in images_of(ref):
            add(image.url, image.kind, why, image)

    def face(uid: int) -> None:
        if picture_of(uid, face_pack_index(), old_portraits()):
            add(f"/faces/{uid}.png", "portrait", "the person's face (face pack or archive portrait)")

    def crest(team_uid: int) -> None:
        with db.engines[world_models.BIND].connect() as connection:
            team = q.teams(connection, [team_uid]).get(team_uid)
        if team and team["nation_uid"] and not team["club_uid"] and resolve_logo_path("nation", team["nation_uid"], "normal"):
            add(f"/logos/nation/normal/{team['nation_uid']}.png", "flag", "the national team's flag")
        elif team and resolve_logo_path("club", team_uid, "normal"):
            add(f"/logos/club/normal/{team_uid}.png", "crest", "the club's crest")

    def trophy(competition_uid: int) -> None:
        uploaded = uploaded_trophy_url(competition_uid)
        add(uploaded or (f"/trophies/{competition_uid}.png" if trophy_path(competition_uid) else None), "trophy", "the competition's trophy")
        if resolve_logo_path("competition", competition_uid, "normal"):
            add(f"/logos/competition/normal/{competition_uid}.png", "logo", "the competition's logo")

    if entity_type:
        ref = world_ref_for(entity_type, int(entity_id), year)
        kind, *parts = ref.split(":")
        if kind == "person":
            archive(ref, "linked to this person")
            face(int(parts[0]))
        elif kind in ("club", "club_season"):
            archive(f"club:{parts[0]}", "linked to this club")
            crest(int(parts[0]))
        elif kind in ("competition", "edition"):
            archive(f"competition:{parts[0]}", "linked to this competition")
            trophy(int(parts[0]))
        elif kind == "nation":
            archive(ref, "linked to this nation")
            if resolve_logo_path("nation", int(parts[0]), "normal"):
                add(f"/logos/nation/normal/{parts[0]}.png", "flag", "the nation's flag")
        elif kind == "match":
            archive(ref, "linked to this match")
            for team_uid in (int(parts[1]), int(parts[2])):  # match:<date>:<home>:<away>
                archive(f"club:{team_uid}", "linked to a club in this match")
                crest(team_uid)
            with db.engines[world_models.BIND].connect() as connection:
                fixture = q._one(connection, "select competition_uid from fixtures where id = :id", id=int(entity_id))
            if fixture and fixture["competition_uid"]:
                trophy(fixture["competition_uid"])
    if query and query.strip():
        like = f"%{query.strip()}%"
        for image in (db.session.query(ArchiveImage).filter(ArchiveImage.is_active.is_(True))
                      .filter((ArchiveImage.caption.ilike(like)) | (ArchiveImage.alt_text.ilike(like))).limit(limit)):
            add(image.url, image.kind, f"caption matches '{query.strip()}'", image)
    if preferred_kinds:
        found.sort(key=lambda f: f["kind"] not in preferred_kinds)
    return {"images": found[:limit], "more": len(found) > limit}


def add_generated_image(ref: str, kind: str, picture: bytes, caption: str | None = None, extension: str = "png") -> ArchiveImage:
    """Keep a picture made by the archive (an action photo) as an upload would be kept, marked as generated."""
    entity, uid = ref.split(":", 1)
    if kind not in KINDS_FOR.get(entity, ()):
        raise ImageError(f"A {entity} cannot have a {kind} picture.")
    folder = Path(current_app.config["GENERATED_DIR"]) / "world" / FOLDERS[kind]
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{entity}-{uid}-{secrets.token_hex(5)}.{extension}"
    (folder / name).write_bytes(picture)
    image = register_archive_image(f"/static/generated/world/{FOLDERS[kind]}/{name}", kind=kind, caption=caption or None, source="generated")
    image.links.append(ArchiveImageLink(entity_type=entity, entity_id=int(uid.split(":")[0]), relation="subject", world_ref=ref))
    db.session.commit()
    return image
