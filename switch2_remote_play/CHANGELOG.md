# Änderungen

## 1.0.3

- Fehler behoben: Das Add-on bekam kein Supervisor-Token (s6-overlay leerte die Umgebung). Darum fehlten die Sensoren und die Optionen im Protokoll beim Start

## 1.0.2

- Sensoren für Home Assistant: Status, Spieler, Sitzung, Gesamtzeit, Sitzungen, Zuletzt gespielt, Aktiv, Konsole, Capture-Card, Bridge – per MQTT als Gerät oder über die REST-API
- Neue Option `use_mqtt`

## 1.0.1

- Neutrale Bezeichnungen: „Spielkonsole“ bzw. „Konsole“ statt Konsolen- und Markennamen in Name, Beschreibung, Oberfläche und Anleitung
- Seitenleisten-Symbol: Gamepad

## 1.0.0

- Erste Version: Video der Konsole über WebRTC (automatisch MJPEG von unterwegs), Touch-Gamepad mit zwei Sticks, echte Controller mit Xbox-/PlayStation-Vorlagen und eigener Belegung
- Raspberry Pi 5 Bridge mit Pro-Controller-Emulation, Watchdog und Ein-Befehl-Installation
