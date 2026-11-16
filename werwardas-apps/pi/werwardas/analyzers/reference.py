"""Offline-Bewertung ohne KI: Vergleich mit Fotos vom aufgeräumten Zimmer.

Das Bild wird in ein Raster aus Kacheln zerlegt. Für jede Kachel wird gezählt, wie viele
Pixel sich gegenüber den Referenzfotos verändert haben. Je mehr Kacheln sich deutlich verändert haben,
desto niedriger der Score. Helligkeit und Kontrast werden vorher angeglichen, damit Tageslicht und
Lampe weniger stören.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import Result

log = logging.getLogger(__name__)

SIZE = (320, 240)
GRID = (16, 12)
MAX_REFERENCES = 5
TILE_CHANGED = 0.15  # Kachel gilt als verändert, wenn 15 % ihrer Pixel anders sind


def _prepare(img: Image.Image) -> np.ndarray:
    gray = ImageOps.grayscale(img).resize(SIZE, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.5))
    arr = np.asarray(gray, dtype=np.float32)
    # Helligkeit und Kontrast angleichen (Mittelwert/Streuung), damit Licht kaum zählt.
    return (arr - arr.mean()) / max(float(arr.std()), 1.0) * 40.0 + 128.0


def _tiles(arr: np.ndarray) -> np.ndarray:
    gw, gh = GRID
    h, w = arr.shape
    th, tw = h // gh, w // gw
    return arr[: th * gh, : tw * gw].reshape(gh, th, gw, tw).mean(axis=(1, 3))


def _mask(ignore_areas: list) -> np.ndarray:
    gw, gh = GRID
    mask = np.ones((gh, gw), dtype=bool)
    for area in ignore_areas or []:
        x, y, w, h = (float(v) / 100.0 for v in area)
        x0, x1 = int(x * gw), int(np.ceil((x + w) * gw))
        y0, y1 = int(y * gh), int(np.ceil((y + h) * gh))
        mask[y0:y1, x0:x1] = False
    return mask


def changed_fraction(img: Image.Image, ref: Image.Image, sensitivity: float, mask: np.ndarray) -> float:
    """Anteil der Kacheln (0..1), in denen sich ein spürbarer Teil der Pixel verändert hat."""
    diff = np.abs(_prepare(img) - _prepare(ref))
    changed_pixels = _tiles((diff > sensitivity).astype(np.float32))
    changed = (changed_pixels > TILE_CHANGED) & mask
    return float(changed.sum()) / max(1, int(mask.sum()))


def difference(img: Image.Image, other: Image.Image, sensitivity: float, ignore_areas: list | None = None) -> float:
    """Anteil (0..1) der Kacheln, die sich zwischen zwei Fotos unterscheiden.

    0 = praktisch identisch. Nutzt denselben Helligkeits-/Kontrastausgleich wie der
    Referenzvergleich, damit reine Lichtänderungen kaum zählen. Damit lässt sich erkennen,
    ob sich das Zimmer seit der letzten Bewertung überhaupt verändert hat.
    """
    return changed_fraction(img, other, sensitivity, _mask(ignore_areas))


class ReferenceAnalyzer:
    name = "reference"

    def __init__(self, cfg: dict, ref_dir: Path):
        self.cfg = cfg
        self.ref_dir = ref_dir
        self.ref_dir.mkdir(parents=True, exist_ok=True)

    def references(self) -> list[Path]:
        return sorted(self.ref_dir.glob("ref_*.jpg"))

    def add_reference(self, img: Image.Image) -> int:
        img.convert("RGB").save(self.ref_dir / f"ref_{int(time.time())}.jpg", quality=90)
        refs = self.references()
        for old in refs[:-MAX_REFERENCES]:
            old.unlink(missing_ok=True)
        return min(len(refs), MAX_REFERENCES)

    def analyze(self, img: Image.Image) -> Result:
        refs = self.references()
        if not refs:
            return Result(
                None,
                "Noch kein Referenzfoto. Räum einmal auf und drück in Home Assistant "
                "auf „Als aufgeräumt speichern“.",
                self.name,
            )
        mask = _mask(self.cfg.get("ignore_areas"))
        sens = float(self.cfg["sensitivity"])
        # Das ähnlichste Referenzfoto zählt (z. B. Tag- und Nachtaufnahme).
        fraction = min(changed_fraction(img, Image.open(p), sens, mask) for p in refs)
        score = max(0.0, 100.0 - fraction * 100.0 * float(self.cfg["gain"]))
        pct = round(fraction * 100)
        if score >= 80:
            reason = "Sieht aus wie im aufgeräumten Zustand."
        else:
            reason = f"Etwa {pct} % des Zimmers sehen anders aus als im aufgeräumten Zustand."
        log.debug("Referenzvergleich: %.3f verändert -> Score %.0f", fraction, score)
        return Result(round(score), reason, self.name)
