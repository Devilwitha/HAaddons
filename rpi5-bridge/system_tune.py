"""Raspberry Pi für gleichmäßiges Streaming einstellen und seinen Zustand messen.

tune(): CPU-Takt-Regler auf "performance" (kein Hochtakten mitten im Bild) und WLAN-Stromsparen aus
(sonst Aussetzer von 50-200 ms, wenn der Pi per WLAN verbunden ist). Beides gilt bis zum Neustart; die
Bridge setzt es bei jedem Start erneut. Braucht Root-Rechte (der Dienst läuft als root).

SystemStats: Temperatur, CPU-Last und Drosselung (vcgencmd get_throttled) für die Messanzeige.
"""
import glob
import logging
import os
import shutil
import subprocess
import time

log = logging.getLogger("bridge.system")

# Bits von "vcgencmd get_throttled" (aktueller Zustand)
THROTTLE_NOW = {0x1: "Unterspannung", 0x2: "Takt begrenzt", 0x4: "gedrosselt", 0x8: "Temperaturgrenze"}
THROTTLE_SINCE_BOOT = 0x50000          # Unterspannung oder Drosselung seit dem Start


def _write(path, value):
    try:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(value)
        return True
    except OSError:
        return False


def tune(cfg):
    """Einstellungen für flüssiges Streaming. cfg: Abschnitt "system" der config.yaml."""
    cfg = cfg or {}
    if not cfg.get("tune", True) or not os.path.isdir("/sys/devices/system/cpu"):
        return
    governor = str(cfg.get("cpu_governor", "performance"))
    policies = glob.glob("/sys/devices/system/cpu/cpufreq/policy*/scaling_governor")
    done = [p for p in policies if _write(p, governor)]
    if policies:
        log.info("CPU-Regler: %s (%d/%d)", governor, len(done), len(policies))
    if cfg.get("wifi_power_save_off", True) and shutil.which("iw"):
        for dev in sorted(glob.glob("/sys/class/net/wl*")):
            name = os.path.basename(dev)
            try:
                subprocess.run(["iw", "dev", name, "set", "power_save", "off"], check=True, timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                log.info("WLAN-Stromsparen aus: %s", name)
            except (OSError, subprocess.SubprocessError) as err:
                log.info("WLAN-Stromsparen für %s nicht änderbar: %s", name, err)


class SystemStats:
    """Zustand des Pi, höchstens alle 2 Sekunden neu gelesen."""

    def __init__(self):
        self._cpu_prev = None
        self._cache = None
        self._cache_at = 0.0
        self._vcgencmd = shutil.which("vcgencmd")

    def _cpu(self):
        try:
            with open("/proc/stat", encoding="ascii") as fh:
                values = [int(v) for v in fh.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if prev is None or total <= prev[1]:
            return None
        return round(100.0 * (1.0 - (idle - prev[0]) / (total - prev[1])))

    @staticmethod
    def _temp():
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", encoding="ascii") as fh:
                return round(int(fh.read().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            return None

    def _throttled(self):
        if not self._vcgencmd:
            return None
        try:
            out = subprocess.run([self._vcgencmd, "get_throttled"], capture_output=True, text=True, timeout=2).stdout
            return int(out.strip().split("=")[-1], 16)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    @staticmethod
    def _governor():
        try:
            with open("/sys/devices/system/cpu/cpufreq/policy0/scaling_governor", encoding="ascii") as fh:
                return fh.read().strip()
        except OSError:
            return None

    def snapshot(self):
        now = time.monotonic()
        if self._cache is not None and now - self._cache_at < 2.0:
            return self._cache
        flags = self._throttled()
        current = [text for bit, text in THROTTLE_NOW.items() if flags is not None and flags & bit]
        self._cache = {
            "temp": self._temp(),
            "cpu": self._cpu(),
            "governor": self._governor(),
            "throttled": current,
            "throttled_before": bool(flags is not None and flags & THROTTLE_SINCE_BOOT),
        }
        self._cache_at = now
        return self._cache
