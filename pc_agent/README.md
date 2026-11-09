# HA PC Agent (Windows)

Meldet deinen Windows-PC an dein eigenes Home Assistant – vergleichbar mit
HASS.Agent. Läuft sichtbar mit Tray-Icon, verbindet sich per MQTT und legt in HA
automatisch ein Gerät an (MQTT-Discovery). Es wird nur an das in `config.json`
eingetragene Home Assistant gesendet, sonst nirgendwohin.

## Was in Home Assistant erscheint
- **Kamera „Bildschirm“** – Screenshot des Desktops im eingestellten Takt
- **Sensoren** – CPU, RAM, aktives Fenster, Betriebszeit (Akku nur bei Laptops)
- **Knöpfe** – Herunterfahren, Neustart, Sperren, Energie sparen, Herunterfahren abbrechen
- **Schalter** – Screenshots senden an/aus

## Voraussetzungen
- Windows mit Python 3 (python.org) – beim Installieren „Add to PATH“ ankreuzen.
- Ein MQTT-Broker, den HA nutzt. Das **Mosquitto-Add-on** reicht; du kannst dich
  mit deinem normalen Home-Assistant-Benutzer und -Passwort anmelden.

## Einrichtung
1. Diesen Ordner `pc_agent` auf den PC kopieren.
2. PowerShell in dem Ordner öffnen und einrichten lassen:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```
   Das legt eine virtuelle Umgebung an, installiert die Abhängigkeiten und richtet
   den Autostart bei der Anmeldung ein (ohne Administratorrechte).
3. `config.json` öffnen und eintragen:
   - `mqtt.host` – IP deines Home Assistant / Brokers (z. B. `192.168.1.201`)
   - `mqtt.username` / `mqtt.password` – dein HA-Login (oder ein eigener MQTT-Benutzer)
   - `device_name` – wie der PC in HA heißen soll
4. Starten:
   ```powershell
   & ".\venv\Scripts\pythonw.exe" ".\agent.py"
   ```
   Beim nächsten Anmelden startet er von selbst. In HA erscheint das Gerät unter
   **Einstellungen → Geräte & Dienste → MQTT**.

## Bedienung
- **Tray-Icon** (unten rechts): Screenshots pausieren, Konfiguration öffnen, Beenden.
- Screenshots lassen sich auch aus HA über den Schalter „Screenshots senden“ pausieren.

## Einstellungen (config.json)
| Feld | Bedeutung |
|---|---|
| `screenshot.enabled` | Kamera/Screenshots überhaupt anbieten |
| `screenshot.interval_seconds` | Takt der Screenshots (Standard 10 s) |
| `screenshot.max_width` | Bild auf diese Breite verkleinern (spart Datenmenge) |
| `screenshot.jpeg_quality` | JPEG-Qualität 1–95 |
| `sensors_interval_seconds` | Takt der Sensorwerte |
| `allow_power_commands` | Herunterfahren/Neustart/Sperren/Sleep erlauben |

## Sicherheit
- Der Agent kann deinen PC herunterfahren/neustarten und überträgt Bildschirmfotos
  in dein HA. Gib HA-Zugriff nur Personen, denen du das erlauben willst.
- Screenshots sind nicht „retained“, bleiben also nicht im Broker liegen.
- Zum Abschalten: `uninstall.ps1` ausführen (entfernt Autostart) und in HA das Gerät löschen.
- Wenn du `allow_power_commands` auf `false` setzt, werden keine Abschalt-Knöpfe angelegt.
