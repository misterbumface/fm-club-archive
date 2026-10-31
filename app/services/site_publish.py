"""The paper goes to print: the public site exported as static pages and published to Cloudflare, so it is up when the
Mac is asleep (25 Oct 2026, for worldsport.uk and an FMScout thread).

The export is the paper, not the world: the stories, the editions, the scores, the tables and the tournaments, the
clubs, competitions and players they link to, the followed clubs' squads and recent games, and the pictures. It walks
the site from the front page, rendering each page with the site itself, and stops at the edges where the world would
explode (a player's page leads to his club, not to his every game; a club that is not followed leads to its
competitions, not to its squad). Cloudflare serves it as a Worker's static assets (at most 20 000 files, 25 MiB each):
the manifest of hashes goes up first, then only the files Cloudflare lacks, then the Worker version. The app makes the
Worker, its workers.dev address and the custom domain itself, from a token kept with the API keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests

from app.extensions import db
from app.models.app_setting import AppSetting

API = "https://api.cloudflare.com/client/v4"
TOKEN_KEY = "CLOUDFLARE_API_TOKEN"
HOSTNAME_SETTING = "publish_hostname"
ACCOUNT_SETTING = "publish_account_id"
LAST_SETTING = "publish_last"
EXPORTED_SETTING = "publish_exported_at"  # when the folder was last rendered: a Publish renders again only when the archive changed since
MAX_FILES = 20_000
MAX_FILE_BYTES = 25 * 1024 * 1024
COMPATIBILITY_DATE = "2025-01-01"
WORKER_JS = 'export default { async fetch(request, env) { return env.ASSETS.fetch(request); } };\n'
NOT_FOUND_HTML = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Not in this edition</title>"
                  "<style>body{font-family:Helvetica,Arial,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#141414}"
                  "a{color:#1a4fa0}</style></head><body><h1>Not in this edition</h1>"
                  "<p>This page is in the archive but not in the printed paper. <a href='/'>Back to the front page</a>.</p></body></html>")
_LINK = re.compile(r"""(?:href|src)=["']([^"'#?]+)""")
_SRCSET = re.compile(r"""srcset=["']([^"']+)["']""")


# -- what the paper prints ---------------------------------------------------------------------------------------------
_EDITION = re.compile(r"^/competitions/\d+/[^/]+/\d{4}$")


def kind_of(path: str) -> str:
    """The kind of page a path is: the crawl's rules go by it."""
    if path.startswith(("/static/", "/logos/", "/faces/", "/trophies/")):
        return "asset"
    if _EDITION.match(path):
        return "edition"  # one season of a competition
    first = path.split("/")[1] if path.count("/") else ""
    return {"": "front", "clubs": "club", "players": "player", "matches": "match"}.get(first, first or "front")


def follow_link(from_kind: str, to_kind: str, from_followed: bool) -> bool:
    """Whether a link found on a page of ``from_kind`` leads to a page the paper prints."""
    if to_kind == "asset":
        return True
    if from_kind in ("competitions", "edition"):  # a competition's page: not its every season, not its every club (653 x 15 seasons once)
        return to_kind in ("tables", "tournaments", "scores", "front")
    if to_kind == "edition":  # a season's page only when a story, an award, a record or the history names it: 8 086 came
        return from_kind not in ("club", "player", "match")  # from the clubs', players' and matches' honours and careers
    if to_kind == "player":  # a player's page only when a story, a tournament or a followed club names him: the awards, records
        return from_kind in ("story", "front", "section", "tournaments") or (from_kind == "club" and from_followed)  # and legends named 8 318
    if to_kind == "club":  # a club's page when a story, a table, a tournament, a followed club or a printed player names it: the
        return from_kind in ("story", "front", "section", "tournaments", "tables", "scores", "player") or (from_kind == "club" and from_followed)  # matches and awards named 6 160
    if from_kind not in ("club", "player", "match"):
        return True  # the paper's own pages lead anywhere
    if from_kind == "club":
        return from_followed or to_kind not in ("player", "match")  # a followed club's squad and games; others' competitions only
    if from_kind == "player":
        return to_kind not in ("player", "match")  # his clubs and competitions, not his every game
    return to_kind not in ("player",)  # a match leads to its clubs, its competition and the stories


def target_path(path: str) -> str:
    """Where a path lands in the export: a page as index.html in its folder, an asset as itself."""
    if kind_of(path) == "asset":
        return path.lstrip("/")
    return "index.html" if path == "/" else path.strip("/") + "/index.html"


@dataclass
class Export:
    out_dir: Path
    pages: int = 0
    assets: int = 0
    skipped: int = 0
    types: dict[str, str] = field(default_factory=dict)  # file path -> content type, for the upload


EXPORT_WORKERS = 8  # pages rendered at once, each worker a process with its own app and caches (the app's caches are not for threads)
UPLOAD_THREADS = 1  # batches in flight at once: four at once brought "an unknown error" from Cloudflare (25 Oct 2026)
UPLOAD_TRIES = 3
RENDER_CHUNK = 400  # paths rendered per round
_SITE = None  # a worker process's public app, made once


def _worker_site():
    global _SITE
    if _SITE is None:
        from app.public import create_public_app

        _SITE = create_public_app()
    return _SITE


def _render_one(path: str) -> tuple[str, int, str, bytes]:
    """One page rendered by this process's own app: its status, content type and body."""
    site = _worker_site()
    with site.app_context():
        try:
            answer = site.test_client().get(path)
        except Exception:  # noqa: BLE001 - a page that fails to render is left out, the export goes on
            return path, 500, "", b""
        return path, answer.status_code, (answer.content_type or "application/octet-stream").split(";")[0], answer.data


def _render_in_process(site):
    client = site.test_client()

    def render(paths: list[str]) -> list[tuple[str, int, str, bytes]]:
        out = []
        with site.app_context():
            for path in paths:
                try:
                    answer = client.get(path)
                except Exception:  # noqa: BLE001
                    out.append((path, 500, "", b""))
                    continue
                out.append((path, answer.status_code, (answer.content_type or "application/octet-stream").split(";")[0], answer.data))
        return out
    return render


def export_site(site, out_dir: Path, followed: tuple[int, ...], progress: Callable[[str], None] = lambda text: None,
                limit: int = MAX_FILES - 1, workers: int = EXPORT_WORKERS) -> Export:  # one place for the 404 page
    """Render the paper's pages with the public site and write them under ``out_dir`` (emptied first): round by round, a
    round's paths rendered side by side by worker processes (in tests, or with one worker, by the site itself here)."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    export = Export(out_dir)
    counts: dict[str, int] = {}
    followed_paths = {f"/clubs/{uid}" for uid in followed}
    seen: set[str] = {"/"}
    frontier: deque[str] = deque(["/"])
    pool = None
    if workers > 1 and not site.testing:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
        render = lambda paths: list(pool.map(_render_one, paths, chunksize=4))  # noqa: E731
    else:
        render = _render_in_process(site)
    try:
        while frontier and export.pages + export.assets < limit:
            batch = [frontier.popleft() for _ in range(min(len(frontier), RENDER_CHUNK, limit - export.pages - export.assets))]
            for path, status, content_type, body in render(batch):
                kind = kind_of(path)
                if status != 200:
                    export.skipped += 1
                    continue
                target = out_dir / target_path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                export.types["/" + target_path(path)] = content_type
                if kind == "asset":
                    export.assets += 1
                    counts["pictures and crests"] = counts.get("pictures and crests", 0) + 1
                    continue
                export.pages += 1
                counts[KIND_WORDS.get(kind, kind)] = counts.get(KIND_WORDS.get(kind, kind), 0) + 1
                from_followed = any(path == p or path.startswith(p + "/") for p in followed_paths)
                for link in _links(body.decode("utf-8", "replace"), path):
                    if link not in seen and follow_link(kind, kind_of(link), from_followed):
                        seen.add(link)
                        frontier.append(link)
            progress(f"Exporting: {_counts_words(counts)}; {len(frontier)} to go")
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
    (out_dir / "404.html").write_text(NOT_FOUND_HTML, encoding="utf-8")
    export.types["/404.html"] = "text/html"
    (out_dir / ".types.json").write_text(json.dumps(export.types), encoding="utf-8")
    progress(f"Exported {export.pages} pages and {export.assets} files: {_counts_words(counts)}")
    return export


KIND_WORDS = {"front": "the front page", "story": "stories", "section": "sections", "scores": "scores", "tables": "tables", "tournaments": "tournaments",
              "competitions": "competitions", "club": "clubs", "player": "players", "match": "matches", "awards": "awards", "records": "records",
              "legends": "legends", "history": "history", "international": "international", "uefa": "UEFA"}


def _counts_words(counts: dict[str, int]) -> str:
    """"412 stories, 388 clubs, 1,020 pictures and crests", the biggest first."""
    return ", ".join(f"{n:,} {word}" for word, n in sorted(counts.items(), key=lambda kv: -kv[1])[:6])


def _links(html: str, base: str) -> list[str]:
    """The site's own links in a page: paths without query or fragment, same site only."""
    found = []
    for raw in _LINK.findall(html) + [part.strip().split(" ")[0] for group in _SRCSET.findall(html) for part in group.split(",")]:
        if raw.startswith(("http://", "https://", "mailto:", "data:", "javascript:")):
            continue
        path = urlsplit(urljoin(base, raw)).path
        if path and path.startswith("/") and path not in found:
            found.append(path)
    return found


# -- Cloudflare -------------------------------------------------------------------------------------------------------------
def file_hash(content: bytes, extension: str) -> str:
    """Cloudflare's asset hash: 32 hex characters of the SHA-256 of the base64 content and the file's extension."""
    return hashlib.sha256(base64.b64encode(content) + extension.encode()).hexdigest()[:32]


def manifest_of(out_dir: Path) -> tuple[dict[str, dict], dict[str, Path]]:
    """The export's manifest ({"/path": {"hash", "size"}}) and the file of each hash."""
    manifest, files = {}, {}
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file() and p.name != ".types.json"):
        content = path.read_bytes()
        if len(content) > MAX_FILE_BYTES:
            continue
        key = "/" + path.relative_to(out_dir).as_posix()
        digest = file_hash(content, path.suffix)
        manifest[key] = {"hash": digest, "size": len(content)}
        files[digest] = path
    return manifest, files


class CloudflareError(RuntimeError):
    pass


class Cloudflare:
    """The few calls the publish needs, each checked for Cloudflare's ``success``."""

    def __init__(self, token: str, session: requests.Session | None = None) -> None:
        self.token = token
        self.http = session or requests.Session()

    def call(self, method: str, path: str, *, bearer: str | None = None, **kwargs) -> dict:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {bearer or self.token}"
        answer = self.http.request(method, API + path, headers=headers, timeout=kwargs.pop("timeout", 120), **kwargs)
        try:
            data = answer.json()
        except ValueError as exc:
            raise CloudflareError(f"Cloudflare answered {answer.status_code} without JSON to {method} {path}") from exc
        if not data.get("success", False):
            errors = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in data.get("errors") or []) or f"HTTP {answer.status_code}"
            raise CloudflareError(f"{method} {path}: {errors}")
        return data.get("result") or {}

    def account_id(self) -> str:
        found = self.call("GET", "/accounts?per_page=5")
        if not found:
            raise CloudflareError("The token sees no account: give it Account Settings: Read, or set the account id in Settings.")
        return found[0]["id"]

    def upload_assets(self, account: str, script: str, manifest: dict, files: dict[str, Path], types: dict[str, str],
                      progress: Callable[[str], None]) -> str:
        """The upload session, the missing files bucket by bucket, and the completion token."""
        progress(f"Asking Cloudflare which of the {len(manifest):,} files it lacks")
        session = self.call("POST", f"/accounts/{account}/workers/scripts/{script}/assets-upload-session", json={"manifest": manifest})
        jwt, buckets = session.get("jwt"), session.get("buckets") or []
        if not buckets:
            progress("Cloudflare has every file already")
            return jwt
        path_of_hash = {entry["hash"]: path for path, entry in manifest.items()}
        done, total = 0, sum(len(b) for b in buckets)
        size = sum(manifest[path_of_hash[d]]["size"] for b in buckets for d in b)
        progress(f"Uploading {total:,} new files, {size / 1e6:.0f} MB, in {len(buckets)} batches")
        completion = None
        lock = threading.Lock()

        def send(number: int, bucket: list[str]) -> str | None:
            nonlocal done
            parts, kinds = {}, {}
            for digest in bucket:
                path = files[digest]
                content_type = types.get(path_of_hash[digest]) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                parts[digest] = (digest, base64.b64encode(path.read_bytes()), content_type)
                word = "pictures" if content_type.startswith("image/") else KIND_WORDS.get(kind_of(path_of_hash[digest]), "pages")
                kinds[word] = kinds.get(word, 0) + 1
            megabytes = sum(manifest[path_of_hash[d]]["size"] for d in bucket) / 1e6
            with lock:
                progress(f"Uploading batch {number} of {len(buckets)}: {_counts_words(kinds)}, {megabytes:.0f} MB ({done:,} of {total:,} sent)")
            for attempt in range(1, UPLOAD_TRIES + 1):
                try:
                    answer = self.call("POST", f"/accounts/{account}/workers/assets/upload?base64=true", bearer=jwt, files=parts, timeout=900)
                    break
                except (CloudflareError, requests.RequestException) as exc:
                    if attempt == UPLOAD_TRIES:
                        raise
                    with lock:
                        progress(f"Batch {number} failed ({exc}); trying again")
                    time.sleep(5 * attempt)
            with lock:
                done += len(bucket)
            return answer.get("jwt")

        with ThreadPoolExecutor(max_workers=UPLOAD_THREADS) as pool:
            for token in pool.map(lambda item: send(*item), enumerate(buckets, start=1)):
                completion = token or completion
        progress(f"Uploaded {done:,} new files")
        if not completion:
            raise CloudflareError("Cloudflare gave no completion token after the uploads.")
        return completion

    def deploy(self, account: str, script: str, completion_jwt: str) -> None:
        metadata = {"main_module": "index.js", "compatibility_date": COMPATIBILITY_DATE,
                    "assets": {"jwt": completion_jwt, "config": {"html_handling": "auto-trailing-slash", "not_found_handling": "404-page"}},
                    "bindings": [{"name": "ASSETS", "type": "assets"}]}
        self.call("PUT", f"/accounts/{account}/workers/scripts/{script}",
                  files={"metadata": ("metadata.json", json.dumps(metadata), "application/json"),
                         "index.js": ("index.js", WORKER_JS, "application/javascript+module")})

    def workers_dev_address(self, account: str, script: str) -> str | None:
        self.call("POST", f"/accounts/{account}/workers/scripts/{script}/subdomain", json={"enabled": True})
        found = self.call("GET", f"/accounts/{account}/workers/subdomain")
        return f"https://{script}.{found['subdomain']}.workers.dev" if found.get("subdomain") else None

    def attach_domain(self, account: str, script: str, hostname: str) -> None:
        apex = ".".join(hostname.split(".")[-2:])
        zones = self.call("GET", f"/zones?name={apex}")
        if not zones:
            raise CloudflareError(f"The domain {apex} is not on this Cloudflare account, or the token cannot see its zone.")
        self.call("PUT", f"/accounts/{account}/workers/domains",
                  json={"environment": "production", "hostname": hostname, "service": script, "zone_id": zones[0]["id"],
                        "override_existing_origin": True})  # the domain moves from another Worker of ours (a new name, 31 Oct 2026)


# -- the publish -----------------------------------------------------------------------------------------------------------
def script_name(archive_name: str, hostname: str | None = None) -> str:
    """A Worker name: from the site's domain when it has one, else from the archive's name; lower case, letters, digits and
    dashes. The name is public (it is in the workers.dev address), so a domain keeps the archive's name, often a person's
    own, out of it (31 Oct 2026)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (hostname or archive_name).lower()).strip("-")[:48] or "archive"
    return f"site-{slug}" if hostname else f"fm-archive-{slug}"


def setting(key: str) -> str | None:
    try:
        found = AppSetting.query.filter_by(key=key).first()
    except Exception:  # noqa: BLE001 - no app context or no table
        return None
    return found.value if found else None


def set_setting(key: str, value: str | None) -> None:
    found = AppSetting.query.filter_by(key=key).first()
    if value is None or value == "":
        if found:
            db.session.delete(found)
    elif found is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        found.value = value
    db.session.commit()


def last_publish() -> dict | None:
    raw = setting(LAST_SETTING)
    return json.loads(raw) if raw else None


def archive_changed_since(when: datetime) -> bool:
    """Whether the archive gained an import or an edition since ``when`` (the export is then stale)."""
    from sqlalchemy import func, select

    from app.models import world as world_models
    from app.models.world_soccer_issue import WorldSoccerIssue

    newest_issue = db.session.query(func.max(WorldSoccerIssue.updated_at)).scalar()
    with db.engines[world_models.BIND].connect() as connection:
        newest_import = connection.execute(select(func.max(world_models.WorldSave.imported_at))).scalar()
    for stamp in (newest_issue, newest_import):
        if stamp is None:
            continue
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp)
        if stamp > when:
            return True
    return False


def export_is_fresh(out_dir: Path) -> bool:
    """Whether the folder holds an export and nothing changed in the archive since it was rendered."""
    stamp = setting(EXPORTED_SETTING)
    if not stamp or not (out_dir / ".types.json").exists():
        return False
    try:
        return not archive_changed_since(datetime.fromisoformat(stamp))
    except Exception:  # noqa: BLE001 - no tables, odd stamps: render again
        return False


def export_from_folder(out_dir: Path) -> Export:
    """The export as the folder holds it, for an upload without rendering."""
    export = Export(out_dir, types=json.loads((out_dir / ".types.json").read_text(encoding="utf-8")))
    export.pages = sum(1 for k in export.types if k.endswith(".html") and k != "/404.html")
    export.assets = len(export.types) - export.pages - 1
    return export


def publish(site, out_dir: Path, archive_name: str, followed: tuple[int, ...], token: str, hostname: str | None,
            account: str | None = None, progress: Callable[[str], None] = lambda text: None, client: Cloudflare | None = None,
            reuse: bool = False, workers: int = EXPORT_WORKERS) -> dict:
    """Export the paper and put it on Cloudflare: the Worker, its files, its workers.dev address and the domain. The folder
    is rendered again only when the archive changed since it was last rendered; ``reuse`` takes it as it is."""
    started = time.time()
    if (out_dir / ".types.json").exists() and (reuse or export_is_fresh(out_dir)):
        export = export_from_folder(out_dir)
        progress(f"Using the export on disk: {export.pages} pages and {export.assets} files, nothing changed since")
    else:
        export = export_site(site, out_dir, followed, progress, workers=workers)
        try:
            set_setting(EXPORTED_SETTING, datetime.now().isoformat(timespec="seconds"))
        except Exception:  # noqa: BLE001 - no app context (tests): the stamp is not kept
            pass
    manifest, files = manifest_of(out_dir)
    if len(manifest) > MAX_FILES:
        raise CloudflareError(f"The export holds {len(manifest)} files; Cloudflare takes at most {MAX_FILES}.")
    types = json.loads((out_dir / ".types.json").read_text(encoding="utf-8"))
    cf = client or Cloudflare(token)
    account = account or cf.account_id()
    script = script_name(archive_name, hostname)
    progress(f"Uploading {len(manifest)} files to Cloudflare as {script}")
    completion = cf.upload_assets(account, script, manifest, files, types, progress)
    progress("Making the new version of the site live")
    cf.deploy(account, script, completion)
    progress("Turning on the workers.dev address")
    address = cf.workers_dev_address(account, script)
    if hostname:
        progress(f"Attaching {hostname}")
        cf.attach_domain(account, script, hostname)
        address = f"https://{hostname}"
    result = {"when": datetime.now().isoformat(timespec="minutes"), "files": len(manifest), "pages": export.pages, "address": address,
              "script": script, "seconds": round(time.time() - started)}
    progress(f"Published {export.pages} pages and {len(manifest) - export.pages} files at {address}")
    return result
