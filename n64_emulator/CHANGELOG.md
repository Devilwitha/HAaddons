# Änderungen

## 1.0.4

- Automatisch speichern: nach `idle_minutes` (Standard 10) ohne Eingabe Spielstand und Auto-Speicherstand sichern, Spiel pausieren, Sitzung beenden; beim nächsten Start Auto-Speicherstand laden
- Pro Home-Assistant-Benutzer nur eine Sitzung: Start auf einem zweiten Gerät fragt nach, das erste Gerät speichert und meldet sich ab, dann geht es mit dem frischen Stand weiter
- Lebenszeichen der Sitzung alle 10 statt 30 Sekunden
- Uploads bis 512 MB (vorher 96 MB); zu große Dateien meldet die Bibliothek schon vor dem Hochladen

## 1.0.3

- Fehler behoben: Das Add-on bekam kein Supervisor-Token und keine eigenen Umgebungswerte (s6-overlay leerte die Umgebung). Darum fehlten die Sensoren, die Version stand auf „dev“ und der Server lief mit GBA-Einstellungen (N64-Uploads wurden abgelehnt)
- Absicherung: Fehlt die System-Kennung, liest der Server sie aus der eigenen Oberfläche

## 1.0.2

- Sensoren für Home Assistant: Wird gespielt, Spieler, Sitzung, Gesamtzeit, Sitzungen (mit Starts pro Spiel), Zuletzt gespielt, Letztes Spiel, Meistgespielt (Rangliste), Spiele, Aktiv – per MQTT als Gerät oder über die REST-API
- Neue Option `use_mqtt`

## 1.0.1

- Controller-Belegung: Vorlagen Standard (LT = Z) und Alternative (RT = Z), Xbox- oder PlayStation-Beschriftung, Knopf für einzelne Tasten
- Controller, der schon vor dem Spielstart aktiv war, wird jetzt Spieler 1 zugeordnet

## 1.0.0

- Erste Version: Bibliothek in der Seitenleiste „N64“ mit Upload, Drag & Drop und Löschen
- Emulator: EmulatorJS 4.2.3 mit mupen64plus_next (auf iPhone/iPad parallel_n64)
- Spielstände (EEPROM/SRAM/Flash/Controller Pak) und Speicherstände (Slot 1–9) liegen auf dem Server unter `/share/n64`
- Touch-Steuerung mit Analogstick, C-Tasten und L/R/Z; einstellbar (Automatisch / Immer an / Aus, Größe, Position, Deckkraft), bedienbar mit Finger, Stift oder Maus
