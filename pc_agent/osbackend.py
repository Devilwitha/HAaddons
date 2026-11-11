"""Plattform-Abstraktion für den HA PC Agent.

Kapselt alles, was sich zwischen Windows und Linux (inkl. Steam Deck / SteamOS
und Bazzite – KDE auf Wayland) unterscheidet: Energie-Aktionen, Audio,
Medientasten, Screenshots, Programm-/Spielestart, Autostart und Pfade.

Die restliche Anwendung (agent.py) ruft nur diese einheitliche API auf.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("pc-agent")

# --------------------------------------------------------------------------- Plattform
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

CREATE_NO_WINDOW = 0x08000000  # nur Windows: Unterprozess ohne Konsolenfenster


def _flags() -> int:
    return CREATE_NO_WINDOW if IS_WINDOWS else 0


def _run(cmd: list[str], timeout: float = 8) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout,
                          creationflags=_flags(), check=False)


def _spawn(cmd: list[str]):
    """Prozess abgekoppelt starten (kehrt sofort zurück)."""
    kw: dict = {"creationflags": _flags()} if IS_WINDOWS else {"start_new_session": True}
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, **kw)


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# --------------------------------------------------------------------------- Linux-Umgebung
def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or \
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def has_display() -> bool:
    if IS_WINDOWS:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _is_steamdeck() -> bool:
    try:
        name = Path("/sys/devices/virtual/dmi/id/product_name").read_text().strip()
        return name in ("Jupiter", "Galileo")
    except Exception:
        return False


def os_model() -> str:
    if IS_WINDOWS:
        return "Windows PC"
    if IS_MAC:
        return "Mac"
    if _is_steamdeck():
        return "Steam Deck"
    return "Linux PC"


# --------------------------------------------------------------------------- Pfade
def data_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "HA-PC-Agent"
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support" / "HA-PC-Agent"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ha-pc-agent"
    base.mkdir(parents=True, exist_ok=True)
    return base


def main_script() -> str:
    """Pfad des gestarteten Skripts (für den Autostart, wenn nicht als Binary)."""
    return os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__)


def launch_target() -> tuple[str, str]:
    """(Programm, Argumente) für den Autostart."""
    if is_frozen():
        return sys.executable, ""
    if IS_WINDOWS:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else sys.executable)
        return exe, f'"{main_script()}"'
    return sys.executable, f'"{main_script()}"'


# --------------------------------------------------------------------------- Autostart
def _autostart_desktop_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart"
    return base / "ha-pc-agent.desktop"


def _autostart_win_link() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    return startup / "HA PC Agent.lnk"


def autostart_enabled() -> bool:
    try:
        return (_autostart_win_link() if IS_WINDOWS else _autostart_desktop_file()).exists()
    except Exception:
        return False


def set_autostart(enabled: bool):
    if IS_WINDOWS:
        _set_autostart_win(enabled)
    else:
        _set_autostart_linux(enabled)


def _set_autostart_win(enabled: bool):
    lnk = _autostart_win_link()
    if not enabled:
        if lnk.exists():
            lnk.unlink()
        return
    target, args = launch_target()
    workdir = str(Path(target).parent if is_frozen() else Path(main_script()).parent)
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


def _set_autostart_linux(enabled: bool):
    f = _autostart_desktop_file()
    if not enabled:
        if f.exists():
            f.unlink()
        return
    exe, args = launch_target()
    exec_line = (f"{exe} {args}").strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=HA PC Agent\n"
        "Comment=Meldet diesen PC an Home Assistant\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- Energie-Aktionen
def do_lock():
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
    elif IS_LINUX:
        if _which("loginctl"):
            _run(["loginctl", "lock-session"])
        elif _which("xdg-screensaver"):
            _run(["xdg-screensaver", "lock"])


def do_sleep():
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
    elif IS_LINUX:
        _run(["systemctl", "suspend"])


def do_shutdown():
    if IS_WINDOWS:
        _run(["shutdown", "/s", "/t", "5"])
    elif IS_LINUX:
        _run(["systemctl", "poweroff"])


def do_restart():
    if IS_WINDOWS:
        _run(["shutdown", "/r", "/t", "5"])
    elif IS_LINUX:
        _run(["systemctl", "reboot"])


def do_cancel_shutdown():
    if IS_WINDOWS:
        _run(["shutdown", "/a"])
    elif IS_LINUX:
        # systemctl poweroff ist sofort; nur geplante shutdowns lassen sich abbrechen
        if _which("shutdown"):
            _run(["shutdown", "-c"])


# --------------------------------------------------------------------------- Aktives Fenster
def active_window_title() -> str:
    try:
        if IS_WINDOWS:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            length = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value[:255]
        if IS_LINUX:
            for tool, args in (("xdotool", ["getactivewindow", "getwindowname"]),
                               ("kdotool", ["getactivewindow", "getwindowname"])):
                if _which(tool):
                    r = _run([tool, *args], timeout=3)
                    if r.returncode == 0 and r.stdout.strip():
                        return r.stdout.strip()[:255]
            return ""  # Wayland ohne Helfer: nicht ermittelbar
    except Exception:
        return ""
    return ""


# --------------------------------------------------------------------------- Audio
def _audio_backend() -> str:
    if IS_WINDOWS:
        return "win" if _WIN_AUDIO else ""
    if IS_LINUX:
        if _which("wpctl"):
            return "wpctl"
        if _which("pactl"):
            return "pactl"
    return ""


# Windows-Audio über pycaw (optional)
try:
    if IS_WINDOWS:
        import comtypes
        from ctypes import POINTER as _POINTER
        from ctypes import cast as _cast
        from comtypes import CLSCTX_ALL as _CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities as _AU
        from pycaw.pycaw import IAudioEndpointVolume as _IAEV
        _WIN_AUDIO = True
    else:
        _WIN_AUDIO = False
except Exception:
    _WIN_AUDIO = False

AUDIO_AVAILABLE = bool(_audio_backend())

_com_local = threading.local()


def _ensure_com():
    if not getattr(_com_local, "ready", False):
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        _com_local.ready = True


def _win_speaker():
    return _AU.GetSpeakers().EndpointVolume


def _win_mic():
    mic = _AU.GetMicrophone()
    return _cast(mic.Activate(_IAEV._iid_, _CLSCTX_ALL, None), _POINTER(_IAEV))


# --- wpctl / pactl Helfer ---------------------------------------------------
_SINK = "@DEFAULT_AUDIO_SINK@"
_SOURCE = "@DEFAULT_AUDIO_SOURCE@"


def _wpctl_vol(target: str) -> tuple[int, bool]:
    r = _run(["wpctl", "get-volume", target])
    vol, muted = 0, False
    for tok in r.stdout.split():
        try:
            vol = int(round(float(tok) * 100))
            break
        except ValueError:
            continue
    muted = "MUTED" in r.stdout.upper()
    return vol, muted


def _pactl_name(kind: str) -> str:
    return "@DEFAULT_SINK@" if kind == "sink" else "@DEFAULT_SOURCE@"


def _pactl_vol(kind: str) -> int:
    r = _run(["pactl", f"get-{kind}-volume", _pactl_name(kind)])
    for tok in r.stdout.replace("/", " ").split():
        if tok.endswith("%"):
            try:
                return int(tok[:-1])
            except ValueError:
                pass
    return 0


def _pactl_mute(kind: str) -> bool:
    r = _run(["pactl", f"get-{kind}-mute", _pactl_name(kind)])
    return "yes" in r.stdout.lower()


def get_volume() -> int:
    b = _audio_backend()
    if b == "win":
        _ensure_com()
        return int(round(_win_speaker().GetMasterVolumeLevelScalar() * 100))
    if b == "wpctl":
        return _wpctl_vol(_SINK)[0]
    if b == "pactl":
        return _pactl_vol("sink")
    return 0


def set_volume(pct: int):
    pct = max(0, min(100, int(pct)))
    b = _audio_backend()
    if b == "win":
        _ensure_com()
        _win_speaker().SetMasterVolumeLevelScalar(pct / 100.0, None)
    elif b == "wpctl":
        _run(["wpctl", "set-volume", "-l", "1.0", _SINK, f"{pct}%"])
    elif b == "pactl":
        _run(["pactl", "set-sink-volume", _pactl_name("sink"), f"{pct}%"])


def get_mute() -> bool:
    b = _audio_backend()
    if b == "win":
        _ensure_com()
        return bool(_win_speaker().GetMute())
    if b == "wpctl":
        return _wpctl_vol(_SINK)[1]
    if b == "pactl":
        return _pactl_mute("sink")
    return False


def set_mute(on: bool):
    b = _audio_backend()
    if b == "win":
        _ensure_com()
        _win_speaker().SetMute(1 if on else 0, None)
    elif b == "wpctl":
        _run(["wpctl", "set-mute", _SINK, "1" if on else "0"])
    elif b == "pactl":
        _run(["pactl", "set-sink-mute", _pactl_name("sink"), "1" if on else "0"])


def get_mic_mute() -> bool:
    b = _audio_backend()
    if b == "win":
        _ensure_com()
        return bool(_win_mic().GetMute())
    if b == "wpctl":
        return _wpctl_vol(_SOURCE)[1]
    if b == "pactl":
        return _pactl_mute("source")
    return False


def set_mic_mute(on: bool):
    b = _audio_backend()
    if b == "win":
        _ensure_com()
        _win_mic().SetMute(1 if on else 0, None)
    elif b == "wpctl":
        _run(["wpctl", "set-mute", _SOURCE, "1" if on else "0"])
    elif b == "pactl":
        _run(["pactl", "set-source-mute", _pactl_name("source"), "1" if on else "0"])


# --------------------------------------------------------------------------- Medientasten
_MEDIA_VK = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2}
_PLAYERCTL = {"play_pause": "play-pause", "next": "next", "prev": "previous", "stop": "stop"}


def media_controls_available() -> bool:
    if IS_WINDOWS:
        return True
    return _which("playerctl")


def media_key(name: str):
    if IS_WINDOWS:
        vk = _MEDIA_VK.get(name)
        if vk:
            import ctypes
            u = ctypes.windll.user32
            u.keybd_event(vk, 0, 0, 0)
            u.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
    elif IS_LINUX and _which("playerctl"):
        cmd = _PLAYERCTL.get(name)
        if cmd:
            _run(["playerctl", cmd], timeout=4)


# --------------------------------------------------------------------------- Screenshot
_SHOT_METHOD: str | None = None


def _save_png_tmp() -> Path:
    fd, p = tempfile.mkstemp(suffix=".png", prefix="hapc_")
    os.close(fd)
    return Path(p)


def _linux_capture_png() -> bytes | None:
    """Versucht mehrere Screenshot-Werkzeuge; merkt sich das erste, das klappt."""
    global _SHOT_METHOD
    wayland = _is_wayland()
    kde = "kde" in _desktop() or "plasma" in _desktop()

    methods: list[str] = []

    def add(name):
        if name not in methods:
            methods.append(name)

    if _SHOT_METHOD:
        add(_SHOT_METHOD)
    if kde and _which("spectacle"):
        add("spectacle")
    if wayland and _which("grim"):
        add("grim")
    if not wayland:
        add("pil")
        for t in ("scrot", "maim", "imagemagick"):
            if _which(t if t != "imagemagick" else "import"):
                add(t)
    for t, exe in (("spectacle", "spectacle"), ("grim", "grim"),
                   ("gnome-screenshot", "gnome-screenshot"), ("scrot", "scrot")):
        if _which(exe):
            add(t)

    for m in methods:
        try:
            data = _run_shot(m)
            if data:
                _SHOT_METHOD = m
                return data
        except Exception:
            log.debug("Screenshot-Methode %s fehlgeschlagen", m, exc_info=True)
    return None


def _run_shot(method: str) -> bytes | None:
    if method == "pil":
        from PIL import ImageGrab
        import io
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    tmp = _save_png_tmp()
    try:
        if method == "spectacle":
            _run(["spectacle", "-b", "-n", "-f", "-o", str(tmp)], timeout=15)
        elif method == "grim":
            _run(["grim", str(tmp)], timeout=15)
        elif method == "gnome-screenshot":
            _run(["gnome-screenshot", "-f", str(tmp)], timeout=15)
        elif method == "scrot":
            _run(["scrot", "-o", "-z", str(tmp)], timeout=15)
        elif method == "maim":
            _run(["maim", str(tmp)], timeout=15)
        elif method == "imagemagick":
            _run(["import", "-window", "root", str(tmp)], timeout=15)
        else:
            return None
        if tmp.exists() and tmp.stat().st_size > 0:
            return tmp.read_bytes()
        return None
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def capture_image(all_monitors: bool = True):
    """Liefert ein PIL.Image des Bildschirms (oder None)."""
    from PIL import Image
    import io
    if IS_WINDOWS:
        from PIL import ImageGrab
        try:
            return ImageGrab.grab(all_screens=bool(all_monitors))
        except Exception:
            log.exception("Screenshot (Windows) fehlgeschlagen")
            return None
    if IS_LINUX:
        png = _linux_capture_png()
        if not png:
            return None
        try:
            return Image.open(io.BytesIO(png)).convert("RGB")
        except Exception:
            log.exception("Screenshot (Linux) konnte nicht gelesen werden")
            return None
    return None


def screenshot_available() -> bool:
    if IS_WINDOWS:
        return True
    if IS_LINUX:
        return any(_which(t) for t in ("spectacle", "grim", "gnome-screenshot",
                                       "scrot", "maim", "import")) or not _is_wayland()
    return False


# --------------------------------------------------------------------------- Programme / Spiele starten
def run_launcher(target: str):
    """Startet Programm, Datei/Ordner, Steam-Spiel-URI oder URL."""
    if not target:
        return
    t = os.path.expandvars(os.path.expanduser(str(target))).strip()
    try:
        if IS_WINDOWS:
            os.startfile(t)  # type: ignore[attr-defined]
        elif IS_LINUX:
            if t.lower().startswith("steam://"):
                if _which("xdg-open"):
                    _spawn(["xdg-open", t])
                elif _which("steam"):
                    _spawn(["steam", t])
                elif _which("flatpak"):
                    _spawn(["flatpak", "run", "com.valvesoftware.Steam", t])
                else:
                    log.warning("Kein Steam/xdg-open zum Öffnen von %s", t)
                    return
            elif _which("xdg-open"):
                _spawn(["xdg-open", t])
            else:
                _spawn([t])
        log.info("Gestartet: %s", t)
    except Exception:
        log.exception("Start fehlgeschlagen: %s", t)


# --------------------------------------------------------------------------- Temperatur (CPU/SoC)
_temp_cache = {"t": 0.0, "val": None}


def _linux_temp() -> "float | None":
    try:
        import psutil
        temps = psutil.sensors_temperatures()
    except Exception:
        temps = {}
    # bevorzugte CPU-/SoC-Sensoren (coretemp=Intel, k10temp/zenpower=AMD,
    # cpu_thermal/soc_thermal=Raspberry Pi/ARM, acpitz=generisch)
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal",
                "soc_thermal", "acpitz", "cpu-thermal"):
        for name, entries in temps.items():
            if name.lower() == key and entries:
                cur = [t.current for t in entries if t.current]
                if cur:
                    return round(max(cur), 1)
    for entries in temps.values():  # sonst irgendein vorhandener Sensor
        cur = [t.current for t in entries if t.current]
        if cur:
            return round(max(cur), 1)
    try:  # Raspberry Pi / generisch über sysfs
        import glob
        vals = []
        for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            try:
                vals.append(int(Path(p).read_text().strip()) / 1000.0)
            except Exception:
                pass
        if vals:
            return round(max(vals), 1)
    except Exception:
        pass
    return None


_WIN_TEMP_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$v = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature |
     Select-Object -ExpandProperty CurrentTemperature
if ($v) { '{0:N1}' -f ((($v | Measure-Object -Maximum).Maximum)/10 - 273.15); exit }
foreach ($ns in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor') {
  $s = Get-CimInstance -Namespace $ns -ClassName Sensor |
       Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU' }
  if ($s) { '{0:N1}' -f (($s | Measure-Object -Property Value -Maximum).Maximum); exit }
}
"""


def _win_temp() -> "float | None":
    """Windows: MSAcpi (meist Laptops) oder Libre/OpenHardwareMonitor, falls aktiv."""
    try:
        r = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WIN_TEMP_PS], timeout=8)
        for tok in r.stdout.replace(",", ".").split():
            try:
                val = float(tok)
                if -20 < val < 150:  # plausible CPU-Temperatur
                    return round(val, 1)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def read_temperature() -> "float | None":
    """CPU-/SoC-Temperatur in °C (oder None, wenn nicht ermittelbar)."""
    if IS_LINUX:
        return _linux_temp()
    if IS_WINDOWS:
        now = time.monotonic()
        if now - _temp_cache["t"] < 60 and _temp_cache["t"] > 0:
            return _temp_cache["val"]
        _temp_cache["val"] = _win_temp()
        _temp_cache["t"] = now
        return _temp_cache["val"]
    return None


_STEAM_SKIP = {"228980"}  # Steamworks Common Redistributables u. ä.


def steam_path() -> "Path | None":
    if IS_WINDOWS:
        try:
            import winreg
        except Exception:
            return None
        for root, key, val in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                with winreg.OpenKey(root, key) as k:
                    return Path(winreg.QueryValueEx(k, val)[0])
            except OSError:
                continue
        return None
    # Linux: übliche Orte inkl. Flatpak
    home = Path.home()
    for c in [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
              home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
              home / ".var/app/com.valvesoftware.Steam/data/Steam"]:
        if (c / "steamapps").exists() or (c / "steamapps" / "libraryfolders.vdf").exists():
            return c
    return None


def steam_games() -> list[dict]:
    """Installierte Steam-Spiele: [{'appid','name'}] (leer, wenn kein Steam)."""
    import re
    base = steam_path()
    if not base:
        return []
    libs = [base]
    vdf = base / "steamapps" / "libraryfolders.vdf"
    try:
        txt = vdf.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"path"\s*"([^"]+)"', txt):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    except Exception:
        pass
    games: dict[str, str] = {}
    for lib in libs:
        apps = lib / "steamapps"
        if not apps.exists():
            continue
        for f in apps.glob("appmanifest_*.acf"):
            try:
                t = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            a = re.search(r'"appid"\s*"(\d+)"', t)
            n = re.search(r'"name"\s*"([^"]+)"', t)
            if a and n and a.group(1) not in _STEAM_SKIP and "Redistributable" not in n.group(1):
                games[a.group(1)] = n.group(1)
    return sorted(({"appid": a, "name": n} for a, n in games.items()), key=lambda x: x["name"].lower())
