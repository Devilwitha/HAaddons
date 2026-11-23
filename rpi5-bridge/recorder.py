"""Aufzeichnung für die Fehlersuche (Messwerte → Aufzeichnung in der Oberfläche).

Sammelt während einer Aufzeichnung die Messwerte der Bridge in Zeitfenstern zu 250 ms (Zähler, Höchst- und
Mittelwerte), einzelne Ereignisse (Lücken der Capture-Card, Hänger, Schlüsselbilder, langsame Bilder) und die
Log-Zeilen. Der Browser holt das Ergebnis beim Beenden ab und schreibt es zusammen mit seinen eigenen Messwerten
in eine Textdatei. Ohne laufende Aufzeichnung kostet jeder Aufruf nur die Abfrage von `active`.
"""
import logging
import os
import threading
import time

WINDOW = 0.25                     # Sekunden pro Zeitfenster
SAMPLE_SECONDS = 0.5              # Abstand der Messung des ganzen Systems (alle Prozesse lesen kostet etwas)
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
        self._stop_sampler()
        with self._lock:
            self._clear()
            self.info = dict(info or {})
            self.active = True
        self._sampler = SystemSampler(self)
        self._sampler.start()

    def _stop_sampler(self):
        sampler = getattr(self, "_sampler", None)
        self._sampler = None
        if sampler is not None:
            sampler.stop()

    def stop(self):
        self._stop_sampler()              # vor der Sperre: der Sampler schreibt selbst mit Sperre
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


def _read(path):
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


class SystemSampler:
    """Misst während einer Aufzeichnung das ganze System (nur Linux): Auslastung aller Kerne, Anteil von Kernel und
    Interrupts, Takt, Wartezeiten (PSI) und die Prozesse mit der meisten Rechenzeit. Zeigt, ob ein anderes Programm
    dem Pi die Kerne wegnimmt oder die Bridge selbst am Anschlag rechnet. Läuft in einem eigenen Thread."""

    def __init__(self, rec):
        self.rec = rec
        self._stop = threading.Event()
        self._thread = None
        self.own = os.getpid()
        self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def start(self):
        if not os.path.isfile("/proc/stat"):
            return
        self._thread = threading.Thread(target=self._run, name="rec-sampler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @staticmethod
    def _cpu():
        text = _read("/proc/stat")
        if not text:
            return None
        values = [int(v) for v in text.split("\n", 1)[0].split()[1:]]
        values += [0] * (8 - len(values))
        return values             # user nice system idle iowait irq softirq steal

    def _procs(self):
        """{pid: (Name, Rechenzeit in Ticks, Eltern-pid)} aller Prozesse."""
        out = {}
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            text = _read(f"/proc/{name}/stat")
            if not text:
                continue
            try:
                head, rest = text.rsplit(")", 1)
                comm = head.split("(", 1)[1]
                fields = rest.split()
                out[int(name)] = (comm, int(fields[11]) + int(fields[12]), int(fields[1]))
            except (IndexError, ValueError):
                continue
        return out

    @staticmethod
    def _psi():
        out = {}
        for kind in ("cpu", "memory", "io"):
            text = _read(f"/proc/pressure/{kind}")
            if not text:
                continue
            for part in text.split("\n", 1)[0].split():
                if part.startswith("total="):
                    out[kind] = int(part[6:])
        return out

    def _own_tree(self, procs):
        children = {}
        for pid, (_comm, _ticks, ppid) in procs.items():
            children.setdefault(ppid, []).append(pid)
        own = set()
        todo = [self.own]
        while todo:
            pid = todo.pop()
            if pid in own:
                continue
            own.add(pid)
            todo.extend(children.get(pid, []))
        return own

    def _run(self):
        prev_cpu = self._cpu()
        prev_procs = self._procs()
        prev_psi = self._psi()
        last = time.monotonic()
        while not self._stop.wait(SAMPLE_SECONDS):
            now = time.monotonic()
            dt = max(0.05, now - last)
            last = now
            cpu = self._cpu()
            if cpu and prev_cpu:
                d = [a - b for a, b in zip(cpu, prev_cpu)]
                total = sum(d) or 1
                self.rec.value("p_busy", round(100.0 * (total - d[3] - d[4]) / total))
                self.rec.value("p_irq", round(100.0 * (d[5] + d[6]) / total))
                self.rec.value("p_sys", round(100.0 * d[2] / total))
                self.rec.value("p_io", round(100.0 * d[4] / total))
            prev_cpu = cpu
            procs = self._procs()
            own = self._own_tree(procs)
            own_pct = 0.0
            others = []
            parts = []
            for pid, (comm, ticks, _ppid) in procs.items():
                old = prev_procs.get(pid)
                if old is None or old[0] != comm:
                    continue
                pct = 100.0 * (ticks - old[1]) / self.hz / dt
                if pct <= 0:
                    continue
                if pid in own:
                    own_pct += pct
                    if pid != self.own:
                        parts.append([comm, pid, round(pct)])
                else:
                    others.append([comm, pid, round(pct)])
            prev_procs = procs
            others.sort(key=lambda item: -item[2])
            parts.sort(key=lambda item: -item[2])
            self.rec.value("p_self", round(own_pct))
            self.rec.value("p_kids", parts[:3])
            self.rec.value("p_top", [item for item in others[:4] if item[2] >= 5])
            freq = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
            if freq and freq.strip().isdigit():
                self.rec.value("p_mhz", int(freq) // 1000)
            psi = self._psi()
            for kind, key in (("cpu", "p_psi_cpu"), ("memory", "p_psi_mem"), ("io", "p_psi_io")):
                if kind in psi and kind in prev_psi:
                    self.rec.value(key, round((psi[kind] - prev_psi[kind]) / 1000.0))
            prev_psi = psi


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
