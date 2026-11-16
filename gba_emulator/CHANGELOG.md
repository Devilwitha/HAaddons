# Änderungen

## 1.0.9

- Fehler behoben: Nach einem Update benutzte der Browser bzw. die Home-Assistant-App teils noch die alten Skripte aus dem Zwischenspeicher – das Spiel blieb dann weiter bei „Spieldaten herunterladen …MB“ stehen. Skripte und Stylesheets tragen jetzt die Version in der Adresse und werden bei jedem Laden beim Server geprüft

## 1.0.8

- Fehler behoben: Große Spiele blieben beim Laden bei „Spieldaten herunterladen …MB“ stehen (große Antworten über Home-Assistant-Ingress). Die Spielseite lädt das Spiel jetzt selbst in Teilen zu 4 MB mit Fortschrittsanzeige und Wiederholung
- Geladene Spiele bleiben im Browser-Cache (bei HTTPS, z. B. über Nabu Casa oder die App) – der nächste Start geht ohne Download

## 1.0.7

- Fehler behoben: Uploads über 16 MB scheiterten mit „Maximum request body size 16777216 exceeded“ – das ist die Grenze von Home-Assistant-Ingress pro Anfrage. ROMs und Speicherstände gehen jetzt in Teilen zu 8 MB (betraf große N64-Spiele und alle N64-Speicherstände)

## 1.0.6

- Automatisch speichern: nach `idle_minutes` (Standard 10) ohne Eingabe Spielstand und Auto-Speicherstand sichern, Spiel pausieren, Sitzung beenden; beim nächsten Start Auto-Speicherstand laden
- Pro Home-Assistant-Benutzer nur eine Sitzung: Start auf einem zweiten Gerät fragt nach, das erste Gerät speichert und meldet sich ab, dann geht es mit dem frischen Stand weiter
- Lebenszeichen der Sitzung alle 10 statt 30 Sekunden
- Zu große Dateien meldet die Bibliothek schon vor dem Hochladen

## 1.0.5

- Fehler behoben: Das Add-on bekam kein Supervisor-Token (s6-overlay leerte die Umgebung). Darum fehlten die Sensoren in Home Assistant und die Version stand auf „dev“
- Absicherung: Fehlt die System-Kennung, liest der Server sie aus der eigenen Oberfläche

## 1.0.4

- Sensoren für Home Assistant: Wird gespielt, Spieler, Sitzung, Gesamtzeit, Sitzungen (mit Starts pro Spiel), Zuletzt gespielt, Letztes Spiel, Meistgespielt (Rangliste), Spiele, Aktiv – per MQTT als Gerät oder über die REST-API
- Neue Option `use_mqtt`

## 1.0.3

- Controller-Belegung: Vorlagen für Xbox und PlayStation (nach Beschriftung oder wie Nintendo), Anzeige mit passender Tastenbeschriftung, Knopf für einzelne Tasten
- Controller, der schon vor dem Spielstart aktiv war, wird jetzt Spieler 1 zugeordnet (vorher wurden seine Tasten ignoriert)

## 1.0.2

- Gemeinsamer Code mit dem neuen N64 Emulator (keine sichtbare Änderung)
- Versionsnummer in der Oberfläche kommt jetzt automatisch aus der Add-on-Version

## 1.0.1

- Touch-Steuerung lässt sich jetzt auch mit Maus oder Stift bedienen (vorher nur mit dem Finger)

## 1.0.0

- Erste Version: Bibliothek in der Seitenleiste „GBA“ mit Upload, Drag & Drop und Löschen
- Emulator: EmulatorJS 4.2.3 mit mGBA-Kern (GBA, GB, GBC), Touch-Gamepad, Tastatur, Controller
- Spielstände (SRAM) und Speicherstände (Slot 1–9) liegen auf dem Server unter `/share/gba`
- Touch-Steuerung einstellbar (Automatisch / Immer an / Aus, Größe, Position, Deckkraft), pro Gerät gespeichert
