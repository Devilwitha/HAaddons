"""Game Remote - Ingress-Server des Home-Assistant-Add-ons.

Aufgaben:
  - liefert die Oberfläche (static/) über Home-Assistant-Ingress aus
  - /api/config: Optionen fürs Frontend (stream_mode, default_touch_overlay, haptic_feedback)
  - /ws: WebSocket-Weiterleitung an die Raspberry Pi 5 Bridge (ws://pi_host:pi_ws_port)
  - /video/<pfad>: HTTP-Weiterleitung an den Video-Server der Bridge
    (http://pi_host:pi_video_port/<pfad>), z. B. /video/offer (WebRTC-Signaling)
  - /video/frames: WebSocket-Weiterleitung der Einzelbilder (JPEG) von der Bridge
  - /api/home_button: drückt die in home_button eingetragene Entität (z. B. ein Servo an der Home-Taste)

Der Browser spricht dadurch nur mit Home Assistant - auch über HTTPS und Nabu Casa,
ohne CORS und ohne direkten Zugriff auf IP und Ports des Pi. Ausnahme: Bei WebRTC laufen
nur das Signaling über diesen Server, die Videodaten selbst gehen direkt (UDP) vom Pi
zum Browser. Ist der Pi nicht direkt erreichbar (unterwegs), schaltet das Frontend
automatisch auf Einzelbilder (JPEG über WebSocket) um, die vollständig über Ingress laufen.
MJPEG als multipart-Antwort kommt über Ingress nicht zuverlässig an (Home Assistant komprimiert und puffert
solche Antworten) - darum WebSocket, das Ingress unverändert durchreicht.
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

import ha_sensors
from ha_sensors import Sensor, SensorHub

OPTIONS_PATH = Path(os.environ.get("SWITCH2_OPTIONS", "/data/options.json"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
PORT = int(os.environ.get("SWITCH2_PORT", "8099"))
INGRESS_PEER = "172.30.32.2"
DEFAULTS = {
    "pi_host": "192.168.1.50",
    "pi_ws_port": 8765,
    "pi_video_port": 8766,
    "stream_mode": "webrtc",
    "default_touch_overlay": True,
    "haptic_feedback": True,
    "use_mqtt": True,
    "home_button": "",
}
# Home-Taste (Servo o. Ä.): Entität und der Dienst, der sie auslöst
HOME_SERVICES = {"button": "press", "input_button": "press", "script": "turn_on"}
HOME_ENTITY = re.compile(r"^(button|input_button|script)\.[a-z0-9_]+$")
HOME_GAP = 3.0                        # Sekunden zwischen zwei Drücken
STATS_PATH = Path(os.environ.get("SWITCH2_STATS", "/data/stats.json"))
VERSION = os.environ.get("SWITCH2_VERSION") or "dev"
# Version in den Adressen von Skripten/Stylesheets: Nach einem Update lädt der Browser sie sicher neu
# (die HA-App hält statische Dateien sonst stundenlang im Zwischenspeicher).
ASSET_TAG = f"{VERSION}-{int(time.time())}"
ASSET_RE = re.compile(r'(static/[\w.-]+\.(?:js|css))"')
TICK_SECONDS = 15
CONSOLE_TEXT = {
    "connected": "Verbunden", "connecting": "Wartet auf Kopplung", "reconnecting": "Verbindet erneut",
    "initializing": "Startet", "disconnected": "Nicht gekoppelt", "crashed": "Fehler", "unavailable": "Nicht verfügbar",
    "paused": "Aus (niemand steuert)",
}
CAPTURE_TEXT = {"ok": "Bild", "no_signal": "Kein Signal", "unavailable": "Nicht verfügbar"}
VIDEO_PATH = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
WS_MAX_MESSAGE = 256 * 1024
FRAME_MAX_MESSAGE = 8 * 1024 * 1024
STREAM_MODES = ("webrtc", "jpeg")
OFFER_MAX_BODY = 256 * 1024
# Aufzeichnungen (Messwerte → Aufzeichnung): zusätzlich zum Download im Browser auf dem HA-Server abgelegt
RECORD_DIR = Path(os.environ.get("SWITCH2_RECORDINGS", "/share/game_remote/aufzeichnungen"))
RECORD_MAX_BODY = 8 * 1024 * 1024
RECORD_KEEP = 30
RECORD_NAME = re.compile(r"^game-remote-aufzeichnung-\d{8}-\d{6}\.txt$")

log = logging.getLogger("switch2")


def load_options():
    """Optionen aus /data/options.json, fehlende Werte aus DEFAULTS (für lokale Tests)."""
    options = dict(DEFAULTS)
    try:
        options.update(json.loads(OPTIONS_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        log.warning("%s fehlt - nutze Standardwerte", OPTIONS_PATH)
    except (OSError, ValueError) as err:
        log.error("%s nicht lesbar (%s) - nutze Standardwerte", OPTIONS_PATH, err)
    options["pi_host"] = str(options["pi_host"]).strip()
    options["pi_ws_port"] = int(options["pi_ws_port"])
    options["pi_video_port"] = int(options["pi_video_port"])
    if options["stream_mode"] == "mjpeg":          # ältere Einstellung: jetzt Einzelbilder
        options["stream_mode"] = "jpeg"
    if options["stream_mode"] not in STREAM_MODES:
        options["stream_mode"] = "webrtc"
    options["default_touch_overlay"] = bool(options["default_touch_overlay"])
    options["haptic_feedback"] = bool(options["haptic_feedback"])
    options["use_mqtt"] = bool(options["use_mqtt"])
    home = str(options.get("home_button") or "").strip().lower()
    if home and not HOME_ENTITY.match(home):
        log.warning("home_button „%s“ ist keine button-, input_button- oder script-Entität - Home-Taste aus", home)
        home = ""
    options["home_button"] = home
    return options


# --------------------------------------------------------------------------- Statistik und Sensoren
def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def player_of(request):
    """Name des HA-Benutzers - Ingress schickt ihn in den X-Remote-User-*-Kopfzeilen mit."""
    return (request.headers.get("X-Remote-User-Display-Name") or request.headers.get("X-Remote-User-Name")
            or "Unbekannt").strip()[:60]


class Stats:
    """Sitzungen (offene Steuerungsseiten) und Spielzeit - gespeichert in /data/stats.json."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"sessions": 0, "seconds": 0.0, "last": None}
        self.active = {}             # sid -> {"player", "start", "booked"}
        self.dirty = False
        self.bridge = {"online": False, "switch": {}, "capture": {}}
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
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError as err:
            log.warning("Statistik nicht gespeichert: %s", err)

    def start(self, player):
        now = time.time()
        sid = secrets.token_hex(8)
        self.active[sid] = {"player": player, "start": now, "booked": now}
        self.data["sessions"] += 1
        self.data["last"] = {"player": player, "time": now}
        self.dirty = True
        log.info("Sitzung gestartet (%s)", player)
        return sid

    def _book(self, session, until):
        delta = max(0.0, until - session["booked"])
        if delta:
            self.data["seconds"] += delta
            session["booked"] = until
            self.dirty = True

    def end(self, sid):
        session = self.active.pop(sid, None)
        if session is None:
            return
        self._book(session, time.time())
        log.info("Sitzung beendet (%s) nach %d Min.", session["player"], (session["booked"] - session["start"]) // 60)

    def tick(self):
        now = time.time()
        for session in self.active.values():
            self._book(session, now)


SENSORS = [
    Sensor("status", "sensor", "Status", "mdi:gamepad-variant-outline"),
    Sensor("player", "sensor", "Spieler", "mdi:account"),
    Sensor("session", "sensor", "Sitzung", "mdi:timer-outline", "s", "duration", "measurement"),
    Sensor("total_time", "sensor", "Gesamtzeit", "mdi:timer-plus-outline", "h", "duration", "total_increasing"),
    Sensor("sessions", "sensor", "Sitzungen", "mdi:counter", None, None, "total_increasing"),
    Sensor("last_played", "sensor", "Zuletzt gespielt", "mdi:clock-end", None, "timestamp"),
    Sensor("active", "binary_sensor", "Aktiv", "mdi:controller", None, "running"),
    Sensor("console", "sensor", "Konsole", "mdi:bluetooth-connect"),
    Sensor("capture", "sensor", "Capture-Card", "mdi:video-input-hdmi"),
    Sensor("bridge", "binary_sensor", "Bridge", "mdi:raspberry-pi", None, "connectivity", None, "diagnostic"),
]


def update_sensors(hub, stats):
    now = time.time()
    current = max(stats.active.values(), key=lambda x: x["start"]) if stats.active else None
    players = sorted({x["player"] for x in stats.active.values()})
    hub.set("status", "Aktiv" if current else "Ruhend", {"geräte": len(stats.active)})
    hub.set("player", ", ".join(players) if players else "Niemand spielt")
    hub.set("session", int(now - current["start"]) if current else 0,
            {"seit": iso(current["start"])} if current else {})
    hub.set("total_time", round(stats.data["seconds"] / 3600, 2))
    hub.set("sessions", stats.data["sessions"])
    last = stats.data.get("last") or {}
    hub.set("last_played", iso(last.get("time")), {"spieler": last.get("player")})
    hub.set("active", bool(stats.active))
    bridge = stats.bridge
    sw = bridge["switch"] if bridge["online"] else {}
    cap = bridge["capture"] if bridge["online"] else {}
    hub.set("console", CONSOLE_TEXT.get(sw.get("state"), "Unbekannt") if bridge["online"] else "Bridge offline",
            {"zustand": sw.get("state"), "detail": sw.get("detail") or None})
    hub.set("capture", CAPTURE_TEXT.get(cap.get("state"), "Unbekannt") if bridge["online"] else "Bridge offline",
            {"zustand": cap.get("state"), "detail": cap.get("detail") or None, "gerät": cap.get("device"),
             "auflösung": f"{cap.get('width')}x{cap.get('height')}" if cap.get("width") else None, "fps": cap.get("fps")})
    hub.set("bridge", bridge["online"])


def bridge_host(options):
    host = options["pi_host"]
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- Handler
async def index(_request):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = ASSET_RE.sub(lambda m: m.group(1) + "?v=" + ASSET_TAG + '"', html)
    return no_cache(web.Response(text=html, content_type="text/html"))


async def api_config(request):
    options = request.app["options"]
    return no_cache(web.json_response({
        "stream_mode": options["stream_mode"],
        "default_touch_overlay": options["default_touch_overlay"],
        "haptic_feedback": options["haptic_feedback"],
        "home_button": bool(options["home_button"]),
        "version": VERSION,
    }))


async def api_home_button(request):
    """Drückt die Entität aus home_button über die REST-API von Home Assistant (höchstens alle HOME_GAP s)."""
    entity = request.app["options"]["home_button"]
    if not entity:
        return web.json_response({"error": "Keine Home-Taste eingerichtet (Option home_button)."}, status=404)
    now = time.monotonic()
    state = request.app["home"]                 # veränderbar: der Zustand der laufenden App selbst ist fest
    if now - state["pressed"] < HOME_GAP:
        return web.json_response({"error": "Home-Taste wurde gerade gedrückt – kurz warten."}, status=429)
    state["pressed"] = now
    domain = entity.split(".", 1)[0]
    url = f"{ha_sensors.SUPERVISOR}/core/api/services/{domain}/{HOME_SERVICES[domain]}"
    try:
        async with request.app["session"].post(url, json={"entity_id": entity},
                                               headers={"Authorization": f"Bearer {ha_sensors.TOKEN}"},
                                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status >= 400:
                text = (await resp.text())[:200]
                log.warning("Home-Taste %s: Home Assistant antwortet %s %s", entity, resp.status, text)
                return web.json_response({"error": f"Home Assistant antwortet {resp.status} – gibt es {entity}?"},
                                         status=502)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        log.warning("Home-Taste %s: %s", entity, err)
        return web.json_response({"error": f"Home Assistant nicht erreichbar: {err}"}, status=502)
    log.info("Home-Taste gedrückt (%s) von %s", entity, player_of(request))
    return no_cache(web.json_response({"ok": True}))


def save_recording(name, data):
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    (RECORD_DIR / name).write_bytes(data)
    old = sorted(RECORD_DIR.glob("game-remote-aufzeichnung-*.txt"))
    for path in old[:-RECORD_KEEP]:
        try:
            path.unlink()
        except OSError:
            pass
    return str(RECORD_DIR / name)


async def api_recording(request):
    """Speichert eine Aufzeichnung (Text) unter /share/game_remote/aufzeichnungen, die letzten 30 bleiben."""
    name = request.query.get("name", "")
    if not RECORD_NAME.match(name):
        return web.json_response({"error": "Ungültiger Name"}, status=400)
    data = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        data.extend(chunk)
        if len(data) > RECORD_MAX_BODY:
            return web.json_response({"error": "Aufzeichnung zu groß"}, status=413)
    try:
        path = await asyncio.get_running_loop().run_in_executor(None, save_recording, name, bytes(data))
    except OSError as err:
        log.warning("Aufzeichnung nicht gespeichert: %s", err)
        return web.json_response({"error": str(err)}, status=500)
    log.info("Aufzeichnung gespeichert: %s (%d KB)", path, len(data) // 1024)
    return no_cache(web.json_response({"path": path}))


async def ws_proxy(request):
    """Verbindet den Browser-WebSocket mit dem WebSocket-Server der Bridge."""
    options = request.app["options"]
    session = request.app["session"]
    client = web.WebSocketResponse(heartbeat=20, max_msg_size=WS_MAX_MESSAGE)
    await client.prepare(request)
    target = f"ws://{bridge_host(options)}:{options['pi_ws_port']}"
    stats = request.app["stats"]
    sid = None

    try:
        async with session.ws_connect(target, heartbeat=20, max_msg_size=WS_MAX_MESSAGE,
                                      timeout=aiohttp.ClientWSTimeout(ws_close=5)) as upstream:
            await client.send_json({"type": "proxy", "status": "connected"})
            # Jede offene Steuerungsseite mit erreichbarer Bridge zählt als Sitzung
            sid = stats.start(player_of(request))
            stats.save()
            request.app["publish_soon"]()

            async def browser_to_bridge():
                async for msg in client:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await upstream.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await upstream.send_bytes(msg.data)
                    else:
                        break

            async def bridge_to_browser():
                async for msg in upstream:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await client.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await client.send_bytes(msg.data)
                    else:
                        break

            tasks = {asyncio.create_task(browser_to_bridge()), asyncio.create_task(bridge_to_browser())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if task.exception() and not isinstance(task.exception(), (ConnectionResetError, asyncio.CancelledError)):
                    log.debug("WebSocket-Weiterleitung beendet: %s", task.exception())
            if not client.closed and upstream.closed:
                await client.send_json({"type": "proxy", "status": "bridge_closed"})
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
        log.warning("Bridge %s nicht erreichbar: %s", target, err)
        if not client.closed:
            await client.send_json({"type": "proxy", "status": "unreachable", "error": str(err) or err.__class__.__name__})
    finally:
        if sid is not None:
            stats.end(sid)
            stats.save()
            request.app["publish_soon"]()
        if not client.closed:
            await client.close()
    return client


async def frames_proxy(request):
    """Einzelbilder der Bridge (WebSocket /frames) an den Browser, Bestätigungen zurück."""
    options = request.app["options"]
    client = web.WebSocketResponse(heartbeat=20, compress=False, max_msg_size=WS_MAX_MESSAGE)
    await client.prepare(request)
    target = f"ws://{bridge_host(options)}:{options['pi_video_port']}/frames"
    try:
        async with request.app["session"].ws_connect(target, heartbeat=20, max_msg_size=FRAME_MAX_MESSAGE,
                                                     timeout=aiohttp.ClientWSTimeout(ws_close=5)) as upstream:
            async def pipe(source, dest):
                async for msg in source:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        await dest.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        await dest.send_str(msg.data)
                    else:
                        break

            tasks = {asyncio.create_task(pipe(client, upstream)), asyncio.create_task(pipe(upstream, client))}
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
        log.warning("Einzelbilder %s nicht erreichbar: %s", target, err)
    if not client.closed:
        await client.close()
    return client


async def video_proxy(request):
    """Leitet HTTP-Anfragen an den Video-Server der Bridge weiter (auch endlose MJPEG-Streams)."""
    path = request.match_info["path"]
    if not VIDEO_PATH.match(path):
        raise web.HTTPNotFound()
    options = request.app["options"]
    session = request.app["session"]
    target = f"http://{bridge_host(options)}:{options['pi_video_port']}/{path}"
    body = None
    if request.method in ("POST", "PUT"):
        if request.content_length and request.content_length > OFFER_MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=OFFER_MAX_BODY, actual_size=request.content_length)
        body = await request.read()
    headers = {}
    if request.content_type and body is not None:
        headers["Content-Type"] = request.headers.get("Content-Type", "application/octet-stream")
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=None if path.endswith(".mjpg") else 20)

    try:
        upstream = await session.request(request.method, target, params=request.query, data=body,
                                         headers=headers, timeout=timeout, allow_redirects=False)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
        log.warning("Video-Server %s nicht erreichbar: %s", target, err)
        return web.json_response({"error": "Raspberry Pi 5 Bridge nicht erreichbar", "detail": str(err)},
                                 status=502, headers={"Cache-Control": "no-store"})

    response = web.StreamResponse(status=upstream.status)
    for name in ("Content-Type", "Content-Length"):
        if name in upstream.headers:
            response.headers[name] = upstream.headers[name]
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Accel-Buffering"] = "no"
    try:
        await response.prepare(request)
        async for chunk in upstream.content.iter_any():
            await response.write(chunk)
        await response.write_eof()
    except (ConnectionResetError, aiohttp.ClientConnectionError, asyncio.CancelledError):
        pass
    finally:
        upstream.release()
    return response


# --------------------------------------------------------------------------- App
async def bridge_watch(app):
    """Eigene Verbindung zur Bridge, nur zum Mitlesen des Status (Konsole, Capture-Card)."""
    options = app["options"]
    stats = app["stats"]
    target = f"ws://{bridge_host(options)}:{options['pi_ws_port']}"
    while True:
        try:
            async with app["session"].ws_connect(target, heartbeat=30, max_msg_size=WS_MAX_MESSAGE,
                                                 timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws:
                if not stats.bridge["online"]:
                    log.info("Bridge %s erreichbar", target)
                stats.bridge["online"] = True
                app["publish_soon"]()
                # Diese Verbindung liest nur mit - sie soll den Bluetooth-Controller nicht wach halten
                await ws.send_str(json.dumps({"type": "system", "action": "control", "active": False}))
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        continue
                    if data.get("type") == "status":
                        changed = (data.get("switch", {}).get("state") != stats.bridge["switch"].get("state")
                                   or data.get("capture", {}).get("state") != stats.bridge["capture"].get("state"))
                        stats.bridge["switch"] = data.get("switch") or {}
                        stats.bridge["capture"] = data.get("capture") or {}
                        if changed:
                            app["publish_soon"]()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        if stats.bridge["online"]:
            log.warning("Bridge %s nicht mehr erreichbar", target)
            stats.bridge["online"] = False
            app["publish_soon"]()
        await asyncio.sleep(10)


def make_app(options, ingress_only=True, stats=None, hub=None):
    @web.middleware
    async def guard(request, handler):
        if ingress_only and request.remote != INGRESS_PEER:
            raise web.HTTPForbidden(text="Nur über Home Assistant Ingress erreichbar")
        response = await handler(request)
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"   # bei jedem Laden kurz beim Server nachfragen
        return response

    async def publish():
        if hub is not None:
            update_sensors(hub, app["stats"])
            await hub.publish()

    def publish_soon():
        task = asyncio.ensure_future(publish())
        app["tasks"].add(task)
        task.add_done_callback(app["tasks"].discard)

    async def ticker():
        while True:
            await asyncio.sleep(TICK_SECONDS)
            app["stats"].tick()
            app["stats"].save()
            try:
                await publish()
            except Exception as err:          # Sensoren dürfen die Steuerung nie stören
                log.warning("Sensoren aktualisieren: %s", err)

    async def on_startup(app):
        app["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))
        if hub is not None:
            await hub.start(app["session"], options["use_mqtt"])
        app["background"] = [asyncio.create_task(ticker()), asyncio.create_task(bridge_watch(app))]
        await publish()

    async def on_cleanup(app):
        for task in app["background"]:
            task.cancel()
        await asyncio.gather(*app["background"], return_exceptions=True)
        for sid in list(app["stats"].active):
            app["stats"].end(sid)
        app["stats"].save()
        if hub is not None:
            await hub.stop()
        await app["session"].close()

    app = web.Application(middlewares=[guard], client_max_size=OFFER_MAX_BODY)
    app["options"] = options
    app["stats"] = stats if stats is not None else Stats(STATS_PATH)
    app["tasks"] = set()
    app["publish_soon"] = publish_soon
    app["home"] = {"pressed": -HOME_GAP}
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/api/config", api_config)
    app.router.add_post("/api/recording", api_recording)
    app.router.add_post("/api/home_button", api_home_button)
    app.router.add_get("/ws", ws_proxy)
    app.router.add_get("/video/frames", frames_proxy)
    app.router.add_route("*", "/video/{path:.*}", video_proxy)
    app.router.add_static("/static/", STATIC_DIR)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    options = load_options()
    log.info("Ingress-Server auf Port %d, Bridge %s (WebSocket %d, Video %d), Stream %s",
             PORT, options["pi_host"], options["pi_ws_port"], options["pi_video_port"], options["stream_mode"])
    hub = SensorHub("game_remote", "Game Remote", "Game Remote (BolliSoft)", VERSION, SENSORS)
    app = make_app(options, ingress_only=os.environ.get("SWITCH2_NO_GUARD") != "1", hub=hub)
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
