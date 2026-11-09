"""HA PC Agent – meldet diesen Windows-PC an das eigene Home Assistant.

Transparentes Selbst-Überwachungs-Werkzeug (wie HASS.Agent): sichtbares Tray-Icon,
Einstellungsfenster, alles abschaltbar. Verbindet sich per MQTT mit dem eigenen
Home Assistant und legt dort per MQTT-Discovery automatisch ein Gerät an mit:

  - Kamera: regelmäßiger Screenshot des Desktops
  - Sensoren: CPU, RAM, aktives Fenster, Betriebszeit (Akku nur bei Laptops)
  - Schalter/Knöpfe: Herunterfahren, Neustart, Sperren, Energie sparen

Es läuft nur gegen das in den Einstellungen eingetragene Home Assistant. Nichts
wird an Dritte gesendet. Fenster schließen minimiert in den Infobereich; Beenden
über das Tray-Icon.
"""
from __future__ import annotations

import ctypes
import io
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import psutil
from PIL import Image, ImageDraw, ImageGrab

APP_NAME = "HA PC Agent"
DISCOVERY_PREFIX = "homeassistant"
CREATE_NO_WINDOW = 0x08000000  # Windows: Unterprozess ohne Konsolenfenster
log = logging.getLogger("pc-agent")

DEFAULT_CONFIG = {
    "mqtt": {"host": "", "port": 1883, "username": "", "password": ""},
    "device_name": "",
    "screenshot": {"enabled": True, "interval_seconds": 10, "max_width": 1280,
                   "jpeg_quality": 55, "all_monitors": True},
    "sensors_interval_seconds": 15,
    "allow_power_commands": True,
    "media_controls": True,
}


# --------------------------------------------------------------------------- Pfade / Umgebung
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    d = Path(os.environ.get("APPDATA", str(Path.home()))) / "HA-PC-Agent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def launch_target() -> tuple[str, str]:
    """(Programm, Argumente) für den Autostart – exe direkt oder pythonw + Skript."""
    if is_frozen():
        return sys.executable, ""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    return exe, f'"{os.path.abspath(__file__)}"'


# --------------------------------------------------------------------------- Konfiguration
def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base.get(k, {}), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            return _merge(DEFAULT_CONFIG, json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            log.exception("config.json unlesbar – nutze Standardwerte")
    return _merge(DEFAULT_CONFIG, {"device_name": socket.gethostname()})


def save_config(cfg: dict):
    config_path().write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def config_is_valid(cfg: dict) -> bool:
    return bool(cfg.get("mqtt", {}).get("host"))


# --------------------------------------------------------------------------- Autostart
def autostart_link() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    return startup / f"{APP_NAME}.lnk"


def autostart_enabled() -> bool:
    return autostart_link().exists()


def set_autostart(enabled: bool):
    lnk = autostart_link()
    if not enabled:
        if lnk.exists():
            lnk.unlink()
        return
    target, args = launch_target()
    workdir = str(Path(target).parent if is_frozen() else Path(__file__).parent)
    ps = (
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{lnk}');"
        f"$s.TargetPath='{target}';"
        f"$s.Arguments='{args}';"
        f"$s.WorkingDirectory='{workdir}';"
        "$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=CREATE_NO_WINDOW, check=False)


# --------------------------------------------------------------------------- Windows-Aktionen
def _win() -> bool:
    return os.name == "nt"


def do_lock():
    if _win():
        ctypes.windll.user32.LockWorkStation()


def do_sleep():
    if _win():
        ctypes.windll.powrprof.SetSuspendState(0, 0, 0)


def do_shutdown():
    if _win():
        subprocess.run(["shutdown", "/s", "/t", "5"], creationflags=CREATE_NO_WINDOW, check=False)


def do_restart():
    if _win():
        subprocess.run(["shutdown", "/r", "/t", "5"], creationflags=CREATE_NO_WINDOW, check=False)


def do_cancel_shutdown():
    if _win():
        subprocess.run(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW, check=False)


def active_window_title() -> str:
    if not _win():
        return ""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        length = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value[:255]
    except Exception:
        return ""


# --------------------------------------------------------------------------- Audio (Lautstärke/Mikrofon)
try:
    import comtypes
    from ctypes import POINTER as _POINTER
    from ctypes import cast as _cast
    from comtypes import CLSCTX_ALL as _CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities as _AU
    from pycaw.pycaw import IAudioEndpointVolume as _IAEV
    AUDIO_AVAILABLE = True
except Exception:
    AUDIO_AVAILABLE = False

_com_local = threading.local()


def _ensure_com():
    if not getattr(_com_local, "ready", False):
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        _com_local.ready = True


def _speaker_ep():
    return _AU.GetSpeakers().EndpointVolume


def _mic_ep():
    mic = _AU.GetMicrophone()
    return _cast(mic.Activate(_IAEV._iid_, _CLSCTX_ALL, None), _POINTER(_IAEV))


def get_volume() -> int:
    _ensure_com()
    return int(round(_speaker_ep().GetMasterVolumeLevelScalar() * 100))


def set_volume(pct: int):
    _ensure_com()
    _speaker_ep().SetMasterVolumeLevelScalar(max(0, min(100, int(pct))) / 100.0, None)


def get_mute() -> bool:
    _ensure_com()
    return bool(_speaker_ep().GetMute())


def set_mute(on: bool):
    _ensure_com()
    _speaker_ep().SetMute(1 if on else 0, None)


def get_mic_mute() -> bool:
    _ensure_com()
    return bool(_mic_ep().GetMute())


def set_mic_mute(on: bool):
    _ensure_com()
    _mic_ep().SetMute(1 if on else 0, None)


_MEDIA_VK = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2}


def media_key(name: str):
    vk = _MEDIA_VK.get(name)
    if vk and _win():
        u = ctypes.windll.user32
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


# --------------------------------------------------------------------------- Screenshot / Sensoren
def grab_screenshot(opts: dict) -> bytes | None:
    try:
        img = ImageGrab.grab(all_screens=bool(opts.get("all_monitors", True)))
    except Exception:
        log.exception("Screenshot fehlgeschlagen")
        return None
    max_w = int(opts.get("max_width", 1280))
    if max_w and img.width > max_w:
        h = int(img.height * max_w / img.width)
        img = img.resize((max_w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=int(opts.get("jpeg_quality", 55)))
    return buf.getvalue()


def read_sensors() -> dict:
    data = {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "ram": round(psutil.virtual_memory().percent, 1),
        "active_window": active_window_title() or "—",
        "uptime": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    try:
        bat = psutil.sensors_battery()
    except Exception:
        bat = None
    if bat is not None:
        data["battery"] = round(bat.percent, 0)
        data["battery_charging"] = "Ja" if bat.power_plugged else "Nein"
    return data


def has_battery() -> bool:
    try:
        return psutil.sensors_battery() is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- MQTT-Agent
class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        name = cfg.get("device_name") or socket.gethostname()
        self.node_id = "pc_" + "".join(c if c.isalnum() else "_" for c in name.lower())
        self.base = f"ha_pc_agent/{self.node_id}"
        self.avail_topic = f"{self.base}/availability"
        self.device_name = name
        self.screenshots_paused = False
        self.status = "getrennt"
        self._stop = threading.Event()

        m = cfg.get("mqtt", {})
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.node_id)
        if m.get("username"):
            self.client.username_pw_set(m["username"], m.get("password", ""))
        self.client.will_set(self.avail_topic, "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self._host = m.get("host", "127.0.0.1")
        self._port = int(m.get("port", 1883))

    def _device_block(self) -> dict:
        return {"identifiers": [self.node_id], "name": self.device_name,
                "manufacturer": "HA PC Agent", "model": "Windows PC", "sw_version": "1.1.0"}

    def _publish_discovery(self):
        dev = self._device_block()
        avail = [{"topic": self.avail_topic, "payload_available": "online",
                  "payload_not_available": "offline"}]

        def base_entity(obj, name):
            return {"unique_id": f"{self.node_id}_{obj}", "object_id": f"{self.node_id}_{obj}",
                    "name": name, "availability": avail, "device": dev}

        entities = []
        sensors = [("cpu", "CPU-Auslastung", "%", "mdi:chip", None),
                   ("ram", "RAM-Auslastung", "%", "mdi:memory", None),
                   ("active_window", "Aktives Fenster", None, "mdi:window-restore", None),
                   ("uptime", "Betriebszeit", "h", "mdi:clock-outline", None)]
        if has_battery():
            sensors += [("battery", "Akku", "%", None, "battery"),
                        ("battery_charging", "Lädt", None, "mdi:power-plug", None)]
        for obj, name, unit, icon, dclass in sensors:
            e = base_entity(obj, name)
            e["state_topic"] = f"{self.base}/sensor/{obj}"
            if unit:
                e["unit_of_measurement"] = unit
            if icon:
                e["icon"] = icon
            if dclass:
                e["device_class"] = dclass
            entities.append(("sensor", obj, e))

        if self.cfg.get("screenshot", {}).get("enabled", True):
            cam = base_entity("screen", "Bildschirm")
            cam["topic"] = f"{self.base}/camera/screen"
            entities.append(("camera", "screen", cam))
            sw = base_entity("screenshots", "Screenshots senden")
            sw["state_topic"] = f"{self.base}/switch/screenshots/state"
            sw["command_topic"] = f"{self.base}/switch/screenshots/set"
            sw["icon"] = "mdi:monitor-screenshot"
            entities.append(("switch", "screenshots", sw))

        if self.cfg.get("allow_power_commands", True):
            for obj, name, icon in [("shutdown", "Herunterfahren", "mdi:power"),
                                    ("restart", "Neustart", "mdi:restart"),
                                    ("lock", "Sperren", "mdi:lock"),
                                    ("sleep", "Energie sparen", "mdi:sleep"),
                                    ("cancel_shutdown", "Herunterfahren abbrechen", "mdi:close-octagon")]:
                b = base_entity(obj, name)
                b["command_topic"] = f"{self.base}/button/{obj}"
                b["icon"] = icon
                entities.append(("button", obj, b))

        if self.cfg.get("media_controls", True):
            for obj, name, icon in [("media_play_pause", "Wiedergabe/Pause", "mdi:play-pause"),
                                    ("media_next", "Nächster Titel", "mdi:skip-next"),
                                    ("media_prev", "Vorheriger Titel", "mdi:skip-previous")]:
                b = base_entity(obj, name)
                b["command_topic"] = f"{self.base}/button/{obj}"
                b["icon"] = icon
                entities.append(("button", obj, b))
            if AUDIO_AVAILABLE:
                n = base_entity("volume", "Lautstärke")
                n["state_topic"] = f"{self.base}/number/volume/state"
                n["command_topic"] = f"{self.base}/number/volume/set"
                n.update({"min": 0, "max": 100, "step": 1, "mode": "slider",
                          "unit_of_measurement": "%", "icon": "mdi:volume-high"})
                entities.append(("number", "volume", n))
                for obj, name, icon in [("mute", "Ton stumm", "mdi:volume-mute"),
                                        ("mic_mute", "Mikrofon stumm", "mdi:microphone-off")]:
                    s = base_entity(obj, name)
                    s["state_topic"] = f"{self.base}/switch/{obj}/state"
                    s["command_topic"] = f"{self.base}/switch/{obj}/set"
                    s["icon"] = icon
                    entities.append(("switch", obj, s))

        for component, obj, payload in entities:
            self.client.publish(f"{DISCOVERY_PREFIX}/{component}/{self.node_id}/{obj}/config",
                                json.dumps(payload), retain=True)
        log.info("%d Entitäten per Discovery angemeldet", len(entities))

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            self.status = f"abgelehnt ({reason_code})"
            log.error("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        self.status = "verbunden"
        log.info("Mit MQTT verbunden (%s:%s)", self._host, self._port)
        client.publish(self.avail_topic, "online", retain=True)
        self._publish_discovery()
        for obj in ("shutdown", "restart", "lock", "sleep", "cancel_shutdown"):
            client.subscribe(f"{self.base}/button/{obj}")
        client.subscribe(f"{self.base}/switch/screenshots/set")
        client.publish(f"{self.base}/switch/screenshots/state",
                       "OFF" if self.screenshots_paused else "ON", retain=True)
        if self.cfg.get("media_controls", True):
            for obj in ("media_play_pause", "media_next", "media_prev"):
                client.subscribe(f"{self.base}/button/{obj}")
            if AUDIO_AVAILABLE:
                client.subscribe(f"{self.base}/number/volume/set")
                client.subscribe(f"{self.base}/switch/mute/set")
                client.subscribe(f"{self.base}/switch/mic_mute/set")
                self._publish_audio_state()

    def _publish_audio_state(self):
        if not (AUDIO_AVAILABLE and self.cfg.get("media_controls", True)):
            return
        try:
            self.client.publish(f"{self.base}/number/volume/state", str(get_volume()))
            self.client.publish(f"{self.base}/switch/mute/state", "ON" if get_mute() else "OFF")
            self.client.publish(f"{self.base}/switch/mic_mute/state", "ON" if get_mic_mute() else "OFF")
        except Exception:
            log.exception("Audio-Status")

    def _on_disconnect(self, client, userdata, *args):
        if not self._stop.is_set():
            self.status = "verbindet"

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode(errors="replace").strip()
        log.info("Befehl: %s %s", topic, payload)
        if topic.endswith("/switch/screenshots/set"):
            self.screenshots_paused = payload.upper() == "OFF"
            client.publish(f"{self.base}/switch/screenshots/state",
                           "OFF" if self.screenshots_paused else "ON", retain=True)
            return
        # Medien / Lautstärke
        if topic.endswith("/number/volume/set"):
            try:
                set_volume(int(float(payload)))
            except Exception:
                log.exception("Lautstärke")
            self._publish_audio_state()
            return
        if topic.endswith("/switch/mute/set"):
            set_mute(payload.upper() == "ON")
            self._publish_audio_state()
            return
        if topic.endswith("/switch/mic_mute/set"):
            set_mic_mute(payload.upper() == "ON")
            self._publish_audio_state()
            return
        if "/button/media_" in topic:
            media_key(topic.rsplit("/button/media_", 1)[-1])
            return
        if not self.cfg.get("allow_power_commands", True):
            return
        action = topic.rsplit("/", 1)[-1]
        {"shutdown": do_shutdown, "restart": do_restart, "lock": do_lock,
         "sleep": do_sleep, "cancel_shutdown": do_cancel_shutdown}.get(action, lambda: None)()

    def _sensor_loop(self):
        interval = int(self.cfg.get("sensors_interval_seconds", 15))
        psutil.cpu_percent(interval=None)
        while not self._stop.wait(2):
            try:
                for obj, val in read_sensors().items():
                    self.client.publish(f"{self.base}/sensor/{obj}", str(val))
                self._publish_audio_state()
            except Exception:
                log.exception("Sensoren")
            self._stop.wait(max(1, interval - 2))

    def _screenshot_loop(self):
        sc = self.cfg.get("screenshot", {})
        if not sc.get("enabled", True):
            return
        interval = int(sc.get("interval_seconds", 10))
        while not self._stop.wait(interval):
            if self.screenshots_paused:
                continue
            data = grab_screenshot(sc)
            if data:
                self.client.publish(f"{self.base}/camera/screen", data)

    def start(self):
        self.status = "verbindet"
        self.client.connect_async(self._host, self._port, keepalive=30)
        self.client.loop_start()
        threading.Thread(target=self._sensor_loop, daemon=True).start()
        threading.Thread(target=self._screenshot_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.status = "getrennt"
        try:
            self.client.publish(self.avail_topic, "offline", retain=True)
            time.sleep(0.2)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


def test_connection(mqtt_cfg: dict, timeout: float = 8) -> tuple[bool, str]:
    """Kurzer Verbindungstest. Gibt (ok, Meldung) zurück."""
    result: dict = {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pc_agent_test")
    if mqtt_cfg.get("username"):
        c.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password", ""))

    def on_connect(cl, u, f, rc, props=None):
        result["rc"] = rc
        cl.disconnect()

    c.on_connect = on_connect
    try:
        c.connect(mqtt_cfg.get("host", ""), int(mqtt_cfg.get("port", 1883)), int(timeout))
    except Exception as e:
        return False, f"Keine Verbindung: {e}"
    c.loop_start()
    t0 = time.time()
    while "rc" not in result and time.time() - t0 < timeout:
        time.sleep(0.1)
    c.loop_stop()
    if "rc" not in result:
        return False, "Zeitüberschreitung – Host/Port prüfen."
    rc = result["rc"]
    ok = getattr(rc, "value", rc) == 0
    return (True, "Verbindung erfolgreich.") if ok else (False, f"Abgelehnt: {rc} (Benutzer/Passwort?)")


# --------------------------------------------------------------------------- Manager
class Manager:
    def __init__(self):
        self.agent: Agent | None = None

    def restart(self):
        self.stop()
        cfg = load_config()
        if not config_is_valid(cfg):
            log.info("Keine gültige Konfiguration – Agent nicht gestartet.")
            return
        self.agent = Agent(cfg)
        self.agent.start()

    def stop(self):
        if self.agent:
            self.agent.stop()
            self.agent = None

    @property
    def status(self) -> str:
        return self.agent.status if self.agent else "nicht eingerichtet"

    @property
    def paused(self) -> bool:
        return bool(self.agent and self.agent.screenshots_paused)

    def set_paused(self, paused: bool):
        if self.agent:
            self.agent.screenshots_paused = paused
            self.agent.client.publish(f"{self.agent.base}/switch/screenshots/state",
                                      "OFF" if paused else "ON", retain=True)


# --------------------------------------------------------------------------- Tray-Icon
def tray_image():
    ico = resource_path("icon.ico")
    if ico.exists():
        try:
            return Image.open(ico)
        except Exception:
            pass
    img = Image.new("RGB", (64, 64), (38, 38, 36))
    d = ImageDraw.Draw(img)
    d.rectangle((12, 16, 52, 44), outline=(217, 119, 87), width=3)
    d.line((26, 48, 38, 48), fill=(217, 119, 87), width=3)
    d.ellipse((28, 26, 36, 34), fill=(217, 119, 87))
    return img


# --------------------------------------------------------------------------- GUI (tkinter)
def run_gui(manager: Manager):
    import tkinter as tk
    from tkinter import ttk, messagebox

    manager.restart()

    root = tk.Tk()
    root.title(APP_NAME)
    root.minsize(430, 560)
    try:
        root.iconbitmap(str(resource_path("icon.ico")))
    except Exception:
        pass

    cfg = load_config()
    mq = cfg["mqtt"]
    sc = cfg["screenshot"]
    V = {
        "host": tk.StringVar(value=mq.get("host", "")),
        "port": tk.StringVar(value=str(mq.get("port", 1883))),
        "user": tk.StringVar(value=mq.get("username", "")),
        "pass": tk.StringVar(value=mq.get("password", "")),
        "name": tk.StringVar(value=cfg.get("device_name", "")),
        "shot_on": tk.BooleanVar(value=sc.get("enabled", True)),
        "interval": tk.StringVar(value=str(sc.get("interval_seconds", 10))),
        "width": tk.StringVar(value=str(sc.get("max_width", 1280))),
        "quality": tk.StringVar(value=str(sc.get("jpeg_quality", 55))),
        "sens": tk.StringVar(value=str(cfg.get("sensors_interval_seconds", 15))),
        "power": tk.BooleanVar(value=cfg.get("allow_power_commands", True)),
        "media": tk.BooleanVar(value=cfg.get("media_controls", True)),
        "autostart": tk.BooleanVar(value=autostart_enabled()),
    }

    pad = {"padx": 10, "pady": 4}
    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)
    row = 0

    def section(title):
        nonlocal row
        ttk.Label(frm, text=title, font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 2))
        row += 1

    def field(label, var, show=None, width=22):
        nonlocal row
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", **pad)
        e = ttk.Entry(frm, textvariable=var, show=show, width=width)
        e.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        return e

    section("Home Assistant (MQTT)")
    field("Host / IP", V["host"])
    field("Port", V["port"], width=8)
    field("Benutzer", V["user"])
    pass_entry = field("Passwort", V["pass"], show="•")
    show_pw = tk.BooleanVar(value=False)
    ttk.Checkbutton(frm, text="Passwort anzeigen", variable=show_pw,
                    command=lambda: pass_entry.config(show="" if show_pw.get() else "•")
                    ).grid(row=row, column=1, sticky="w", **pad)
    row += 1

    section("Gerät")
    field("Name in HA", V["name"])

    section("Screenshots")
    ttk.Checkbutton(frm, text="Screenshots an HA senden", variable=V["shot_on"]
                    ).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
    row += 1
    field("Takt (Sekunden)", V["interval"], width=8)
    field("Max. Breite (px)", V["width"], width=8)
    field("JPEG-Qualität (1–95)", V["quality"], width=8)

    section("Weiteres")
    field("Sensoren-Takt (Sekunden)", V["sens"], width=8)
    ttk.Checkbutton(frm, text="Herunterfahren / Neustart / Sperren erlauben", variable=V["power"]
                    ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
    row += 1
    media_text = "Medientasten & Lautstärke steuern"
    if not AUDIO_AVAILABLE:
        media_text += "  (Lautstärke/Mikrofon nicht verfügbar)"
    ttk.Checkbutton(frm, text=media_text, variable=V["media"]
                    ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
    row += 1
    ttk.Checkbutton(frm, text="Automatisch mit Windows starten", variable=V["autostart"]
                    ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
    row += 1

    status_var = tk.StringVar(value="")
    ttk.Label(frm, textvariable=status_var, foreground="#c96442").grid(
        row=row, column=0, columnspan=3, sticky="w", pady=(12, 2))
    row += 1

    def collect() -> dict:
        def num(s, d):
            try:
                return int(float(s))
            except Exception:
                return d
        return {
            "mqtt": {"host": V["host"].get().strip(), "port": num(V["port"].get(), 1883),
                     "username": V["user"].get().strip(), "password": V["pass"].get()},
            "device_name": V["name"].get().strip() or socket.gethostname(),
            "screenshot": {"enabled": V["shot_on"].get(), "interval_seconds": num(V["interval"].get(), 10),
                           "max_width": num(V["width"].get(), 1280), "jpeg_quality": num(V["quality"].get(), 55),
                           "all_monitors": True},
            "sensors_interval_seconds": num(V["sens"].get(), 15),
            "allow_power_commands": V["power"].get(),
            "media_controls": V["media"].get(),
        }

    def on_test():
        status_var.set("Teste Verbindung …")

        def work():
            ok, msg = test_connection(collect()["mqtt"])
            root.after(0, lambda: (status_var.set(msg),
                                   messagebox.showinfo(APP_NAME, msg) if ok
                                   else messagebox.showwarning(APP_NAME, msg)))
        threading.Thread(target=work, daemon=True).start()

    def on_save():
        new = collect()
        if not new["mqtt"]["host"]:
            messagebox.showwarning(APP_NAME, "Bitte Host / IP des Home Assistant eintragen.")
            return
        save_config(new)
        try:
            set_autostart(V["autostart"].get())
        except Exception:
            log.exception("Autostart setzen fehlgeschlagen")
        manager.restart()
        status_var.set("Gespeichert. Agent neu verbunden.")

    btns = ttk.Frame(frm)
    btns.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
    ttk.Button(btns, text="Verbindung testen", command=on_test).pack(side="left")
    ttk.Button(btns, text="In Infobereich", command=lambda: root.withdraw()).pack(side="right")
    ttk.Button(btns, text="Speichern & Anwenden", command=on_save).pack(side="right", padx=6)

    # Laufender Verbindungsstatus
    conn_var = tk.StringVar()
    ttk.Label(frm, textvariable=conn_var, foreground="#75736b").grid(
        row=row + 1, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def refresh_status():
        conn_var.set(f"Status: {manager.status}")
        root.after(2000, refresh_status)
    refresh_status()

    # --- Tray ---
    import pystray
    hinted = {"done": False}

    def show_window(icon=None, item=None):
        root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force()))

    def toggle_pause(icon, item):
        manager.set_paused(not manager.paused)

    def quit_app(icon=None, item=None):
        manager.stop()
        try:
            tray.stop()
        except Exception:
            pass
        root.after(0, root.destroy)

    tray = pystray.Icon(APP_NAME, tray_image(), APP_NAME, menu=pystray.Menu(
        pystray.MenuItem("Einstellungen öffnen", show_window, default=True),
        pystray.MenuItem(lambda i: "Screenshots: pausiert" if manager.paused else "Screenshots: aktiv",
                         toggle_pause),
        pystray.MenuItem("Beenden", quit_app),
    ))
    threading.Thread(target=tray.run, daemon=True).start()

    def on_close():
        root.withdraw()
        if not hinted["done"]:
            hinted["done"] = True
            try:
                tray.notify("Läuft weiter im Infobereich. Zum Beenden dort das Menü nutzen.", APP_NAME)
            except Exception:
                pass

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Beim ersten Start ohne gültige Konfiguration Fenster zeigen, sonst in den Tray
    if config_is_valid(cfg):
        root.after(200, root.withdraw)

    root.mainloop()


# --------------------------------------------------------------------------- main
def main():
    handlers = [logging.handlers.RotatingFileHandler(
        data_dir() / "agent.log", maxBytes=512_000, backupCount=2, encoding="utf-8")]
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    manager = Manager()
    try:
        run_gui(manager)
    except Exception:
        log.exception("GUI konnte nicht starten – laufe ohne Fenster weiter")
        manager.restart()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            manager.stop()


if __name__ == "__main__":
    main()
