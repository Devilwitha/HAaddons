"""Korrekturen an nxbt 0.1.4 (ungepflegt): Wiederverbinden statt Kopplungsmodus.

nxbt gibt das Wiederverbinden sehr schnell auf: beim Start nach einem einzigen Versuch, nach einem
Verbindungsabbruch (Konsole im Standby, außer Reichweite) nach zwei Versuchen in etwa einer Sekunde. Danach
wartet es im Kopplungsmodus („connecting“) – die Konsole verbindet sich dann aber nur noch im Menü
„Griffweise/Reihenfolge ändern“, nach dem Aufwecken bleibt die Steuerung also tot.

Mit diesem Patch versucht der Controller es mit der bekannten Konsole so lange, bis sie wieder erreichbar ist
(Status „reconnecting“). Koppeln mit einer neuen Konsole geht weiter über „Konsole koppeln“: Die Bridge legt den
Controller dann ohne Adresse neu an, und nur dann wartet er im Kopplungsmodus.

Der Patch ändert die Klasse ControllerServer im Hauptprozess, bevor nxbt.Nxbt() seine Prozesse startet; die
Controller-Prozesse entstehen per fork und erben ihn.
"""
import logging
import time

log = logging.getLogger("bridge.controller")

RETRY_SECONDS = 2.0                 # Pause zwischen zwei Verbindungsversuchen zur Konsole


def install(server_cls, retry_seconds=RETRY_SECONDS, sleep=time.sleep):
    """Patcht server_cls (nxbt.controller.server.ControllerServer). Mehrfacher Aufruf schadet nicht."""
    if getattr(server_cls, "_bollisoft_patched", False):
        return False
    orig_run = server_cls.run
    orig_mainloop = server_cls.mainloop
    orig_connect = server_cls.connect

    def run(self, reconnect_address=None):
        # Adresse(n) aus der Bridge merken; None = Kopplungsmodus gewünscht
        self._bollisoft_targets = reconnect_address or None
        return orig_run(self, reconnect_address)

    def mainloop(self, itr, ctrl):
        # Verbunden: Adresse der Konsole merken, damit nach einem Abbruch auch eine frisch gekoppelte Konsole
        # wiedergefunden wird (nxbt selbst merkt sich nach dem Koppeln die eigene statt der Konsolen-Adresse)
        try:
            self._bollisoft_peer = itr.getpeername()[0]
        except (OSError, IndexError, TypeError):
            pass
        return orig_mainloop(self, itr, ctrl)

    def connect(self):
        targets = getattr(self, "_bollisoft_targets", None) or getattr(self, "_bollisoft_peer", None)
        if not targets:
            return orig_connect(self)                 # Koppeln mit einer neuen Konsole
        # Nach einem Abbruch drückt nxbt vor dem Koppeln einmal L+R (Pflicht im Griffweise-Menü) –
        # beim Wiederverbinden mitten im Spiel wäre das ein falscher Tastendruck
        try:
            self.input.current_macro_commands = None
        except AttributeError:
            pass
        attempt = 0
        while True:
            attempt += 1
            try:
                itr, ctrl = self.reconnect(targets)
                if attempt > 1:
                    log.info("Konsole nach %d Versuchen wieder verbunden", attempt)
                return itr, ctrl
            except OSError as err:
                self.state["state"] = "reconnecting"
                if attempt == 1 or attempt % 30 == 0:
                    log.info("Konsole nicht erreichbar (%s) - versuche es weiter", err)
                sleep(retry_seconds)

    server_cls.run = run
    server_cls.mainloop = mainloop
    server_cls.connect = connect
    server_cls._bollisoft_patched = True
    return True


def install_nxbt():
    """Patch für das installierte nxbt; False, wenn nxbt fehlt oder anders aufgebaut ist."""
    try:
        from nxbt.controller import server
    except Exception as err:                          # nxbt fehlt oder lässt sich nicht laden
        log.debug("nxbt-Patch übersprungen: %s", err)
        return False
    cls = getattr(server, "ControllerServer", None)
    if cls is None or not all(hasattr(cls, name) for name in ("run", "mainloop", "connect", "reconnect")):
        log.warning("nxbt hat einen unerwarteten Aufbau - Wiederverbinden bleibt wie in nxbt")
        return False
    import multiprocessing
    method = multiprocessing.get_start_method()
    if method != "fork":
        log.warning("multiprocessing startet mit %s statt fork - der nxbt-Patch wirkt evtl. nicht", method)
    if install(cls):
        log.info("nxbt: Wiederverbinden mit der gekoppelten Konsole ohne Zeitlimit")
    return True
