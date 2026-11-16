"""Fotos holen und mit einem Vision-Modell über Ollama zählen lassen.

Kameras:
  - Adresse eines Fotos, z. B. ESP32-CAM  http://192.168.1.170/capture?flash=1
    oder die WerWarDas-/Pi-Kamera, jede Adresse, die ein JPEG liefert
  - Kamera aus Home Assistant: camera.kuehlschrank (Foto über die Home-Assistant-API)

Ollama: mehrere Server der Reihe nach (z. B. erst der PC mit Grafikkarte, dann das Ollama-Add-on).
"""
import asyncio
import base64
import io
import json
import logging
import os

import aiohttp
from PIL import Image, ImageOps

log = logging.getLogger("vision")

SUPERVISOR = os.environ.get("VORRAT_SUPERVISOR_URL", "http://supervisor")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CAMERA_TIMEOUT = 25
PROBE_TIMEOUT = 4
MAX_COUNT = 500

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
                "required": ["name", "count"],
            },
        },
    },
    "required": ["items"],
}


class VisionError(Exception):
    pass


# --------------------------------------------------------------------------- Kameras
async def fetch_photo(session, source):
    """JPEG-Bytes einer Kamera."""
    source = source.strip()
    if source.startswith("camera."):
        url = f"{SUPERVISOR}/core/api/camera_proxy/{source}"
        headers = {"Authorization": f"Bearer {TOKEN}"}
    elif source.startswith(("http://", "https://")):
        url, headers = source, {}
    else:
        raise VisionError(f"Unbekannte Kamera-Quelle „{source}“ (http://… oder camera.…)")
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=CAMERA_TIMEOUT)) as resp:
            if resp.status != 200:
                raise VisionError(f"Kamera {source}: HTTP {resp.status}")
            data = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise VisionError(f"Kamera {source} nicht erreichbar ({err.__class__.__name__})") from err
    if len(data) < 100:
        raise VisionError(f"Kamera {source}: kein Bild")
    return data


def prepare(data, rotation, max_width):
    """Drehen, verkleinern und als JPEG zurückgeben (für Ollama und die Vorschau)."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as err:            # Pillow meldet viele verschiedene Fehler
        raise VisionError(f"Bild nicht lesbar: {err}") from err
    if rotation:
        img = img.rotate(-int(rotation), expand=True)
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


# --------------------------------------------------------------------------- Auswertung
def build_prompt(location, known, hint):
    lines = [
        f"Das Foto zeigt: {location}.",
        "Zähle alle Vorräte, die du sicher erkennst (Lebensmittel, Getränke, Verpackungen, Haushaltswaren).",
        "Gleiche Produkte zusammenfassen und jeweils die Anzahl der einzelnen Stücke angeben "
        "(zwei Dosen Cola = 1 Eintrag mit count 2).",
        "Namen kurz, auf Deutsch und im Singular, so genau wie erkennbar, z. B. „Dose Cola“, „Milch“, „Ei“, "
        "„Joghurt Erdbeere“, „Butter“.",
        "Nicht zählen: Regale, Fächer, Behälter ohne erkennbaren Inhalt, Hände, Hintergrund.",
    ]
    if known:
        lines.append("Bereits bekannte Namen – genau so verwenden, wenn das Produkt dazu passt: "
                     + ", ".join(f"„{name}“" for name in known) + ".")
    if hint:
        lines.append("Hinweis: " + hint.strip())
    lines.append('Antworte nur mit JSON: {"items": [{"name": "...", "count": 1}]}. Ist nichts zu sehen: {"items": []}.')
    return "\n".join(lines)


def parse_items(data):
    counts = {}
    for entry in data.get("items") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        try:
            count = int(entry.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if name and 0 < count <= MAX_COUNT:
            counts[name] = counts.get(name, 0) + count
    return counts


class Ollama:
    def __init__(self, servers):
        self.servers = [s for s in servers if s.get("url") and s.get("model")]
        self.last_server = ""

    async def _probe(self, session, base, model):
        try:
            async with session.get(f"{base}/api/tags", timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT)) as resp:
                resp.raise_for_status()
                names = {m.get("name") for m in (await resp.json()).get("models", [])}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            return f"nicht erreichbar ({err.__class__.__name__})"
        wanted = model if ":" in model else f"{model}:latest"
        return "" if wanted in names else f"Modell {model} nicht geladen (in Ollama: ollama pull {model})"

    async def count(self, session, jpeg, prompt):
        """Gezählte Vorräte {Name: Anzahl} vom ersten Server, der antwortet."""
        if not self.servers:
            raise VisionError("Kein Ollama-Server eingetragen (Konfiguration: ollama_servers)")
        errors = []
        image = base64.b64encode(jpeg).decode()
        for server in self.servers:
            base = server["url"].rstrip("/")
            problem = await self._probe(session, base, server["model"])
            if problem:
                errors.append(f"{base}: {problem}")
                continue
            payload = {
                "model": server["model"],
                "stream": False,
                "format": SCHEMA,
                "options": {"temperature": 0},
                "messages": [{"role": "user", "content": prompt, "images": [image]}],
            }
            timeout = aiohttp.ClientTimeout(total=float(server.get("timeout_seconds") or 900))
            try:
                async with session.post(f"{base}/api/chat", json=payload, timeout=timeout) as resp:
                    if resp.status != 200:
                        raise VisionError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                    body = await resp.json()
                data = json.loads(body["message"]["content"])
            except (aiohttp.ClientError, asyncio.TimeoutError, VisionError) as err:
                errors.append(f"{base}: {err.__class__.__name__} {err}".strip())
                continue
            except (KeyError, ValueError, TypeError) as err:
                errors.append(f"{base}: unerwartete Antwort ({err})")
                continue
            self.last_server = f"{server['model']} @ {base}"
            return parse_items(data if isinstance(data, dict) else {})
        raise VisionError("Kein Ollama-Server konnte zählen – " + "; ".join(errors))
