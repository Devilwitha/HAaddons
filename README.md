# HAaddons

Home-Assistant-Add-on-Repository von **BolliSoft**.

[![Repository hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FDevilwitha%2FHAaddons)

## Add-ons

### [Claude Code](claude_code/)
Chatte mit Claude direkt in der HA-Seitenleiste, im Stil von claude.ai. Claude legt
Automationen an und ändert die Konfiguration, jede Änderung bestätigst du per Klick.
Mit Remote Control geht das auch von unterwegs in der Claude App. Details: [DOCS.md](claude_code/DOCS.md)

### [Bollisoft Server Monitor](bollisoft_monitor/)
CPU, RAM, Laufwerke, Laufzeit, Netzwerk, Ollama und die Dienste eines Windows-Servers in der
HA-Seitenleiste und als Sensoren – mit Knöpfen für Neustart (mit Vorlauf, abbrechbar), Neustart
der Bollisoft-Dienste und Entladen der Ollama-Modelle. Kein Herunterfahren, kein Sleep. Die Daten
schickt `bollisoft-agent.exe` vom Server. Details: [DOCS.md](bollisoft_monitor/DOCS.md)

### [GBA Emulator](gba_emulator/)
Game-Boy-Advance-, Game-Boy- und Game-Boy-Color-Spiele in der HA-Seitenleiste spielen, am PC,
Tablet oder Handy (mit Touch-Gamepad). ROMs per Upload oder Samba nach `/share/gba`. Spielstände
und Speicherstände liegen auf dem Server, so geht es auf jedem Gerät am selben Stand weiter.
Details: [DOCS.md](gba_emulator/DOCS.md)

### [N64 Emulator](n64_emulator/)
Nintendo-64-Spiele in der HA-Seitenleiste spielen, am PC, Tablet oder Handy (Touch-Gamepad mit
Analogstick und C-Tasten). ROMs per Upload oder Samba nach `/share/n64`. Spielstände und
Speicherstände liegen auf dem Server. Details: [DOCS.md](n64_emulator/DOCS.md)

### [Game Remote](switch2_remote_play/)
Eine Spielkonsole aus der HA-Seitenleiste spielen: Video über WebRTC (automatisch MJPEG von
unterwegs), Touch-Gamepad mit zwei Sticks und echte Controller (Xbox, PlayStation) mit einstellbarer
Belegung. Braucht einen Raspberry Pi 5 mit USB-Capture-Card neben der Konsole, der sich per
Bluetooth als Pro Controller ausgibt – eingerichtet mit einem Befehl, siehe
[rpi5-bridge](rpi5-bridge/). Details: [DOCS.md](switch2_remote_play/DOCS.md)

### [WerWarDas Kamera](werwardas/) und [Ollama (WerWarDas)](ollama/)
Aufräum-Wächter: Eine Kamera fotografiert regelmäßig das Zimmer, eine KI bewertet die Ordnung und meldet sie an
Home Assistant. Ist es zu unordentlich, zeigt der Windows-PC oder das Kinderhandy einen Hinweis und sperrt
später. Dazu gehören die Kamera-Software für den Raspberry Pi, die Windows-App, die Android-Apps (Kind und
Eltern), eine ESP32-CAM-Firmware und ein Blueprint – alles in [werwardas-apps](werwardas-apps/).

### [Vorrat](vorrat/)
Zählt Vorräte mit mehreren Kameras (ESP32-CAM, Raspberry Pi oder jede Kamera aus Home Assistant) und einem
Vision-Modell über Ollama und führt die Liste je Ort von selbst: zwei Dosen in den Kühlschrank, eine wieder
heraus – noch eine da. Mit Verlauf, Korrektur von Hand, Mindestbestand mit Einkaufsliste und Sensoren je
Vorrat. Details: [DOCS.md](vorrat/DOCS.md)

### [BolliSoft Themes](bollisoft_themes/)
14 fertige Designs für Home Assistant und ein Editor mit Live-Vorschau, mit dem sich eigene Themes ohne YAML
zusammenklicken lassen (Farben, Hell/Dunkel, Hintergrund mit Leuchten oder Bild, Rundung, Schatten, Schrift).
Dazu drei Karten fürs Dashboard: Glow-Karte (Sensor mit leuchtendem Rand und Verlauf), Raum-Karte (Temperatur,
Luftfeuchte, Schaltknöpfe) und Personen-Karte (wer ist da, Akku des Handys). Details: [DOCS.md](bollisoft_themes/DOCS.md)

### [BolliSoft Browser](bollisoft_browser/)
Ein Webbrowser in der HA-Seitenleiste: öffnet Router, Drucker, NAS, ESPHome und andere Geräte im Heimnetz auch
von unterwegs, ohne VPN. Im Add-on läuft ein echter Chromium, die Seitenleiste zeigt das Bild und gibt Maus, Touch
und Tastatur weiter. Mit Tabs, Lesezeichen, den Geräten aus Home Assistant auf der Startseite, Passwortabfragen,
Datei-Upload (Firmware) und Downloads. Details: [DOCS.md](bollisoft_browser/DOCS.md)

### [BolliSoft Kamera](esp32cam/) (ESP32-CAM-Firmware)
Macht aus einer ESP32-CAM eine WLAN-Kamera für Vorrat und WerWarDas: Foto mit dimmbarer LED, Livestream,
Einrichtung über einen eigenen Hotspot und automatische Updates von GitHub. Details: [README](esp32cam/README.md)

## Installation
Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories →
`https://github.com/Devilwitha/HAaddons` hinzufügen.
