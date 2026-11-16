"""Hauptdienst: Fotos im Intervall, Bewertung, MQTT + Home-Assistant-Discovery."""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

from . import __version__, analyzers
from .analyzers.reference import ReferenceAnalyzer, difference
from .camera import Camera, CameraError, to_jpeg
from .config import Config
from .streak import StreakTracker

log = logging.getLogger(__name__)

ANALYZERS = ["reference", "ollama", "claude"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_active_hours(spec: str) -> str:
    """Prüft und vereinheitlicht ein Zeitfenster: ' 8:00 - 22:00 ' -> '08:00-22:00', 'immer'/'' -> ''.
    Wirft ValueError bei ungültiger Eingabe."""
    spec = (spec or "").strip()
    if spec.lower() in ("", "immer", "always"):
        return ""
    if "-" not in spec:
        raise ValueError(f"Zeitfenster braucht die Form HH:MM-HH:MM: {spec!r}")
    start_s, end_s = (part.strip() for part in spec.split("-", 1))
    start = datetime.strptime(start_s, "%H:%M")
    end = datetime.strptime(end_s, "%H:%M")
    return f"{start:%H:%M}-{end:%H:%M}"


def in_active_hours(spec: str, now: datetime | None = None) -> bool:
    """'08:00-22:00' -> True, wenn jetzt in diesem Zeitfenster. Leer = immer aktiv.
    Über Mitternacht (z. B. '20:00-02:00') funktioniert auch."""
    if not spec or not spec.strip():
        return True
    now = now or datetime.now()
    start_s, end_s = (part.strip() for part in spec.split("-", 1))
    start = datetime.strptime(start_s, "%H:%M").time()
    end = datetime.strptime(end_s, "%H:%M").time()
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.camera = Camera(cfg["camera"])
        self.references = ReferenceAnalyzer(cfg["reference"], cfg.storage_dir / "reference")
        self.streak = StreakTracker(cfg.storage_dir / "streak.json")
        self.photo_dir = cfg.storage_dir / "photos"
        self.photo_dir.mkdir(parents=True, exist_ok=True)

        m = cfg["mqtt"]
        self.base = f"{m['topic_prefix']}/cam"
        self.node_id = m.get("node_id") or "werwardas_" + socket.gethostname().replace("-", "_").lower()
        self.jobs: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.next_run = time.monotonic() + 15  # erste Prüfung kurz nach dem Start

        # Für "unveränderte Fotos überspringen": Foto und Ergebnis der letzten echten Bewertung.
        self._last_eval_img = None
        self._last_result: analyzers.Result | None = None
        self._last_eval_name: str | None = None

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.node_id)
        if m.get("username"):
            self.client.username_pw_set(m["username"], m.get("password") or None)
        self.client.will_set(f"{self.base}/availability", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)

    # ------------------------------------------------------------------ MQTT

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        log.info("Mit MQTT verbunden")
        client.subscribe(f"{self.base}/cmd/#")
        client.subscribe(f"{self.base}/set/#")
        client.subscribe(f"{self.cfg['mqtt']['discovery_prefix']}/status")
        self._publish_discovery()
        self._publish_settings()
        client.publish(f"{self.base}/availability", "online", retain=True)
        client.publish(f"{self.base}/busy", "OFF", retain=True)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace").strip()
        topic = msg.topic
        try:
            if topic == f"{self.cfg['mqtt']['discovery_prefix']}/status" and payload == "online":
                self._publish_discovery()  # Home Assistant wurde neu gestartet
                self._publish_settings()
            elif topic == f"{self.base}/cmd/check":
                self.jobs.put(("check", payload or "manual"))
            elif topic == f"{self.base}/cmd/reference":
                self.jobs.put(("reference", "reference"))
            elif topic == f"{self.base}/set/threshold":
                self.cfg.set_runtime("threshold", int(float(payload)))
                self._publish_settings()
                self.jobs.put(("check", "settings"))
            elif topic == f"{self.base}/set/interval":
                self.cfg.set_runtime("interval_minutes", max(1, int(float(payload))))
                self.next_run = time.monotonic() + self.cfg["interval_minutes"] * 60
                self._publish_settings()
            elif topic == f"{self.base}/set/active_hours":
                self.cfg.set_runtime("active_hours", normalize_active_hours(payload))
                self._publish_settings()
                self.jobs.put(("check", "settings"))
            elif topic == f"{self.base}/set/analyzer":
                if payload not in ANALYZERS:
                    raise ValueError(payload)
                self.cfg.set_runtime("analyzer", payload)
                self._publish_settings()
                self.jobs.put(("check", "settings"))
            elif topic == f"{self.base}/set/ollama_fallback":
                self.cfg.set_runtime("ollama_fallback", payload.upper() == "ON")
                self._publish_settings()
            elif topic == f"{self.base}/set/skip_unchanged":
                self.cfg.set_runtime("skip_unchanged", payload.upper() == "ON")
                self._publish_settings()
            elif topic == f"{self.base}/set/active":
                self.cfg.set_runtime("active", payload.upper() == "ON")
                self._publish_settings()
                self.jobs.put(("check", "settings"))
        except (ValueError, KeyError) as exc:
            log.warning("Ungültige Nachricht auf %s: %r (%s)", topic, payload, exc)

    def _publish_settings(self) -> None:
        c = self.cfg
        settings = {
            "threshold": c["threshold"],
            "interval": c["interval_minutes"],
            "analyzer": c["analyzer"],
            "active": "ON" if c["active"] else "OFF",
            "active_hours": c["active_hours"] or "",
            "ollama_fallback": "ON" if c["ollama_fallback"] else "OFF",
            "skip_unchanged": "ON" if c["skip_unchanged"] else "OFF",
            "references": len(self.references.references()),
        }
        self.client.publish(f"{self.base}/settings", json.dumps(settings), retain=True)

    def _publish_discovery(self) -> None:
        prefix = self.cfg["mqtt"]["discovery_prefix"]
        device = {
            "identifiers": [self.node_id],
            "name": "WerWarDas Kamera",
            "manufacturer": "WerWarDas",
            "model": "Aufräum-Wächter",
            "sw_version": __version__,
        }
        common = {
            "device": device,
            "availability_topic": f"{self.base}/availability",
        }
        state = f"{self.base}/state"
        settings = f"{self.base}/settings"
        entities = {
            ("sensor", "score"): {
                "name": "Ordnung",
                "icon": "mdi:broom",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "state_topic": state,
                "value_template": "{{ value_json.score }}",
                "json_attributes_topic": state,
            },
            ("binary_sensor", "messy"): {
                "name": "Unordentlich",
                "device_class": "problem",
                "state_topic": state,
                "value_template": "{{ 'ON' if value_json.tidy == false else 'OFF' }}",
            },
            ("sensor", "reason"): {
                "name": "Begründung",
                "icon": "mdi:message-text",
                "state_topic": state,
                "value_template": "{{ (value_json.reason or '')[:250] }}",
            },
            ("sensor", "last_check"): {
                "name": "Letzte Prüfung",
                "device_class": "timestamp",
                "state_topic": state,
                "value_template": "{{ value_json.timestamp }}",
            },
            ("sensor", "streak"): {
                "name": "Streak",
                "icon": "mdi:fire",
                "unit_of_measurement": "Tage",
                "state_class": "measurement",
                "state_topic": state,
                "value_template": "{{ value_json.streak }}",
            },
            ("sensor", "record"): {
                "name": "Rekord",
                "icon": "mdi:trophy",
                "unit_of_measurement": "Tage",
                "state_class": "measurement",
                "state_topic": state,
                "value_template": "{{ value_json.record }}",
            },
            ("binary_sensor", "busy"): {
                "name": "Prüfung läuft",
                "device_class": "running",
                "state_topic": f"{self.base}/busy",
            },
            ("camera", "photo"): {
                "name": "Zimmer",
                "topic": f"{self.base}/image",
            },
            ("button", "check"): {
                "name": "Jetzt prüfen",
                "icon": "mdi:camera-iris",
                "command_topic": f"{self.base}/cmd/check",
                "payload_press": "homeassistant",
            },
            ("button", "reference"): {
                "name": "Als aufgeräumt speichern",
                "icon": "mdi:check-decagram",
                "command_topic": f"{self.base}/cmd/reference",
            },
            ("number", "threshold"): {
                "name": "Grenzwert",
                "icon": "mdi:gauge",
                "min": 0,
                "max": 100,
                "step": 5,
                "unit_of_measurement": "%",
                "command_topic": f"{self.base}/set/threshold",
                "state_topic": settings,
                "value_template": "{{ value_json.threshold }}",
                "entity_category": "config",
            },
            ("number", "interval"): {
                "name": "Intervall",
                "icon": "mdi:timer-outline",
                "min": 1,
                "max": 240,
                "step": 1,
                "unit_of_measurement": "min",
                "mode": "box",
                "command_topic": f"{self.base}/set/interval",
                "state_topic": settings,
                "value_template": "{{ value_json.interval }}",
                "entity_category": "config",
            },
            ("text", "active_hours"): {
                "name": "Prüfzeit",
                "icon": "mdi:clock-outline",
                "min": 0,
                "max": 20,
                # leer = immer prüfen; über Mitternacht geht auch (z. B. 20:00-02:00)
                "pattern": r"^(\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*|immer|)$",
                "command_topic": f"{self.base}/set/active_hours",
                "state_topic": settings,
                "value_template": "{{ value_json.active_hours }}",
                "entity_category": "config",
            },
            ("select", "analyzer"): {
                "name": "Bewertung",
                "icon": "mdi:brain",
                "options": ANALYZERS,
                "command_topic": f"{self.base}/set/analyzer",
                "state_topic": settings,
                "value_template": "{{ value_json.analyzer }}",
                "entity_category": "config",
            },
            ("switch", "ollama_fallback"): {
                "name": "Pi-KI als Ersatz",
                "icon": "mdi:raspberry-pi",
                "command_topic": f"{self.base}/set/ollama_fallback",
                "state_topic": settings,
                "value_template": "{{ value_json.ollama_fallback }}",
                "entity_category": "config",
            },
            ("switch", "skip_unchanged"): {
                "name": "Unveränderte überspringen",
                "icon": "mdi:image-multiple",
                "command_topic": f"{self.base}/set/skip_unchanged",
                "state_topic": settings,
                "value_template": "{{ value_json.skip_unchanged }}",
                "entity_category": "config",
            },
            ("switch", "active"): {
                "name": "Überwachung",
                "icon": "mdi:eye-check",
                "command_topic": f"{self.base}/set/active",
                "state_topic": settings,
                "value_template": "{{ value_json.active }}",
            },
        }
        for (component, key), conf in entities.items():
            conf = {**common, **conf, "unique_id": f"{self.node_id}_{key}"}
            topic = f"{prefix}/{component}/{self.node_id}/{key}/config"
            self.client.publish(topic, json.dumps(conf), retain=True)

    # ------------------------------------------------------------- Prüfung

    def _publish_state(self, score, tidy, reason, trigger, analyzer, error=False) -> dict:
        self.streak.update(tidy)
        state = {
            "score": score,
            "tidy": tidy,
            "threshold": self.cfg["threshold"],
            "reason": reason,
            "analyzer": analyzer,
            "trigger": trigger,
            "error": error,
            "paused": analyzer == "paused",
            "streak": self.streak.streak,
            "record": self.streak.record,
            "timestamp": now_iso(),
        }
        self.client.publish(f"{self.base}/state", json.dumps(state, ensure_ascii=False), retain=True)
        log.info("Status: score=%s tidy=%s (%s) – %s", score, tidy, trigger, reason)
        return state

    def _capture(self):
        img = self.camera.capture()
        name = datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
        width = int(self.cfg["storage"].get("photo_width", 1280))
        (self.photo_dir / name).write_bytes(to_jpeg(img, max_width=width, quality=80))
        self.client.publish(f"{self.base}/image", to_jpeg(img, max_width=1280, quality=80), retain=True)
        self.cleanup()
        return img

    def _reuse_last(self, img, name: str, trigger: str):
        """Letztes Ergebnis übernehmen, wenn das Zimmer seit der letzten Bewertung praktisch
        unverändert ist – sonst None (dann wird neu bewertet).

        Greift nur bei automatischen Intervall-Prüfungen: ein manuelles „Jetzt prüfen“ und jede
        Einstellungsänderung bewerten absichtlich immer neu. Verglichen wird mit demselben
        Analyzer, der zuletzt lief, damit kein fremder Score übernommen wird.
        """
        if not self.cfg["skip_unchanged"] or trigger != "interval":
            return None
        last = self._last_result
        if last is None or last.score is None or self._last_eval_img is None or self._last_eval_name != name:
            return None
        try:
            fraction = difference(
                img,
                self._last_eval_img,
                float(self.cfg["reference"]["sensitivity"]),
                self.cfg["reference"].get("ignore_areas"),
            )
        except Exception as exc:  # pragma: no cover - ein Vergleichsfehler darf die Prüfung nie stoppen
            log.debug("Vergleich mit letztem Foto fehlgeschlagen: %s", exc)
            return None
        if fraction <= float(self.cfg["unchanged_fraction"]):
            log.info("Zimmer unverändert (%.1f %% geändert) – Score %s übernommen", fraction * 100, last.score)
            return analyzers.Result(last.score, last.reason, last.analyzer)
        return None

    def check(self, trigger: str) -> dict:
        if not self.cfg["active"]:
            return self._publish_state(None, True, "Überwachung ist ausgeschaltet.", trigger, "paused")
        if not in_active_hours(self.cfg["active_hours"]):
            return self._publish_state(
                None, True, f"Außerhalb der Überwachungszeit ({self.cfg['active_hours']}).", trigger, "paused"
            )
        name = self.cfg["analyzer"]
        self.client.publish(f"{self.base}/busy", "ON", retain=True)
        try:
            img = self._capture()
            result = self._reuse_last(img, name, trigger)
            if result is None:
                if name == "reference":
                    analyzer = self.references
                else:
                    analyzer = analyzers.create(name, self.cfg.data, self.cfg.storage_dir)
                result = analyzer.analyze(img)
                # Nur gültige Bewertungen als Anker merken; der Anker bleibt beim Überspringen stehen,
                # damit auch langsames "Zumüllen" über viele Intervalle irgendwann auffällt.
                if result.score is not None:
                    self._last_eval_img = img
                    self._last_result = result
                    self._last_eval_name = name
        except (CameraError, analyzers.AnalyzerError) as exc:
            log.error("%s", exc)
            # Bei Fehlern gilt das Zimmer als "unbekannt" -> der PC bleibt frei.
            return self._publish_state(None, None, f"Fehler: {exc}", trigger, name, error=True)
        finally:
            self.client.publish(f"{self.base}/busy", "OFF", retain=True)

        tidy = None if result.score is None else result.score >= self.cfg["threshold"]
        return self._publish_state(result.score, tidy, result.reason, trigger, result.analyzer)

    def save_reference(self) -> None:
        try:
            img = self._capture()
        except CameraError as exc:
            log.error("%s", exc)
            return
        count = self.references.add_reference(img)
        log.info("Referenzfoto gespeichert (%d vorhanden)", count)
        self._publish_settings()

    def cleanup(self) -> None:
        storage = self.cfg["storage"]
        cutoff = datetime.now() - timedelta(days=int(storage["keep_days"]))
        max_photos = int(storage.get("max_photos", 0))
        # Dateinamen sind Zeitstempel -> alphabetisch = chronologisch, neueste zuletzt
        photos = sorted(self.photo_dir.glob("*.jpg"))
        for i, photo in enumerate(photos):
            too_many = max_photos and i < len(photos) - max_photos
            if too_many or datetime.fromtimestamp(photo.stat().st_mtime) < cutoff:
                photo.unlink(missing_ok=True)

    # ---------------------------------------------------------------- Loop

    def run(self) -> None:
        m = self.cfg["mqtt"]
        log.info("Verbinde mit MQTT-Broker %s:%s", m["host"], m["port"])
        self.client.connect_async(m["host"], int(m["port"]), keepalive=60)
        self.client.loop_start()
        try:
            while not self.stop_event.is_set():
                timeout = max(1.0, self.next_run - time.monotonic())
                try:
                    job, trigger = self.jobs.get(timeout=timeout)
                except queue.Empty:
                    job, trigger = "check", "interval"
                    self.next_run = time.monotonic() + self.cfg["interval_minutes"] * 60
                if job == "stop":
                    break
                if job == "reference":
                    self.save_reference()
                self.check(trigger)
        finally:
            self.client.publish(f"{self.base}/availability", "offline", retain=True).wait_for_publish(5)
            self.client.loop_stop()
            self.client.disconnect()

    def stop(self) -> None:
        self.stop_event.set()
        self.jobs.put(("stop", "stop"))
