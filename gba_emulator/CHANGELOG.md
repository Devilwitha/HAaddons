# Änderungen

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
