# Änderungen

## 0.1.1

- ESP32-CAM: Beispiele mit gedimmter LED (`?led=60`) für die neue BolliSoft-Kamera-Firmware (Livestream,
  Einrichtung per Hotspot, automatische Updates), Hinweise für Kameras im Kühlschrank

## 0.1.0

- Erste Version: Vorräte mit mehreren Kameras (ESP32-CAM, Raspberry Pi, Kameras aus Home Assistant) und einem
  Vision-Modell über Ollama zählen, Liste je Ort mit Verlauf („Dose Cola 2 → 1“)
- Zählen nach Zeitplan, per Knopf in der Oberfläche oder per Knopf-Entität in Home Assistant (z. B. wenn die
  Kühlschranktür zugeht)
- Von Hand korrigieren, umbenennen/zusammenführen, ignorieren, Mindestbestand mit Einkaufsliste (To-do)
- Sensoren je Ort und je Vorrat per MQTT-Discovery (Gerät „Vorrat“), sonst über die REST-API
