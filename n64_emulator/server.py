"""Retro-Emulator (GBA / N64) - Home-Assistant-Add-on von BolliSoft.

Liefert über Ingress (Seitenleiste) eine Spielebibliothek und den Emulator (EmulatorJS,
läuft als WebAssembly im Browser). Auf dem Server liegen nur die Dateien:

  <rom_folder>/            ROMs - per Upload oder Samba
  <rom_folder>/saves/      Spielstände des Spiels selbst (SRAM/EEPROM, .sav)
  <rom_folder>/states/     Schnellspeicherstände (Slot 1-9, Slot 0 = Auto) mit Vorschaubild

So spielt man auf PC, Tablet und Handy am selben Stand weiter.

Dieselbe Datei steckt im GBA- und im N64-Add-on; welches System gilt, bestimmt EMU_SYSTEM
(im Dockerfile gesetzt). Auch web/ ist identisch bis auf web/static/system.js.
"""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

from ha_sensors import Sensor, SensorHub

MB = 1024 * 1024
SYSTEMS = {
    "gba": {
        "name": "GBA Emulator",
        "folder": "/share/gba",
        "ext": {".gba": "GBA", ".gbc": "GBC", ".gb": "GB", ".zip": "GBA", ".7z": "GBA"},
        "rom_max": 64 * MB,       # größte GBA-ROM: 32 MB, Archive etwas Luft
        "save_max": 2 * MB,       # SRAM/Flash: höchstens 128 KB, RTC-Daten dazu
        "state_max": 8 * MB,      # mGBA-Savestate: ca. 0,5 MB
    },
    "n64": {
        "name": "N64 Emulator",
        "folder": "/share/n64",
        "ext": {".z64": "N64", ".n64": "N64", ".v64": "N64", ".zip": "N64", ".7z": "N64"},
        "rom_max": 512 * MB,      # größte N64-ROM: 64 MB; Luft für Archive und ROM-Hacks
        "save_max": 4 * MB,       # EEPROM/SRAM/Flash/Controller Pak zusammen < 0,5 MB
        "state_max": 64 * MB,     # Savestate mit Expansion Pak: ca. 16-20 MB
    },
}

VERSION = os.environ.get("EMU_VERSION") or "dev"   # aus config.yaml (BUILD_VERSION beim Bauen)
OPTIONS_PATH = Path(os.environ.get("EMU_OPTIONS", "/data/options.json"))
WEB_DIR = Path(os.environ.get("EMU_WEB", Path(__file__).resolve().parent / "web"))


def detect_system():
    """EMU_SYSTEM aus dem Dockerfile; fehlt es, das System aus web/static/system.js lesen."""
    env = os.environ.get("EMU_SYSTEM")
    if env in SYSTEMS:
        return env
    try:
        match = re.search(r'\bid:\s*"(\w+)"', (WEB_DIR / "static" / "system.js").read_text(encoding="utf-8"))
        if match and match.group(1) in SYSTEMS:
            return match.group(1)
    except OSError:
        pass
    return "gba"


SYSTEM_ID = detect_system()
SYSTEM = SYSTEMS[SYSTEM_ID]
EJS_DIR = Path(os.environ.get("EMU_EJS", "/opt/emulatorjs/data"))
PORT = int(os.environ.get("EMU_PORT", "8099"))
INGRESS_PEER = os.environ.get("EMU_INGRESS_PEER", "172.30.32.2")
ALLOWED_ROOTS = (Path("/share"), Path("/media"))
ROM_EXT = set(SYSTEM["ext"])
ROM_MAX = SYSTEM["rom_max"]
SAVE_MAX = SYSTEM["save_max"]
STATE_MAX = SYSTEM["state_max"]
SHOT_MAX = 2 * MB
CHUNK_MAX = 8 * MB               # Home-Assistant-Ingress nimmt höchstens 16 MB pro Anfrage an
UPLOAD_TTL = 30 * 60             # unvollständige Uploads werden danach verworfen
SLOTS = range(0, 10)             # 0 = Auto-Speicherstand (Inaktivität, Gerätewechsel)
EXT_TEXT = ", ".join(sorted(ROM_EXT, key=lambda e: (e in (".zip", ".7z"), e)))
STATS_PATH = Path(os.environ.get("EMU_STATS", "/data/stats.json"))
SESSION_TIMEOUT = 180            # ohne Lebenszeichen so lange -> Sitzung beendet (Tab zu, Gerät aus)
TAKEOVER_DEAD = 15               # antwortet das andere Gerät so lange nicht, wird es abgemeldet
TAKEOVER_MAX = 40                # spätestens dann wird das andere Gerät abgemeldet
TICK_SECONDS = 15                # Spielzeit verbuchen und Sensoren aktualisieren

log = logging.getLogger(SYSTEM_ID)


# --------------------------------------------------------------------------- Optionen / Pfade
def load_options():
    try:
        return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def idle_minutes():
    """Minuten ohne Eingabe bis zum automatischen Speichern und Pausieren (0 = aus)."""
    try:
        return max(0, min(240, int(load_options().get("idle_minutes", 10))))
    except (TypeError, ValueError):
        return 10


def load_rom_dir():
    options = load_options()
    default = SYSTEM["folder"]
    raw = str(options.get("rom_folder") or default).strip()
    path = Path(raw)
    if os.environ.get("EMU_ALLOW_ANY_DIR") != "1":
        resolved = Path(os.path.normpath(path))
        if not path.is_absolute() or not any(resolved == r or r in resolved.parents for r in ALLOWED_ROOTS):
            log.warning("rom_folder %r liegt nicht unter /share oder /media - nehme %s", raw, default)
            resolved = Path(default)
        path = resolved
    for sub in ("", "saves", "states"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.saves = self.root / "saves"
        self.states = self.root / "states"

    def rom(self, name):
        """Pfad einer vorhandenen ROM - nur ein reiner Dateiname aus dem Ordner ist erlaubt."""
        if not valid_name(name):
            raise web.HTTPBadRequest(text="Ungültiger Dateiname")
        path = self.root / name
        if not path.is_file():
            raise web.HTTPNotFound(text="Spiel nicht gefunden")
        return path

    def save_path(self, name):
        return self.saves / (Path(name).stem + ".sav")

    def state_path(self, name, slot, ext="state"):
        return self.states / f"{Path(name).stem}.slot{slot}.{ext}"

    def slots(self, name):
        found = []
        for slot in SLOTS:
            state = self.state_path(name, slot)
            if state.is_file():
                found.append({"slot": slot, "time": int(state.stat().st_mtime),
                              "shot": self.state_path(name, slot, "png").is_file()})
        return found

    def listing(self):
        roms = []
        for path in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if not path.is_file() or not valid_name(path.name):
                continue
            stat = path.stat()
            save = self.save_path(path.name)
            slots = self.slots(path.name)
            times = [s["time"] for s in slots] + ([save.stat().st_mtime] if save.is_file() else [])
            roms.append({
                "file": path.name,
                "title": title_of(path.name),
                "system": system_of(path.name),
                "size": stat.st_size,
                "added": int(stat.st_mtime),
                "save": int(save.stat().st_mtime) if save.is_file() else None,
                "slots": slots,
                "played": int(max(times)) if times else None,
            })
        return roms


def valid_name(name):
    return (isinstance(name, str) and 0 < len(name) <= 200 and name == Path(name).name
            and not name.startswith(".") and "/" not in name and "\\" not in name
            and Path(name).suffix.lower() in ROM_EXT)


def title_of(name):
    stem = Path(name).stem.replace("_", " ")
    stem = re.sub(r"^\d{3,4}\s*-\s*", "", stem)          # "1234 - Name" aus No-Intro-Sets
    return re.sub(r"\s+", " ", stem).strip() or name


def system_of(name):
    return SYSTEM["ext"].get(Path(name).suffix.lower(), SYSTEM_ID.upper())


def slot_of(request):
    try:
        slot = int(request.query.get("slot", "1"))
    except ValueError:
        slot = 0
    if slot not in SLOTS:
        raise web.HTTPBadRequest(text="Slot 0-9")
    return slot


async def read_body(request, limit):
    if request.content_length is not None and request.content_length > limit:
        raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=request.content_length)
    data = await request.read()
    if len(data) > limit:
        raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(data))
    return data


def write_atomic(path, data, backup=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    if backup and path.is_file():
        os.replace(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)


def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- Statistik
def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def player_of(request):
    """Name des HA-Benutzers - Ingress schickt ihn in den X-Remote-User-*-Kopfzeilen mit."""
    return (request.headers.get("X-Remote-User-Display-Name") or request.headers.get("X-Remote-User-Name")
            or "Unbekannt").strip()[:60]


def user_of(request):
    """Eindeutige Kennung des HA-Benutzers (für "eine Sitzung pro Benutzer")."""
    return (request.headers.get("X-Remote-User-Id") or request.headers.get("X-Remote-User-Name")
            or player_of(request)).strip()[:80]


class Stats:
    """Sitzungen, Spielzeit und Starts pro Spiel - gespeichert in /data/stats.json."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"games": {}, "sessions": 0, "seconds": 0.0, "last": None}
        self.active = {}             # sid -> {"file", "title", "player", "user", "start", "ping", "booked", "stop"}
        self.dirty = False
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=1).encode("utf-8"))
            self.dirty = False
        except OSError as err:
            log.warning("Statistik nicht gespeichert: %s", err)

    def _game(self, file, title):
        game = self.data["games"].setdefault(file, {"title": title, "starts": 0, "seconds": 0.0, "last": None})
        game["title"] = title
        return game

    def claim(self, file, title, player, user, takeover=False):
        """Pro Benutzer nur eine Sitzung.

        Rückgabe ("ok", sid) | ("conflict", info) | ("wait", info):
        - conflict: der Benutzer spielt schon auf einem anderen Gerät
        - wait: Übernahme läuft - das andere Gerät speichert noch und meldet sich ab
        """
        now = time.time()
        others = [(sid, x) for sid, x in self.active.items() if x["user"] == user]
        if others and not takeover:
            other = max((x for _sid, x in others), key=lambda x: x["start"])
            return "conflict", {"title": other["title"], "since": iso(other["start"]), "file": other["file"]}
        for sid, other in others:
            if not other.get("stop"):
                other["stop"] = now
                log.info("Übernahme: %s soll %s speichern und beenden", user, other["title"])
            if now - other["ping"] > TAKEOVER_DEAD or now - other["stop"] > TAKEOVER_MAX:
                log.info("Übernahme: anderes Gerät antwortet nicht - Sitzung %s beendet", other["title"])
                self.end(sid, until=other["ping"])
        if any(x["user"] == user for x in self.active.values()):
            return "wait", {}
        return "ok", self.start(file, title, player, user)

    def start(self, file, title, player, user=None):
        now = time.time()
        sid = secrets.token_hex(8)
        self.active[sid] = {"file": file, "title": title, "player": player, "user": user or player,
                            "start": now, "ping": now, "booked": now, "stop": None}
        game = self._game(file, title)
        game["starts"] += 1
        game["last"] = now
        self.data["sessions"] += 1
        self.data["last"] = {"file": file, "title": title, "player": player, "time": now}
        self.dirty = True
        log.info("Spiel gestartet: %s (%s)", title, player)
        return sid

    def ping(self, sid):
        """None = unbekannt, "stop" = anderes Gerät übernimmt (speichern und beenden), sonst "ok"."""
        session = self.active.get(sid)
        if session is None:
            return None
        session["ping"] = time.time()
        return "stop" if session.get("stop") else "ok"

    def _book(self, session, until):
        delta = max(0.0, until - session["booked"])
        if delta:
            self._game(session["file"], session["title"])["seconds"] += delta
            self.data["seconds"] += delta
            session["booked"] = until
            self.dirty = True

    def end(self, sid, until=None):
        session = self.active.pop(sid, None)
        if session is None:
            return False
        self._book(session, until or time.time())
        log.info("Spiel beendet: %s nach %d Min.", session["title"], (session["booked"] - session["start"]) // 60)
        return True

    def tick(self):
        now = time.time()
        for sid, session in list(self.active.items()):
            if now - session["ping"] > SESSION_TIMEOUT:
                self.end(sid, until=session["ping"])
            else:
                self._book(session, now)

    def current(self):
        """Zuletzt gestartete laufende Sitzung."""
        return max(self.active.values(), key=lambda x: x["start"]) if self.active else None


SENSORS = [
    Sensor("playing", "sensor", "Wird gespielt", "mdi:gamepad-variant"),
    Sensor("player", "sensor", "Spieler", "mdi:account"),
    Sensor("session", "sensor", "Sitzung", "mdi:timer-outline", "s", "duration", "measurement"),
    Sensor("total_time", "sensor", "Gesamtzeit", "mdi:timer-plus-outline", "h", "duration", "total_increasing"),
    Sensor("sessions", "sensor", "Sitzungen", "mdi:counter", None, None, "total_increasing"),
    Sensor("last_played", "sensor", "Zuletzt gespielt", "mdi:clock-end", None, "timestamp"),
    Sensor("last_game", "sensor", "Letztes Spiel", "mdi:history"),
    Sensor("most_played", "sensor", "Meistgespielt", "mdi:trophy-outline"),
    Sensor("library", "sensor", "Spiele", "mdi:bookshelf", "Spiele", None, "measurement"),
    Sensor("active", "binary_sensor", "Aktiv", "mdi:controller", None, "running"),
]


def update_sensors(hub, stats, library):
    data = stats.data
    games = data["games"]
    current = stats.current()
    players = sorted({s["player"] for s in stats.active.values()})
    now = time.time()
    hub.set("playing", current["title"] if current else "Ruhend",
            {"datei": current["file"], "system": system_of(current["file"]),
             "alle": [s["title"] for s in stats.active.values()]} if current else {})
    hub.set("player", ", ".join(players) if players else "Niemand spielt")
    hub.set("session", int(now - current["start"]) if current else 0,
            {"spiel": current["title"], "seit": iso(current["start"])} if current else {})
    hub.set("total_time", round(data["seconds"] / 3600, 2))
    hub.set("sessions", data["sessions"],
            {"starts_pro_spiel": {g["title"]: g["starts"] for g in sorted(games.values(), key=lambda g: -g["starts"])}})
    last = data.get("last") or {}
    hub.set("last_played", iso(last.get("time")), {"spiel": last.get("title"), "spieler": last.get("player")})
    hub.set("last_game", last.get("title") or "–", {"datei": last.get("file")})
    ranking = sorted(games.values(), key=lambda g: (-g["seconds"], -g["starts"]))
    hub.set("most_played", ranking[0]["title"] if ranking else "–",
            {"rangliste": [{"spiel": g["title"], "starts": g["starts"], "stunden": round(g["seconds"] / 3600, 2),
                            "zuletzt": iso(g.get("last"))} for g in ranking[:10]]})
    try:
        count = len(library.listing())
    except OSError:
        count = None
    hub.set("library", count)
    hub.set("active", bool(stats.active))


# --------------------------------------------------------------------------- Web-App
def make_app(library, ingress_only=True, stats=None, hub=None):
    @web.middleware
    async def guard(request, handler):
        if ingress_only and request.remote != INGRESS_PEER:
            raise web.HTTPForbidden(text="Nur über Home Assistant erreichbar")
        return await handler(request)

    async def page(name):
        return no_cache(web.FileResponse(WEB_DIR / name))

    async def index(_request):
        return await page("index.html")

    async def play(_request):
        return await page("play.html")

    async def roms(_request):
        return no_cache(web.json_response({"roms": library.listing(), "folder": str(library.root),
                                           "version": VERSION, "system": SYSTEM_ID,
                                           "rom_max": ROM_MAX}))

    async def rom_get(request):
        return web.FileResponse(library.rom(request.match_info["name"]),
                                headers={"Cache-Control": "no-cache"})

    async def rom_delete(request):
        path = library.rom(request.match_info["name"])
        path.unlink()
        log.info("ROM gelöscht: %s (Spielstände bleiben)", path.name)
        return web.json_response({"ok": True})

    async def upload(request):
        reader = await request.multipart()
        saved = []
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "file" or not part.filename:
                continue
            name = Path(part.filename.replace("\\", "/")).name
            if not valid_name(name):
                raise web.HTTPBadRequest(text=f"{name}: erlaubt sind nur {EXT_TEXT}")
            target = library.root / name
            if target.exists():
                raise web.HTTPConflict(text=f"{name} gibt es schon")
            tmp = target.with_name("." + name + ".upload")
            size = 0
            try:
                with tmp.open("wb") as handle:
                    while chunk := await part.read_chunk(256 * 1024):
                        size += len(chunk)
                        if size > ROM_MAX:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=ROM_MAX, actual_size=size,
                                text=f"{name} ist zu groß (höchstens {ROM_MAX // MB} MB)")
                        handle.write(chunk)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            log.info("ROM hochgeladen: %s (%d KB)", name, size // 1024)
            saved.append(name)
        if not saved:
            raise web.HTTPBadRequest(text="Keine Datei erhalten")
        return web.json_response({"ok": True, "files": saved})

    async def save_get(request):
        name = request.match_info["name"]
        library.rom(name)
        path = library.save_path(name)
        if not path.is_file():
            raise web.HTTPNotFound(text="Noch kein Spielstand")
        return no_cache(web.FileResponse(path, headers={"Content-Type": "application/octet-stream"}))

    async def save_put(request):
        name = request.match_info["name"]
        library.rom(name)
        data = await read_body(request, SAVE_MAX)
        if not data:
            raise web.HTTPBadRequest(text="Leerer Spielstand")
        write_atomic(library.save_path(name), data, backup=True)
        return web.json_response({"ok": True, "time": int(time.time())})

    async def state_get(request):
        name = request.match_info["name"]
        library.rom(name)
        path = library.state_path(name, slot_of(request))
        if not path.is_file():
            raise web.HTTPNotFound(text="Slot ist leer")
        return no_cache(web.FileResponse(path, headers={"Content-Type": "application/octet-stream"}))

    # -------------------------------------------------- Upload in Stücken (ROMs und Speicherstände)
    # Ingress begrenzt jede Anfrage auf 16 MB. Größere Dateien kommen deshalb in Teilen zu höchstens
    # CHUNK_MAX: start -> chunk (mehrfach, in Reihenfolge) -> finish.
    uploads = {}

    def drop_upload(uid):
        entry = uploads.pop(uid, None)
        if entry:
            entry["tmp"].unlink(missing_ok=True)

    def expire_uploads():
        now = time.time()
        for uid in [u for u, e in uploads.items() if now - e["time"] > UPLOAD_TTL]:
            drop_upload(uid)

    async def blob_start(request):
        data = await read_json(request)
        kind = data.get("kind")
        name = Path(str(data.get("name", "")).replace("\\", "/")).name
        try:
            size = int(data.get("size"))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="Größe fehlt")
        if size <= 0:
            raise web.HTTPBadRequest(text="Leere Datei")
        if kind == "rom":
            if not valid_name(name):
                raise web.HTTPBadRequest(text=f"{name}: erlaubt sind nur {EXT_TEXT}")
            if size > ROM_MAX:
                raise web.HTTPRequestEntityTooLarge(max_size=ROM_MAX, actual_size=size,
                                                    text=f"{name} ist zu groß (höchstens {ROM_MAX // MB} MB)")
            target = library.root / name
            if target.exists():
                raise web.HTTPConflict(text=f"{name} gibt es schon")
            extra = {}
        elif kind == "state":
            library.rom(name)
            try:
                slot = int(data.get("slot"))
            except (TypeError, ValueError):
                slot = -1
            if slot not in SLOTS:
                raise web.HTTPBadRequest(text="Slot 0-9")
            if size > STATE_MAX:
                raise web.HTTPRequestEntityTooLarge(max_size=STATE_MAX, actual_size=size, text="Speicherstand zu groß")
            target = library.state_path(name, slot)
            extra = {"slot": slot}
        else:
            raise web.HTTPBadRequest(text="Unbekannte Art")
        expire_uploads()
        uid = secrets.token_hex(8)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".upload-{uid}"
        tmp.write_bytes(b"")
        uploads[uid] = {"kind": kind, "name": name, "target": target, "tmp": tmp, "size": size,
                        "received": 0, "time": time.time(), **extra}
        return no_cache(web.json_response({"id": uid, "chunk": CHUNK_MAX}))

    async def blob_chunk(request):
        entry = uploads.get(request.match_info["uid"])
        if entry is None:
            raise web.HTTPNotFound(text="Upload unbekannt oder abgelaufen")
        try:
            offset = int(request.query.get("offset", "-1"))
        except ValueError:
            offset = -1
        if offset != entry["received"]:
            return no_cache(web.json_response({"received": entry["received"]}, status=409))
        data = await read_body(request, CHUNK_MAX)
        if not data or entry["received"] + len(data) > entry["size"]:
            raise web.HTTPBadRequest(text="Teil passt nicht zur angekündigten Größe")
        with entry["tmp"].open("ab") as handle:
            handle.write(data)
        entry["received"] += len(data)
        entry["time"] = time.time()
        return no_cache(web.json_response({"received": entry["received"]}))

    async def blob_finish(request):
        uid = request.match_info["uid"]
        entry = uploads.get(uid)
        if entry is None:
            raise web.HTTPNotFound(text="Upload unbekannt oder abgelaufen")
        if entry["received"] != entry["size"]:
            raise web.HTTPBadRequest(text=f"Unvollständig: {entry['received']} von {entry['size']} Bytes")
        if entry["kind"] == "rom" and entry["target"].exists():
            drop_upload(uid)
            raise web.HTTPConflict(text=f"{entry['name']} gibt es schon")
        os.replace(entry["tmp"], entry["target"])
        uploads.pop(uid, None)
        if entry["kind"] == "state":
            library.state_path(entry["name"], entry["slot"], "png").unlink(missing_ok=True)
        else:
            log.info("ROM hochgeladen: %s (%d KB)", entry["name"], entry["size"] // 1024)
        return no_cache(web.json_response({"ok": True, "file": entry["name"]}))

    async def blob_abort(request):
        drop_upload(request.match_info["uid"])
        return no_cache(web.json_response({"ok": True}))

    async def state_put(request):
        name = request.match_info["name"]
        library.rom(name)
        slot = slot_of(request)
        data = await read_body(request, STATE_MAX)
        if not data:
            raise web.HTTPBadRequest(text="Leerer Speicherstand")
        write_atomic(library.state_path(name, slot), data)
        library.state_path(name, slot, "png").unlink(missing_ok=True)   # altes Bild passt nicht mehr
        return web.json_response({"ok": True, "slot": slot})

    async def shot_get(request):
        name = request.match_info["name"]
        library.rom(name)
        path = library.state_path(name, slot_of(request), "png")
        if not path.is_file():
            raise web.HTTPNotFound()
        return no_cache(web.FileResponse(path, headers={"Content-Type": "image/png"}))

    async def shot_put(request):
        name = request.match_info["name"]
        library.rom(name)
        slot = slot_of(request)
        if not library.state_path(name, slot).is_file():
            raise web.HTTPNotFound(text="Erst den Speicherstand senden")
        data = await read_body(request, SHOT_MAX)
        if not data.startswith(b"\x89PNG"):
            raise web.HTTPBadRequest(text="Nur PNG")
        write_atomic(library.state_path(name, slot, "png"), data)
        return web.json_response({"ok": True})

    if stats is None:
        stats = Stats(STATS_PATH)

    async def publish():
        if hub is not None:
            update_sensors(hub, stats, library)
            await hub.publish()

    async def read_json(request):
        try:
            data = json.loads(await request.text() or "{}")   # sendBeacon schickt text/plain
        except ValueError:
            raise web.HTTPBadRequest(text="JSON erwartet")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="JSON-Objekt erwartet")
        return data

    async def session_start(request):
        data = await read_json(request)
        name = str(data.get("rom", ""))
        library.rom(name)
        result, payload = stats.claim(name, title_of(name), player_of(request), user_of(request),
                                      takeover=bool(data.get("takeover")))
        if result == "conflict":
            return no_cache(web.json_response({"conflict": payload}, status=409))
        if result == "wait":
            return no_cache(web.json_response({"waiting": True}, status=202))
        stats.save()
        asyncio.create_task(publish())
        return no_cache(web.json_response({"sid": payload}))

    async def session_ping(request):
        data = await read_json(request)
        result = stats.ping(str(data.get("sid", "")))
        if result is None:
            raise web.HTTPNotFound(text="Sitzung unbekannt")
        return no_cache(web.json_response({"ok": True, "stop": result == "stop"}))

    async def config_get(_request):
        return no_cache(web.json_response({"idle_minutes": idle_minutes()}))

    async def session_end(request):
        data = await read_json(request)
        if stats.end(str(data.get("sid", ""))):
            stats.save()
            asyncio.create_task(publish())
        return no_cache(web.json_response({"ok": True}))

    async def stats_get(_request):
        values = {key: hub.values.get(key, (None, {}))[0] for key in hub.sensors} if hub else {}
        return no_cache(web.json_response({"stats": stats.data, "sensors": values, "active": len(stats.active)}))

    async def ticker(_app):
        async def loop():
            while True:
                await asyncio.sleep(TICK_SECONDS)
                stats.tick()
                expire_uploads()
                stats.save()
                try:
                    await publish()
                except Exception as err:          # Sensoren dürfen das Spielen nie stören
                    log.warning("Sensoren aktualisieren: %s", err)
        task = asyncio.create_task(loop())
        yield
        task.cancel()
        for sid in list(stats.active):
            stats.end(sid)
        stats.save()

    app = web.Application(middlewares=[guard], client_max_size=ROM_MAX + 1024 * 1024)
    app["stats"] = stats
    app.cleanup_ctx.append(ticker)
    app.router.add_post("/api/session/start", session_start)
    app.router.add_post("/api/session/ping", session_ping)
    app.router.add_post("/api/session/end", session_end)
    app.router.add_get("/api/stats", stats_get)
    app.router.add_get("/api/config", config_get)
    app.router.add_get("/", index)
    app.router.add_get("/play", play)
    app.router.add_get("/api/roms", roms)
    app.router.add_post("/api/upload", upload)
    app.router.add_post("/api/blob/start", blob_start)
    app.router.add_put("/api/blob/{uid}", blob_chunk)
    app.router.add_post("/api/blob/{uid}/finish", blob_finish)
    app.router.add_delete("/api/blob/{uid}", blob_abort)
    app.router.add_get("/api/rom/{name}", rom_get)
    app.router.add_delete("/api/rom/{name}", rom_delete)
    app.router.add_get("/api/save/{name}", save_get)
    app.router.add_put("/api/save/{name}", save_put)
    app.router.add_get("/api/state/{name}", state_get)
    app.router.add_put("/api/state/{name}", state_put)
    app.router.add_get("/api/shot/{name}", shot_get)
    app.router.add_put("/api/shot/{name}", shot_put)
    app.router.add_static("/static/", WEB_DIR / "static")
    app.router.add_static("/ejs/", EJS_DIR)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    root = load_rom_dir()
    log.info("%s %s - Spiele in %s", SYSTEM["name"], VERSION, root)
    if not (EJS_DIR / "loader.js").is_file():
        log.error("EmulatorJS fehlt in %s", EJS_DIR)
    library = Library(root)
    stats = Stats(STATS_PATH)
    hub = SensorHub(f"{SYSTEM_ID}_emulator", SYSTEM["name"], "Emulator (BolliSoft)", VERSION, SENSORS)
    use_mqtt = bool(load_options().get("use_mqtt", True))

    async def start_sensors(_app):
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        await hub.start(session, use_mqtt)
        update_sensors(hub, stats, library)
        await hub.publish()
        yield
        await hub.stop()
        await session.close()

    app = make_app(library, ingress_only=os.environ.get("EMU_NO_GUARD") != "1", stats=stats, hub=hub)
    app.cleanup_ctx.insert(0, start_sensors)
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
