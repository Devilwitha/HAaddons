"""Bewertung der Ordnung. Jeder Analyzer liefert einen Score 0–100 (100 = perfekt aufgeräumt)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

PROMPT = """Du bekommst ein Foto eines Zimmers von einer fest montierten Kamera.
Bewerte, wie aufgeräumt das Zimmer ist, mit einem Score von 0 bis 100:
100 = perfekt aufgeräumt, 80 = ordentlich mit Kleinigkeiten, 60 = einiges liegt herum,
40 = deutlich unordentlich, 20 = sehr unordentlich, 0 = Chaos.

Achte vor allem auf:
- Gegenstände, Kleidung oder Taschen auf dem Boden
- Kleidung, die über Stühlen oder auf dem Bett liegt
- Müll, Verpackungen, Flaschen, Geschirr
- einen vollgestellten, chaotischen Schreibtisch
- ein ungemachtes Bett (nur leicht abwerten)
Möbel, Deko und Dinge an ihrem festen Platz sind keine Unordnung.

Antworte ausschließlich mit JSON: {"score": <0-100>, "reason": "<1-2 kurze Sätze auf Deutsch, was konkret aufgeräumt werden muss, oder ein Lob>"}"""


def build_prompt(hint: str) -> str:
    if hint:
        return f"{PROMPT}\n\nZusätzliche Hinweise zum Zimmer: {hint}"
    return PROMPT


@dataclass
class Result:
    score: float | None
    reason: str
    analyzer: str


class AnalyzerError(RuntimeError):
    pass


def create(name: str, cfg: dict, storage_dir: Path):
    if name == "reference":
        from .reference import ReferenceAnalyzer

        return ReferenceAnalyzer(cfg["reference"], storage_dir / "reference")
    if name == "ollama":
        from .ollama import OllamaAnalyzer

        return OllamaAnalyzer(cfg["ollama"], cfg.get("prompt_hint", ""), bool(cfg.get("ollama_fallback", True)))
    if name == "claude":
        from .claude import ClaudeAnalyzer

        return ClaudeAnalyzer(cfg["claude"], cfg.get("prompt_hint", ""))
    raise AnalyzerError(f"Unbekannter Analyzer: {name}")


def clamp_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise AnalyzerError(f"Ungültiger Score: {value!r}") from exc
    return max(0.0, min(100.0, score))


__all__ = ["Result", "AnalyzerError", "create", "build_prompt", "clamp_score", "Image"]
