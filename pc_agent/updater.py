"""Selbst-Update des HA PC Agent über GitHub Releases.

Die veröffentlichte Windows-.exe prüft, ob ein neueres Release vorliegt, lädt die
neue .exe und tauscht sich beim nächsten Start selbst aus (ein kleines Batch
wartet, bis der laufende Prozess beendet ist, kopiert die neue Datei über die
alte und startet sie neu).

Nur sinnvoll für die gebündelte Windows-.exe. Unter Linux/aus dem Quelltext wird
ein verfügbares Update nur gemeldet (Aktualisierung dort über install_pi.sh bzw.
git pull).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import osbackend as osb

log = logging.getLogger("pc-agent")

REPO = "Devilwitha/HAaddons"
TAG_PREFIX = "pcagent-v"
ASSET_NAME = "HA PC Agent.exe"
_UA = {"User-Agent": "HA-PC-Agent-Updater", "Accept": "application/vnd.github+json"}


def parse_version(s: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def _is_newer(remote: tuple[int, ...], local: tuple[int, ...]) -> bool:
    return remote > local


def _get_json(url: str, timeout: float = 10):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def latest_release() -> "tuple[tuple[int, ...], str, str] | None":
    """Neuestes passendes Release: (Version, Tag, Download-URL der .exe) oder None."""
    try:
        data = _get_json(f"https://api.github.com/repos/{REPO}/releases?per_page=30")
    except Exception:
        log.debug("Update-Prüfung: GitHub nicht erreichbar", exc_info=True)
        return None
    best = None
    for rel in data if isinstance(data, list) else []:
        tag = rel.get("tag_name", "")
        if not tag.startswith(TAG_PREFIX) or rel.get("draft"):
            continue
        ver = parse_version(tag[len(TAG_PREFIX):])
        asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET_NAME), None)
        if asset and (best is None or ver > best[0]):
            best = (ver, tag, asset["browser_download_url"])
    return best


def download(url: str) -> Path:
    fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="ha-pc-agent-upd-")
    os.close(fd)
    req = urllib.request.Request(url, headers={"User-Agent": "HA-PC-Agent-Updater"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
    return Path(tmp)


def _spawn_swapper(new_exe: Path):
    """Batch startet, wartet auf Prozess-Ende, ersetzt die .exe und startet neu."""
    cur = os.path.abspath(sys.executable)
    pid = os.getpid()
    bat = Path(tempfile.gettempdir()) / "ha-pc-agent-update.bat"
    bat.write_text(
        "@echo off\r\n"
        f'powershell -NoProfile -Command "try {{ Wait-Process -Id {pid} -Timeout 60 }} catch {{}}"\r\n'
        "ping 127.0.0.1 -n 2 >nul\r\n"
        f'copy /Y "{new_exe}" "{cur}" >nul\r\n'
        f'start "" "{cur}"\r\n'
        f'del "{new_exe}" >nul 2>&1\r\n'
        '(goto) 2>nul & del "%~f0"\r\n',
        encoding="utf-8",
    )
    DETACHED = 0x00000008
    subprocess.Popen(["cmd", "/c", str(bat)],
                     creationflags=osb.CREATE_NO_WINDOW | DETACHED,
                     close_fds=True)


def maybe_update(current: str, apply: bool = True,
                 on_apply=None) -> "str | None":
    """Prüft auf ein neueres Release.

    Rückgabe:
      None        – kein Update / nicht erreichbar
      "<tag>"     – Update verfügbar, aber hier nicht selbst anwendbar
                    (kein gebündeltes Windows-.exe) oder apply=False
      "applying"  – Update geladen, Austausch wurde angestoßen (Aufrufer beenden)
    """
    info = latest_release()
    if not info:
        return None
    ver, tag, url = info
    if not _is_newer(ver, parse_version(current)):
        return None
    log.info("Update verfügbar: %s (aktuell %s)", tag, current)
    if not (osb.is_frozen() and osb.IS_WINDOWS):
        return tag
    if not apply:
        return tag
    try:
        path = download(url)
    except Exception:
        log.exception("Update-Download fehlgeschlagen")
        return tag
    log.info("Update geladen – wird beim Neustart angewendet.")
    if on_apply:
        try:
            on_apply()
        except Exception:
            pass
    _spawn_swapper(path)
    return "applying"
