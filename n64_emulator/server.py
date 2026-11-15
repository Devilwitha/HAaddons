"""Retro-Emulator (GBA / N64) - Home-Assistant-Add-on von BolliSoft.

Liefert über Ingress (Seitenleiste) eine Spielebibliothek und den Emulator (EmulatorJS,
läuft als WebAssembly im Browser). Auf dem Server liegen nur die Dateien:

  <rom_folder>/            ROMs - per Upload oder Samba
  <rom_folder>/saves/      Spielstände des Spiels selbst (SRAM/EEPROM, .sav)
  <rom_folder>/states/     Schnellspeicherstände (Slot 1-9) mit Vorschaubild

So spielt man auf PC, Tablet und Handy am selben Stand weiter.

Dieselbe Datei steckt im GBA- und im N64-Add-on; welches System gilt, bestimmt EMU_SYSTEM
(im Dockerfile gesetzt). Auch web/ ist identisch bis auf web/static/system.js.
"""
import json
import logging
import os
import re
import time
from pathlib import Path

from aiohttp import web

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
        "rom_max": 96 * MB,       # größte N64-ROM: 64 MB
        "save_max": 4 * MB,       # EEPROM/SRAM/Flash/Controller Pak zusammen < 0,5 MB
        "state_max": 64 * MB,     # Savestate mit Expansion Pak: ca. 16-20 MB
    },
}

VERSION = os.environ.get("EMU_VERSION") or "dev"   # aus config.yaml (BUILD_VERSION beim Bauen)
SYSTEM_ID = os.environ.get("EMU_SYSTEM", "gba")
SYSTEM = SYSTEMS[SYSTEM_ID]
OPTIONS_PATH = Path(os.environ.get("EMU_OPTIONS", "/data/options.json"))
WEB_DIR = Path(os.environ.get("EMU_WEB", Path(__file__).resolve().parent / "web"))
EJS_DIR = Path(os.environ.get("EMU_EJS", "/opt/emulatorjs/data"))
PORT = int(os.environ.get("EMU_PORT", "8099"))
INGRESS_PEER = os.environ.get("EMU_INGRESS_PEER", "172.30.32.2")
ALLOWED_ROOTS = (Path("/share"), Path("/media"))
ROM_EXT = set(SYSTEM["ext"])
ROM_MAX = SYSTEM["rom_max"]
SAVE_MAX = SYSTEM["save_max"]
STATE_MAX = SYSTEM["state_max"]
SHOT_MAX = 2 * MB
SLOTS = range(1, 10)
EXT_TEXT = ", ".join(sorted(ROM_EXT, key=lambda e: (e in (".zip", ".7z"), e)))

log = logging.getLogger(SYSTEM_ID)


# --------------------------------------------------------------------------- Optionen / Pfade
def load_rom_dir():
    try:
        options = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        options = {}
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
        raise web.HTTPBadRequest(text="Slot 1-9")
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


# --------------------------------------------------------------------------- Web-App
def make_app(library, ingress_only=True):
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
                            raise web.HTTPRequestEntityTooLarge(max_size=ROM_MAX, actual_size=size)
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

    app = web.Application(middlewares=[guard], client_max_size=ROM_MAX + 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/play", play)
    app.router.add_get("/api/roms", roms)
    app.router.add_post("/api/upload", upload)
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
    app = make_app(Library(root), ingress_only=os.environ.get("EMU_NO_GUARD") != "1")
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
