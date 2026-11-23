"""Aufzeichnung für die Fehlersuche (Messwerte → Aufzeichnung in der Oberfläche).

Sammelt während einer Aufzeichnung die Messwerte der Bridge in Zeitfenstern zu 250 ms (Zähler, Höchst- und
Mittelwerte), einzelne Ereignisse (Lücken der Capture-Card, Hänger, Schlüsselbilder, langsame Bilder) und die
Log-Zeilen. Der Browser holt das Ergebnis beim Beenden ab und schreibt es zusammen mit seinen eigenen Messwerten
in eine Textdatei. Ohne laufende Aufzeichnung kostet jeder Aufruf nur die Abfrage von `active`.
"""
import logging
import threading
import time

WINDOW = 0.25                     # Sekunden pro Zeitfenster
MAX_SECONDS = 15 * 60             # danach endet die Aufzeichnung von selbst
MAX_EVENTS = 4000
MAX_LOGS = 2000


class Recorder:
    def __init__(self):
        self.active = False
        self._lock = threading.Lock()
        self._clear()

    def _clear(self):
        self.t0 = time.monotonic()
        self.started = time.time()
        self.ended = None
        self.info = {}
        self.windows = {}
        self.events = []
        self.logs = []
        self.lost = 0

    # ------------------------------------------------------------------ Steuerung
    def start(self, info=None):
        with self._lock:
            self._clear()
            self.info = dict(info or {})
            self.active = True

    def stop(self):
        with self._lock:
            if self.active:
                self.ended = time.monotonic()
            self.active = False
            windows = []
            for index in sorted(self.windows):
                values = {}
                for key, value in self.windows[index].items():
                    values[key] = round(value, 1) if isinstance(value, float) else value
                windows.append([index, values])
            return {
                "info": self.info,
                "started": self.started,
                "duration": round((self.ended or time.monotonic()) - self.t0, 2),
                "window": WINDOW,
                "windows": windows,
                "events": list(self.events),
                "logs": list(self.logs),
                "lost": self.lost,
            }

    # ------------------------------------------------------------------ Werte
    def _window(self):
        """Aktuelles Zeitfenster (nur mit gehaltener Sperre aufrufen); None, wenn die Zeit um ist."""
        now = time.monotonic()
        if now - self.t0 > MAX_SECONDS:
            self.active = False
            self.ended = now
            return None
        index = int((now - self.t0) / WINDOW)
        window = self.windows.get(index)
        if window is None:
            window = self.windows[index] = {}
        return window

    def count(self, key, amount=1):
        if not self.active:
            return
        with self._lock:
            window = self._window()
            if window is not None:
                window[key] = window.get(key, 0) + amount

    def sample(self, key, ms):
        """Millisekunden-Wert: je Fenster Höchstwert (key), Summe (key_s) und Anzahl (key_n)."""
        if not self.active:
            return
        ms = float(ms)
        with self._lock:
            window = self._window()
            if window is not None:
                window[key] = max(window.get(key, 0.0), ms)
                window[key + "_s"] = window.get(key + "_s", 0.0) + ms
                window[key + "_n"] = window.get(key + "_n", 0) + 1

    def value(self, key, value):
        if not self.active or value is None:
            return
        with self._lock:
            window = self._window()
            if window is not None:
                window[key] = value

    def event(self, kind, text):
        if not self.active:
            return
        with self._lock:
            if len(self.events) >= MAX_EVENTS:
                self.lost += 1
                return
            self.events.append([round(time.monotonic() - self.t0, 3), kind, text])

    def log(self, line):
        if not self.active:
            return
        with self._lock:
            if len(self.logs) >= MAX_LOGS:
                self.lost += 1
                return
            self.logs.append([round(time.monotonic() - self.t0, 3), line])


class RecordHandler(logging.Handler):
    """Gibt Log-Zeilen der Bridge während einer Aufzeichnung an diese weiter."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        if REC.active:
            try:
                REC.log(self.format(record))
            except Exception:             # Logging darf die Bridge nie stören
                pass


REC = Recorder()
