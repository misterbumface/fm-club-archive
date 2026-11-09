"""Curator-facing application settings: the AI model that writes bios, stories and reports."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import AppSetting
from app.services import auto_pilot, social_fans, social_feed, writing_voices
from app.services import picture_makers, public_site, save_jobs, site_publish
from app.services.ai_settings import (
    CHECKER_MODEL_SETTING_KEY,
    OLLAMA_URL,
    lmstudio_url,
    set_lmstudio_url,
    ai_provider_label,
    deepseek_model_label,
    get_deepseek_model,
    get_checker_model,
    get_papers_model,
    model_options,
    stored_key,
    set_deepseek_model,
    set_checker_model,
    set_papers_model,
    set_stored_key,
)

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


@settings_bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        requested_model = (request.form.get("deepseek_model") or "").strip()
        try:
            set_deepseek_model(requested_model)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        else:
            flash(
                f"{ai_provider_label(requested_model)} model changed to "
                f"{deepseek_model_label(requested_model)}. "
                "New AI jobs will use it immediately.",
                "success",
            )
        return redirect(url_for("settings.index"))

    from config import ARCHIVE_DIR, read_archive_settings

    archive = read_archive_settings(ARCHIVE_DIR)
    saves = sorted((path.name for path in archive.games_dir.glob("*.fm")), key=str.casefold) if archive.games_dir.is_dir() else []
    dt_selected = picture_makers.draw_things_selected()
    return render_template(
        "settings/index.html",
        selected_model=get_deepseek_model(),
        model_options=model_options(), ollama_url=OLLAMA_URL, lmstudio_url=lmstudio_url(), selected_label=deepseek_model_label(get_deepseek_model()),
        api_key_configured=bool(current_app.config.get("DEEPSEEK_API_KEY") or stored_key("DEEPSEEK_API_KEY")),
        openai_api_key_configured=bool(current_app.config.get("OPENAI_API_KEY") or stored_key("OPENAI_API_KEY")),
        wavespeed_key_configured=bool(stored_key("WAVESPEED_API_KEY")), picture_maker=picture_makers.maker(),
        picture_makers=picture_makers.MAKERS, wavespeed_model=picture_makers.wavespeed_model(), wavespeed_models=picture_makers.WAVESPEED_MODELS,
        wavespeed_edit_model=picture_makers.wavespeed_edit_model(), wavespeed_edit_models=picture_makers.WAVESPEED_EDIT_MODELS,
        dt_illustration=picture_makers._app_setting(picture_makers.DT_ILLUSTRATION_KEY, ""),
        dt_portrait=picture_makers.draw_things_portrait_model(), dt_edit=picture_makers.draw_things_edit_model(),
        dt_selected=dt_selected, dt_known=picture_makers.draw_things_known(dt_selected),
        invoke_url=picture_makers.invoke_url(), invoke_create_model=picture_makers.invoke_create_model(),
        invoke_edit_model=picture_makers.invoke_edit_model(), invoke_models=picture_makers.invoke_models(),
        openai_image_model=picture_makers.openai_model(), openai_image_models=picture_makers.OPENAI_MODELS,
        openai_image_quality=picture_makers.openai_quality(), openai_qualities=picture_makers.OPENAI_QUALITIES,
        archive=archive, archive_dir=ARCHIVE_DIR, saves=saves, restart=request.args.get("restart") == "1",
        automatic=auto_pilot.enabled(), automatic_last=auto_pilot.last(), automatic_minutes=auto_pilot.CHECK_SECONDS // 60,
        automatic_watch=auto_pilot.watching(),
        voices=writing_voices.VOICES, selected_voice=writing_voices.default_voice(), social_on=social_feed.enabled(),
        slow_on=social_fans.slow_enabled(), papers_model=get_papers_model(), checker_model=get_checker_model(),
        checker_chosen=bool(AppSetting.query.filter_by(key=CHECKER_MODEL_SETTING_KEY).first()),
        app_version=_app_version(), followed=_followed_rows(), tunnel=public_site.status(), site_offline=public_site.offline(),
        cloudflare_token_configured=bool(stored_key(site_publish.TOKEN_KEY)), publish_hostname=site_publish.setting(site_publish.HOSTNAME_SETTING) or "",
        publish_account=site_publish.setting(site_publish.ACCOUNT_SETTING) or "", publish_workers=site_publish.export_workers(),
        publish_worker_choices=site_publish.EXPORT_WORKER_CHOICES, last_publish=site_publish.last_publish(),
        job=save_jobs.job(),
    )


@settings_bp.post("/papers-model")
def papers_model_settings():
    try:
        set_papers_model((request.form.get("papers_model") or "").strip())
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash(f"The papers are now written with {deepseek_model_label(get_papers_model())}.", "success")
    return redirect(url_for("settings.index", _anchor="papers"))


@settings_bp.post("/checker-model")
def checker_model_settings():
    try:
        set_checker_model((request.form.get("checker_model") or "").strip())
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash(f"The fact checker now reads with {deepseek_model_label(get_checker_model())}.", "success")
    return redirect(url_for("settings.index", _anchor="checker"))


@settings_bp.post("/lmstudio")
def lmstudio_settings():
    """The LM Studio server's address (this Mac or another machine of the network); its models join the choices."""
    set_lmstudio_url(request.form.get("lmstudio_url"))
    db.session.commit()
    flash(f"LM Studio is looked for at {lmstudio_url()}.", "success")
    return redirect(url_for("settings.index", _anchor="lmstudio"))


@settings_bp.post("/voice")
def voice_settings():
    try:
        writing_voices.set_default_voice((request.form.get("voice") or "").strip())
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash(f"New texts are written in this voice unless you choose another: {writing_voices.voice_label(writing_voices.default_voice())}.",
              "success")
    return redirect(url_for("settings.index", _anchor="voice"))


@settings_bp.post("/social")
def social_settings():
    if "slow" in request.form:
        slow = request.form.get("slow") == "on"
        social_fans.set_slow(slow)
        db.session.commit()
        flash("The fans write by themselves while the app is open." if slow else "The fans write only when you ask them.", "success")
        return redirect(url_for("settings.index", _anchor="social"))
    on = request.form.get("social") == "on"
    social_feed.set_enabled(on)
    db.session.commit()
    flash("The social feed is on." if on else "The social feed is off: no new posts, and the Feed is hidden. Its posts are kept.",
          "success")
    return redirect(url_for("settings.index", _anchor="social"))


def _app_version() -> str:
    try:
        from macapp.version import APP_VERSION
    except ImportError:
        return "source"
    return APP_VERSION


@settings_bp.route("/archive", methods=["POST"])
def archive_settings():
    """This archive's name, game folders, save and face-pack folder (archive.json). The server reads
    them when it starts: the app restarts it."""
    from dataclasses import replace

    from config import ARCHIVE_DIR, read_archive_settings, write_archive_settings

    form = {key: (request.form.get(key) or "").strip() for key in ("name", "fm_user_dir", "game_dir", "save_file", "face_folder")}
    if not form["name"] or not form["fm_user_dir"] or not form["face_folder"]:
        flash("The archive needs a name, a game folder and a face-pack folder name.", "error")
        return redirect(url_for("settings.index"))
    current = read_archive_settings(ARCHIVE_DIR)
    write_archive_settings(replace(current, name=form["name"], fm_user_dir=form["fm_user_dir"], game_dir=form["game_dir"] or current.game_dir,
                                   save_file=form["save_file"] or None, face_folder=form["face_folder"]), ARCHIVE_DIR)
    flash("Archive settings saved. They take effect when the archive restarts.", "success")
    return redirect(url_for("settings.index", restart=1))


@settings_bp.route("/keys", methods=["POST"])
def ai_keys():
    """The AI keys, kept in the app's keys file. An empty field leaves a key as it is; "remove" deletes it."""
    changed = []
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "WAVESPEED_API_KEY", "CLOUDFLARE_API_TOKEN"):
        value = (request.form.get(name) or "").strip()
        if request.form.get(f"remove_{name}"):
            set_stored_key(name, None)
            changed.append(name)
        elif value:
            set_stored_key(name, value)
            changed.append(name)
    flash("Keys saved." if changed else "No key changed.", "success" if changed else "info")
    return redirect(url_for("settings.index"))


@settings_bp.route("/automatic", methods=["POST"])
def automatic():
    """The automatic switch: import the newest save, write the papers and publish, by itself, while the app runs."""
    from app.services import auto_pilot

    on = "on" in request.form.getlist("automatic")  # the form sends "off" from a hidden field and "on" from the checkbox
    auto_pilot.set_enabled(on)
    flash("Automatic is on: the archive reads a new save by itself, writes the papers and publishes." if on else "Automatic is off.", "info")
    return redirect(url_for("settings.index") + "#automatic")


@settings_bp.route("/public-site", methods=["POST"])
def public_site_settings():
    """The public site's switch: offline (the holding page) or online; or a fresh check of the tunnel and the site."""
    from app.services import public_site

    if request.form.get("offline") in ("on", "off"):
        public_site.set_offline(request.form["offline"] == "on")
        flash("The public site shows its holding page." if request.form["offline"] == "on" else "The public site is back.", "info")
    else:
        found = public_site.status(fresh=True)
        flash(f"Checked: {found.word}." + (f" {found.address} answered {found.http_status}." if found.http_status else ""), "info")
    return redirect(url_for("settings.index") + "#public")


@settings_bp.route("/publish", methods=["POST"])
def publish_site():
    """Save the cloud settings (the hostname, the account id) and, on "Publish", put the paper on Cloudflare in the background."""
    from app.services import site_publish

    site_publish.set_setting(site_publish.HOSTNAME_SETTING, (request.form.get("hostname") or "").strip().lower() or None)
    site_publish.set_setting(site_publish.ACCOUNT_SETTING, (request.form.get("account_id") or "").strip() or None)
    site_publish.set_setting(site_publish.WORKERS_SETTING, request.form.get("workers"))
    if request.form.get("action") in ("publish", "resume"):
        if not stored_key(site_publish.TOKEN_KEY):
            flash("Add a Cloudflare API token in the keys card first.", "error")
            return redirect(url_for("settings.index") + "#public")
        try:
            save_jobs.start_publish(current_app._get_current_object(), reuse=request.form["action"] == "resume")
        except RuntimeError as exc:
            flash(str(exc), "error")
        else:
            flash("Publishing the paper to Cloudflare: the export first, then the upload. The Desk shows the progress.", "info")
    else:
        flash("Cloud settings saved.", "info")
    return redirect(url_for("settings.index") + "#public")


@settings_bp.route("/pictures", methods=["POST"])
def picture_settings():
    """Who draws the pictures: Draw Things on this Mac, or WaveSpeed's API with a model path."""
    from app.services import picture_makers

    try:
        picture_makers.set_maker(request.form.get("picture_maker") or "", request.form.get("wavespeed_model"), request.form.get("wavespeed_edit_model"),
                                 request.form.get("openai_image_model"), request.form.get("openai_image_quality"), commit=False)
        picture_makers.set_draw_things_models(request.form.get("dt_illustration"), request.form.get("dt_portrait"), request.form.get("dt_edit"))
        picture_makers.set_invoke(request.form.get("invoke_url"), request.form.get("invoke_create_model"), request.form.get("invoke_edit_model"))
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash(f"Pictures are drawn by {picture_makers.MAKERS[picture_makers.maker()]}.", "success")
    return redirect(url_for("settings.index") + "#pictures")


@settings_bp.route("/outlets", methods=["GET", "POST"])
def outlets():
    """The news outlets (name, voice, beat) and the followed clubs."""
    from app.models.outlet import Outlet
    from app.services import outlets as outlet_service

    all_outlets = outlet_service.ensure_outlets()
    if request.method == "POST":
        outlet = db.session.get(Outlet, request.form.get("outlet_id", type=int) or 0)
        if outlet is None:
            flash("That outlet does not exist.", "error")
            return redirect(url_for("settings.outlets"))
        name = (request.form.get("name") or "").strip()
        voice = (request.form.get("voice") or "").strip()
        beats = [b.strip() for b in (request.form.get("beats") or "").replace("\n", ",").split(",") if b.strip()]
        if not name or not voice or not beats:
            flash("An outlet needs a name, a voice and at least one beat.", "error")
            return redirect(url_for("settings.outlets") + f"#outlet-{outlet.id}")
        outlet.name, outlet.voice, outlet.beats = name, voice, beats
        outlet.tagline = (request.form.get("tagline") or "").strip() or None
        outlet.byline = (request.form.get("byline") or "").strip() or name
        outlet.articles_per_edition = max(1, min(request.form.get("articles_per_edition", type=int) or 6, 30))
        outlet.min_score = max(0.0, request.form.get("min_score", type=float) or 0.0)
        outlet.active = request.form.get("active") == "on"
        db.session.commit()
        flash(f"{outlet.name} saved.", "success")
        return redirect(url_for("settings.outlets") + f"#outlet-{outlet.id}")
    return render_template("settings/outlets.html", outlets=all_outlets, followed=_followed_rows(), nations=_nation_names())


@settings_bp.route("/followed-clubs", methods=["POST"])
def followed_clubs():
    """Add or remove a followed club; its stories get the "home" beat and a boost."""
    from app.services import outlets as outlet_service

    uids = list(outlet_service.followed_clubs())
    if request.form.get("remove", type=int):
        uids = [u for u in uids if u != request.form.get("remove", type=int)]
    elif (wanted := (request.form.get("club") or "").strip()):
        found = _find_club(wanted)
        if found is None:
            flash(f"No club called “{wanted}” is in the world database (use the full name or the FM id).", "error")
            return redirect(_back_to_followed())
        uids.append(found)
    outlet_service.set_followed_clubs(uids)
    db.session.commit()
    return redirect(_back_to_followed())


def _back_to_followed() -> str:
    """The followed-clubs card the form was sent from: on Settings, or on Outlets."""
    return (url_for("settings.index") if request.form.get("from") == "settings" else url_for("settings.outlets")) + "#followed"


def _world_rows(sql: str, **params) -> list[dict]:
    from app.models import world as world_models
    from app.services import world_queries

    with db.engines[world_models.BIND].connect() as connection:
        return world_queries._rows(connection, sql, **params)


def _followed_rows() -> list[dict]:
    from app.services import outlets as outlet_service

    uids = outlet_service.followed_clubs()
    names = {r["fm_uid"]: r["name"] for r in _world_rows(f"select fm_uid, name from clubs where fm_uid in ({','.join(str(int(u)) for u in uids) or '0'})")}
    return [{"uid": uid, "name": names.get(uid, f"club {uid}")} for uid in uids]


def _find_club(wanted: str) -> int | None:
    if wanted.isdigit():
        rows = _world_rows("select fm_uid from clubs where fm_uid = :u", u=int(wanted))
    else:
        rows = _world_rows("select c.fm_uid from clubs c left join teams t on t.fm_uid = c.fm_uid where lower(c.name) = lower(:n) "
                           "or lower(c.short_name) = lower(:n) order by coalesce(t.reputation, 0) desc", n=wanted)
    return rows[0]["fm_uid"] if rows else None


def _nation_names() -> dict[int, str]:
    return {r["fm_uid"]: r["name"] for r in _world_rows("select fm_uid, name from nations")}


@settings_bp.route("/writers/<int:writer_id>", methods=["POST"])
def save_writer(writer_id: int):
    """Edit a writer, move them to another outlet, or switch them off."""
    from app.models.outlet import Outlet, Writer

    writer = db.session.get(Writer, writer_id)
    if writer is None:
        flash("That writer does not exist.", "error")
        return redirect(url_for("settings.outlets"))
    _fill_writer(writer, Outlet)
    db.session.commit()
    flash(f"{writer.name} saved.", "success")
    return redirect(url_for("settings.outlets") + f"#writer-{writer.id}")


@settings_bp.route("/outlets/<int:outlet_id>/writers", methods=["POST"])
def add_writer(outlet_id: int):
    from app.models.outlet import Outlet, Writer

    outlet = db.session.get(Outlet, outlet_id)
    if outlet is None:
        flash("That outlet does not exist.", "error")
        return redirect(url_for("settings.outlets"))
    writer = Writer(outlet=outlet, sort_order=len(outlet.writers), active=True, name="", persona="", style="")
    _fill_writer(writer, Outlet, new=True)
    if not writer.name or not writer.persona or not writer.style:
        db.session.rollback()
        flash("A writer needs a name, a persona and a style.", "error")
        return redirect(url_for("settings.outlets") + f"#outlet-{outlet.id}")
    db.session.add(writer)
    db.session.commit()
    flash(f"{writer.name} joins {outlet.name}.", "success")
    return redirect(url_for("settings.outlets") + f"#writer-{writer.id}")


def _fill_writer(writer, outlet_model, new: bool = False) -> None:
    writer.name = (request.form.get("name") or writer.name).strip()[:120]
    writer.persona = (request.form.get("persona") or writer.persona).strip()
    writer.style = (request.form.get("style") or writer.style).strip()
    writer.quirks = (request.form.get("quirks") or "").strip() or None
    if not new:
        writer.active = request.form.get("active") == "on"
        moved = db.session.get(outlet_model, request.form.get("outlet_id", type=int) or 0)
        if moved is not None:
            writer.outlet = moved


@settings_bp.route("/backup", methods=["POST"])
def backup():
    """Save a copy of the library (texts, editions, pictures' records, settings) in instance/backups."""
    from pathlib import Path

    from app.services.backup_service import create_backup, sqlite_path_from_uri

    try:
        result = create_backup(sqlite_path_from_uri(current_app.config["SQLALCHEMY_DATABASE_URI"]),
                               Path(current_app.instance_path) / "backups")
    except (OSError, ValueError) as exc:
        flash(f"No backup: {exc}", "error")
    else:
        flash(f"Backup saved: {result.path.name} ({result.size_bytes / 1_048_576:.1f} MB).", "success")
    return redirect(url_for("settings.index"))
