"""Home Assistant: Themes neu laden, Standard setzen, geladene Themes abfragen, configuration.yaml prüfen."""
import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path

import aiohttp

log = logging.getLogger("hass")

SUPERVISOR = os.environ.get("THEMES_SUPERVISOR_URL", "http://supervisor")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
TIMEOUT = aiohttp.ClientTimeout(total=20)
THEMES_LINE = "themes: !include_dir_merge_named themes"
TOP_KEY_RE = re.compile(r"^([A-Za-z_][\w]*)\s*:(.*)$")


class HomeAssistantError(Exception):
    pass


class HomeAssistant:
    def __init__(self, session):
        self.session = session
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    async def _post(self, path, data=None):
        try:
            async with self.session.post(f"{SUPERVISOR}/core/api{path}", json=data or {}, headers=self.headers,
                                         timeout=TIMEOUT) as resp:
                if resp.status >= 400:
                    raise HomeAssistantError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Home Assistant nicht erreichbar ({err.__class__.__name__})") from err

    async def reload_themes(self):
        await self._post("/services/frontend/reload_themes")

    async def set_default(self, name):
        """Theme als Standard für hell und dunkel (gilt für alle mit Design „Backend-ausgewählt“)."""
        await self._post("/services/frontend/set_theme", {"name": name})
        await self._post("/services/frontend/set_theme", {"name": name, "mode": "dark"})

    async def check_config(self):
        result = await self._post("/config/core/check_config")
        return result.get("result") == "valid", result.get("errors") or ""

    async def restart(self):
        await self._post("/services/homeassistant/restart")

    async def ws(self, command):
        """Ein Befehl über die WebSocket-API; Fehler von Home Assistant als HomeAssistantError."""
        url = SUPERVISOR.replace("http", "ws", 1) + "/core/websocket"
        try:
            async with self.session.ws_connect(url, timeout=10, heartbeat=None) as ws:
                msg = await ws.receive_json(timeout=10)
                if msg.get("type") == "auth_required":
                    await ws.send_json({"type": "auth", "access_token": TOKEN})
                    msg = await ws.receive_json(timeout=10)
                if msg.get("type") != "auth_ok":
                    raise HomeAssistantError("Anmeldung an Home Assistant fehlgeschlagen")
                await ws.send_json({"id": 1, **command})
                while True:
                    msg = await ws.receive_json(timeout=10)
                    if msg.get("id") == 1:
                        if not msg.get("success"):
                            error = msg.get("error") or {}
                            raise HomeAssistantError(error.get("message") or error.get("code") or str(error))
                        return msg.get("result")
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError) as err:
            raise HomeAssistantError(f"WebSocket nicht erreichbar ({err.__class__.__name__})") from err

    async def themes(self):
        """Von Home Assistant geladene Themes: {themes, default_theme, default_dark_theme}."""
        return await self.ws({"type": "frontend/get_themes"})

    async def ensure_resource(self, prefix, url):
        """Dashboard-Ressource (JavaScript-Modul) eintragen oder auf die neue Adresse ändern."""
        resources = await self.ws({"type": "lovelace/resources"}) or []
        mine = [r for r in resources if str(r.get("url", "")).split("?")[0] == prefix]
        if mine and mine[0].get("url") == url:
            return "ok"
        if mine:
            await self.ws({"type": "lovelace/resources/update", "resource_id": mine[0]["id"],
                           "res_type": "module", "url": url})
            return "updated"
        await self.ws({"type": "lovelace/resources/create", "res_type": "module", "url": url})
        return "created"


# --------------------------------------------------------------------------- configuration.yaml
def _frontend_block(lines):
    """Zeilennummern (start, ende) des Abschnitts frontend: oder None."""
    for i, line in enumerate(lines):
        m = TOP_KEY_RE.match(line)
        if m and m.group(1) == "frontend":
            end = i + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#"):
                end += 1
            return i, end
    return None


def themes_setup(config_dir):
    """Wohin Themes gehören: {"state": "ok"|"missing"|"unsupported", "dir": Path|None, "detail": str}."""
    path = Path(config_dir) / "configuration.yaml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        return {"state": "unsupported", "dir": None, "detail": f"configuration.yaml nicht lesbar ({err.strerror})"}
    block = _frontend_block(lines)
    if block is None:
        return {"state": "missing", "dir": None, "detail": "In der configuration.yaml fehlt frontend: themes:"}
    start, end = block
    inline = TOP_KEY_RE.match(lines[start]).group(2).strip()
    if inline.startswith("!include"):
        return {"state": "unsupported", "dir": None,
                "detail": f"frontend steht in einer eigenen Datei ({inline}) – dort themes: eintragen"}
    for line in lines[start + 1:end]:
        stripped = line.strip()
        if not stripped.startswith("themes:"):
            continue
        value = stripped[len("themes:"):].split("#")[0].strip()
        m = re.match(r"^!include_dir_merge_named\s+(\S+)$", value)
        if m:
            folder = (Path(config_dir) / m.group(1).strip("'\"")).resolve()
            return {"state": "ok", "dir": folder, "detail": f"themes: !include_dir_merge_named {m.group(1)}"}
        return {"state": "unsupported", "dir": None,
                "detail": f"themes: {value or '(Liste in der Datei)'} – nötig ist !include_dir_merge_named themes"}
    return {"state": "missing", "dir": None, "detail": "frontend: ist da, aber ohne themes:"}


def add_themes_line(config_dir):
    """themes: !include_dir_merge_named themes in die configuration.yaml eintragen (mit Sicherung)."""
    path = Path(config_dir) / "configuration.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    block = _frontend_block(lines)
    if block is None:
        new = text.rstrip("\n") + "\n\nfrontend:\n  " + THEMES_LINE + "\n"
    else:
        start, end = block
        if TOP_KEY_RE.match(lines[start]).group(2).strip():
            raise HomeAssistantError("frontend: hat schon einen Wert in derselben Zeile – bitte von Hand eintragen")
        indent = "  "
        for line in lines[start + 1:end]:
            if line.strip() and not line.lstrip().startswith("#"):
                indent = line[:len(line) - len(line.lstrip())]
                break
        lines.insert(start + 1, indent + THEMES_LINE)
        new = "\n".join(lines) + "\n"
    backup = path.with_name(f"configuration.yaml.vor-themes-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    path.write_text(new, encoding="utf-8")
    (Path(config_dir) / "themes").mkdir(exist_ok=True)
    log.info("themes: in configuration.yaml eingetragen, Sicherung %s", backup.name)
    return backup.name
