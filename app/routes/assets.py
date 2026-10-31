"""Routes for large user-supplied FM graphics packs kept outside app/static."""

from __future__ import annotations

from flask import Blueprint, abort, send_file

from app.services.logo_assets import resolve_logo_path, trophy_path

assets_bp = Blueprint("assets", __name__)


@assets_bp.get("/logos/<entity_type>/<size>/<fm_uid>.png")
def logo(entity_type: str, size: str, fm_uid: str):
    path = resolve_logo_path(entity_type, fm_uid, size)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", conditional=True, max_age=86400)


@assets_bp.get("/trophies/<fm_uid>.png")
def trophy(fm_uid: str):
    path = trophy_path(fm_uid)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, max_age=86400)
