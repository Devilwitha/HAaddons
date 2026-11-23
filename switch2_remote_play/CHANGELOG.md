# Änderungen

## 1.0.14

- Hält 60 fps auch in bewegten Szenen: Der Pi kodiert VP8 mit der Geschwindigkeit, die WebRTC in Browsern
  auf ARM-Geräten nutzt (`webrtc_speed: -12`, aiortc nahm -6 wie auf PCs). libvpx begrenzt damit die
  Rechenzeit pro Bild, statt bei Schnee oder Explosionen auf 30-45 fps einzubrechen. Das Bild ist dabei
  minimal weicher; wer lieber schärfer will, stellt in der config.yaml der Bridge -6 ein
- Verkleinern und Farbumrechnung laufen neben dem Kodieren des vorigen Bildes statt davor
- Messwerte → **Kodieren + Senden** zeigt zusätzlich die Spitze der letzten Sekunden und wird gelb/rot, sobald
  sie die Zeit pro Bild (16,7 ms bei 60 fps) erreicht
- Bridge mit `install.sh` aktualisieren

## 1.0.13

- „Keine Verbindung zur Bridge“ nach einem Neustart des Pi oder der Bridge, obwohl das Video läuft: Die Bridge
  öffnete die Steuerverbindung erst, wenn Bluetooth fertig gestartet war (mit mehreren Versuchen bis zu einer
  Minute). Jetzt ist sie sofort da; die Konsole zeigt so lange „Controller wird gestartet …“
- Bridge mit `install.sh` aktualisieren

## 1.0.12

- Capture-Card liefert wieder volle 60 fps statt immer wieder nur ~40: Die Bridge öffnete die Karte mit einem
  einzigen Bildpuffer. Solange sie ein Bild kopierte, hatte die Karte keinen Platz für das nächste, und es ging
  verloren. Jetzt 3 Puffer (`capture_buffers`); der Lese-Thread holt die Bilder sofort ab, die Verzögerung
  steigt dadurch nicht
- Bridge mit `install.sh` aktualisieren

## 1.0.11

- Keine Hänger mehr durch Schlüsselbilder: aiortc baute den Video-Encoder bei jeder Änderung der Datenrate um
  mehr als 10 % neu auf und schickte danach ein großes Komplettbild. Die Datenrate ist jetzt fest
  (`webrtc_fixed_bitrate`, im Heimnetz kein Problem)
- Der Pi kodiert mit allen 4 Kernen statt mit 2 (`webrtc_threads`)
- Bilder werden im festen Takt gesendet (gleichmäßige Abstände)
- Die Bridge stellt den Pi beim Start auf gleichmäßige Leistung: CPU-Regler „performance“ und
  WLAN-Stromsparen aus (Abschnitt `system` in der config.yaml)
- Messwerte → **Pi**: Temperatur, CPU-Last und Drosselung (Unterspannung, Hitze)
- Bridge mit `install.sh` aktualisieren

## 1.0.10

- *720p · 60 fps* ist jetzt wirklich 720p: Capture-Cards wie die Cam Link 4K liefern immer das HDMI-Signal der
  Konsole (1920×1080) und ignorieren die gewünschte Größe. Die Bridge verkleinert das Bild jetzt selbst – der Pi
  kodiert nur noch 44 % der Bildpunkte und schafft so 60 fps
- Karten ohne MJPEG werden im NV12-Format gelesen (Cam Link 4K): weniger Daten über USB, und das Bild für WebRTC
  entsteht direkt daraus statt über eine doppelte Farbumrechnung
- Messwerte → **Capture-Card** zeigt Format und Verkleinerung, z. B. „1920×1080 · 60 fps · NV12 → 1280×720“
- Bridge mit `install.sh` aktualisieren

## 1.0.9

- Neue Wahl unter **Messwerte → Übertragung**: *Video – Pi kodiert* (WebRTC, wenig Datenrate, mit Ton) oder
  *Einzelbilder – Gerät dekodiert* (JPEG). Bei Einzelbildern kodiert der Pi keinen Videostrom mehr: Liefert die
  Capture-Card selbst JPEG, wird es unverändert durchgereicht, sonst nur schnell als JPEG verpackt. Dekodiert wird
  im Browser. Die Wahl merkt sich der Browser; `stream_mode` in der Konfiguration ist nur die Vorgabe
- Einzelbilder laufen als WebSocket über Home Assistant und ersetzen MJPEG, das über Ingress nicht ankam
  (`stream_mode: mjpeg` gilt jetzt als `jpeg`). Neue Bilder gibt es erst, wenn das Gerät die vorigen gezeichnet
  hat – bei langsamer Leitung sinkt die Bildrate, das Bild bleibt aber aktuell statt hinterherzuhängen
- Auch der automatische Wechsel, wenn WebRTC nicht zustande kommt, nutzt jetzt Einzelbilder
- Bridge mit `install.sh` aktualisieren

## 1.0.8

- Bridge findet die Capture-Card selbst (`device: "auto"`), auch wenn sie nach Umstecken, Neustart oder
  „Capture-Card neu starten“ eine andere Nummer bekommt (z. B. `/dev/video1` statt `/dev/video0`). Ein eingetragenes
  `/dev/videoN`, unter dem keine Capture-Card mehr hängt, wird automatisch durch die gefundene Karte ersetzt
- Bridge mit `install.sh` aktualisieren

## 1.0.7

- Schärferes Bild: Unter **Messwerte** lässt sich die **Bildqualität** umschalten – *720p · 60 fps* (schnell) oder
  *1080p · 30 fps* (scharf). Die Wahl merkt sich der Pi
- WebRTC startet nicht mehr mit niedriger Datenrate: Untergrenze 3 Mbit/s (`webrtc_min_bitrate`), vorher blieb das
  Bild oft lange matschig. Neue Zeile **Datenrate** in den Messwerten
- Neu: **Capture-Card neu starten** (unter Messwerte) – setzt den USB-Anschluss der Karte zurück, wie Aus- und
  Einstecken, auch wenn Game Remote über Home Assistant geöffnet ist
- Bridge mit `install.sh` aktualisieren

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
