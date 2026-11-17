"""BolliSoft Browser: Chromium im Add-on, bedient über die HA-Seitenleiste (Ingress)."""
import asyncio
import json
import logging
import os
import re
import shutil
import signal
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp
from aiohttp import web

from browser import Browser, Viewer, normalize_url, read_json, safe_filename, write_json
from hass import Devices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("server")

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DATA_DIR = Path(os.environ.get("BROWSER_DATA", "/data"))
OPTIONS_FILE = Path(os.environ.get("BROWSER_OPTIONS", DATA_DIR / "options.json"))
DOWNLOAD_DIR = Path(os.environ.get("BROWSER_DOWNLOADS", "/share/browser/downloads"))
UPLOAD_DIR = DATA_DIR / "uploads"
PORT = int(os.environ.get("BROWSER_PORT", "8099"))
INGRESS_PEER = "172.30.32.2"
VERSION = os.environ.get("BROWSER_VERSION", "dev")
ASSET_TAG = re.sub(r"[^\w.-]", "", VERSION) or "dev"
ASSET_RE = re.compile(r'(static/[\w.-]+\.(?:js|css))"')
UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
CHUNK = 8 * 1024 * 1024
MAX_UPLOAD = 2 * 1024 * 1024 * 1024


def load_options():
    opts = read_json(OPTIONS_FILE, {})
    return {
        "start_url": str(opts.get("start_url") or "").strip(),
        "ignore_certificate_errors": bool(opts.get("ignore_certificate_errors", True)),
        "idle_minutes": int(opts.get("idle_minutes", 30) or 0),
    }


def find_chromium():
    custom = os.environ.get("BROWSER_CHROMIUM")
    if custom:
        return custom
    for name in ("chromium", "chromium-browser", "google-chrome", "msedge"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("Chromium nicht gefunden")


def browser_user():
    if os.name != "posix" or os.geteuid() != 0:
        return None
    try:
        import pwd
        pwd.getpwnam("browser")
        return "browser"
    except KeyError:
        return None


class Bookmarks:
    def __init__(self, path):
        self.path = path
        self.items = read_json(path, [])

    def save(self):
        write_json(self.path, self.items)

    def apply(self, body):
        action = body.get("action")
        url = str(body.get("url") or "").strip()
        if action == "add":
            url, err = normalize_url(url)
            if err:
                raise web.HTTPBadRequest(text=err)
            title = str(body.get("title") or "").strip()[:80] or urlsplit(url).netloc or url
            self.items = [b for b in self.items if b["url"] != url]
            self.items.append({"url": url, "title": title})
        elif action == "remove":
            self.items = [b for b in self.items if b["url"] != url]
        elif action == "rename":
            for item in self.items:
                if item["url"] == url:
                    item["title"] = str(body.get("title") or "").strip()[:80] or item["title"]
        elif action == "move":
            index = next((i for i, b in enumerate(self.items) if b["url"] == url), None)
            delta = -1 if body.get("dir") == "up" else 1
            if index is not None and 0 <= index + delta < len(self.items):
                self.items[index], self.items[index + delta] = self.items[index + delta], self.items[index]
        else:
            raise web.HTTPBadRequest(text="Unbekannte Aktion")
        self.save()
        return self.items


class Favicons:
    """favicon.ico der Seiten (für Lesezeichen und Tabs), eine Stunde im Speicher."""

    def __init__(self, session):
        self.session = session
        self.cache = {}

    async def get(self, url):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return None
        origin = f"{parts.scheme}://{parts.netloc}"
        hit = self.cache.get(origin)
        if hit and time.monotonic() - hit[0] < 3600:
            return hit[1]
        icon = None
        try:
            async with self.session.get(origin + "/favicon.ico", ssl=False, allow_redirects=True,
                                        timeout=aiohttp.ClientTimeout(total=3)) as resp:
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()
                if resp.status == 200 and (ctype.startswith("image/") or ctype in ("", "application/octet-stream")):
                    data = await resp.content.read(256 * 1024)
                    if data and (ctype == "image/svg+xml" or not data.lstrip().startswith(b"<")):
                        icon = (data, ctype if ctype.startswith("image/") else "image/x-icon")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            icon = None
        self.cache[origin] = (time.monotonic(), icon)
        return icon


def cleanup_uploads():
    """Hochgeladene Dateien für Formulare nach einem Tag löschen."""
    if not UPLOAD_DIR.is_dir():
        return
    limit = time.time() - 86400
    for folder in UPLOAD_DIR.iterdir():
        try:
            if folder.stat().st_mtime < limit:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def make_app(browser, devices, bookmarks, favicons, options, ingress_only=True):
    index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    index_html = ASSET_RE.sub(lambda m: f'{m.group(1)}?v={ASSET_TAG}"', index_html)

    @web.middleware
    async def guard(request, handler):
        if ingress_only and request.remote != INGRESS_PEER:
            raise web.HTTPForbidden(text="Nur über Home Assistant erreichbar")
        response = await handler(request)
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def index(_request):
        return web.Response(text=index_html, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=1024 * 1024)
        await ws.prepare(request)
        user = request.headers.get("X-Remote-User-Display-Name") or request.headers.get("X-Remote-User-Name") or ""
        viewer = Viewer(ws, user)
        browser.add_viewer(viewer)
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                try:
                    await browser.handle(viewer, data)
                except Exception as err:  # noqa: BLE001 – Fehler einer Eingabe melden, Verbindung behalten
                    log.warning("Befehl %s: %s", data.get("t"), err)
                    viewer.send({"t": "toast", "text": f"Fehler: {err}"})
        finally:
            browser.remove_viewer(viewer)
        return ws

    async def start_page(_request):
        items, error = await devices.list()
        return web.json_response({
            "bookmarks": bookmarks.items, "recent": browser.recent[:12], "devices": items, "devices_error": error,
            "start_url": options["start_url"], "version": VERSION})

    async def bookmark_action(request):
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Ungültige Anfrage")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Ungültige Anfrage")
        return web.json_response({"bookmarks": bookmarks.apply(body)})

    async def recent_clear(_request):
        browser.recent = []
        write_json(DATA_DIR / "recent.json", [])
        return web.json_response({"ok": True})

    async def favicon(request):
        icon = await favicons.get(request.query.get("url", ""))
        if icon is None:
            raise web.HTTPNotFound()
        return web.Response(body=icon[0], content_type=icon[1], headers={"Cache-Control": "max-age=86400"})

    async def upload(request):
        """Datei für ein Formular der Seite, in Teilen zu höchstens 8 MB (Ingress nimmt nur 16 MB je Anfrage)."""
        upload_id = request.query.get("id", "")
        if not UPLOAD_ID.fullmatch(upload_id):
            raise web.HTTPBadRequest(text="Ungültige Kennung")
        name = safe_filename(unquote(request.query.get("name", "")), "datei")
        try:
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            raise web.HTTPBadRequest(text="Ungültiger Versatz")
        final = request.query.get("final") == "1"
        folder = UPLOAD_DIR / upload_id
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / (name + ".part")
        current = part.stat().st_size if part.exists() else 0
        if offset == 0:
            current = 0
        elif offset != current:
            raise web.HTTPConflict(text=f"Erwartet Versatz {current}")
        data = await request.read()
        if len(data) > CHUNK + 1024 or current + len(data) > MAX_UPLOAD:
            raise web.HTTPRequestEntityTooLarge(max_size=CHUNK, actual_size=len(data))
        with part.open("r+b" if offset else "wb") as fh:
            fh.seek(offset)
            fh.write(data)
            fh.truncate()
        size = current + len(data)
        if final:
            os.replace(part, folder / name)
            os.chmod(folder / name, 0o644)
            os.chmod(folder, 0o755)
        return web.json_response({"size": size})

    def download_list():
        items = []
        if DOWNLOAD_DIR.is_dir():
            for path in DOWNLOAD_DIR.iterdir():
                if path.is_file():
                    stat = path.stat()
                    items.append({"name": path.name, "size": stat.st_size, "time": int(stat.st_mtime)})
        items.sort(key=lambda d: d["time"], reverse=True)
        return items

    def download_path(request):
        name = request.match_info["name"]
        path = DOWNLOAD_DIR / name
        if "/" in name or "\\" in name or name.startswith(".") or not path.is_file():
            raise web.HTTPNotFound()
        return path

    async def downloads(_request):
        return web.json_response({"downloads": download_list(), "folder": str(DOWNLOAD_DIR)})

    async def download_get(request):
        path = download_path(request)
        disposition = "attachment; filename*=UTF-8''" + aiohttp.helpers.quote(path.name, safe="")
        return web.FileResponse(path, headers={"Content-Disposition": disposition})

    async def download_delete(request):
        download_path(request).unlink()
        return web.json_response({"downloads": download_list()})

    app = web.Application(middlewares=[guard], client_max_size=CHUNK + 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/start", start_page)
    app.router.add_post("/api/bookmarks", bookmark_action)
    app.router.add_post("/api/recent/clear", recent_clear)
    app.router.add_get("/api/favicon", favicon)
    app.router.add_post("/api/upload", upload)
    app.router.add_get("/api/downloads", downloads)
    app.router.add_get("/api/downloads/{name}", download_get)
    app.router.add_delete("/api/downloads/{name}", download_delete)
    app.router.add_static("/static/", WEB_DIR / "static")
    return app


async def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    options = load_options()
    cleanup_uploads()
    async with aiohttp.ClientSession() as session:
        browser = Browser(options, DATA_DIR, DOWNLOAD_DIR, UPLOAD_DIR, session, find_chromium(), browser_user())
        devices = Devices(session)
        bookmarks = Bookmarks(DATA_DIR / "bookmarks.json")
        favicons = Favicons(session)
        app = make_app(browser, devices, bookmarks, favicons, options,
                       ingress_only=os.environ.get("BROWSER_NO_GUARD") != "1")
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        log.info("BolliSoft Browser %s auf Port %d (Chromium: %s, Downloads: %s)", VERSION, PORT,
                 browser.binary, DOWNLOAD_DIR)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await stop.wait()
        finally:
            await browser.stop("Add-on beendet")
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
