"""Game Remote - Ingress-Server des Home-Assistant-Add-ons.

Aufgaben:
  - liefert die Oberfläche (static/) über Home-Assistant-Ingress aus
  - /api/config: Optionen fürs Frontend (stream_mode, default_touch_overlay, haptic_feedback)
  - /ws: WebSocket-Weiterleitung an die Raspberry Pi 5 Bridge (ws://pi_host:pi_ws_port)
  - /video/<pfad>: HTTP-Weiterleitung an den Video-Server der Bridge
    (http://pi_host:pi_video_port/<pfad>), z. B. /video/offer (WebRTC-Signaling) und
    /video/stream.mjpg (MJPEG)

Der Browser spricht dadurch nur mit Home Assistant - auch über HTTPS und Nabu Casa,
ohne CORS und ohne direkten Zugriff auf IP und Ports des Pi. Ausnahme: Bei WebRTC laufen
nur das Signaling über diesen Server, die Videodaten selbst gehen direkt (UDP) vom Pi
zum Browser. Ist der Pi nicht direkt erreichbar (unterwegs), schaltet das Frontend
automatisch auf MJPEG um, das vollständig über Ingress läuft.
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import aiohttp
from aiohttp import web

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
}
VIDEO_PATH = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
WS_MAX_MESSAGE = 256 * 1024
OFFER_MAX_BODY = 256 * 1024

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
    if options["stream_mode"] not in ("webrtc", "mjpeg"):
        options["stream_mode"] = "webrtc"
    options["default_touch_overlay"] = bool(options["default_touch_overlay"])
    options["haptic_feedback"] = bool(options["haptic_feedback"])
    return options


def bridge_host(options):
    host = options["pi_host"]
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- Handler
async def index(_request):
    return no_cache(web.FileResponse(STATIC_DIR / "index.html"))


async def api_config(request):
    options = request.app["options"]
    return no_cache(web.json_response({
        "stream_mode": options["stream_mode"],
        "default_touch_overlay": options["default_touch_overlay"],
        "haptic_feedback": options["haptic_feedback"],
    }))


async def ws_proxy(request):
    """Verbindet den Browser-WebSocket mit dem WebSocket-Server der Bridge."""
    options = request.app["options"]
    session = request.app["session"]
    client = web.WebSocketResponse(heartbeat=20, max_msg_size=WS_MAX_MESSAGE)
    await client.prepare(request)
    target = f"ws://{bridge_host(options)}:{options['pi_ws_port']}"

    try:
        async with session.ws_connect(target, heartbeat=20, max_msg_size=WS_MAX_MESSAGE,
                                      timeout=aiohttp.ClientWSTimeout(ws_close=5)) as upstream:
            await client.send_json({"type": "proxy", "status": "connected"})

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
def make_app(options, ingress_only=True):
    @web.middleware
    async def guard(request, handler):
        if ingress_only and request.remote != INGRESS_PEER:
            raise web.HTTPForbidden(text="Nur über Home Assistant Ingress erreichbar")
        return await handler(request)

    async def on_startup(app):
        app["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))

    async def on_cleanup(app):
        await app["session"].close()

    app = web.Application(middlewares=[guard], client_max_size=OFFER_MAX_BODY)
    app["options"] = options
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/api/config", api_config)
    app.router.add_get("/ws", ws_proxy)
    app.router.add_route("*", "/video/{path:.*}", video_proxy)
    app.router.add_static("/static/", STATIC_DIR)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    options = load_options()
    log.info("Ingress-Server auf Port %d, Bridge %s (WebSocket %d, Video %d), Stream %s",
             PORT, options["pi_host"], options["pi_ws_port"], options["pi_video_port"], options["stream_mode"])
    app = make_app(options, ingress_only=os.environ.get("SWITCH2_NO_GUARD") != "1")
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
