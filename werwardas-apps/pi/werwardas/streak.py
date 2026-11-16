"""Zählt Tage in Folge ohne 'unordentlich' (Streak) inklusive Rekord, persistiert auf Platte."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


class StreakTracker:
    def __init__(self, path: Path):
        self.path = path
        self.day: date | None = None  # erst beim ersten update() bekannt
        self.had_check = False
        self.messy_today = False
        self.streak = 0
        self.record = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.day = date.fromisoformat(saved["day"])
            self.had_check = bool(saved["had_check"])
            self.messy_today = bool(saved["messy_today"])
            self.streak = int(saved["streak"])
            self.record = int(saved["record"])
        except (OSError, ValueError, KeyError) as exc:
            log.warning("streak.json nicht lesbar: %s", exc)

    def _save(self) -> None:
        saved = {
            "day": self.day.isoformat(),
            "had_check": self.had_check,
            "messy_today": self.messy_today,
            "streak": self.streak,
            "record": self.record,
        }
        self.path.write_text(json.dumps(saved), encoding="utf-8")

    def _close_day(self) -> None:
        # Kein einziger echter Check an diesem Tag (z. B. Dienst war aus) -> zählt weder für noch gegen den Streak.
        if self.had_check:
            if self.messy_today:
                self.streak = 0
            else:
                self.streak += 1
                self.record = max(self.record, self.streak)
        self.had_check = False
        self.messy_today = False

    def update(self, tidy: bool | None, now: datetime | None = None) -> None:
        """Mit dem Ergebnis jeder Prüfung aufrufen. tidy=None (Fehler) zählt nicht."""
        today = (now or datetime.now()).date()
        if self.day is None:
            self.day = today
        while self.day < today:
            self._close_day()
            self.day += timedelta(days=1)
        if tidy is not None:
            self.had_check = True
            if not tidy:
                self.messy_today = True
        self._save()
