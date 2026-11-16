# Änderungen

## 1.0.6

- Ton: Der Ton der Capture-Card kommt bei WebRTC mit (Schalter **Ton** in der Leiste, startet nach dem ersten
  Tippen auf die Seite). Bild und Ton werden bewusst nicht abgeglichen, damit das Bild nicht auf den Ton wartet
- Koppeln zuverlässiger: Mehrfaches Drücken bricht einen laufenden Versuch nicht mehr ab; nach einem Absturz der
  Controller-Emulation startet die Bridge sie selbst neu, statt mit „No adapters available“ hängen zu bleiben
- Bridge mit `install.sh` aktualisieren (neu: Paket `alsa-utils` für die Tonaufnahme)

## 1.0.5

- Neu: **Messwerte** in der oberen Leiste – Bildrate der Capture-Card, gesendete Bilder, Bildalter und Kodierzeit am Pi, Ping direkt und über Home Assistant, Puffer/Dekodieren/verworfene Bilder im Browser (grün/gelb/rot)
- Neu: **Reaktionstest** – misst die Zeit vom Tastendruck bis zur Bildänderung (alles außer dem Bildschirm)
- Bridge (mit `install.sh` aktualisieren): liefert die Messwerte, Ping über den Datenkanal; das Testbild zeigt gedrückte Tasten an

## 1.0.4

- Weniger Verzögerung: Eingaben gehen bei WebRTC über einen Datenkanal direkt an den Pi (nicht mehr über Home Assistant), werden sofort statt zum nächsten Bild geschickt, Controller werden alle 4 ms statt 60-mal pro Sekunde abgefragt, der Browser zeigt Bilder mit minimalem Puffer an
- Bridge (mit `install.sh` aktualisieren): WebRTC mit 60 statt 30 Bildern pro Sekunde, schnellere Farbumrechnung, Eingaben gehen sofort an den Controller statt im nächsten Takt
- Nach Updates lädt die Seite immer die neuen Skripte (vorher teils noch alte aus dem Zwischenspeicher der HA-App)
- Pro Controller wird erkannt (wie Xbox und PlayStation): Beschriftung A B X Y / L R ZL ZR und passende Vorlage „Nach Beschriftung“ (rechte Taste A = A)

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
