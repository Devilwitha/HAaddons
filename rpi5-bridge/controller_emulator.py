"""Emulation eines Pro Controllers für die Spielkonsole über Bluetooth (nxbt / BlueZ).

- Verwaltet Tasten und Analogsticks (-100 bis 100) und schickt sie mit input_rate_hz an nxbt.
- Safety-Watchdog: Kommen länger als watchdog_seconds keine Heartbeats/Eingaben vom Client,
  werden alle Tasten losgelassen und die Sticks auf (0, 0) gesetzt.
- Merkt sich die Bluetooth-Adresse der gekoppelten Konsole und verbindet sich nach einem
  Neustart oder Abbruch (Konsole im Standby) von selbst wieder, sobald sie erreichbar ist
  (nxbt_patch.py – nxbt allein fiele nach einer Sekunde in den Kopplungsmodus).
- Pausiert: Will niemand steuern (in keinem offenen Game Remote Touch-Steuerung oder Controller an), legt die
  Bridge den Controller ab – die Bluetooth-Verbindung zur Konsole wird getrennt, es gibt keine
  Verbindungsversuche mehr. Sobald wieder jemand steuern will, verbindet er sich neu.
- Stürzt nxbt ab (z. B. weil der Bluetooth-Dienst kurz weg war), wird nxbt komplett neu gestartet –
  sonst bliebe der Adapter belegt und jede weitere Kopplung endete mit "No adapters available".

nxbt braucht Root-Rechte (der systemd-Dienst läuft als root). Fehlt nxbt (z. B. beim Testen
auf einem anderen Rechner) oder ist der Controller abgeschaltet, läuft die Bridge im
Testmodus weiter: Eingaben werden angenommen, aber nicht gesendet.
"""
import asyncio
import atexit
import json
import logging
import time
from pathlib import Path

from recorder import REC

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
PAIR_BUSY_SECONDS = 90              # so lange gilt ein Kopplungsversuch als laufend
RECOVER_AFTER = 3.0                 # nach einem Absturz so lange warten, dann nxbt neu starten
RECOVER_MIN_GAP = 30.0              # höchstens alle 30 s automatisch neu starten
STOP_SECONDS = 6                    # höchstens so lange auf nxbt warten beim Beenden
RESTART_ATTEMPTS = 6                # Versuche beim Neustart (der Bluetooth-Dienst startet dabei neu)


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
        self._changed = None              # asyncio.Event: neue Eingabe -> sofort senden
        self._connected = False
        self._connected_checked = 0.0
        self._pairing_since = 0.0
        self._recovering = False
        self._last_recover = 0.0
        self._crashed_since = 0.0
        self._press_logged = 0.0
        self.paused = False               # Controller abgelegt, weil niemand steuern will
        self.wanted = lambda: True        # von der Bridge: will gerade jemand steuern?

    # ------------------------------------------------------------------ Start / Kopplung
    async def start(self):
        if not self.enabled:
            log.info("Controller-Emulation aus - Testmodus")
            return
        loop = asyncio.get_running_loop()
        async with self._lock:            # Koppeln wartet, bis der Start fertig ist
            try:
                await loop.run_in_executor(None, self._start_nxbt)
            except Exception as err:      # nxbt meldet Fehler als allgemeine Exceptions
                self.error = f"{err.__class__.__name__}: {err}"
                log.error("nxbt konnte nicht gestartet werden: %s", self.error)

    def _start_nxbt(self):
        import nxbt
        import nxbt_patch
        nxbt_patch.install_nxbt()             # Wiederverbinden statt Kopplungsmodus (vor nxbt.Nxbt())
        self.nxbt = nxbt
        addresses = self._load_addresses()
        if addresses:
            log.info("Verbinde erneut mit gekoppelter Konsole: %s", ", ".join(addresses))
        else:
            log.info("Noch keine Konsole gekoppelt - warte auf Kopplung (Griffweise/Reihenfolge ändern)")
        # Niemand will steuern (kein Game Remote offen, Touch aus): nxbt starten, aber ohne Controller –
        # sonst verbände er sich kurz mit der Konsole und würde gleich wieder getrennt
        self._restart_nxbt(addresses or None, create=self.wanted())
        if self.index is None:
            self.paused = True
            log.info("Niemand steuert - Bluetooth-Controller bleibt aus, bis Touch-Steuerung oder Controller an ist")

    def _shutdown_nxbt(self):
        """Beendet die Prozesse von nxbt und gibt damit den Bluetooth-Adapter frei."""
        old = self.nx
        self.nx = None
        self.index = None
        self._connected = False
        if old is None:
            return
        try:
            atexit.unregister(old._on_exit)
        except AttributeError:
            pass
        try:
            if old.controllers.is_alive():
                old.controllers.terminate()
                old.controllers.join(5)
        except Exception as err:
            log.debug("nxbt-Prozess beenden: %s", err)
        try:
            old.resource_manager.shutdown()
        except Exception as err:
            log.debug("nxbt-Manager beenden: %s", err)

    def _restart_nxbt(self, reconnect_address, create=True):
        """nxbt komplett neu starten und einen Controller anlegen. nxbt startet dabei den Bluetooth-Dienst
        neu; bis der Adapter wieder da ist, meldet es "No adapters available" – darum mehrere Versuche."""
        self._shutdown_nxbt()
        last = None
        for attempt in range(1, RESTART_ATTEMPTS + 1):
            try:
                self.nx = self.nxbt.Nxbt()
                if create:
                    self._create(reconnect_address)
                return
            except Exception as err:
                last = err
                log.warning("nxbt-Start Versuch %d/%d fehlgeschlagen: %s", attempt, RESTART_ATTEMPTS, err)
                self._shutdown_nxbt()
                time.sleep(3)
        raise last

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
        if reconnect_address is None:
            self._pairing_since = time.monotonic()      # Kopplungsmodus: Konsole kann sich jetzt verbinden

    def _remove(self):
        """Controller ablegen: der Controller-Prozess endet, die Verbindung zur Konsole ist weg."""
        if self.nx is not None and self.index is not None:
            try:
                self.nx.remove_controller(self.index)
            except Exception as err:              # abgestürzter Controller: nxbt ganz beenden
                log.warning("Controller ablegen fehlgeschlagen (%s) - beende nxbt", err)
                self._shutdown_nxbt()
        self.index = None
        self._connected = False

    def can_pause(self):
        """Nicht mitten in einer Kopplung oder einem Neustart ablegen."""
        if not self.enabled or self.paused or self._lock.locked() or self._recovering:
            return False
        if self.nx is None or self.index is None:
            return self.nx is not None
        return not (self._state() == "connecting" and time.monotonic() - self._pairing_since < PAIR_BUSY_SECONDS)

    async def pause(self):
        """Niemand will steuern: Controller ablegen und keine Verbindungsversuche mehr."""
        if not self.enabled or self.paused:
            return
        self.status()                                 # frisch gekoppelt: Adresse vorher noch speichern
        async with self._lock:
            self.release_all()
            await asyncio.get_running_loop().run_in_executor(None, self._remove)
            self.paused = True
        log.info("Touch-Steuerung und Controller aus - Bluetooth-Controller getrennt, keine Verbindungsversuche mehr")
        REC.event("controller", "Bluetooth-Controller abgelegt (niemand steuert)")

    async def resume(self):
        """Wieder jemand am Steuern: mit der gekoppelten Konsole verbinden (ohne gekoppelte Konsole: Kopplungsmodus)."""
        if not self.paused:
            return
        async with self._lock:
            self.paused = False
            addresses = self._load_addresses() or None
            log.info("Touch-Steuerung bzw. Controller an - Bluetooth-Controller %s",
                     "verbindet mit der Konsole" if addresses else "wartet auf Kopplung")
            REC.event("controller", "Bluetooth-Controller wieder angelegt")
            loop = asyncio.get_running_loop()
            try:
                if self.nx is None:
                    await loop.run_in_executor(None, self._restart_nxbt, addresses)
                else:
                    try:
                        await loop.run_in_executor(None, self._create, addresses)
                    except Exception as err:
                        log.warning("Controller anlegen fehlgeschlagen (%s) - starte nxbt neu", err)
                        await loop.run_in_executor(None, self._restart_nxbt, addresses)
            except Exception as err:
                self.error = f"{err.__class__.__name__}: {err}"
                log.error("Bluetooth-Controller konnte nicht angelegt werden: %s", self.error)

    def _state(self):
        try:
            return self.nx.state[self.index].get("state", "unknown")
        except Exception:                 # Index weg oder Manager-Prozess beendet
            return "crashed"

    async def pair(self):
        """Neue Kopplung: Controller ohne Wiederverbindungsadresse neu anlegen.
        Auf der Konsole muss dazu "Controller -> Griffweise/Reihenfolge ändern" offen sein."""
        if not self.enabled:
            return False, "Controller-Emulation ist aus (Testmodus)."
        if self._lock.locked():
            return True, "Bluetooth wird gerade vorbereitet – bitte einen Moment warten und nicht erneut drücken."
        if self.nxbt is None:
            return False, "nxbt läuft nicht: " + (self.error or "unbekannter Fehler")
        if self.nx is None:
            state = "crashed"
        elif self.index is None:
            state = "paused"
        else:
            state = self._state()
        if state == "connected":
            # Nicht ablehnen: nxbt meldet „connected“, solange die Bluetooth-Verbindung steht – auch wenn die Konsole
            # den Controller gar nicht mehr benutzt. Wer koppeln drückt, will neu koppeln.
            log.info("Konsole gilt als verbunden - Verbindung wird für eine neue Kopplung getrennt")
        if state == "connecting" and time.monotonic() - self._pairing_since < PAIR_BUSY_SECONDS:
            return True, ("Kopplung läuft bereits – auf der Konsole „Controller → Griffweise/Reihenfolge ändern“ "
                          "offen lassen und bis zu einer Minute warten.")
        async with self._lock:
            self.paused = False                       # Koppeln heißt: es soll gesteuert werden
            loop = asyncio.get_running_loop()
            try:
                if state == "crashed":
                    # Abgestürzter Versuch: nxbt neu starten, damit der Adapter wieder frei ist
                    log.info("Controller-Emulation abgestürzt - starte nxbt für die Kopplung neu")
                    await loop.run_in_executor(None, self._restart_nxbt, None)
                else:
                    try:
                        await loop.run_in_executor(None, self._create, None)
                    except Exception as err:
                        log.warning("Kopplung ohne Neustart fehlgeschlagen (%s) - starte nxbt neu", err)
                        await loop.run_in_executor(None, self._restart_nxbt, None)
            except Exception as err:
                self.error = f"{err.__class__.__name__}: {err}"
                log.error("Kopplung fehlgeschlagen: %s", self.error)
                return False, ("Kopplung fehlgeschlagen: " + self.error +
                               " – Bluetooth am Pi prüfen (sudo systemctl status bluetooth).")
        log.info("Kopplungsmodus aktiv")
        return True, ("Kopplung gestartet – auf der Konsole „Controller → Griffweise/Reihenfolge ändern“ öffnen "
                      "und bis zu einer Minute warten.")

    def _check_crash(self, state, now):
        """Nach einem Absturz nxbt nach kurzer Zeit von selbst neu starten (mit Wiederverbindung)."""
        if state != "crashed":
            self._crashed_since = 0.0
            return
        if not self._crashed_since:
            self._crashed_since = now
            return
        if (self._recovering or self._lock.locked() or now - self._crashed_since < RECOVER_AFTER
                or now - self._last_recover < RECOVER_MIN_GAP):
            return
        self._recovering = True
        self._last_recover = now
        asyncio.get_running_loop().create_task(self._recover())

    async def _recover(self):
        try:
            async with self._lock:
                addresses = self._load_addresses()
                log.warning("Controller-Emulation abgestürzt - starte nxbt neu%s",
                            " und verbinde mit der Konsole" if addresses else "")
                await asyncio.get_running_loop().run_in_executor(None, self._restart_nxbt, addresses or None)
        except Exception as err:
            self.error = f"{err.__class__.__name__}: {err}"
            log.error("Neustart von nxbt fehlgeschlagen: %s", self.error)
        finally:
            self._recovering = False
            self._crashed_since = 0.0

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
        if new[0] and new[0] != self.buttons:
            REC.count("in_press")
            now = time.monotonic()
            if self.enabled and now - self._press_logged > 10:   # zur Fehlersuche, höchstens alle 10 s
                self._press_logged = now
                log.info("Taste %s angekommen - Konsole: %s", ",".join(sorted(new[0])),
                         "verbunden" if self._connected else self.status().get("state"))
        changed = new != (self.buttons, self.ls, self.rs)
        self.buttons, self.ls, self.rs = new
        self.heartbeat()
        if changed and self._changed is not None:
            self._changed.set()

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
        if self.paused and not self._lock.locked():
            return {"state": "paused", "detail": ""}
        if self._recovering or self._lock.locked():
            return {"state": "initializing",
                    "detail": "Bluetooth wird gestartet …" if self.nx is None else "Bluetooth wird neu gestartet …"}
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
        self._changed = asyncio.Event()
        while True:
            self._changed.clear()
            started = time.monotonic()
            if not self.watchdog_tripped and started - self.last_heartbeat > self.watchdog_seconds:
                self.watchdog_tripped = True
                if not self._is_neutral():
                    log.warning("Keine Heartbeats seit %.1f s - alle Tasten losgelassen, Sticks auf (0, 0)",
                                self.watchdog_seconds)
                self.release_all()
            if self.nx is not None and self.index is not None and not self._lock.locked():
                current = (self.buttons, self.ls, self.rs)
                try:
                    # Verbindungszustand nur alle 250 ms abfragen (jede Abfrage geht an einen anderen Prozess)
                    if started - self._connected_checked > 0.25:
                        state = self._state()
                        REC.sample("nxbt", (time.monotonic() - started) * 1000.0)
                        self._connected = state == "connected"
                        self._connected_checked = started
                        self._check_crash(state, started)
                    if self._connected:
                        # Änderungen sofort, sonst den gleichen Zustand alle 50 ms erneut setzen
                        if current != last_sent or started - last_time > 0.05:
                            sent_at = time.monotonic()
                            self.nx.set_controller_input(self.index, self._packet())
                            if current[0] and current != last_sent:
                                REC.count("nx_press")
                            spent = (time.monotonic() - sent_at) * 1000.0
                            REC.sample("nxbt", spent)
                            if spent > 15:
                                REC.event("nxbt", f"Eingabe an nxbt dauerte {spent:.0f} ms (blockiert die Bridge)")
                            last_sent = current
                            last_time = started
                except Exception as err:
                    log.debug("Eingabe senden: %s", err)
            elapsed = time.monotonic() - started
            # Bis zum nächsten Takt warten - neue Eingaben wecken die Schleife sofort
            try:
                await asyncio.wait_for(self._changed.wait(), max(0.0, period - elapsed))
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        """nxbt-Prozesse beenden, ohne dessen atexit-Handler: der startet BlueZ neu, wirft dabei Fehler
        (reset_address, TypeError) und hielt das Beenden auf, bis systemd nach 90 s hart abbrach."""
        self.release_all()
        if self.nx is None:
            return
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, self._shutdown_nxbt), STOP_SECONDS)
        except asyncio.TimeoutError:
            log.warning("nxbt hat sich nicht in %d s beendet - wird mit dem Dienst beendet", STOP_SECONDS)
        except Exception as err:
            log.debug("nxbt beenden: %s", err)
