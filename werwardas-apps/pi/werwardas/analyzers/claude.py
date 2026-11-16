"""Bewertung über die Claude API (Cloud). Benötigt einen API-Key von console.anthropic.com."""

from __future__ import annotations

import base64
import json

from PIL import Image

from . import AnalyzerError, Result, build_prompt, clamp_score
from ..camera import to_jpeg

SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["score", "reason"],
    "additionalProperties": False,
}


class ClaudeAnalyzer:
    name = "claude"

    def __init__(self, cfg: dict, hint: str):
        try:
            import anthropic
        except ImportError as exc:
            raise AnalyzerError(
                "Paket 'anthropic' fehlt. Installieren mit: sudo /opt/werwardas/venv/bin/pip install anthropic"
            ) from exc
        self._anthropic = anthropic
        # Ohne api_key in der Config liest das SDK ANTHROPIC_API_KEY aus der Umgebung.
        self.client = anthropic.Anthropic(api_key=cfg.get("api_key") or None)
        self.model = cfg["model"]
        self.prompt = build_prompt(hint)

    def analyze(self, img: Image.Image) -> Result:
        image_b64 = base64.standard_b64encode(to_jpeg(img, max_width=1568)).decode()
        anthropic = self._anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": SCHEMA},
                },
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                            },
                            {"type": "text", "text": self.prompt},
                        ],
                    }
                ],
            )
        except anthropic.AuthenticationError as exc:
            raise AnalyzerError("Claude API-Key ungültig") from exc
        except anthropic.RateLimitError as exc:
            raise AnalyzerError("Claude API: Rate-Limit erreicht") from exc
        except anthropic.APIStatusError as exc:
            raise AnalyzerError(f"Claude API Fehler {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AnalyzerError(f"Claude API nicht erreichbar: {exc}") from exc

        if response.stop_reason == "refusal":
            raise AnalyzerError("Claude hat die Bewertung abgelehnt")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AnalyzerError(f"Unerwartete Antwort von Claude: {text[:200]}") from exc
        return Result(round(clamp_score(data.get("score"))), str(data.get("reason", "")).strip(), self.name)
