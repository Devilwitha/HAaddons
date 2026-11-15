"""Emulation eines Pro Controllers für die Spielkonsole über Bluetooth (nxbt / BlueZ).

- Verwaltet Tasten und Analogsticks (-100 bis 100) und schickt sie mit input_rate_hz an nxbt.
- Safety-Watchdog: Kommen länger als watchdog_seconds keine Heartbeats/Eingaben vom Client,
  werden alle Tasten losgelassen und die Sticks auf (0, 0) gesetzt.
- Merkt sich die Bluetooth-Adresse der gekoppelten Konsole und verbindet sich nach einem
  Neustart von selbst wieder, solange die Konsole an ist.

nxbt braucht Root-Rechte (der systemd-Dienst läuft als root). Fehlt nxbt (z. B. beim Testen
auf einem anderen Rechner) oder ist der Controller abgeschaltet, läuft die Bridge im
Testmodus weiter: Eingaben werden angenommen, aber nicht gesendet.
"""
import asyncio
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("bridge.controller")

BUTTON_KEYS = {
    "A": "A", "B": "B", "X": "X", "Y": "Y",
    "L": "L", "R": "R", "ZL": "ZL", "ZR": "ZR",
    "PLUS": "PLUS", "MINUS": "MINUS", "HOME": "HOME", "CAPTURE": "CAPTURE",
    "DPAD_UP": "DPAD_UP", "DPAD_DOWN": "DPAD_DOWN", "DPAD_LEFT": "DPAD_LEFT", "DPAD_RIGHT": "DPAD_RIGHT",
}
STICK_PRESS = {"LS": "L_STICK", "RS": "R_STICK"}
VALID_BUTTONS = set(BUTTON_KEYS) | set(STICK_PRESS)
# Bluetooth-Name, unter dem sich die Konsole bei BlueZ meldet (technisch nötig zum Wiederfinden)
SWITCH_ALIAS_PREFIX = "NINTENDO SWITCH"


def clamp_stick(value):
    """Stick-Paar auf -100..100 begrenzen; ungültige Werte werden zu (0, 0)."""
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return (0.0, 0.0)
    if x != x or y != y:              # NaN
        return (0.0, 0.0)
    return (max(-100.0, min(100.0, x)), max(-100.0, min(100.0, y)))


class ControllerEmulator:
    def __init__(self, cfg, dry_run=False):
        self.enabled = bool(cfg.get("enabled", True)) and not dry_run
        self.watchdog_seconds = float(cfg.get("watchdog_seconds", 2.0))
        self.input_rate_hz = max(30, int(cfg.get("input_rate_hz", 120)))
        self.reconnect_file = Path(cfg.get("reconnect_file", "/var/lib/switch2-bridge/switch_addresses.json"))
        self.nx = None
        self.nxbt = None
        self.index = None
        self.error = "" if self.enabled else "Testmodus (Controller-Emulation aus)"
        self.buttons = frozenset()
        self.ls = (0.0, 0.0)
        self.rs = (0.0, 0.0)
        self.last_heartbeat = 0.0
        self.watchdog_tripped = True
        self._saved_connected = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ Start / Kopplung
    async def start(self):
        if not self.enabled:
            log.info("Controller-Emulation aus - Testmodus")
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._start_nxbt)
        except Exception as err:          # nxbt meldet Fehler als allgemeine Exceptions
            self.error = f"{err.__class__.__name__}: {err}"
            log.error("nxbt konnte nicht gestartet werden: %s", self.error)

    def _start_nxbt(self):
        import nxbt
        self.nxbt = nxbt
        self.nx = nxbt.Nxbt()
        addresses = self._load_addresses()
        if addresses:
            log.info("Verbinde erneut mit gekoppelter Konsole: %s", ", ".join(addresses))
        else:
            log.info("Noch keine Konsole gekoppelt - warte auf Kopplung (Griffweise/Reihenfolge ändern)")
        self._create(addresses or None)

    def _create(self, reconnect_address):
        if self.index is not None:
            try:
                self.nx.remove_controller(self.index)
            except Exception as err:
                log.warning("Alten Controller entfernen fehlgeschlagen: %s", err)
            self.index = None
        self._saved_connected = False
        self.index = self.nx.create_controller(self.nxbt.PRO_CONTROLLER, reconnect_address=reconnect_address)
        self.error = ""

    async def pair(self):
        """Neue Kopplung: Controller ohne Wiederverbindungsadresse neu anlegen.
        Auf der Konsole muss dazu "Controller -> Griffweise/Reihenfolge ändern" offen sein."""
        if not self.enabled:
            return False, "Controller-Emulation ist aus (Testmodus)."
        if self.nx is None:
            return False, "nxbt läuft nicht: " + (self.error or "unbekannter Fehler")
        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._create, None)
            except Exception as err:
                self.error = f"{err.__class__.__name__}: {err}"
                log.error("Kopplung fehlgeschlagen: %s", self.error)
                return False, "Kopplung fehlgeschlagen: " + self.error
        log.info("Kopplungsmodus aktiv")
        return True, "Kopplung gestartet – auf der Konsole „Controller → Griffweise/Reihenfolge ändern“ öffnen."

    # ------------------------------------------------------------------ Adressen merken
    def _load_addresses(self):
        try:
            data = json.loads(self.reconnect_file.read_text(encoding="utf-8"))
            return [a for a in data.get("addresses", []) if isinstance(a, str)]
        except (OSError, ValueError):
            return []

    def _save_addresses(self):
        addresses = set()
        try:
            addresses.update(self.nx.get_switch_addresses())
        except Exception as err:
            log.debug("get_switch_addresses: %s", err)
        try:
            addresses.update(self._find_switch_devices())
        except Exception as err:
            log.debug("Konsole suchen: %s", err)
        if not addresses:
            return
        self.reconnect_file.parent.mkdir(parents=True, exist_ok=True)
        self.reconnect_file.write_text(json.dumps({"addresses": sorted(addresses)}, indent=2), encoding="utf-8")
        log.info("Adresse der Konsole gespeichert: %s", ", ".join(sorted(addresses)))

    def _find_switch_devices(self):
        """Alle gekoppelten BlueZ-Geräte, deren Bluetooth-Name mit SWITCH_ALIAS_PREFIX beginnt."""
        import dbus
        bus = dbus.SystemBus()
        manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        found = []
        for _path, interfaces in manager.GetManagedObjects().items():
            device = interfaces.get("org.bluez.Device1")
            if not device:
                continue
            alias = str(device.get("Alias", "")).upper()
            if alias.startswith(SWITCH_ALIAS_PREFIX) and (device.get("Paired") or device.get("Connected")):
                found.append(str(device.get("Address")))
        return found

    # ------------------------------------------------------------------ Eingaben
    def heartbeat(self):
        self.last_heartbeat = time.monotonic()
        self.watchdog_tripped = False

    def set_inputs(self, buttons, ls, rs):
        new = (frozenset(b for b in buttons if b in VALID_BUTTONS), clamp_stick(ls), clamp_stick(rs))
        if not self.enabled and new != (self.buttons, self.ls, self.rs):
            log.info("Testmodus - Eingabe: Tasten=%s LS=%s RS=%s", ",".join(sorted(new[0])) or "-", new[1], new[2])
        self.buttons, self.ls, self.rs = new
        self.heartbeat()

    def release_all(self):
        self.buttons = frozenset()
        self.ls = (0.0, 0.0)
        self.rs = (0.0, 0.0)

    def _is_neutral(self):
        return not self.buttons and self.ls == (0.0, 0.0) and self.rs == (0.0, 0.0)

    def _packet(self):
        packet = self.nx.create_input_packet()
        for name in self.buttons:
            if name in BUTTON_KEYS:
                packet[BUTTON_KEYS[name]] = True
            elif name in STICK_PRESS:
                packet[STICK_PRESS[name]]["PRESSED"] = True
        packet["L_STICK"]["X_VALUE"] = int(round(self.ls[0]))
        packet["L_STICK"]["Y_VALUE"] = int(round(self.ls[1]))
        packet["R_STICK"]["X_VALUE"] = int(round(self.rs[0]))
        packet["R_STICK"]["Y_VALUE"] = int(round(self.rs[1]))
        return packet

    # ------------------------------------------------------------------ Status
    def status(self):
        if not self.enabled:
            return {"state": "unavailable", "detail": self.error}
        if self.nx is None or self.index is None:
            return {"state": "unavailable" if self.error else "initializing", "detail": self.error}
        try:
            info = self.nx.state[self.index]
            state = info.get("state", "unknown")
            detail = info.get("errors") or ""
        except Exception as err:                      # Index weg oder Manager-Prozess beendet
            return {"state": "crashed", "detail": str(err)}
        if state == "connected" and not self._saved_connected:
            self._saved_connected = True
            try:
                self._save_addresses()
            except OSError as err:
                log.warning("Adresse der Konsole nicht gespeichert: %s", err)
        return {"state": state, "detail": str(detail) if detail else ""}

    # ------------------------------------------------------------------ Sendeschleife
    async def run(self):
        """Schickt den aktuellen Zustand mit input_rate_hz an nxbt und überwacht die Heartbeats."""
        period = 1.0 / self.input_rate_hz
        last_sent = None
        last_time = 0.0
        while True:
            started = time.monotonic()
            if not self.watchdog_tripped and started - self.last_heartbeat > self.watchdog_seconds:
                self.watchdog_tripped = True
                if not self._is_neutral():
                    log.warning("Keine Heartbeats seit %.1f s - alle Tasten losgelassen, Sticks auf (0, 0)",
                                self.watchdog_seconds)
                self.release_all()
            if self.nx is not None and self.index is not None:
                current = (self.buttons, self.ls, self.rs)
                try:
                    if self.nx.state[self.index].get("state") == "connected":
                        # Änderungen sofort, sonst den gleichen Zustand alle 50 ms erneut setzen
                        if current != last_sent or started - last_time > 0.05:
                            self.nx.set_controller_input(self.index, self._packet())
                            last_sent = current
                            last_time = started
                except Exception as err:
                    log.debug("Eingabe senden: %s", err)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, period - elapsed))

    async def stop(self):
        self.release_all()
        if self.nx is not None and self.index is not None:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self.nx.remove_controller, self.index)
            except Exception as err:
                log.debug("Controller entfernen: %s", err)
