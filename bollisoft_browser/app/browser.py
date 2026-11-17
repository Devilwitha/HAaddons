"""Chromium im Add-on: Prozess, DevTools-Protokoll, Tabs, Bildübertragung, Eingaben, Dialoge, Downloads."""
import asyncio
import base64
import collections
import json
import logging
import os
import re
import shutil
import struct
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import aiohttp

log = logging.getLogger("browser")

BLANK = "about:blank"
SEARCH = "https://duckduckgo.com/?q="
BLOCKED_SCHEMES = {"file", "chrome", "chrome-untrusted", "devtools", "edge", "javascript", "filesystem"}
SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
HOST_RE = re.compile(r"^(\[[0-9a-fA-F:]+\]|[\w-]+(\.[\w-]+)+|localhost|\d{1,3}(\.\d{1,3}){3})(:\d{1,5})?([/?#].*)?$")
MAX_FRAME_EDGE = 2560
ACCEPT_LANGUAGE = "de-DE,de;q=0.9,en;q=0.8"

# Element unter dem Finger: <select> öffnet in Chromium ohne Fenster ein eigenes Popup, das im Bild fehlt –
# darum zeigt die Oberfläche die Auswahl selbst. Geht auch in Iframes derselben Herkunft und Shadow DOM.
PICK_JS = """(function (x, y) {
  let doc = document, el = null, ox = 0, oy = 0;
  for (let i = 0; i < 8; i++) {
    el = doc.elementFromPoint(x - ox, y - oy);
    while (el && el.shadowRoot) {
      const inner = el.shadowRoot.elementFromPoint(x - ox, y - oy);
      if (!inner || inner === el) break;
      el = inner;
    }
    if (el && (el.tagName === 'IFRAME' || el.tagName === 'FRAME')) {
      let d = null;
      try { d = el.contentDocument; } catch (e) { d = null; }
      if (!d) break;
      const r = el.getBoundingClientRect();
      ox += r.left + el.clientLeft; oy += r.top + el.clientTop;
      doc = d;
      continue;
    }
    break;
  }
  if (el && el.tagName === 'OPTION') el = el.closest('select');
  if (!el || el.tagName !== 'SELECT' || el.disabled || el.multiple || el.size > 1) return null;
  window.__bollisoftSelect = el;
  const options = [];
  for (const o of el.options) {
    const g = o.parentElement && o.parentElement.tagName === 'OPTGROUP' ? o.parentElement.label : '';
    options.push({ label: (o.label || o.text || '').trim(), disabled: o.disabled, group: g });
  }
  let lbl = el.getAttribute('aria-label') || '';
  if (!lbl && el.labels && el.labels[0]) {
    const c = el.labels[0].cloneNode(true);
    c.querySelectorAll('select, option').forEach((n) => n.remove());
    lbl = c.textContent.replace(/\\s+/g, ' ').trim();
  }
  return { options: options, index: el.selectedIndex, label: lbl.slice(0, 80) };
})(%s, %s)"""

CHOOSE_JS = """(function (i) {
  const el = window.__bollisoftSelect;
  if (!el || !el.isConnected) return false;
  el.focus();
  if (el.selectedIndex !== i) {
    el.selectedIndex = i;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  return true;
})(%d)"""

# Aktives Eingabefeld (für die Bildschirmtastatur am Handy)
FOCUS_JS = """(function () {
  let el = document.activeElement;
  for (let i = 0; i < 8 && el; i++) {
    if (el.shadowRoot && el.shadowRoot.activeElement) { el = el.shadowRoot.activeElement; continue; }
    if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
      let d = null;
      try { d = el.contentDocument; } catch (e) { d = null; }
      if (d && d.activeElement) { el = d.activeElement; continue; }
    }
    break;
  }
  if (!el) return null;
  if (el.isContentEditable || el.tagName === 'TEXTAREA') return { type: 'text', mode: el.inputMode || '' };
  if (el.tagName !== 'INPUT') return null;
  const t = (el.type || 'text').toLowerCase();
  if (['button', 'submit', 'reset', 'checkbox', 'radio', 'file', 'image', 'range', 'color', 'hidden'].includes(t)) return null;
  return { type: t, mode: el.inputMode || '' };
})()"""

# Markierter Text (auch in Eingabefeldern und Iframes derselben Herkunft)
SELECTION_JS = """(function () {
  function sel(doc) {
    const a = doc.activeElement;
    if (a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT')) {
      try { const s = a.value.substring(a.selectionStart, a.selectionEnd); if (s) return s; } catch (e) {}
    }
    if (a && (a.tagName === 'IFRAME' || a.tagName === 'FRAME')) {
      try { const r = sel(a.contentDocument); if (r) return r; } catch (e) {}
    }
    return doc.getSelection ? String(doc.getSelection()) : '';
  }
  return sel(document);
})()"""


class CdpError(Exception):
    pass


def clamp(value, low, high):
    return max(low, min(high, value))


def normalize_url(raw):
    """Eingabe der Adresszeile → Adresse. Gibt (url, fehler) zurück."""
    text = (raw or "").strip()
    if not text:
        return None, "Keine Adresse"
    match = SCHEME_RE.match(text)
    if match and not HOST_RE.match(text):
        scheme = match.group(1).lower()
        if scheme in BLOCKED_SCHEMES:
            return None, f"Adressen mit {scheme}: sind im Add-on gesperrt"
        if scheme in ("http", "https", "about", "data", "view-source", "blob"):
            return text, None
        return None, f"Adressen mit {scheme}: werden nicht unterstützt"
    if " " not in text and HOST_RE.match(text):
        return "http://" + text, None
    return SEARCH + quote(text), None


def allowed_url(url):
    match = SCHEME_RE.match(url or "")
    return bool(match) and match.group(1).lower() in ("http", "https")


def safe_filename(name, fallback="download"):
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", (name or "").strip()).strip(". ")
    return name[:180] or fallback


def unique_path(folder, name):
    path = folder / name
    stem, suffix = path.stem, path.suffix
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){suffix}"
        n += 1
    return path


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data, private=False):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)


# --------------------------------------------------------------------------- Chromium-Prozess
class Chromium:
    """Ein Chromium ohne Fenster mit DevTools-Verbindung (nur 127.0.0.1 im Container)."""

    def __init__(self, binary, profile, args, env, user=None):
        self.binary = binary
        self.profile = Path(profile)
        self.args = args
        self.env = env
        self.user = user
        self.proc = None
        self.ws = None
        self.next_id = 0
        self.pending = {}
        self.outq = asyncio.Queue()
        self.tasks = []
        self.tail = collections.deque(maxlen=30)
        self.closing = False
        self.on_event = None
        self.on_exit = None

    @property
    def alive(self):
        return self.proc is not None and self.proc.returncode is None and self.ws is not None and not self.ws.closed

    async def start(self, session):
        port_file = self.profile / "DevToolsActivePort"
        try:
            port_file.unlink()
        except OSError:
            pass
        kwargs = {}
        if self.user:
            kwargs.update(user=self.user, group=self.user, extra_groups=[])
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, *self.args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, env=self.env, cwd=str(self.profile), **kwargs)
        self.tasks.append(asyncio.create_task(self._drain()))
        deadline = time.monotonic() + 45
        while True:
            if self.proc.returncode is not None:
                raise CdpError(f"Chromium beendet (Code {self.proc.returncode}). {self.last_lines()}")
            try:
                lines = port_file.read_text().splitlines()
                if len(lines) >= 2 and lines[0].strip().isdigit() and lines[1].startswith("/"):
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                await self.kill()
                raise CdpError("Chromium startet nicht (Zeitüberschreitung)")
            await asyncio.sleep(0.1)
        url = f"ws://127.0.0.1:{int(lines[0])}{lines[1].strip()}"
        self.ws = await session.ws_connect(url, max_msg_size=0, heartbeat=None, timeout=10)
        self.tasks.append(asyncio.create_task(self._read()))
        self.tasks.append(asyncio.create_task(self._write()))

    def last_lines(self):
        lines = [line for line in self.tail if line.strip()]
        return " | ".join(lines[-4:])[:600]

    async def _drain(self):
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                self.tail.append(text)
                log.debug("chromium: %s", text)
        except (OSError, ValueError):
            pass

    async def _write(self):
        while True:
            text = await self.outq.get()
            try:
                await self.ws.send_str(text)
            except (ConnectionError, RuntimeError, aiohttp.ClientError):
                return

    async def _read(self):
        try:
            async for msg in self.ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                mid = data.get("id")
                if mid is not None:
                    fut = self.pending.pop(mid, None)
                    if fut is not None and not fut.done():
                        if "error" in data:
                            fut.set_exception(CdpError(data["error"].get("message") or "Fehler"))
                        else:
                            fut.set_result(data.get("result") or {})
                    continue
                if self.on_event:
                    try:
                        self.on_event(data.get("method"), data.get("params") or {}, data.get("sessionId"))
                    except Exception:  # noqa: BLE001 – ein Fehler darf die Verbindung nicht beenden
                        log.exception("Fehler bei %s", data.get("method"))
        except (aiohttp.ClientError, ValueError, ConnectionError) as err:
            log.warning("DevTools-Verbindung: %s", err)
        finally:
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(CdpError("Verbindung zu Chromium getrennt"))
            self.pending.clear()
            if not self.closing and self.on_exit:
                self.on_exit()

    def call(self, method, params=None, session=None, want=True):
        """Befehl senden; mit want eine Future für die Antwort, sonst ohne Rückmeldung."""
        if not self.alive:
            raise CdpError("Chromium läuft nicht")
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut = None
        if want:
            fut = asyncio.get_running_loop().create_future()
            self.pending[self.next_id] = fut
        self.outq.put_nowait(json.dumps(msg))
        return fut

    async def send(self, method, params=None, session=None, timeout=20):
        fut = self.call(method, params, session)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as err:
            raise CdpError(f"{method}: keine Antwort") from err

    async def kill(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()

    async def close(self):
        self.closing = True
        if self.alive:
            try:
                await self.send("Browser.close", timeout=5)
            except CdpError:
                pass
        if self.proc and self.proc.returncode is None:
            try:
                await asyncio.wait_for(self.proc.wait(), 8)
            except asyncio.TimeoutError:
                await self.kill()
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()
        for task in self.tasks:
            task.cancel()


# --------------------------------------------------------------------------- Zuschauer
class Viewer:
    """Ein Browserfenster mit der Oberfläche. Bilder werden nur geschickt, wenn das letzte gezeichnet ist."""

    def __init__(self, ws, user=""):
        self.ws = ws
        self.user = user
        self.id = uuid.uuid4().hex[:8]
        self.ready = True
        self.view = None
        self.swallow = False
        self.out = collections.deque()
        self.frame = None
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self._run())

    def send(self, obj):
        self.out.append(json.dumps(obj, ensure_ascii=False))
        self.wake.set()

    def send_frame(self, blob):
        self.ready = False
        self.frame = blob
        self.wake.set()

    async def _run(self):
        try:
            while not self.ws.closed:
                await self.wake.wait()
                self.wake.clear()
                while self.out:
                    await self.ws.send_str(self.out.popleft())
                if self.frame is not None:
                    blob, self.frame = self.frame, None
                    await self.ws.send_bytes(blob)
        except (ConnectionError, RuntimeError, aiohttp.ClientError):
            pass

    def close(self):
        self.task.cancel()


# --------------------------------------------------------------------------- Tabs
class Tab:
    def __init__(self, target_id, session, url="", title=""):
        self.id = target_id
        self.session = session
        self.url = url
        self.title = title
        self.loading = False
        self.can_back = False
        self.can_forward = False
        self.metrics = None

    def info(self):
        return {"id": self.id, "url": self.url, "title": self.title, "loading": self.loading}


# --------------------------------------------------------------------------- Browser
class Browser:
    def __init__(self, options, data_dir, download_dir, upload_dir, http, binary, user=None):
        self.opts = options
        self.data = Path(data_dir)
        self.profile = self.data / "chromium"
        self.download_dir = Path(download_dir)
        self.download_tmp = self.download_dir / ".laden"
        self.upload_dir = Path(upload_dir)
        self.http = http
        self.binary = binary
        self.user = user
        self.chrome = None
        self.status = "stopped"
        self.error = ""
        self.tabs = {}
        self.active = None
        self.viewers = set()
        self.view = {"w": 1280, "h": 800, "dpr": 1.0, "zoom": 1.0, "mobile": False, "quality": 70}
        self.view_owner = None
        self.frame = None
        self.frame_seq = 0
        self.chrome_ack = None
        self.ack_handle = None
        self.cast_session = None
        self.cast_key = None
        self.lock = asyncio.Lock()
        self.idle_handle = None
        self.state_handle = None
        self.session_handle = None
        self.recent_handle = None
        self.ua = ""
        self.ua_mobile = ""
        self.dialogs = {}
        self.auths = {}
        self.auth_sent = {}
        self.auth_users = {}
        self.choosers = {}
        self.downloads = collections.OrderedDict()
        self.attaching = set()
        self.activate_next = set()
        self.last_input = None
        self.crashes = collections.deque(maxlen=5)
        self.recent = read_json(self.data / "recent.json", [])
        self.logins = read_json(self.data / "logins.json", {})

    # ------------------------------------------------------------------ Hilfen
    @property
    def alive(self):
        return self.chrome is not None and self.chrome.alive

    def _tab(self, tab_id=None):
        return self.tabs.get(tab_id or self.active)

    def _tab_by_session(self, session):
        for tab in self.tabs.values():
            if tab.session == session:
                return tab
        return None

    def _call(self, method, params=None, session=None):
        if self.alive:
            try:
                self.chrome.call(method, params, session, want=False)
            except CdpError:
                pass

    async def _send(self, method, params=None, session=None, timeout=20):
        if not self.alive:
            raise CdpError("Chromium läuft nicht")
        return await self.chrome.send(method, params, session, timeout)

    async def _eval(self, tab, expression, timeout=1.5):
        if not tab or self.dialogs.get(tab.session):
            return None
        try:
            result = await self._send("Runtime.evaluate", {"expression": expression, "returnByValue": True},
                                      session=tab.session, timeout=timeout)
        except CdpError:
            return None
        return (result.get("result") or {}).get("value")

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        task.add_done_callback(self._task_done)
        return task

    @staticmethod
    def _task_done(task):
        if not task.cancelled() and task.exception() is not None:
            err = task.exception()
            if isinstance(err, CdpError):
                log.debug("DevTools: %s", err)
            else:
                log.error("Fehler im Browser", exc_info=err)

    def broadcast(self, obj):
        for viewer in list(self.viewers):
            viewer.send(obj)

    def toast(self, text, viewer=None):
        msg = {"t": "toast", "text": text}
        if viewer is not None:
            viewer.send(msg)
        else:
            self.broadcast(msg)

    # ------------------------------------------------------------------ Zustand
    def state(self):
        tab = self._tab()
        dialog = None
        for session, info in self.dialogs.items():
            if dialog is None or (tab and session == tab.session):
                dialog = info
        auth = None
        for info in self.auths.values():
            auth = {k: v for k, v in info.items() if k != "session"}
            break
        downloads = [{k: d[k] for k in ("guid", "name", "state", "received", "total", "file")}
                     for d in list(self.downloads.values())[-6:]]
        return {
            "t": "state", "status": self.status, "error": self.error,
            "tabs": [t.info() for t in self.tabs.values()], "active": self.active,
            "back": bool(tab and tab.can_back), "forward": bool(tab and tab.can_forward),
            "dialog": dialog, "auth": auth, "downloads": downloads, "viewers": len(self.viewers),
        }

    def push_state(self):
        if self.state_handle is None:
            self.state_handle = asyncio.get_running_loop().call_later(0.04, self._flush_state)

    def _flush_state(self):
        self.state_handle = None
        self.broadcast(self.state())

    def _save_session_soon(self):
        if self.session_handle is not None:
            self.session_handle.cancel()
        self.session_handle = asyncio.get_running_loop().call_later(2, self._save_session)

    def _save_session(self):
        self.session_handle = None
        if not self.tabs:
            return
        urls, active = [], 0
        for tab in self.tabs.values():
            if allowed_url(tab.url):
                if tab.id == self.active:
                    active = len(urls)
                urls.append(tab.url)
        write_json(self.data / "session.json", {"tabs": urls, "active": active})

    def _remember(self, url, title=""):
        if not allowed_url(url):
            return
        self.recent = [r for r in self.recent if r.get("url") != url]
        self.recent.insert(0, {"url": url, "title": title, "time": int(time.time())})
        del self.recent[24:]
        self._save_recent_soon()

    def _retitle(self, url, title):
        for item in self.recent[:5]:
            if item.get("url") == url and title and item.get("title") != title:
                item["title"] = title
                self._save_recent_soon()

    def _save_recent_soon(self):
        if self.recent_handle is None:
            def save():
                self.recent_handle = None
                write_json(self.data / "recent.json", self.recent)
            self.recent_handle = asyncio.get_running_loop().call_later(3, save)

    # ------------------------------------------------------------------ Zuschauer
    def add_viewer(self, viewer):
        self.viewers.add(viewer)
        if self.idle_handle is not None:
            self.idle_handle.cancel()
            self.idle_handle = None
        viewer.send(self.state())
        if self.frame is not None:
            viewer.send_frame(self.frame)
        self._spawn(self.ensure_running())
        self.push_state()

    def remove_viewer(self, viewer):
        self.viewers.discard(viewer)
        viewer.close()
        if self.last_input is viewer:
            self.last_input = None
        if self.view_owner is viewer:
            self.view_owner = None
            others = [v for v in self.viewers if v.view]
            if others:
                self._spawn(self._take_view(others[-1]))
        if not self.viewers:
            self._spawn(self._cast_stop())
            idle = int(self.opts.get("idle_minutes", 30) or 0)
            if idle > 0 and self.status == "running":
                self.idle_handle = asyncio.get_running_loop().call_later(
                    idle * 60, lambda: self._spawn(self.stop("ohne Zuschauer")))
        self.push_state()

    # ------------------------------------------------------------------ Start/Stopp
    def _chromium_args(self):
        args = [
            "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-smooth-scrolling", "--disable-background-networking", "--disable-sync",
            "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication,DialMediaRouteProvider",
            "--password-store=basic", "--lang=de-DE", f"--accept-lang={ACCEPT_LANGUAGE}",
            "--window-size=1280,800", "--force-color-profile=srgb", "--mute-audio",
        ]
        if os.name == "posix":
            args.append("--no-sandbox")
        if self.opts.get("ignore_certificate_errors", True):
            args.append("--ignore-certificate-errors")
        args.append(BLANK)
        return args

    def _chromium_env(self):
        env = {"LANG": "de_DE.UTF-8", "TZ": os.environ.get("TZ", "Europe/Berlin")}
        for key in ("PATH", "SYSTEMROOT", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "USERPROFILE"):
            if key in os.environ:
                env[key] = os.environ[key]
        if os.name == "posix":
            home = Path("/home/browser") if self.user else self.data
            env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"))
        return env

    def _prepare_dirs(self):
        for folder in (self.profile, self.download_dir, self.download_tmp, self.upload_dir):
            folder.mkdir(parents=True, exist_ok=True)
        if self.user and os.name == "posix":
            import pwd  # nur unter Linux vorhanden
            info = pwd.getpwnam(self.user)
            # /data nur durchqueren (für /data/chromium und /data/uploads), nicht auflisten
            mode = self.data.stat().st_mode & 0o777
            if mode & 0o001 == 0:
                os.chmod(self.data, mode | 0o001)
            for folder in (self.profile, self.download_dir, self.download_tmp):
                if folder.stat().st_uid != info.pw_uid:
                    for root, dirs, files in os.walk(folder):
                        for name in dirs + files:
                            try:
                                os.lchown(os.path.join(root, name), info.pw_uid, info.pw_gid)
                            except OSError:
                                pass
                    os.chown(folder, info.pw_uid, info.pw_gid)
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                try:
                    (self.profile / name).unlink()
                except OSError:
                    pass

    async def ensure_running(self):
        async with self.lock:
            if self.alive:
                return
            self.status = "starting"
            self.error = ""
            self.push_state()
            try:
                await self._start()
            except (CdpError, OSError, aiohttp.ClientError, KeyError) as err:
                log.error("Chromium startet nicht: %s", err)
                if self.chrome is not None:
                    self.chrome.closing = True
                    await self.chrome.close()
                self.chrome = None
                self.status = "error"
                self.error = str(err) or err.__class__.__name__
                self.push_state()
                return
            self.status = "running"
            self.push_state()

    async def _start(self):
        await asyncio.get_running_loop().run_in_executor(None, self._prepare_dirs)
        chrome = Chromium(self.binary, self.profile, self._chromium_args(), self._chromium_env(), self.user)
        chrome.on_event = self._on_event
        chrome.on_exit = self._on_exit
        self.chrome = chrome
        self.tabs.clear()
        self.active = None
        self.frame = None
        self.cast_session = None
        log.info("Chromium startet")
        await chrome.start(self.http)
        version = await self._send("Browser.getVersion")
        self.ua = re.sub(r"Headless(Chrome)?", lambda m: m.group(1) or "", version.get("userAgent", ""))
        chrome_ver = re.search(r"Chrome/([\d.]+)", self.ua)
        major = chrome_ver.group(1).split(".")[0] if chrome_ver else "140"
        self.ua_mobile = (f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                          f"Chrome/{major}.0.0.0 Mobile Safari/537.36")
        log.info("%s läuft", version.get("product", "Chromium"))
        await self._send("Browser.setDownloadBehavior", {
            "behavior": "allowAndName", "downloadPath": str(self.download_tmp), "eventsEnabled": True})
        await self._send("Target.setDiscoverTargets", {"discover": True})
        targets = await self._send("Target.getTargets")
        for info in targets.get("targetInfos", []):
            if info.get("type") == "page":
                await self._attach(info)
        if not self.tabs:
            await self.new_tab()
        await self._restore()

    async def _restore(self):
        saved = read_json(self.data / "session.json", {})
        urls = [u for u in saved.get("tabs", []) if isinstance(u, str) and allowed_url(u)][:12]
        first = next(iter(self.tabs.values()))
        if urls:
            self._call("Page.navigate", {"url": urls[0]}, first.session)
            for url in urls[1:]:
                await self.new_tab(url, activate=False)
            index = saved.get("active", 0)
            tabs = list(self.tabs.values())
            if isinstance(index, int) and 0 <= index < len(tabs):
                await self.activate(tabs[index].id)
        elif self.opts.get("start_url"):
            url, _err = normalize_url(self.opts["start_url"])
            if url:
                self._call("Page.navigate", {"url": url}, first.session)

    async def stop(self, reason=""):
        async with self.lock:
            if self.chrome is None:
                return
            if self.tabs:
                self._save_session()
            log.info("Chromium wird beendet%s", f" ({reason})" if reason else "")
            chrome, self.chrome = self.chrome, None
            await chrome.close()
            self._reset()
            self.status = "stopped"
            self.push_state()

    def _reset(self):
        self.tabs.clear()
        self.active = None
        self.frame = None
        self.cast_session = None
        self.cast_key = None
        self.chrome_ack = None
        self.dialogs.clear()
        self.auths.clear()
        self.choosers.clear()

    def _on_exit(self):
        """Chromium ist unerwartet weg: neu starten, solange jemand zusieht (höchstens 3× in 2 Minuten)."""
        log.warning("Chromium wurde unerwartet beendet. %s", self.chrome.last_lines() if self.chrome else "")
        self.chrome = None
        self._reset()
        now = time.monotonic()
        self.crashes.append(now)
        recent = [t for t in self.crashes if now - t < 120]
        if len(recent) >= 3:
            self.status = "error"
            self.error = "Chromium stürzt wiederholt ab – „Browser neu starten“ versucht es erneut."
        else:
            self.status = "stopped"
            self.error = "Chromium wurde unerwartet beendet."
            if self.viewers:
                asyncio.get_running_loop().call_later(1.5, lambda: self._spawn(self.ensure_running()))
        self.push_state()

    async def restart(self):
        self.crashes.clear()
        await self.stop("Neustart")
        await self.ensure_running()

    async def clear_data(self):
        await self.stop("Daten löschen")
        await asyncio.get_running_loop().run_in_executor(None, shutil.rmtree, self.profile, True)
        for name in ("session.json", "recent.json", "logins.json"):
            try:
                (self.data / name).unlink()
            except OSError:
                pass
        self.recent = []
        self.logins = {}
        await self.ensure_running()

    # ------------------------------------------------------------------ Tabs
    async def _attach(self, info):
        tid = info["targetId"]
        if tid in self.tabs or tid in self.attaching:
            return
        self.attaching.add(tid)
        try:
            result = await self._send("Target.attachToTarget", {"targetId": tid, "flatten": True})
            tab = Tab(tid, result["sessionId"], info.get("url", ""), info.get("title", ""))
            self.tabs[tid] = tab
            session = tab.session
            await asyncio.gather(
                self._send("Page.enable", session=session),
                self._send("Page.setInterceptFileChooserDialog", {"enabled": True}, session=session),
                self._send("Emulation.setFocusEmulationEnabled", {"enabled": True}, session=session),
                self._send("Fetch.enable", {"handleAuthRequests": True,
                                            "patterns": [{"urlPattern": "*", "resourceType": "Document"}]},
                           session=session),
                self._apply_view(tab),
            )
        except CdpError as err:
            log.debug("Tab %s nicht übernommen: %s", tid, err)
            self.tabs.pop(tid, None)
            return
        finally:
            self.attaching.discard(tid)
        if tid in self.activate_next or info.get("openerId") or self.active is None:
            self.activate_next.discard(tid)
            await self.activate(tid)
        self.push_state()

    async def activate(self, tid):
        tab = self.tabs.get(tid)
        if tab is None:
            return
        if self.active != tid:
            await self._cast_stop()
            self.active = tid
            self.frame = None
            self._call("Target.activateTarget", {"targetId": tid})
        await self._apply_view(tab)
        await self._cast_start()
        self._spawn(self._refresh_history(tab))
        self._save_session_soon()
        self.push_state()

    async def new_tab(self, url=BLANK, activate=True):
        result = await self._send("Target.createTarget", {"url": url})
        tid = result["targetId"]
        if activate:
            self.activate_next.add(tid)
        for _ in range(60):
            if tid in self.tabs:
                break
            await asyncio.sleep(0.05)
        tab = self.tabs.get(tid)
        if tab is None:
            targets = await self._send("Target.getTargets")
            for info in targets.get("targetInfos", []):
                if info.get("targetId") == tid:
                    await self._attach(info)
            tab = self.tabs.get(tid)
        if tab is not None and activate and self.active != tid:
            await self.activate(tid)
        return tab

    async def close_tab(self, tid):
        tab = self.tabs.get(tid)
        if tab is None:
            return
        if len(self.tabs) == 1:
            await self._send("Page.navigate", {"url": BLANK}, session=tab.session)
            return
        await self._send("Target.closeTarget", {"targetId": tid})

    def _remove_tab(self, tid):
        tab = self.tabs.pop(tid, None)
        if tab is None:
            return
        self.dialogs.pop(tab.session, None)
        for rid in [r for r, a in self.auths.items() if a["session"] == tab.session]:
            del self.auths[rid]
        if self.cast_session == tab.session:
            self.cast_session = None
            self.chrome_ack = None
        if self.active == tid:
            self.active = None
            self.frame = None
            if self.tabs:
                self._spawn(self.activate(list(self.tabs)[-1]))
            elif self.alive:
                self._spawn(self.new_tab())
        self._save_session_soon()
        self.push_state()

    async def _refresh_title(self, tab):
        """Seitentitel nachfragen: Chromium meldet geänderte Titel nicht immer von selbst."""
        try:
            info = (await self._send("Target.getTargetInfo", {"targetId": tab.id}, timeout=5)).get("targetInfo") or {}
        except CdpError:
            return
        title = info.get("title", "")
        if title and title != tab.title and tab.id in self.tabs:
            tab.title = title
            self._retitle(tab.url, title)
            self.push_state()

    async def _refresh_history(self, tab):
        try:
            hist = await self._send("Page.getNavigationHistory", session=tab.session, timeout=5)
        except CdpError:
            return
        index, count = hist.get("currentIndex", 0), len(hist.get("entries", []))
        tab.can_back, tab.can_forward = index > 0, index < count - 1
        self.push_state()

    async def _history_go(self, delta):
        tab = self._tab()
        if tab is None:
            return
        hist = await self._send("Page.getNavigationHistory", session=tab.session)
        index = hist.get("currentIndex", 0) + delta
        entries = hist.get("entries", [])
        if 0 <= index < len(entries):
            await self._send("Page.navigateToHistoryEntry", {"entryId": entries[index]["id"]}, session=tab.session)

    async def navigate(self, raw, viewer=None):
        url, err = normalize_url(raw)
        if err:
            self.toast(err, viewer)
            return
        tab = self._tab() or await self.new_tab()
        if tab is not None:
            self._call("Page.navigate", {"url": url}, tab.session)

    # ------------------------------------------------------------------ Ansicht und Bild
    def _metrics(self):
        v = self.view
        zoom = clamp(float(v.get("zoom", 1)), 0.5, 2.0)
        width = int(clamp(round(v["w"] / zoom), 240, 3840))
        height = int(clamp(round(v["h"] / zoom), 200, 3840))
        dsf = clamp(float(v.get("dpr", 1)) * zoom, 0.5, 3.0)
        dsf = min(dsf, MAX_FRAME_EDGE / width, MAX_FRAME_EDGE / height)
        return width, height, round(max(dsf, 0.25), 3), bool(v.get("mobile"))

    async def _apply_view(self, tab):
        width, height, dsf, mobile = metrics = self._metrics()
        if tab.metrics == metrics:
            return
        tab.metrics = metrics
        session = tab.session
        await asyncio.gather(
            self._send("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": dsf, "mobile": mobile,
                "screenWidth": width, "screenHeight": height}, session=session),
            self._send("Emulation.setUserAgentOverride", {
                "userAgent": self.ua_mobile if mobile else self.ua, "acceptLanguage": ACCEPT_LANGUAGE},
                session=session),
        )

    def _set_view(self, view):
        try:
            new = {
                "w": int(clamp(int(view["w"]), 200, 4000)), "h": int(clamp(int(view["h"]), 150, 4000)),
                "dpr": clamp(float(view.get("dpr", 1)), 0.5, 3), "zoom": clamp(float(view.get("zoom", 1)), 0.5, 2),
                "mobile": bool(view.get("mobile")), "quality": int(clamp(int(view.get("quality", 70)), 30, 95)),
            }
        except (KeyError, TypeError, ValueError):
            return None
        return new

    async def _take_view(self, viewer):
        if viewer.view is None:
            return
        self.view_owner = viewer
        if viewer.view == self.view:
            return
        self.view = dict(viewer.view)
        tab = self._tab()
        if tab is not None and self.alive:
            await self._apply_view(tab)
            await self._cast_start()

    async def _cast_start(self):
        tab = self._tab()
        if tab is None or not self.viewers or not self.alive:
            return
        width, height, dsf, _mobile = self._metrics()
        key = (tab.session, int(width * dsf), int(height * dsf), self.view["quality"])
        if self.cast_session == tab.session and self.cast_key == key:
            return
        if self.cast_session is not None:
            await self._cast_stop()
        self.cast_session = tab.session
        self.cast_key = key
        self.chrome_ack = None
        try:
            await self._send("Page.startScreencast", {
                "format": "jpeg", "quality": key[3], "maxWidth": key[1], "maxHeight": key[2], "everyNthFrame": 1},
                session=tab.session)
        except CdpError as err:
            log.warning("Bildübertragung startet nicht: %s", err)
            self.cast_session = None
            self.cast_key = None

    async def _cast_stop(self):
        if self.cast_session is not None:
            self._call("Page.stopScreencast", session=self.cast_session)
        self.cast_session = None
        self.cast_key = None
        self.chrome_ack = None

    def _on_frame(self, session, params):
        tab = self._tab()
        if tab is None or tab.session != session or not self.viewers:
            self._call("Page.screencastFrameAck", {"sessionId": params.get("sessionId")}, session)
            return
        meta = params.get("metadata") or {}
        self.frame_seq += 1
        header = json.dumps({
            "seq": self.frame_seq, "dw": meta.get("deviceWidth"), "dh": meta.get("deviceHeight"),
            "ot": meta.get("offsetTop", 0)}).encode()
        self.frame = struct.pack(">I", len(header)) + header + base64.b64decode(params["data"])
        if self.chrome_ack is not None:
            self._ack_chrome()
        self.chrome_ack = (session, params.get("sessionId"))
        for viewer in self.viewers:
            if viewer.ready:
                viewer.send_frame(self.frame)
        # Chromium liefert das nächste Bild erst nach der Bestätigung: so viele Bilder, wie der schnellste
        # Zuschauer zeichnen kann. Spätestens nach einer Sekunde trotzdem bestätigen.
        if self.ack_handle is not None:
            self.ack_handle.cancel()
        self.ack_handle = asyncio.get_running_loop().call_later(1.0, self._ack_chrome)

    def _ack_chrome(self):
        if self.ack_handle is not None:
            self.ack_handle.cancel()
            self.ack_handle = None
        if self.chrome_ack is not None:
            session, num = self.chrome_ack
            self.chrome_ack = None
            self._call("Page.screencastFrameAck", {"sessionId": num}, session)

    def frame_drawn(self, viewer):
        viewer.ready = True
        if self.chrome_ack is not None:
            self._ack_chrome()

    # ------------------------------------------------------------------ Ereignisse von Chromium
    def _on_event(self, method, params, session):
        if method == "Page.screencastFrame":
            self._on_frame(session, params)
        elif method == "Fetch.requestPaused":
            self._call("Fetch.continueRequest", {"requestId": params["requestId"]}, session)
        elif method == "Target.targetCreated":
            info = params.get("targetInfo") or {}
            if info.get("type") == "page" and self.status in ("running", "starting"):
                self._spawn(self._attach(info))
        elif method == "Target.targetInfoChanged":
            info = params.get("targetInfo") or {}
            tab = self.tabs.get(info.get("targetId"))
            if tab is not None:
                changed = (tab.url, tab.title) != (info.get("url", ""), info.get("title", ""))
                tab.url, tab.title = info.get("url", ""), info.get("title", "")
                if changed:
                    self._retitle(tab.url, tab.title)
                    self._save_session_soon()
                    self.push_state()
        elif method == "Target.targetDestroyed":
            self._remove_tab(params.get("targetId"))
        elif method == "Target.targetCrashed":
            tab = self.tabs.get(params.get("targetId"))
            if tab is not None:
                log.warning("Seite abgestürzt: %s", tab.url)
                self.toast("Die Seite ist abgestürzt und wird neu geladen.")
                self._call("Page.reload", {}, tab.session)
        elif method in ("Page.frameStartedLoading", "Page.frameStoppedLoading"):
            tab = self._tab_by_session(session)
            if tab is not None and params.get("frameId") == tab.id:
                tab.loading = method == "Page.frameStartedLoading"
                if not tab.loading:
                    self._spawn(self._refresh_title(tab))
                self.push_state()
        elif method in ("Page.domContentEventFired", "Page.loadEventFired"):
            tab = self._tab_by_session(session)
            if tab is not None:
                self._spawn(self._refresh_title(tab))
        elif method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            tab = self._tab_by_session(session)
            if tab is not None and not frame.get("parentId"):
                url = frame.get("url", "") + frame.get("urlFragment", "")
                tab.url = url
                self._remember(url, tab.title)
                self._spawn(self._refresh_history(tab))
                self._save_session_soon()
                self.push_state()
        elif method == "Page.navigatedWithinDocument":
            tab = self._tab_by_session(session)
            if tab is not None and params.get("frameId") == tab.id:
                tab.url = params.get("url", tab.url)
                self._spawn(self._refresh_history(tab))
                self._spawn(self._refresh_title(tab))
                self.push_state()
        elif method == "Page.javascriptDialogOpening":
            tab = self._tab_by_session(session)
            if tab is not None:
                self.dialogs[session] = {
                    "tab": tab.id, "title": tab.title or tab.url, "type": params.get("type", "alert"),
                    "message": (params.get("message") or "")[:4000], "default": params.get("defaultPrompt", "")}
                self.push_state()
        elif method == "Page.javascriptDialogClosed":
            if self.dialogs.pop(session, None) is not None:
                self.push_state()
        elif method == "Fetch.authRequired":
            self._on_auth(session, params)
        elif method == "Page.fileChooserOpened":
            token = uuid.uuid4().hex
            self.choosers[token] = {"session": session, "node": params.get("backendNodeId")}
            msg = {"t": "files", "token": token, "multiple": params.get("mode") == "selectMultiple"}
            target = self.last_input if self.last_input in self.viewers else None
            if target is not None:
                target.send(msg)
            else:
                self.broadcast(msg)
        elif method == "Browser.downloadWillBegin":
            self._on_download_begin(params)
        elif method == "Browser.downloadProgress":
            self._on_download_progress(params)

    def _on_auth(self, session, params):
        rid = params["requestId"]
        challenge = params.get("authChallenge") or {}
        origin = challenge.get("origin", "")
        realm = challenge.get("realm", "")
        key = f"{origin} {realm}"
        now = time.monotonic()
        recent = self.auth_sent.get(key)
        retry = recent is not None and now - recent < 20
        saved = self.logins.get(key)
        if saved and not retry:
            self.auth_sent[key] = now
            self._call("Fetch.continueWithAuth", {"requestId": rid, "authChallengeResponse": {
                "response": "ProvideCredentials", "username": saved["user"], "password": saved["password"]}}, session)
            return
        self.auths[rid] = {"id": rid, "session": session, "origin": origin, "realm": realm, "retry": retry,
                           "proxy": challenge.get("source") == "Proxy", "saved": bool(saved),
                           "user": (saved or {}).get("user") or self.auth_users.get(key, "")}
        self.push_state()

    def _on_download_begin(self, params):
        guid = params.get("guid")
        self.downloads[guid] = {
            "guid": guid, "name": safe_filename(params.get("suggestedFilename")), "url": params.get("url", ""),
            "state": "progress", "received": 0, "total": 0, "file": None}
        while len(self.downloads) > 20:
            self.downloads.popitem(last=False)
        self.push_state()

    def _on_download_progress(self, params):
        item = self.downloads.get(params.get("guid"))
        if item is None:
            return
        item["received"] = int(params.get("receivedBytes") or 0)
        item["total"] = int(params.get("totalBytes") or 0)
        state = params.get("state")
        tmp = self.download_tmp / item["guid"]
        if state == "completed":
            item["state"] = "saving"
            self._spawn(self._finish_download(item, tmp))
        elif state == "canceled":
            item["state"] = "canceled"
            try:
                tmp.unlink()
            except OSError:
                pass
        self.push_state()

    async def _finish_download(self, item, tmp):
        """Fertige Datei aus dem Zwischenordner unter ihrem Namen ablegen (Chromium braucht evtl. einen Moment)."""
        for _ in range(30):
            if tmp.exists():
                break
            await asyncio.sleep(0.1)
        target = unique_path(self.download_dir, item["name"])
        try:
            os.replace(tmp, target)
            item["file"] = target.name
            item["state"] = "done"
            self.toast(f"Download fertig: {target.name}")
        except OSError as err:
            item["state"] = "error"
            log.warning("Download %s nicht abgelegt: %s", item["name"], err)
            self.toast(f"Download {item['name']} fehlgeschlagen")
        self.push_state()

    # ------------------------------------------------------------------ Befehle der Oberfläche
    async def handle(self, viewer, msg):
        kind = msg.get("t")
        if kind == "ack":
            self.frame_drawn(viewer)
            return
        if kind == "view":
            view = self._set_view(msg)
            if view is not None:
                first = viewer.view is None
                viewer.view = view
                # Ein neu geöffnetes Fenster bestimmt die Größe, sonst wer zuletzt etwas eingegeben hat
                if first or self.view_owner in (None, viewer) or len(self.viewers) == 1:
                    await self._take_view(viewer)
            return
        if kind in ("mouse", "key", "text") and viewer.view and self.view_owner is not viewer:
            await self._take_view(viewer)
        self.last_input = viewer
        if kind == "restart":
            await self.restart()
            return
        if kind == "clear_data":
            await self.clear_data()
            self.toast("Browserdaten gelöscht")
            return
        if not self.alive:
            if kind in ("nav", "tab_new"):
                await self.ensure_running()
            if not self.alive:
                return
        handler = {
            "mouse": self._mouse, "key": self._key, "text": self._text, "nav": self._nav, "tab_new": self._tab_new,
            "tab_close": self._tab_close, "tab_switch": self._tab_switch, "select": self._select,
            "dialog": self._dialog, "auth": self._auth, "files": self._files, "copy": self._copy,
            "back": self._back, "forward": self._forward, "reload": self._reload, "stop": self._stop,
        }.get(kind)
        if handler is not None:
            await handler(viewer, msg)

    async def _mouse(self, viewer, msg):
        tab = self._tab()
        if tab is None:
            return
        try:
            x, y = float(msg["x"]), float(msg["y"])
        except (KeyError, TypeError, ValueError):
            return
        mods = int(msg.get("mods") or 0) & 15
        kind = msg.get("type")
        if kind == "wheel":
            self._call("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": x, "y": y, "deltaX": float(msg.get("dx") or 0),
                "deltaY": float(msg.get("dy") or 0), "modifiers": mods}, tab.session)
            return
        types = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}
        if kind not in types:
            return
        button = msg.get("button") if msg.get("button") in ("left", "middle", "right") else "none"
        if kind == "move":
            button = {1: "left", 2: "right", 4: "middle"}.get(int(msg.get("buttons") or 0) & 7, "none")
        if kind == "down" and button == "left" and int(msg.get("clicks") or 1) == 1:
            picked = await self._eval(tab, PICK_JS % (x, y))
            if picked:
                viewer.swallow = True
                viewer.send({"t": "select", **picked})
                return
        if kind == "up" and viewer.swallow:
            viewer.swallow = False
            return
        self._call("Input.dispatchMouseEvent", {
            "type": types[kind], "x": x, "y": y, "button": button, "buttons": int(msg.get("buttons") or 0),
            "clickCount": int(clamp(int(msg.get("clicks") or (0 if kind == "move" else 1)), 0, 3)),
            "modifiers": mods}, tab.session)
        if kind == "up" and msg.get("touch"):
            await asyncio.sleep(0.15)
            focus = await self._eval(tab, FOCUS_JS)
            viewer.send({"t": "focus", "field": focus})

    async def _key(self, _viewer, msg):
        tab = self._tab()
        kind = msg.get("type")
        if tab is None or kind not in ("keyDown", "keyUp", "rawKeyDown"):
            return
        code = int(msg.get("keyCode") or 0)
        params = {
            "type": kind, "modifiers": int(msg.get("mods") or 0) & 15, "key": str(msg.get("key") or "")[:32],
            "code": str(msg.get("code") or "")[:32], "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code,
            "autoRepeat": bool(msg.get("repeat")), "location": int(msg.get("loc") or 0)}
        text = msg.get("text")
        if kind == "keyDown" and isinstance(text, str) and text:
            params["text"] = params["unmodifiedText"] = text[:4]
        self._call("Input.dispatchKeyEvent", params, tab.session)

    async def _text(self, _viewer, msg):
        tab = self._tab()
        text = msg.get("text")
        if tab is not None and isinstance(text, str) and text:
            self._call("Input.insertText", {"text": text[:20000]}, tab.session)

    async def _nav(self, viewer, msg):
        await self.navigate(msg.get("url"), viewer)

    async def _tab_new(self, viewer, msg):
        url = BLANK
        if msg.get("url"):
            url, err = normalize_url(msg["url"])
            if err:
                self.toast(err, viewer)
                return
        await self.new_tab(url)

    async def _tab_close(self, _viewer, msg):
        await self.close_tab(msg.get("id"))

    async def _tab_switch(self, _viewer, msg):
        await self.activate(msg.get("id"))

    async def _select(self, _viewer, msg):
        tab = self._tab()
        index = msg.get("index")
        if tab is not None and isinstance(index, int) and index >= 0:
            await self._eval(tab, CHOOSE_JS % index)

    async def _dialog(self, _viewer, msg):
        tab = self._tab(msg.get("tab"))
        if tab is None or tab.session not in self.dialogs:
            return
        params = {"accept": bool(msg.get("accept"))}
        if isinstance(msg.get("text"), str):
            params["promptText"] = msg["text"]
        self.dialogs.pop(tab.session, None)
        self.push_state()
        await self._send("Page.handleJavaScriptDialog", params, session=tab.session)

    async def _auth(self, _viewer, msg):
        info = self.auths.pop(msg.get("id"), None)
        if info is None:
            return
        self.push_state()
        key = f"{info['origin']} {info['realm']}"
        if msg.get("cancel"):
            response = {"response": "CancelAuth"}
        else:
            user, password = str(msg.get("user") or ""), str(msg.get("password") or "")
            response = {"response": "ProvideCredentials", "username": user, "password": password}
            self.auth_sent[key] = time.monotonic()
            self.auth_users[key] = user
            if msg.get("remember"):
                self.logins[key] = {"user": user, "password": password}
                write_json(self.data / "logins.json", self.logins, private=True)
            elif key in self.logins:
                del self.logins[key]
                write_json(self.data / "logins.json", self.logins, private=True)
        self._call("Fetch.continueWithAuth", {"requestId": info["id"], "authChallengeResponse": response},
                   info["session"])

    async def _files(self, viewer, msg):
        chooser = self.choosers.pop(msg.get("token"), None)
        if chooser is None:
            return
        files = []
        for upload_id in msg.get("ids") or []:
            if not re.fullmatch(r"[0-9a-f]{32}", str(upload_id)):
                continue
            folder = self.upload_dir / upload_id
            for path in sorted(folder.glob("*")) if folder.is_dir() else []:
                if path.is_file() and not path.name.endswith(".part"):
                    files.append(str(path))
        if not files:
            return
        try:
            await self._send("DOM.setFileInputFiles", {"files": files, "backendNodeId": chooser["node"]},
                             session=chooser["session"])
        except CdpError as err:
            self.toast(f"Datei nicht übernommen: {err}", viewer)

    async def _copy(self, viewer, _msg):
        text = await self._eval(self._tab(), SELECTION_JS)
        viewer.send({"t": "copied", "text": text or ""})

    async def _back(self, _viewer, _msg):
        await self._history_go(-1)

    async def _forward(self, _viewer, _msg):
        await self._history_go(1)

    async def _reload(self, _viewer, _msg):
        tab = self._tab()
        if tab is not None:
            self._call("Page.reload", {"ignoreCache": False}, tab.session)

    async def _stop(self, _viewer, _msg):
        tab = self._tab()
        if tab is not None:
            self._call("Page.stopLoading", {}, tab.session)
