"""The public site as the world sees it: the Cloudflare tunnel that carries it (a system service the app can watch but
not start), the hostnames it serves, whether the site answers, and the "offline" switch that hands readers a holding page
while the archive is being worked on. Found and set up on 25 Oct 2026 for worldsport.uk."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.extensions import db
from app.models.app_setting import AppSetting

SERVICE_PLIST = Path("/Library/LaunchDaemons/com.cloudflare.cloudflared.plist")
SERVICE_LOG = Path("/Library/Logs/com.cloudflare.cloudflared.err.log")
OFFLINE_SETTING = "public_site_offline"
CHECK_TIMEOUT = 6  # seconds to wait for the site through the tunnel
STATUS_CACHE_SECONDS = 60
_HOSTNAME = re.compile(r'\\?"hostname\\?":\s*\\?"([^"\\]+)\\?"')
_cache: tuple[float, "TunnelStatus"] | None = None


@dataclass
class TunnelStatus:
    installed: bool = False
    running: bool = False
    hostnames: list[str] = field(default_factory=list)
    reachable: bool | None = None  # None: not checked (no hostname)
    http_status: int | None = None
    errors: list[str] = field(default_factory=list)  # the log's last error lines
    checked_at: float = 0.0

    @property
    def address(self) -> str | None:
        """The site's address: the bare domain when it is served, else the first hostname."""
        if not self.hostnames:
            return None
        bare = [h for h in self.hostnames if not h.startswith("www.")]
        return f"https://{(bare or self.hostnames)[0]}"

    @property
    def short(self) -> str:
        """One or two words for a badge."""
        return "Online" if self.reachable else "Not running" if not self.running else "Not answering"

    @property
    def word(self) -> str:
        if not self.installed:
            return "no tunnel service on this Mac"
        if not self.running:
            return "the tunnel service is installed but not running"
        if not self.hostnames:
            return "the tunnel runs but serves no hostname yet"
        if self.reachable is False:
            return "the tunnel runs but the site does not answer"
        return "online" if self.reachable else "unchecked"


def _running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", "cloudflared tunnel run"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def hostnames_from_log(text: str) -> list[str]:
    """The hostnames of the tunnel's newest configuration line in its log ("Updated to new configuration")."""
    lines = [line for line in text.splitlines() if "Updated to new configuration" in line]
    if not lines:
        return []
    found = _HOSTNAME.findall(lines[-1])
    return list(dict.fromkeys(found))


def errors_from_log(text: str, limit: int = 3) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if " ERR " in line]
    return [re.sub(r"^\S+\s+ERR\s+", "", line)[:200] for line in lines[-limit:]]


def _reach(address: str) -> int | None:
    try:
        import urllib.request

        with urllib.request.urlopen(urllib.request.Request(address, headers={"User-Agent": "FM Club Archive"}), timeout=CHECK_TIMEOUT) as answer:
            return answer.status
    except Exception as exc:  # noqa: BLE001 - any failure: the site is not reachable this way
        code = getattr(exc, "code", None)
        return int(code) if isinstance(code, int) else None


def status(fresh: bool = False) -> TunnelStatus:
    """The tunnel and the site as they are now, kept for a minute unless ``fresh``."""
    global _cache
    if not fresh and _cache and time.time() - _cache[0] < STATUS_CACHE_SECONDS:
        return _cache[1]
    found = TunnelStatus(installed=SERVICE_PLIST.exists(), checked_at=time.time())
    if found.installed:
        found.running = _running()
        try:
            text = SERVICE_LOG.read_text(errors="replace")[-200_000:]
        except OSError:
            text = ""
        found.hostnames = hostnames_from_log(text)
        found.errors = errors_from_log(text)
    if found.running and found.hostnames:  # every hostname in turn: a resolver may still hold an old answer for one
        for host in [found.address] + [f"https://{h}" for h in found.hostnames]:
            found.http_status = _reach(host)
            if found.http_status is not None:
                break
        found.reachable = found.http_status is not None and found.http_status < 500
    _cache = (time.time(), found)
    return found


def offline() -> bool:
    """Whether the public site shows its holding page."""
    try:
        setting = AppSetting.query.filter_by(key=OFFLINE_SETTING).first()
    except Exception:  # noqa: BLE001 - no app context or no table: the site is on
        return False
    return bool(setting and json.loads(setting.value))


def set_offline(value: bool) -> None:
    setting = AppSetting.query.filter_by(key=OFFLINE_SETTING).first()
    if setting is None:
        setting = AppSetting(key=OFFLINE_SETTING, value=json.dumps(bool(value)))
        db.session.add(setting)
    else:
        setting.value = json.dumps(bool(value))
    db.session.commit()
