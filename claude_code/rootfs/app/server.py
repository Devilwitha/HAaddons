"""Claude Code Web-UI für Home Assistant (Ingress).

Startet `claude` im stream-json-Modus, leitet Ausgaben per WebSocket an den Browser
weiter, zeigt Erlaubnis-Abfragen als Buttons und übernimmt Login + Remote Control.

Bilder: Der Browser lädt sie per HTTP hoch (/api/upload, Ingress-WebSockets vertragen nur ~4 MB pro
Nachricht). Für Claude wird eine verkleinerte Fassung in /data/uploads abgelegt, das Original kommt nach
/share/claude/uploads, damit Claude es auch weiterverwenden kann (z. B. nach /config/www kopieren).
"""
import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from aiohttp import web, WSMsgType

log = logging.getLogger("claude-ui")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

CWD = os.environ.get("CC_CWD", "/config")
PORT = int(os.environ.get("CC_PORT", "8099"))
STATIC = Path(__file__).with_name("static")
OPTIONS_FILE = Path(os.environ.get("CC_OPTIONS", "/data/options.json"))
CLAUDE = os.environ.get("CC_CLAUDE") or shutil.which("claude") or "claude"
EXTRA_ARGS = json.loads(os.environ.get("CC_EXTRA_ARGS", "[]"))  # nur für lokale Tests
UPLOAD_DIR = Path(os.environ.get("CC_UPLOADS", "/data/uploads"))            # Fassung für Claude
ORIGINAL_DIR = Path(os.environ.get("CC_ORIGINALS", "/share/claude/uploads"))  # Originale
MAX_UPLOAD = 15 * 1024 * 1024       # Ingress nimmt höchstens 16 MB pro Anfrage an
MAX_EDGE = 1568                     # längste Kante, mehr nutzt Claude nicht
MAX_IMAGES = 10
IMAGE_ID = re.compile(r"[0-9a-f]{32}")
ATTACH_NOTE = re.compile(r"\n*\[Angehängte Bilder \(Originale\): [^\]]*\]\s*$")
VERSION_TAG = f"{os.environ.get('BUILD_VERSION', 'dev')}-{int(time.time())}"


def options() -> dict:
    try:
        return json.loads(OPTIONS_FILE.read_text())
    except Exception:
        return {}


# UI-Einstellungen (z. B. Automatik-Modus), persistent in /data
UI_SETTINGS_FILE = Path(os.environ.get("CC_UI_SETTINGS", "/data/ui_settings.json"))


def ui_settings() -> dict:
    try:
        return json.loads(UI_SETTINGS_FILE.read_text())
    except Exception:
        return {}


def auto_mode() -> bool:
    return bool(ui_settings().get("auto"))


def set_auto_mode(on: bool):
    s = ui_settings()
    s["auto"] = on
    try:
        UI_SETTINGS_FILE.write_text(json.dumps(s))
    except OSError:
        log.exception("Einstellungen konnten nicht gespeichert werden")


# Abfragen, die auch im Automatik-Modus den Benutzer brauchen
NEEDS_USER = {"AskUserQuestion"}


def projects_dir() -> Path:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return base / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", CWD)


async def run_cmd(*args, timeout=30) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*args, cwd=CWD, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return -1, "timeout"
    return p.returncode, out.decode(errors="replace")


# --------------------------------------------------------------------------- Clients
clients: set[web.WebSocketResponse] = set()


async def broadcast(msg: dict):
    data = json.dumps(msg)
    for ws in list(clients):
        try:
            await ws.send_str(data)
        except Exception:
            clients.discard(ws)


# --------------------------------------------------------------------------- Claude-Prozess
class Runner:
    """Eine laufende Claude-Unterhaltung (ein Prozess, mehrere Nachrichten)."""

    def __init__(self, resume: str | None):
        self.session_id = resume
        self.proc: asyncio.subprocess.Process | None = None
        self.busy = False
        self.pending: dict[str, dict] = {}   # offene Erlaubnis-Abfragen
        self._n = 0

    async def start(self):
        opts = options()
        args = [CLAUDE, "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
                "--include-partial-messages", "--permission-prompt-tool", "stdio",
                "--permission-mode", opts.get("permission_mode") or "default", *EXTRA_ARGS]
        if opts.get("model"):
            args += ["--model", opts["model"]]
        if self.session_id:
            args += ["--resume", self.session_id]
        log.info("Starte Claude (resume=%s)", self.session_id)
        self.proc = await asyncio.create_subprocess_exec(
            *args, cwd=CWD, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024)
        asyncio.create_task(self._read())
        asyncio.create_task(self._stderr())
        await self._write({"type": "control_request", "request_id": self._rid(),
                           "request": {"subtype": "initialize", "hooks": None}})

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def _rid(self) -> str:
        self._n += 1
        return f"ui-{self._n}"

    async def _write(self, obj: dict):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def send(self, text: str, images: list[str] | None = None):
        content: str | list = text
        if images:
            blocks, originals = [], []
            for image_id in images:
                info = uploaded_image(image_id)
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": info["media_type"],
                    "data": base64.b64encode(info["path"].read_bytes()).decode()}})
                if info.get("original"):
                    originals.append(info["original"])
            note = f"\n\n[Angehängte Bilder (Originale): {', '.join(originals)}]" if originals else ""
            blocks.append({"type": "text", "text": (text or "Siehe Bild.") + note})
            content = blocks
        self.busy = True
        await broadcast({"t": "busy", "busy": True})
        await self._write({"type": "user", "message": {"role": "user", "content": content},
                           "parent_tool_use_id": None, "session_id": self.session_id or "default"})

    async def approve_all_pending(self):
        for rid, req in list(self.pending.items()):
            if req.get("tool_name") not in NEEDS_USER:
                await self.answer(rid, True)

    async def answer(self, rid: str, allow: bool, always: bool = False, answers: dict | None = None,
                     quiet: bool = False):
        req = self.pending.pop(rid, None)
        if not req:
            return
        if allow:
            inp = dict(req.get("input") or {})
            if answers is not None:
                inp["answers"] = answers
            resp = {"behavior": "allow", "updatedInput": inp}
            if always and req.get("permission_suggestions"):
                resp["updatedPermissions"] = req["permission_suggestions"]
        else:
            resp = {"behavior": "deny", "message": "Der Benutzer hat das abgelehnt."}
        await self._write({"type": "control_response",
                           "response": {"subtype": "success", "request_id": rid, "response": resp}})
        if not quiet:
            await broadcast({"t": "perm_done", "id": rid, "allow": allow})

    async def interrupt(self):
        if self.alive() and self.busy:
            for rid in list(self.pending):
                await self.answer(rid, False)
            await self._write({"type": "control_request", "request_id": self._rid(),
                               "request": {"subtype": "interrupt"}})

    async def stop(self):
        if self.alive():
            self.proc.kill()
            await self.proc.wait()

    async def _stderr(self):
        async for line in self.proc.stderr:
            s = line.decode(errors="replace").rstrip()
            if s:
                log.warning("claude: %s", s)

    async def _read(self):
        async for raw in self.proc.stdout:
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            t = o.get("type")
            if t == "stream_event":
                ev = o.get("event", {})
                d = ev.get("delta", {})
                if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                    await broadcast({"t": "delta", "text": d.get("text", "")})
                elif ev.get("type") == "content_block_start" and ev.get("content_block", {}).get("type") == "thinking":
                    await broadcast({"t": "thinking"})
            elif t == "system" and o.get("subtype") == "init":
                new = o.get("session_id")
                if new != self.session_id:
                    self.session_id = new
                    await broadcast({"t": "session", "id": new})
            elif t in ("assistant", "user"):
                if o.get("parent_tool_use_id"):
                    continue  # Unteragenten nicht einzeln anzeigen
                await broadcast({"t": t, "message": o.get("message", {})})
            elif t == "control_request":
                req = o.get("request", {})
                if req.get("subtype") == "can_use_tool":
                    self.pending[o["request_id"]] = req
                    if auto_mode() and req.get("tool_name") not in NEEDS_USER:
                        await self.answer(o["request_id"], True, quiet=True)
                        continue
                    await broadcast({"t": "perm", "id": o["request_id"], "tool": req.get("tool_name"),
                                     "input": req.get("input"), "description": req.get("description"),
                                     "can_always": bool(req.get("permission_suggestions"))})
                else:
                    await self._write({"type": "control_response", "response": {
                        "subtype": "error", "request_id": o.get("request_id"), "error": "not supported"}})
            elif t == "result":
                self.busy = False
                await broadcast({"t": "result", "ok": o.get("subtype") == "success" and not o.get("is_error"),
                                 "text": o.get("result") if o.get("is_error") else None})
                await broadcast({"t": "busy", "busy": False})
        # Prozess beendet
        code = await self.proc.wait()
        log.info("Claude beendet (exit %s)", code)
        if self.busy:
            self.busy = False
            await broadcast({"t": "result", "ok": False, "text": f"Claude wurde unerwartet beendet (Code {code})."})
            await broadcast({"t": "busy", "busy": False})
        self.pending.clear()


runner: Runner | None = None
runner_lock = asyncio.Lock()


async def ensure_runner(session_id: str | None) -> Runner:
    global runner
    async with runner_lock:
        if runner and runner.alive() and (session_id is None or runner.session_id == session_id):
            return runner
        if runner:
            await runner.stop()
        runner = Runner(session_id)
        await runner.start()
        return runner


async def reset_runner(session_id: str | None):
    global runner
    async with runner_lock:
        if runner:
            await runner.stop()
        runner = None
    await broadcast({"t": "session", "id": session_id})
    await broadcast({"t": "busy", "busy": False})


# --------------------------------------------------------------------------- Bilder
def _image_meta(image_id: str) -> Path:
    return UPLOAD_DIR / f"{image_id}.json"


def uploaded_image(image_id: str) -> dict:
    if not IMAGE_ID.fullmatch(image_id or ""):
        raise ValueError("Ungültiges Bild")
    try:
        meta = json.loads(_image_meta(image_id).read_text())
    except (OSError, ValueError):
        raise ValueError("Bild nicht mehr vorhanden – bitte neu anhängen") from None
    meta["path"] = UPLOAD_DIR / meta["file"]
    if not meta["path"].is_file():
        raise ValueError("Bild nicht mehr vorhanden – bitte neu anhängen")
    return meta


def _safe_name(name: str) -> str:
    stem = Path(name).stem
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        stem = stem.replace(a, b)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:40]
    return stem or "bild"


def store_image(data: bytes, filename: str) -> dict:
    """Bild prüfen, für Claude verkleinern und das Original ablegen."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as err:
        raise ValueError("Kein lesbares Bild (JPG, PNG, WebP oder GIF)") from err
    fmt = (img.format or "").upper()
    img = ImageOps.exif_transpose(img)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    if has_alpha:
        img.convert("RGBA").save(out, "PNG", optimize=True)
        media_type, ext = "image/png", "png"
    if not has_alpha or out.tell() > 3_500_000:
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=85)
        media_type, ext = "image/jpeg", "jpg"
    image_id = uuid.uuid4().hex
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / f"{image_id}.{ext}").write_bytes(out.getvalue())
    original = ""
    try:
        ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
        orig_ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(fmt, ext)
        base = f"{datetime.now():%Y%m%d-%H%M%S}-{_safe_name(filename)}"
        target = ORIGINAL_DIR / f"{base}.{orig_ext}"
        n = 2
        while target.exists():
            target = ORIGINAL_DIR / f"{base}-{n}.{orig_ext}"
            n += 1
        target.write_bytes(data)
        original = target.as_posix()
    except OSError:
        log.exception("Original nicht gespeichert")
    meta = {"file": f"{image_id}.{ext}", "media_type": media_type, "original": original,
            "name": filename, "width": img.width, "height": img.height}
    _image_meta(image_id).write_text(json.dumps(meta))
    return {"id": image_id, **meta}


def cleanup_uploads():
    """Fassungen für Claude nach 2 Tagen, Originale nach 30 Tagen löschen."""
    now = time.time()
    for folder, days in ((UPLOAD_DIR, 2), (ORIGINAL_DIR, 30)):
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            try:
                if f.is_file() and now - f.stat().st_mtime > days * 86400:
                    f.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- Verlauf
def _user_parts(content) -> tuple[str | None, list[int]]:
    """Text (ohne Hinweis auf die Originale) und Positionen der Bilder einer Benutzer-Nachricht.

    Text None = keine eigene Nachricht (z. B. Werkzeug-Ergebnisse)."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return None, []
    if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
        return None, []
    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    images = [i for i, c in enumerate(content) if isinstance(c, dict) and c.get("type") == "image"]
    if not texts and not images:
        return None, []
    text = ATTACH_NOTE.sub("", "\n".join(texts))
    if images and text == "Siehe Bild.":
        text = ""
    return text, images


def _user_text(content) -> str | None:
    text, images = _user_parts(content)
    if text is None:
        return None
    return text or ("Bild" if images else text)


def read_history(session_id: str) -> list[dict]:
    f = projects_dir() / f"{session_id}.jsonl"
    out = []
    if not f.is_file():
        return out
    for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines()):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("isSidechain") or o.get("isMeta"):
            continue
        t, msg = o.get("type"), o.get("message") or {}
        if t == "user":
            txt, images = _user_parts(msg.get("content"))
            if txt is not None:
                if txt.lstrip().startswith("<") or txt.startswith("[Request interrupted"):
                    continue
                out.append({"t": "user_text", "text": txt,
                            "images": [f"api/sessions/{session_id}/image/{n}/{i}" for i in images]})
            else:
                out.append({"t": "user", "message": msg})
        elif t == "assistant":
            out.append({"t": "assistant", "message": msg})
    return out


def list_sessions() -> list[dict]:
    d = projects_dir()
    if not d.is_dir():
        return []
    res = []
    for f in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
        title = None
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("type") == "summary" and o.get("summary"):
                        title = o["summary"]
                        break
                    if o.get("type") == "user" and not o.get("isMeta") and not o.get("isSidechain"):
                        txt = _user_text((o.get("message") or {}).get("content"))
                        if txt and not txt.lstrip().startswith("<"):
                            title = txt.strip().splitlines()[0][:80]
                            break
        except OSError:
            continue
        if title:
            res.append({"id": f.stem, "title": title, "mtime": f.stat().st_mtime})
    return res


# --------------------------------------------------------------------------- Login
class Login:
    proc: asyncio.subprocess.Process | None = None


async def auth_status() -> dict:
    code, out = await run_cmd(CLAUDE, "auth", "status", "--json", timeout=20)
    try:
        return json.loads(out[out.index("{"):])
    except ValueError:
        return {"loggedIn": False}


async def auth_login(_req):
    if Login.proc and Login.proc.returncode is None:
        Login.proc.kill()
    env = dict(os.environ, BROWSER="true")
    Login.proc = await asyncio.create_subprocess_exec(
        CLAUDE, "auth", "login", "--claudeai", cwd=CWD, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    buf = ""
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            chunk = await asyncio.wait_for(Login.proc.stdout.read(4096), 5)
        except asyncio.TimeoutError:
            continue
        if not chunk:
            break
        buf += chunk.decode(errors="replace")
        m = re.search(r"https://\S+oauth\S+", buf)
        if m:
            return web.json_response({"url": m.group(0)})
    return web.json_response({"error": buf.strip() or "Kein Anmelde-Link erhalten"}, status=500)


async def auth_code(req):
    code = (await req.json()).get("code", "").strip()
    p = Login.proc
    if not p or p.returncode is not None:
        return web.json_response({"error": "Anmeldung abgelaufen – bitte neu starten."}, status=400)
    p.stdin.write((code + "\n").encode())
    await p.stdin.drain()
    out = ""
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            chunk = await asyncio.wait_for(p.stdout.read(4096), 2)
        except asyncio.TimeoutError:
            continue
        if not chunk:  # Prozess beendet -> erfolgreich oder endgültig gescheitert
            break
        out += chunk.decode(errors="replace")
        if re.search(r"invalid|error|failed", out, re.I):
            # CLI wartet weiter auf einen Code: Prozess offen lassen, erneuter Versuch möglich
            return web.json_response({"error": "Der Code ist ungültig. Bitte den kompletten Code kopieren.",
                                      "retry": True}, status=400)
    st = await auth_status()
    if not st.get("loggedIn"):
        if p.returncode is None:
            p.kill()
        return web.json_response({"error": "Anmeldung fehlgeschlagen. Bitte noch einmal versuchen."}, status=400)
    RemoteControl.kick()
    return web.json_response(st)


async def auth_logout(_req):
    await reset_runner(None)
    await RemoteControl.stop()
    await run_cmd(CLAUDE, "auth", "logout")
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------- Remote Control
class RemoteControl:
    proc: asyncio.subprocess.Process | None = None
    state = "aus"
    last = ""
    _wake = asyncio.Event()

    @classmethod
    def kick(cls):
        cls._wake.set()

    @classmethod
    async def stop(cls):
        if cls.proc and cls.proc.returncode is None:
            cls.proc.kill()

    bypass_failed = False  # bypassPermissions als root abgelehnt -> acceptEdits als Ersatz

    @classmethod
    async def restart(cls):
        """Mit neuen Einstellungen (z. B. Automatik) sofort neu starten."""
        cls.bypass_failed = False
        await cls.stop()
        cls.kick()

    @classmethod
    async def loop(cls):
        while True:
            cls._wake.clear()
            opts = options()
            if not opts.get("remote_control", True):
                cls.state = "aus"
            elif not (await auth_status()).get("loggedIn"):
                cls.state = "wartet auf Anmeldung"
            else:
                cls.state = "verbindet"
                env = dict(os.environ)
                mode = opts.get("permission_mode") or "default"
                use_bypass = auto_mode() and not cls.bypass_failed
                if use_bypass:
                    mode = "bypassPermissions"
                    env["IS_SANDBOX"] = "1"  # Container läuft als root
                elif auto_mode():
                    mode = "acceptEdits"
                args = [CLAUDE, "remote-control", "--name", opts.get("session_name") or "HAOS",
                        "--permission-mode", mode]
                log.info("remote-control startet (Modus %s)", mode)
                started = time.time()
                cls.proc = await asyncio.create_subprocess_exec(
                    *args, cwd=CWD, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT)
                proc = cls.proc

                async def mark_active():
                    await asyncio.sleep(5)
                    if proc.returncode is None:
                        cls.state = "aktiv"
                activate = asyncio.create_task(mark_active())
                async for line in proc.stdout:
                    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line.decode(errors="replace")).strip()
                    if s:
                        cls.last = s
                        log.info("remote-control: %s", s)
                code = await proc.wait()
                activate.cancel()
                log.info("remote-control beendet (exit %s)", code)
                if use_bypass and code not in (0, None, -9) and time.time() - started < 15:
                    log.warning("bypassPermissions nicht möglich – nutze acceptEdits")
                    cls.bypass_failed = True
                    cls.kick()
                cls.state = "neu verbinden"
            try:
                await asyncio.wait_for(cls._wake.wait(), 30)
            except asyncio.TimeoutError:
                pass


# --------------------------------------------------------------------------- HTTP
async def index(_req):
    # ?v= an Skripte und Stylesheets: die HA-App hält statische Dateien sonst stundenlang im Cache
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = re.sub(r'(static/[\w.-]+\.(?:js|css))"', lambda m: f'{m.group(1)}?v={VERSION_TAG}"', html)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


@web.middleware
async def static_no_cache(request, handler):
    response = await handler(request)
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


async def api_upload(req):
    data = await req.read()
    if not data:
        raise web.HTTPBadRequest(text="Leere Datei")
    if len(data) > MAX_UPLOAD:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=len(data),
                                            text="Bild größer als 15 MB")
    try:
        name = unquote(req.headers.get("X-Filename") or "bild")
        info = await asyncio.to_thread(store_image, data, name)
    except ValueError as err:
        raise web.HTTPBadRequest(text=str(err))
    log.info("Bild angehängt: %s (%sx%s)", info["name"], info["width"], info["height"])
    return web.json_response({"id": info["id"], "url": f"api/upload/{info['id']}", "name": info["name"],
                              "original": info["original"]})


async def api_upload_get(req):
    try:
        info = uploaded_image(req.match_info["id"])
    except ValueError:
        raise web.HTTPNotFound()
    return web.FileResponse(info["path"], headers={"Content-Type": info["media_type"],
                                                   "Cache-Control": "private, max-age=86400"})


async def api_session_image(req):
    """Bild aus dem gespeicherten Verlauf (Zeile der JSONL-Datei, Position in der Nachricht)."""
    sid = req.match_info["sid"]
    if not re.fullmatch(r"[0-9a-f-]{36}", sid):
        raise web.HTTPBadRequest()
    line_no, idx = int(req.match_info["line"]), int(req.match_info["idx"])
    f = projects_dir() / f"{sid}.jsonl"
    try:
        block = None
        with f.open(encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n == line_no:
                    block = json.loads(line)["message"]["content"][idx]
                    break
        if not block:
            raise web.HTTPNotFound()
        src = block["source"]
        data = base64.b64decode(src["data"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type=src.get("media_type") or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


async def api_status(_req):
    st = await auth_status()
    opts = options()
    return web.json_response({
        "auth": {k: st.get(k) for k in ("loggedIn", "email", "subscriptionType", "authMethod")},
        "remote": {"enabled": opts.get("remote_control", True), "name": opts.get("session_name") or "HAOS",
                   "state": RemoteControl.state},
        "session": runner.session_id if runner else None,
        "busy": bool(runner and runner.busy),
        "auto": auto_mode(),
    })


async def apply_auto(on: bool):
    set_auto_mode(on)
    log.info("Automatik-Modus %s", "an" if on else "aus")
    await broadcast({"t": "auto", "on": on})
    if on and runner:
        await runner.approve_all_pending()
    asyncio.create_task(RemoteControl.restart())


async def api_auto(req):
    on = bool((await req.json()).get("on"))
    await apply_auto(on)
    return web.json_response({"auto": auto_mode()})


async def api_sessions(_req):
    return web.json_response(list_sessions())


async def api_session(req):
    sid = req.match_info["sid"]
    if not re.fullmatch(r"[0-9a-f-]{36}", sid):
        raise web.HTTPBadRequest()
    return web.json_response(read_history(sid))


async def ws_handler(req):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(req)
    clients.add(ws)
    await ws.send_json({"t": "auto", "on": auto_mode()})
    if runner:
        await ws.send_json({"t": "busy", "busy": runner.busy})
        for rid, r in runner.pending.items():
            await ws.send_json({"t": "perm", "id": rid, "tool": r.get("tool_name"), "input": r.get("input"),
                                "description": r.get("description"),
                                "can_always": bool(r.get("permission_suggestions"))})
    try:
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                continue
            d = json.loads(m.data)
            t = d.get("t")
            try:
                if t == "send":
                    images = [i for i in (d.get("images") or []) if isinstance(i, str)][:MAX_IMAGES]
                    for image_id in images:
                        uploaded_image(image_id)  # vor dem Start prüfen
                    r = await ensure_runner(d.get("session"))
                    await r.send(d.get("text", ""), images)
                elif t == "perm" and runner:
                    await runner.answer(d["id"], bool(d.get("allow")), bool(d.get("always")), d.get("answers"))
                elif t == "auto":
                    await apply_auto(bool(d.get("on")))
                elif t == "stop" and runner:
                    await runner.interrupt()
                elif t == "new":
                    await reset_runner(None)
                elif t == "open":
                    if not (runner and runner.busy and runner.session_id == d.get("session")):
                        await reset_runner(d.get("session"))
            except Exception as e:  # noqa: BLE001
                log.exception("ws")
                await ws.send_json({"t": "result", "ok": False, "text": f"Fehler: {e}"})
    finally:
        clients.discard(ws)
    return ws


async def on_startup(app):
    await asyncio.to_thread(cleanup_uploads)
    app["rc"] = asyncio.create_task(RemoteControl.loop())


def main():
    app = web.Application(middlewares=[static_no_cache], client_max_size=MAX_UPLOAD + 1024 * 1024)
    app.add_routes([
        web.get("/", index),
        web.post("/api/upload", api_upload),
        web.get("/api/upload/{id}", api_upload_get),
        web.get(r"/api/sessions/{sid}/image/{line:\d+}/{idx:\d+}", api_session_image),
        web.get("/ws", ws_handler),
        web.get("/api/status", api_status),
        web.get("/api/sessions", api_sessions),
        web.post("/api/auto", api_auto),
        web.get("/api/sessions/{sid}", api_session),
        web.post("/api/auth/login", auth_login),
        web.post("/api/auth/code", auth_code),
        web.post("/api/auth/logout", auth_logout),
        web.static("/static", STATIC),
    ])
    app.on_startup.append(on_startup)
    log.info("Claude-UI auf Port %s, Arbeitsordner %s", PORT, CWD)
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
