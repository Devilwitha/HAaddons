# HA PC Agent (Windows)

Meldet deinen Windows-PC an dein eigenes Home Assistant – vergleichbar mit
HASS.Agent. Läuft als Fenster-App mit Infobereich-Symbol (Tray), verbindet sich
per MQTT und legt in HA automatisch ein Gerät an (MQTT-Discovery). Es wird nur an
das in den Einstellungen eingetragene Home Assistant gesendet, sonst nirgendwohin.

## Was in Home Assistant erscheint
- **Kamera „Bildschirm“** – Screenshot des Desktops im eingestellten Takt
- **Sensoren** – CPU, RAM, aktives Fenster, Betriebszeit (Akku nur bei Laptops)
- **Knöpfe** – Herunterfahren, Neustart, Sperren, Energie sparen, Herunterfahren abbrechen
- **Schalter** – Screenshots senden an/aus

## Bedienung
- **Doppelklick auf die .exe** öffnet das **Einstellungsfenster**.
- **Fenster schließen** beendet das Programm **nicht** – es läuft im Infobereich
  (unten rechts neben der Uhr) weiter.
- **Rechtsklick aufs Tray-Symbol:** Einstellungen öffnen, Screenshots pausieren, Beenden.
- Im Fenster: MQTT-Zugang, Gerätename, Screenshot-Takt/Größe, Sensoren-Takt,
  Abschaltbefehle erlauben, **„Automatisch mit Windows starten“**, sowie
  **„Verbindung testen“** und **„Speichern & Anwenden“**.

## Variante A – fertige .exe bauen (empfohlen)
Einmalig, danach brauchst du kein Python mehr auf dem Ziel-PC.
1. Ordner `pc_agent` auf einen PC mit Python 3 kopieren.
2. PowerShell im Ordner öffnen:
   ```powershell
   powershell -ExecutionPolicy Bypass -File build.ps1
   ```
3. Ergebnis: `dist\HA PC Agent.exe`. Diese Datei irgendwohin kopieren und
   doppelklicken. Beim ersten Start öffnet sich das Einstellungsfenster.

## Variante B – aus dem Quelltext starten
1. `powershell -ExecutionPolicy Bypass -File install.ps1`
2. Starten: `& ".\venv\Scripts\pythonw.exe" ".\agent.py"`

## Voraussetzungen
- Windows. Für den Build (Variante A) Python 3 (python.org, „Add to PATH“).
- Ein MQTT-Broker, den HA nutzt. Das **Mosquitto-Add-on** reicht; als MQTT-Benutzer
  und -Passwort funktioniert dein normaler Home-Assistant-Login.

In Home Assistant erscheint das Gerät automatisch unter
**Einstellungen → Geräte & Dienste → MQTT**. Eine fertige Dashboard-Karte liegt in
[home_assistant/dashboard_card.yaml](home_assistant/dashboard_card.yaml).

## Wo liegen die Einstellungen?
In `%APPDATA%\HA-PC-Agent\config.json` (nicht neben der .exe). Dort liegt auch
`agent.log`. Die Werte änderst du am besten im Fenster, nicht von Hand.

## Sicherheit
- Der Agent kann deinen PC herunterfahren/neustarten und überträgt Bildschirmfotos
  in dein HA. Gib HA-Zugriff nur Personen, denen du das erlauben willst.
- Screenshots sind nicht „retained“, bleiben also nicht im Broker liegen.
- Screenshots jederzeit pausierbar – im Fenster, im Tray-Menü oder aus HA.
- Ohne „Herunterfahren … erlauben“ werden keine Abschalt-Knöpfe angelegt.
- Entfernen: „Automatisch mit Windows starten“ abwählen (oder `uninstall.ps1`),
  Programm beenden, in HA das Gerät löschen.
