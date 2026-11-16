"""Vorrat – Vorräte mit Kameras und KI zählen (Home-Assistant-Add-on von BolliSoft).

Mehrere Kameras (ESP32-CAM, Raspberry Pi, Kameras aus Home Assistant) gehören zu Orten wie
„Kühlschrank“ oder „Vorratsregal“. Ein Durchlauf fotografiert alle Kameras eines Ortes, lässt die
Vorräte von einem Vision-Modell über Ollama zählen und übernimmt das Ergebnis in die Liste. Zeigen
mehrere Kameras denselben Ort (z. B. zwei Fächer), werden ihre Zahlen addiert.

Durchläufe: nach Zeitplan (interval_minutes, active_hours), per Knopf in der Oberfläche oder per
Knopf-Entität in Home Assistant (z. B. Automation „Kühlschranktür zu“).
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from ha import HomeAssistant
from inventory import Inventory, slugify
from vision import Ollama, VisionError, build_prompt, fetch_photo, prepare

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("vorrat")

VERSION = os.environ.get("VORRAT_VERSION") or "dev"
OPTIONS_PATH = Path(os.environ.get("VORRAT_OPTIONS", "/data/options.json"))
DATA_DIR = Path(os.environ.get("VORRAT_DATA", "/data"))
WEB_DIR = Path(os.environ.get("VORRAT_WEB", Path(__file__).resolve().parent / "web"))
PORT = int(os.environ.get("VORRAT_PORT", "8099"))
INGRESS_PEER = os.environ.get("VORRAT_INGRESS_PEER", "172.30.32.2")
ASSET_TAG = f"{VERSION}-{int(time.time())}"
ASSET_RE = re.compile(r'(static/[\w.-]+\.(?:js|css))"')


# --------------------------------------------------------------------------- Konfiguration
def load_options():
    try:
        opts = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        log.error("Konfiguration %s nicht lesbar: %s", OPTIONS_PATH, err)
        opts = {}
    cameras, used = [], set()
    for cam in opts.get("cameras") or []:
        name = str(cam.get("name") or "").strip() or "Kamera"
        source = str(cam.get("source") or "").strip()
        if not source:
            continue
        slug = slugify(name)
        while slug in used:
            slug += "_2"
        used.add(slug)
        cameras.append({"name": name, "slug": slug, "source": source,
                        "location": str(cam.get("location") or "").strip() or "Vorrat",
                        "rotation": int(cam.get("rotation") or 0)})
    servers = []
    for s in opts.get("ollama_servers") or []:
        if s.get("url") and s.get("model"):
            servers.append({"url": str(s["url"]), "model": str(s["model"]),
                            "timeout_seconds": int(s.get("timeout_seconds") or 900),
                            "max_width": int(s.get("max_width") or 1024)})
    locations = []
    for cam in cameras:
        if cam["location"] not in locations:
            locations.append(cam["location"])
    return {
        "cameras": cameras,
        "locations": locations,
        "servers": servers,
        "interval_minutes": int(opts.get("interval_minutes") or 0),
        "active_hours": str(opts.get("active_hours") or "").strip(),
        "items_hint": str(opts.get("items_hint") or "").strip(),
        "todo_entity": str(opts.get("todo_entity") or "").strip(),
        "use_mqtt": bool(opts.get("use_mqtt", True)),
    }


def in_active_hours(spec, now=None):
    """'06:00-23:00' (auch über Mitternacht, z. B. '22:00-06:00'); leer = immer."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec or "")
    if not match:
        return True
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    now = now or datetime.now()
    minute, start, end = now.hour * 60 + now.minute, h1 * 60 + m1, h2 * 60 + m2
    return start <= minute < end if start <= end else minute >= start or minute < end


# --------------------------------------------------------------------------- Zählen
class Counter:
    """Warteschlange und Ablauf der Durchläufe (immer nur einer gleichzeitig – Ollama ist der Engpass)."""

    def __init__(self, cfg, inventory, ha, session):
        self.cfg = cfg
        self.inventory = inventory
        self.ha = ha
        self.session = session
        self.ollama = Ollama(cfg["servers"])
        self.queue = asyncio.Queue()
        self.pending = set()
        self.current = None
        self.step = ""
        self.cameras = {cam["slug"]: {"time": None, "error": ""} for cam in cfg["cameras"]}
        self.snap_dir = DATA_DIR / "snapshots"
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        for slug, state in self.cameras.items():
            path = self.snap_dir / f"{slug}.jpg"
            if path.exists():
                state["time"] = path.stat().st_mtime
        self.next_due = time.time() + 60 if cfg["interval_minutes"] else None

    @property
    def max_width(self):
        return max([s["max_width"] for s in self.cfg["servers"]] or [1024])

    def request(self, location=None):
        """Durchlauf einreihen (None = alle Orte). Doppelte Anfragen werden zusammengefasst."""
        for name in ([location] if location else self.cfg["locations"]):
            if name in self.cfg["locations"] and name not in self.pending and name != self.current:
                self.pending.add(name)
                self.queue.put_nowait(name)

    def status_text(self):
        if self.current:
            return f"Zählt {self.current}" + (f" ({self.step})" if self.step else "")
        if self.pending:
            return f"Wartet ({len(self.pending)})"
        errors = [loc for loc in self.cfg["locations"] if self.inventory.location(loc).get("error")]
        return "Fehler: " + ", ".join(errors) if errors else "Bereit"

    async def publish(self):
        await self.ha.publish(self.inventory, self.cfg["locations"], self.status_text(), self.cfg["todo_entity"])

    async def worker(self):
        while True:
            location = await self.queue.get()
            self.pending.discard(location)
            self.current = location
            await self.publish()
            started = time.monotonic()
            try:
                changes = await self.scan(location)
                text = ", ".join(f"{name} {old} → {new}" for name, old, new in changes) or "keine Änderung"
                log.info("%s gezählt in %.0f s: %s", location, time.monotonic() - started, text)
            except VisionError as err:
                log.warning("%s nicht gezählt: %s", location, err)
                self.inventory.scan_failed(location, err)
            except Exception as err:        # unerwarteter Fehler: weiterlaufen, aber melden
                log.exception("%s: unerwarteter Fehler", location)
                self.inventory.scan_failed(location, f"{err.__class__.__name__}: {err}")
            finally:
                self.current = None
                self.step = ""
                await self.publish()

    async def scan(self, location):
        cams = [cam for cam in self.cfg["cameras"] if cam["location"] == location]
        loop = asyncio.get_running_loop()
        photos = []
        # Erst alle Fotos (möglichst gleichzeitig), dann zählen – sonst ändert sich der Ort zwischendurch
        for cam in cams:
            self.step = f"Foto {cam['name']}"
            try:
                raw = await fetch_photo(self.session, cam["source"])
                jpeg = await loop.run_in_executor(None, prepare, raw, cam["rotation"], self.max_width)
            except VisionError as err:
                self.cameras[cam["slug"]]["error"] = str(err)
                raise
            (self.snap_dir / f"{cam['slug']}.jpg").write_bytes(jpeg)
            self.cameras[cam["slug"]] = {"time": time.time(), "error": ""}
            photos.append((cam, jpeg))
        prompt = build_prompt(location, self.inventory.known_names(location), self.cfg["items_hint"])
        total = {}
        for index, (cam, jpeg) in enumerate(photos, 1):
            self.step = f"KI {index}/{len(photos)}" if len(photos) > 1 else "KI"
            counts = await self.ollama.count(self.session, jpeg, prompt)
            log.info("%s: %s", cam["name"], counts or "nichts erkannt")
            for name, count in counts.items():
                total[name] = total.get(name, 0) + count
        return self.inventory.apply_scan(location, total)

    async def scheduler(self):
        while True:
            await asyncio.sleep(20)
            if not self.next_due or time.time() < self.next_due:
                continue
            self.next_due = time.time() + self.cfg["interval_minutes"] * 60
            if in_active_hours(self.cfg["active_hours"]):
                self.request(None)


# --------------------------------------------------------------------------- Web
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def make_app(cfg, inventory, counter, ingress_only=True):
    @web.middleware
    async def guard(request, handler):
        if ingress_only and request.remote != INGRESS_PEER:
            raise web.HTTPForbidden(text="Nur über Home Assistant erreichbar")
        response = await handler(request)
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def index(_request):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        html = ASSET_RE.sub(lambda m: f'{m.group(1)}?v={ASSET_TAG}"', html)
        return no_cache(web.Response(text=html, content_type="text/html"))

    async def state(_request):
        locations = []
        for name in cfg["locations"]:
            loc = inventory.location(name)
            items = [{"name": item_name, "count": item["count"], "min": item.get("min", 0),
                      "ignored": bool(item.get("ignored")), "low": item_name in inventory.low_items(name)}
                     for item_name, item in loc["items"].items()]
            items.sort(key=lambda i: (i["ignored"], i["count"] == 0, i["name"].casefold()))
            cams = [{"name": cam["name"], "slug": cam["slug"], **counter.cameras.get(cam["slug"], {})}
                    for cam in cfg["cameras"] if cam["location"] == name]
            locations.append({"name": name, "items": items, "last_scan": loc.get("last_scan"),
                              "error": loc.get("error") or "", "cameras": cams,
                              "busy": counter.current == name, "pending": name in counter.pending})
        history = [h for h in inventory.data["history"] if h["location"] in cfg["locations"]][-100:][::-1]
        return no_cache(web.json_response({
            "version": VERSION, "status": counter.status_text(), "step": counter.step,
            "locations": locations, "history": history,
            "interval_minutes": cfg["interval_minutes"], "active_hours": cfg["active_hours"],
            "next_scan": counter.next_due, "servers": [f"{s['model']} @ {s['url']}" for s in cfg["servers"]],
            "last_server": counter.ollama.last_server, "ha": counter.ha.mode, "todo": cfg["todo_entity"],
        }))

    async def scan(request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        location = body.get("location") or None
        if location and location not in cfg["locations"]:
            raise web.HTTPNotFound(text="Unbekannter Ort")
        counter.request(location)
        await counter.publish()
        return web.json_response({"ok": True})

    async def item(request):
        try:
            body = await request.json()
            location = str(body["location"])
            name = str(body.get("name") or "")
            action = str(body["action"])
        except (ValueError, KeyError):
            raise web.HTTPBadRequest(text="Ungültige Anfrage")
        if location not in cfg["locations"]:
            raise web.HTTPNotFound(text="Unbekannter Ort")
        items = inventory.location(location)["items"]
        if action != "add" and name not in items:
            raise web.HTTPNotFound(text="Unbekannter Vorrat")
        try:
            value = body.get("value")
            if action in ("add", "count"):
                if action == "add" and inventory.find(location, name):
                    name = inventory.find(location, name)
                name = inventory.set_count(location, name, int(value if value is not None else 1))
            elif action == "min":
                inventory.set_min(location, name, int(value))
            elif action == "ignore":
                inventory.set_ignored(location, name, bool(value))
            elif action == "rename":
                name = inventory.rename(location, name, str(value or ""))
            elif action == "delete":
                inventory.delete(location, name)
            else:
                raise web.HTTPBadRequest(text="Unbekannte Aktion")
        except (TypeError, ValueError) as err:
            raise web.HTTPBadRequest(text=str(err) or "Ungültiger Wert")
        await counter.publish()
        return web.json_response({"ok": True, "name": name})

    async def snapshot(request):
        slug = request.match_info["slug"]
        path = DATA_DIR / "snapshots" / f"{slug}.jpg"
        if slug not in counter.cameras or not path.exists():
            raise web.HTTPNotFound()
        return no_cache(web.FileResponse(path, headers={"Content-Type": "image/jpeg"}))

    app = web.Application(middlewares=[guard], client_max_size=64 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/scan", scan)
    app.router.add_post("/api/item", item)
    app.router.add_get("/api/snapshot/{slug}", snapshot)
    app.router.add_static("/static/", WEB_DIR / "static")
    return app


async def main():
    cfg = load_options()
    if not cfg["cameras"]:
        log.warning("Keine Kamera eingetragen - in der Konfiguration des Add-ons unter cameras ergänzen")
    if not cfg["servers"]:
        log.warning("Kein Ollama-Server eingetragen - in der Konfiguration unter ollama_servers ergänzen")
    log.info("Vorrat %s: %d Kamera(s), Orte: %s", VERSION, len(cfg["cameras"]), ", ".join(cfg["locations"]) or "–")
    inventory = Inventory(DATA_DIR / "inventory.json")
    inventory.sync_locations(cfg["locations"])
    ha = HomeAssistant(VERSION, DATA_DIR)
    async with aiohttp.ClientSession() as session:
        counter = Counter(cfg, inventory, ha, session)

        def on_scan(location):
            counter.request(location)
            asyncio.get_running_loop().create_task(counter.publish())

        await ha.start(session, cfg["use_mqtt"], on_scan)
        await counter.publish()
        tasks = [asyncio.create_task(counter.worker()), asyncio.create_task(counter.scheduler())]
        app = make_app(cfg, inventory, counter, ingress_only=os.environ.get("VORRAT_NO_GUARD") != "1")
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        log.info("Oberfläche auf Port %d", PORT)
        try:
            await asyncio.Event().wait()
        finally:
            for task in tasks:
                task.cancel()
            await ha.stop()
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
