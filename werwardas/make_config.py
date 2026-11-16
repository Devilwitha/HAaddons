"""Add-on-Optionen (/data/options.json) + MQTT-Zugang (Umgebung) -> config.yaml für den Kameradienst."""

import json
import os
import sys

import yaml

options_path, config_path = sys.argv[1], sys.argv[2]
with open(options_path, encoding="utf-8") as fh:
    o = json.load(fh)

servers = []
for s in o.get("ollama_servers") or []:
    servers.append({k: v for k, v in s.items() if v not in (None, "")})

config = {
    "mqtt": {
        "host": os.environ["MQTT_HOST"],
        "port": int(os.environ.get("MQTT_PORT") or 1883),
        "username": os.environ.get("MQTT_USER", ""),
        "password": os.environ.get("MQTT_PASSWORD", ""),
        # feste Geräte-ID, damit HA immer dasselbe Gerät "WerWarDas Kamera" sieht
        "node_id": "werwardas_kamera",
    },
    "camera": {"type": "http", "url": o["camera_url"], "rotation": int(o.get("rotation", 0))},
    # Anfangswerte; Änderungen aus HA/der Windows-App landen in /data/werwardas/runtime.json und haben Vorrang
    "analyzer": o.get("analyzer", "ollama"),
    "active_hours": o.get("active_hours") or "",
    "interval_minutes": int(o.get("interval_minutes", 30)),
    "threshold": int(o.get("threshold", 60)),
    "prompt_hint": o.get("prompt_hint") or "",
    "ollama": {"servers": servers},
    "claude": {"api_key": o.get("claude_api_key") or ""},
    "storage": {"dir": "/data/werwardas", "keep_days": 7},
}

with open(config_path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=False)
