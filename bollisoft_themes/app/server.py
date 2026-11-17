"""BolliSoft Themes – Designs für Home Assistant mit Editor (Home-Assistant-Add-on von BolliSoft).

Die mitgelieferten Designs (presets.py) und eigene Themes aus dem Editor werden als YAML-Dateien in
<config>/themes/bollisoft/ geschrieben und danach mit frontend.reload_themes geladen. Eigene Themes
liegen in /data/themes.json. Andere Dateien im Theme-Ordner fasst das Add-on nicht an.

Die BolliSoft-Karten (cards/bollisoft-cards.js) kommen nach <config>/www/bollisoft-themes/ und werden als
Dashboard-Ressource eingetragen.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

import aiohttp
import yaml
from aiohttp import web

import themegen
from hass import HomeAssistant, HomeAssistantError, add_themes_line, themes_setup
from presets import PRESETS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("themes")

VERSION = os.environ.get("THEMES_VERSION") or "dev"
DATA_DIR = Path(os.environ.get("THEMES_DATA", "/data"))
CONFIG_DIR = Path(os.environ.get("THEMES_CONFIG_DIR", "/config"))
WEB_DIR = Path(os.environ.get("THEMES_WEB", Path(__file__).resolve().parent / "web"))
PRESET_DIR = Path(__file__).resolve().parent / "themes"
CARDS_FILE = Path(__file__).resolve().parent / "cards" / "bollisoft-cards.js"
CARDS_URL = "/local/bollisoft-themes/bollisoft-cards.js"
PORT = int(os.environ.get("THEMES_PORT", "8099"))
INGRESS_PEER = os.environ.get("THEMES_INGRESS_PEER", "172.30.32.2")
ASSET_TAG = f"{VERSION}-{int(time.time())}"
ASSET_RE = re.compile(r'((?:static|cards)/[\w.-]+\.(?:js|css))"')
SUBFOLDER = "bollisoft"
MARKER = "# BolliSoft Themes"
HEADER = ["BolliSoft Themes – vom Add-on verwaltet, Änderungen in dieser Datei werden überschrieben.",
          "Bearbeiten im Add-on: Seitenleiste → Themes"]
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
MAX_IMAGE = 12 * 1024 * 1024
TOP_NAME_RE = re.compile(r"""^(["']?)([^\s#"'][^:"'#]*?)\1\s*:\s*(#.*)?$""")
PRESET_IDS = {p["id"] for p in PRESETS}


def slugify(text):
    text = unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "theme"


def preset_params(preset):
    return {k: v for k, v in preset.items() if k not in ("id", "description", "file")}


def parse_yaml_theme(text):
    """Ein einzelnes Theme aus YAML: (Name, Variablen je Modus)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ValueError(f"YAML-Fehler: {str(err).splitlines()[0]}") from err
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError("Bitte genau ein Theme einfügen (Name: und darunter die Variablen)")
    name, body = next(iter(data.items()))
    name = str(name).strip()
    if not isinstance(body, dict) or not name or len(name) > 60:
        raise ValueError("Unter dem Theme-Namen müssen die Variablen stehen")
    base = {str(k): str(v) for k, v in body.items()
            if k != "modes" and not str(k).startswith("card-mod") and not isinstance(v, (dict, list))}
    modes = body.get("modes") if isinstance(body.get("modes"), dict) else None
    if modes:
        result = {m: {**base, **{str(k): str(v) for k, v in (modes.get(m) or {}).items()}}
                  for m in ("dark", "light") if m in modes}
    else:
        result = {"light": base}
    if not result:
        raise ValueError("Keine Variablen gefunden")
    return name, result


class Store:
    def __init__(self, path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("builtin", {})
        self.data.setdefault("custom", [])

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def entries(self):
        """Alle Themes mit fertig erzeugten Variablen."""
        out = []
        for preset in PRESETS:
            params = preset_params(preset)
            extra = {}
            if preset.get("file"):
                text = (PRESET_DIR / preset["file"]).read_text(encoding="utf-8")
                name, modes = parse_yaml_theme(text)
                theme = {"name": name, "modes": modes, "card_mod": None}
                extra["yaml"] = text
            else:
                theme = themegen.render(params)
            out.append({"id": preset["id"], "kind": "builtin", "name": theme["name"],
                        "description": preset["description"],
                        "enabled": self.data["builtin"].get(preset["id"], {}).get("enabled", True),
                        "params": themegen.normalize(params), "theme": theme, **extra})
        for item in self.data["custom"]:
            try:
                if item["kind"] == "yaml":
                    name, modes = parse_yaml_theme(item["yaml"])
                    theme = {"name": name, "modes": modes, "card_mod": None}
                    entry = {"yaml": item["yaml"]}
                else:
                    theme = themegen.render(item["params"])
                    entry = {"params": themegen.normalize(item["params"])}
            except (ValueError, KeyError) as err:
                log.warning("Theme %s fehlerhaft: %s", item.get("id"), err)
                continue
            out.append({"id": item["id"], "kind": item["kind"], "name": theme["name"], "description": "",
                        "enabled": item.get("enabled", True), "theme": theme, **entry})
        return out

    def custom(self, theme_id):
        for item in self.data["custom"]:
            if item["id"] == theme_id:
                return item
        return None

    def new_id(self, name):
        base = "eigen-" + slugify(name)
        used = {i["id"] for i in self.data["custom"]} | PRESET_IDS
        theme_id, n = base, 2
        while theme_id in used:
            theme_id, n = f"{base}-{n}", n + 1
        return theme_id


class Themes:
    def __init__(self, store, ha):
        self.store = store
        self.ha = ha
        self.lock = asyncio.Lock()
        self.restart_needed = False
        self.last_error = ""

    def setup(self):
        return themes_setup(CONFIG_DIR)

    def check_name(self, name, own_id=None):
        for entry in self.store.entries():
            if entry["enabled"] and entry["id"] != own_id and entry["name"].casefold() == name.casefold():
                raise ValueError(f"Der Name „{name}“ ist schon vergeben ({entry['name']})")

    def foreign_names(self, folder):
        """Themes in anderen Dateien des Theme-Ordners: {Name: relativer Pfad}."""
        found = {}
        if not folder or not folder.is_dir():
            return found
        for path in sorted(folder.rglob("*.yaml")):
            rel = path.relative_to(folder)
            if rel.parts[0] == SUBFOLDER or any(part.startswith(".") for part in rel.parts):
                continue
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    m = TOP_NAME_RE.match(line)
                    if m:
                        found.setdefault(m.group(2).strip(), str(rel).replace("\\", "/"))
            except OSError:
                continue
        return found

    async def sync(self):
        """Dateien schreiben, alte entfernen, Themes in Home Assistant neu laden."""
        async with self.lock:
            setup = self.setup()
            if setup["state"] != "ok":
                return False
            folder = setup["dir"] / SUBFOLDER
            folder.mkdir(parents=True, exist_ok=True)
            wanted, names = {}, set()
            for entry in self.store.entries():
                if not entry["enabled"]:
                    continue
                if entry["name"].casefold() in names:
                    log.warning("Theme „%s“ doppelt – übersprungen", entry["name"])
                    continue
                names.add(entry["name"].casefold())
                if "yaml" in entry:
                    text = "\n".join(f"# {h}" for h in HEADER) + "\n" + entry["yaml"].strip() + "\n"
                else:
                    text = themegen.to_yaml(entry["theme"], HEADER)
                wanted[f"{entry['id']}.yaml"] = text
            changed = False
            for path in folder.glob("*.yaml"):
                if path.name in wanted:
                    continue
                try:
                    if path.read_text(encoding="utf-8").startswith(MARKER):
                        path.unlink()
                        changed = True
                except OSError as err:
                    log.warning("%s nicht entfernt: %s", path.name, err)
            for filename, text in wanted.items():
                path = folder / filename
                try:
                    if path.exists() and path.read_text(encoding="utf-8") == text:
                        continue
                except OSError:
                    pass
                path.write_text(text, encoding="utf-8")
                changed = True
            if changed:
                log.info("%d Theme(s) in %s geschrieben", len(wanted), folder)
            try:
                await self.ha.reload_themes()
                self.last_error = ""
            except HomeAssistantError as err:
                self.last_error = f"Neu laden fehlgeschlagen: {err}"
                log.warning(self.last_error)
            return True


class Cards:
    """Karten-Datei nach /config/www kopieren und als Dashboard-Ressource eintragen."""

    def __init__(self, ha):
        self.ha = ha
        self.state = "pending"
        self.detail = ""
        self.url = ""
        self.www_created = False

    async def install(self):
        data = CARDS_FILE.read_bytes()
        self.url = f"{CARDS_URL}?v={hashlib.sha1(data).hexdigest()[:10]}"
        www = CONFIG_DIR / "www"
        try:
            if not www.is_dir():
                self.www_created = True
            folder = www / "bollisoft-themes"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / CARDS_FILE.name
            if not target.exists() or target.read_bytes() != data:
                target.write_bytes(data)
                log.info("Karten nach %s kopiert", target)
        except OSError as err:
            self.state, self.detail = "error", f"Datei nicht geschrieben: {err}"
            log.warning(self.detail)
            return
        try:
            result = await self.ha.ensure_resource(CARDS_URL, self.url)
            self.state = "ok"
            self.detail = {"ok": "eingetragen", "updated": "aktualisiert", "created": "neu eingetragen"}[result]
            if result != "ok":
                log.info("Dashboard-Ressource %s %s", self.url, self.detail)
        except HomeAssistantError as err:
            self.state, self.detail = "manual", str(err)
            log.warning("Dashboard-Ressource nicht eingetragen (%s) - bitte von Hand: %s", err, self.url)

    def status(self):
        return {"state": self.state, "detail": self.detail, "url": self.url, "www_created": self.www_created}


# --------------------------------------------------------------------------- Web
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def make_app(themes, cards, ingress_only=True):
    store = themes.store

    @web.middleware
    async def guard(request, handler):
        if ingress_only and request.remote != INGRESS_PEER:
            raise web.HTTPForbidden(text="Nur über Home Assistant erreichbar")
        response = await handler(request)
        if request.path.startswith(("/static/", "/cards/")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def read_json(request):
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Ungültige Anfrage")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Ungültige Anfrage")
        return body

    async def index(_request):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        html = ASSET_RE.sub(lambda m: f'{m.group(1)}?v={ASSET_TAG}"', html)
        return no_cache(web.Response(text=html, content_type="text/html"))

    async def state(_request):
        setup = themes.setup()
        try:
            loaded = await themes.ha.themes()
            ha = {"ok": True, "default_theme": loaded.get("default_theme"),
                  "default_dark_theme": loaded.get("default_dark_theme"),
                  "loaded": sorted((loaded.get("themes") or {}).keys())}
        except HomeAssistantError as err:
            ha = {"ok": False, "error": str(err), "loaded": []}
        entries = store.entries()
        foreign = themes.foreign_names(setup["dir"]) if setup["state"] == "ok" else {}
        conflicts = {e["name"]: foreign[e["name"]] for e in entries if e["enabled"] and e["name"] in foreign}
        if ha["ok"] and any(e["enabled"] and e["name"] in ha["loaded"] for e in entries):
            themes.restart_needed = False
        return no_cache(web.json_response({
            "version": VERSION,
            "setup": {"state": setup["state"], "detail": setup["detail"],
                      "dir": str(setup["dir"]) if setup["dir"] else ""},
            "restart_needed": themes.restart_needed,
            "error": themes.last_error,
            "ha": ha,
            "conflicts": conflicts,
            "fonts": list(themegen.FONTS),
            "cards": cards.status(),
            "themes": entries,
        }))

    async def render(request):
        body = await read_json(request)
        try:
            if "yaml" in body:
                name, modes = parse_yaml_theme(str(body["yaml"] or ""))
                return web.json_response({"theme": {"name": name, "modes": modes, "card_mod": None}})
            theme = themegen.render(body.get("params"))
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err))
        return web.json_response({"theme": theme, "yaml": themegen.to_yaml(theme, HEADER)})

    async def theme_action(request):
        body = await read_json(request)
        action = body.get("action")
        theme_id = str(body.get("id") or "")
        result = {"ok": True}
        try:
            if action == "save":
                params = themegen.normalize(body.get("params"))
                item = store.custom(theme_id) if theme_id else None
                if item and item["kind"] != "editor":
                    raise ValueError("Dieses Theme ist kein Editor-Theme")
                themes.check_name(params["name"], theme_id or None)
                if item:
                    item["params"] = params
                else:
                    theme_id = store.new_id(params["name"])
                    store.data["custom"].append({"id": theme_id, "kind": "editor", "enabled": True,
                                                 "params": params})
                result["id"] = theme_id
            elif action == "save_yaml":
                text = str(body.get("yaml") or "")
                if len(text) > 200_000:
                    raise ValueError("YAML zu groß")
                name, _modes = parse_yaml_theme(text)
                item = store.custom(theme_id) if theme_id else None
                if item and item["kind"] != "yaml":
                    raise ValueError("Dieses Theme ist kein YAML-Theme")
                themes.check_name(name, theme_id or None)
                if item:
                    item["yaml"] = text
                else:
                    theme_id = store.new_id(name)
                    store.data["custom"].append({"id": theme_id, "kind": "yaml", "enabled": True, "yaml": text})
                result["id"] = theme_id
            elif action == "enable":
                value = bool(body.get("value"))
                entry = next((e for e in store.entries() if e["id"] == theme_id), None)
                if not entry:
                    raise web.HTTPNotFound(text="Unbekanntes Theme")
                if value:
                    themes.check_name(entry["name"], theme_id)
                if theme_id in PRESET_IDS:
                    store.data["builtin"].setdefault(theme_id, {})["enabled"] = value
                else:
                    store.custom(theme_id)["enabled"] = value
            elif action == "delete":
                item = store.custom(theme_id)
                if not item:
                    raise web.HTTPNotFound(text="Unbekanntes Theme")
                store.data["custom"].remove(item)
            elif action == "default":
                entry = next((e for e in store.entries() if e["id"] == theme_id), None)
                if not entry:
                    raise web.HTTPNotFound(text="Unbekanntes Theme")
                if not entry["enabled"]:
                    raise ValueError("Theme erst aktivieren")
                await themes.sync()
                await themes.ha.set_default(entry["name"])
                return web.json_response(result)
            elif action == "default_reset":
                await themes.ha.set_default("default")
                return web.json_response(result)
            else:
                raise web.HTTPBadRequest(text="Unbekannte Aktion")
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err))
        except HomeAssistantError as err:
            raise web.HTTPBadGateway(text=str(err))
        store.save()
        result["written"] = await themes.sync()
        return web.json_response(result)

    async def setup_fix(_request):
        setup = themes.setup()
        if setup["state"] == "ok":
            return web.json_response({"ok": True})
        if setup["state"] != "missing":
            raise web.HTTPBadRequest(text=setup["detail"])
        try:
            backup = await asyncio.to_thread(add_themes_line, CONFIG_DIR)
        except (OSError, HomeAssistantError) as err:
            raise web.HTTPBadRequest(text=f"Eintragen fehlgeschlagen: {err}")
        themes.restart_needed = True
        await themes.sync()
        return web.json_response({"ok": True, "backup": backup})

    async def restart(_request):
        try:
            valid, errors = await themes.ha.check_config()
            if not valid:
                raise web.HTTPBadRequest(text=f"Konfiguration fehlerhaft, kein Neustart: {errors}")
            await themes.ha.restart()
        except HomeAssistantError as err:
            raise web.HTTPBadGateway(text=str(err))
        return web.json_response({"ok": True})

    async def conflict(request):
        body = await read_json(request)
        setup = themes.setup()
        rel = themes.foreign_names(setup["dir"]).get(str(body.get("name") or ""))
        if setup["state"] != "ok" or not rel:
            raise web.HTTPNotFound(text="Keine solche Datei")
        path = (setup["dir"] / rel).resolve()
        if setup["dir"].resolve() not in path.parents:
            raise web.HTTPBadRequest(text="Ungültiger Pfad")
        target = path.with_name(path.name + ".bak")
        path.replace(target)
        log.info("Alte Theme-Datei %s umbenannt in %s", rel, target.name)
        await themes.sync()
        return web.json_response({"ok": True, "renamed": f"{rel}.bak"})

    async def upload(request):
        name = request.headers.get("X-Filename", "")
        suffix = Path(name).suffix.lower()
        if suffix not in IMAGE_TYPES:
            raise web.HTTPBadRequest(text="Nur Bilder (jpg, png, webp, gif, avif)")
        data = await request.read()
        if not data or len(data) > MAX_IMAGE:
            raise web.HTTPBadRequest(text="Bild leer oder größer als 12 MB")
        www = CONFIG_DIR / "www"
        existed = www.is_dir()
        folder = www / "bollisoft-themes"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{slugify(Path(name).stem)[:40]}-{int(time.time())}{suffix}"
        (folder / filename).write_bytes(data)
        log.info("Hintergrundbild %s gespeichert", filename)
        return web.json_response({"ok": True, "url": f"/local/bollisoft-themes/{filename}",
                                  "restart_needed": not existed})

    async def download(request):
        theme_id = request.match_info["id"]
        entry = next((e for e in store.entries() if e["id"] == theme_id), None)
        if not entry:
            raise web.HTTPNotFound()
        if "yaml" in entry:
            text = entry["yaml"].strip() + "\n"
        else:
            text = themegen.to_yaml(entry["theme"], [f"{entry['name']} – erstellt mit BolliSoft Themes"])
        return no_cache(web.Response(text=text, content_type="text/yaml", headers={
            "Content-Disposition": f'attachment; filename="{slugify(entry["name"])}.yaml"'}))

    async def cards_install(_request):
        await cards.install()
        return web.json_response(cards.status())

    async def cards_file(_request):
        return web.FileResponse(CARDS_FILE, headers={"Content-Type": "text/javascript; charset=utf-8"})

    app = web.Application(middlewares=[guard], client_max_size=MAX_IMAGE + 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/render", render)
    app.router.add_post("/api/theme", theme_action)
    app.router.add_post("/api/setup/fix", setup_fix)
    app.router.add_post("/api/setup/restart", restart)
    app.router.add_post("/api/conflict", conflict)
    app.router.add_post("/api/upload", upload)
    app.router.add_get("/api/download/{id}", download)
    app.router.add_post("/api/cards/install", cards_install)
    app.router.add_get("/cards/bollisoft-cards.js", cards_file)
    app.router.add_static("/static/", WEB_DIR / "static")
    return app


async def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = Store(DATA_DIR / "themes.json")
    async with aiohttp.ClientSession() as session:
        ha = HomeAssistant(session)
        themes = Themes(store, ha)
        cards = Cards(ha)
        setup = themes.setup()
        log.info("BolliSoft Themes %s: %s", VERSION, setup["detail"])
        if await themes.sync():
            log.info("Themes in %s/%s aktuell", setup["dir"], SUBFOLDER)
        else:
            log.warning("Themes noch nicht eingerichtet - in der Oberfläche „Automatisch eintragen“ wählen")
        await cards.install()
        app = make_app(themes, cards, ingress_only=os.environ.get("THEMES_NO_GUARD") != "1")
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        log.info("Oberfläche auf Port %d", PORT)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
