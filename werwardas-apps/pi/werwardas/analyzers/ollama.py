"""Bewertung mit einem lokalen Vision-Modell über Ollama (z. B. qwen2.5vl, llava, gemma3).

Es können mehrere Ollama-Server angegeben werden. Sie werden der Reihe nach versucht, z. B. erst der schnelle
PC mit Grafikkarte und, wenn der aus ist, ein kleineres Modell direkt auf dem Raspberry Pi:

    ollama:
      servers:
        - {url: "http://192.168.1.139:11434", model: "qwen2.5vl:7b"}
        - {url: "http://045e59a3-ollama:11434", model: "qwen2.5vl:3b", timeout_seconds: 1800}

Server mit `fallback: true` sind nur Ersatz. Sie lassen sich in Home Assistant mit dem Schalter
"Pi-KI als Ersatz" abschalten (Laufzeitwert `ollama_fallback`), dann wird nur mit den übrigen Servern bewertet.

Ohne `servers` gelten wie bisher `url` und `model`.
"""

from __future__ import annotations

import base64
import json
import logging

import requests
from PIL import Image

from . import AnalyzerError, Result, build_prompt, clamp_score
from ..camera import to_jpeg

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["score", "reason"],
}

# So lange darf ein Server brauchen, um überhaupt zu antworten (bevor das eigentliche Bewerten startet).
PROBE_TIMEOUT = 3


def servers_from_config(cfg: dict, use_fallback: bool = True) -> list[dict]:
    defaults = {
        "model": cfg.get("model", "qwen2.5vl:7b"),
        "timeout_seconds": cfg.get("timeout_seconds", 600),
        "max_width": cfg.get("max_width", 1024),
    }
    servers = cfg.get("servers") or [{"url": cfg.get("url", "")}]
    return [{**defaults, **s} for s in servers if s.get("url") and (use_fallback or not s.get("fallback"))]


class OllamaAnalyzer:
    name = "ollama"

    def __init__(self, cfg: dict, hint: str, use_fallback: bool = True):
        self.servers = servers_from_config(cfg, use_fallback)
        self.fallback_off = not use_fallback and len(self.servers) < len(servers_from_config(cfg))
        self.prompt = build_prompt(hint)
        if not self.servers:
            raise AnalyzerError("Kein Ollama-Server aktiv (ollama.url/ollama.servers, Schalter 'Pi-KI als Ersatz')")

    def analyze(self, img: Image.Image) -> Result:
        errors = []
        for server in self.servers:
            base = server["url"].rstrip("/")
            problem = self._probe(base, server["model"])
            if problem:
                log.info("Ollama %s übersprungen: %s", base, problem)
                errors.append(f"{base}: {problem}")
                continue
            try:
                result = self._analyze(base, server, img)
            except AnalyzerError as exc:
                log.warning("%s", exc)
                errors.append(str(exc))
                continue
            log.info("Bewertet von %s (%s)", base, server["model"])
            return result
        note = " (Pi-KI als Ersatz ist ausgeschaltet)" if self.fallback_off else ""
        raise AnalyzerError(f"Kein Ollama-Server konnte bewerten{note} – " + "; ".join(errors))

    @staticmethod
    def _probe(base: str, model: str) -> str:
        """Leerer String = Server erreichbar und Modell vorhanden, sonst der Grund."""
        try:
            resp = requests.get(f"{base}/api/tags", timeout=PROBE_TIMEOUT)
            resp.raise_for_status()
            names = {m.get("name") for m in resp.json().get("models", [])}
        except (requests.RequestException, ValueError) as exc:
            return f"nicht erreichbar ({exc.__class__.__name__})"
        wanted = model if ":" in model else f"{model}:latest"
        if wanted not in names:
            return f"Modell {model} nicht geladen"
        return ""

    def _analyze(self, base: str, server: dict, img: Image.Image) -> Result:
        image_b64 = base64.b64encode(to_jpeg(img, max_width=int(server["max_width"]))).decode()
        payload = {
            "model": server["model"],
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0},
            "messages": [{"role": "user", "content": self.prompt, "images": [image_b64]}],
        }
        url = f"{base}/api/chat"
        try:
            resp = requests.post(url, json=payload, timeout=float(server["timeout_seconds"]))
            resp.raise_for_status()
            data = json.loads(resp.json()["message"]["content"])
        except requests.RequestException as exc:
            raise AnalyzerError(f"Ollama {base} fehlgeschlagen: {exc}") from exc
        except (KeyError, ValueError) as exc:
            raise AnalyzerError(f"Unerwartete Antwort von Ollama {base}: {exc}") from exc
        return Result(round(clamp_score(data.get("score"))), str(data.get("reason", "")).strip(), self.name)
