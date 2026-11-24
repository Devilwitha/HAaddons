# Änderungen

## 1.0.27

- Neue Bildqualität **1080p · 60 fps** für die Übertragung *Einzelbilder*: Der Pi kodiert dafür zwei JPEG-Bilder
  gleichzeitig. Ein 1080p-JPEG dauert auf dem Pi 5 etwa so lange, wie ein Bild bei 60 fps Zeit hat, nacheinander
  reichte es nur für etwa 45 fps. JPEG-Qualität in dieser Stufe höchstens 75, sonst wird die Datenrate zu groß
  (je nach Bild 50–150 Mbit/s). *Video* (WebRTC) bleibt in dieser Stufe bei 30 fps – mehr schafft der Pi mit
  H.264 in 1080p nicht
- Messwerte und Aufzeichnung bewerten Einzelbilder nach ihrer eigenen Bildrate
- Bridge mit `install.sh` aktualisieren

## 1.0.26

- **Bluetooth nur, wenn jemand steuert:** Ist in keinem offenen Game Remote die Touch-Steuerung an (und kein
  Controller am Gerät), trennt die Bridge nach 10 s den Bluetooth-Controller von der Konsole und versucht auch nicht
  mehr, sich zu verbinden. Das gilt auch, wenn niemand Game Remote offen hat. Touch-Steuerung einschalten oder am
  Controller eine Taste drücken verbindet sofort wieder. Status der Konsole dann „Bluetooth aus – niemand steuert“
- **Konsole koppeln** funktioniert auch in diesem Zustand
- Bridge mit `install.sh` aktualisieren

## 1.0.25

- **Konsole koppeln** geht jetzt immer: Bisher lehnte die Bridge mit „Die Konsole ist schon verbunden“ ab, sobald
  nxbt eine Bluetooth-Verbindung meldete – auch wenn die Konsole den Controller gar nicht benutzte. Jetzt wird die
  Verbindung getrennt und der Kopplungsmodus gestartet
- Fehlersuche Steuerung: Die Aufzeichnung zählt Tastendrücke im Browser, am Pi und an die Konsole weitergegeben;
  das Protokoll der Bridge zeigt höchstens alle 10 s „Taste … angekommen – Konsole: …“
- Bridge mit `install.sh` aktualisieren

## 1.0.24

- Aufzeichnung bewertet jedes Zeitfenster nach der **gerade eingestellten** Bildqualität: Nach dem Umschalten auf
  1080p · 30 fps galt bisher alles als Einbruch, weil weiter mit 60 fps gerechnet wurde. Bei 30 fps zählt ein Bild
  Versatz an der Fenstergrenze nicht mehr als Einbruch
- Umschalten der Bildqualität und neues HDMI-Signal stehen als Ereignis im Verlauf und als Ursache bei der Lücke
- „Gesendet“ je Zuschauer (mit zwei offenen Fenstern stand bisher die doppelte Bildrate da)
- „langsames Bild“ erst, wenn Kodieren länger dauert als die Zeit pro Bild (30 fps: 33 ms)
- Bridge mit `install.sh` aktualisieren

## 1.0.23

- Ohne HDMI-Signal wartete die Bridge bei jedem Leseversuch 10 s auf die Capture-Card und schrieb jedes Mal
  „select() timeout“ ins Protokoll. Jetzt 2 s (`OPENCV_VIDEOIO_V4L_SELECT_TIMEOUT`), „kein Bild“ erscheint schneller,
  im Protokoll steht einmal „Capture-Card liefert kein Bild mehr (kein HDMI-Signal? Konsole im Standby?)“
- Kein nxbt-Traceback („reset_address“) mehr beim Beenden der Bridge
- Bridge mit `install.sh` aktualisieren

## 1.0.22

- **Steuerung nach Standby der Konsole tot:** nxbt gab das Wiederverbinden nach etwa einer Sekunde auf (beim Start
  der Bridge sogar nach einem Versuch) und wartete danach nur noch im Kopplungsmodus („Konsole: connecting“) – nach
  dem Aufwecken verband sich die Konsole nicht mehr. Die Bridge versucht es jetzt alle 2 s mit der gekoppelten
  Konsole, bis sie wieder erreichbar ist. Kopplungsmodus nur noch über **Konsole koppeln**
- **Schwarzes Bild** wird erkannt: Die Cam Link liefert ohne HDMI-Signal weiter (schwarze) Bilder, bisher stand dann
  „Videosignal ok“. Jetzt wird „Capture“ gelb mit „Bild seit … s schwarz – Konsole im Standby?“
- **4K-Signal:** Messwerte → Capture-Card rät zu 1080p in den Konsolen-Einstellungen (die Cam Link schafft 3840×2160
  über USB am Pi nur mit etwa 20 fps)
- Aufzeichnung: nach einem Neuaufbau von WebRTC (z. B. Codec-Wechsel) keine negativen Werte mehr
- Bridge mit `install.sh` aktualisieren

## 1.0.21

- **Capture-Card neu starten** meldete bei manchen Karten (z. B. Cam Link 4K) „USB-Reset fehlgeschlagen: [Errno 19]
  No such device“: Die Karte meldet sich beim Zurücksetzen komplett ab und neu an – das ist jetzt kein Fehler mehr.
  Die Bridge wartet bis zu 15 s, bis die Karte wieder da ist, und meldet erst dann Erfolg oder „meldet sich nicht
  zurück“. Klappt der USB-Reset gar nicht, meldet sie die Karte über sysfs ab und wieder an
- Bridge mit `install.sh` aktualisieren

## 1.0.20

- **H.264** als Video-Codec (Messwerte → Video-Codec, Vorgabe): Die Aufzeichnungen zeigten, dass der Pi 5 VP8 bei
  1080p kaum schafft (28 ms pro Bild bei 33 ms Zeit, Spitzen über 100 ms, kein anderes Programm beteiligt). x264
  kodiert bei gleicher Datenrate etwa doppelt so schnell und verteilt jedes Bild auf alle Kerne. Fehlt x264 auf dem
  Pi oder H.264 im Browser, bleibt es automatisch bei VP8
- Messwerte → **Video** zeigt den Codec, die Aufzeichnung ebenso
- Bridge mit `install.sh` aktualisieren

## 1.0.19

- Aufzeichnung misst jetzt das ganze System des Pi (alle 0,5 s): Auslastung aller Kerne, Rechenzeit der Bridge
  (mit nxbt und Ton), Takt, Kernel/Interrupts, Wartezeiten (PSI) und die Programme mit der meisten Rechenzeit.
  Einbrüche nennen so auch „anderes Programm auf dem Pi: …“, „Bridge rechnete am Anschlag“ oder „Takt gesenkt“
- Kodieren läuft mit niedrigerer Priorität als die übrige Bridge (`webrtc_encode_nice: -5`): Rechnet der Encoder an
  aufwendigen Bildern mit allen Kernen, kommen Eingaben, nxbt und die Capture-Card trotzdem sofort dran – dann fehlen
  höchstens Videobilder, die Steuerung hakt nicht
- Bridge mit `install.sh` aktualisieren

## 1.0.18

- Messwerte → **Aufzeichnung**: zeichnet alles auf, was Browser und Pi messen (alle 250 ms) – Bilder der
  Capture-Card, gesendet, empfangen und wirklich angezeigt, Kodierzeit, Hänger der Bridge, Aufrufe an nxbt,
  verlorene Pakete, Antwortzeiten, Puffer – dazu einzelne Ereignisse (Lücken, Schlüsselbilder, Einfrieren) und das
  Protokoll der Bridge. Während der Aufzeichnung setzt **Ruckler!** oben in der Leiste eine Markierung
- Beim Beenden entsteht eine Textdatei mit Auswertung: Einbrüche mit vermuteter Ursache (Capture-Card, Kodieren,
  Bridge-Hänger, nxbt, Netz/WLAN, Browser), Zusammenfassung und Verlauf. Download im Browser, Kopieren in die
  Zwischenablage, dazu auf dem HA-Server unter `/share/game_remote/aufzeichnungen` (die letzten 30)
- Bridge mit `install.sh` aktualisieren (sonst enthält die Aufzeichnung nur die Werte des Browsers)

## 1.0.17

- Updates der Bridge unterbrechen nur noch wenige Sekunden statt fast 2 Minuten: Beim Beenden lief der
  Aufräum-Handler von nxbt, startete Bluetooth neu, warf Fehler (`reset_address`) und hielt den Dienst auf, bis
  systemd ihn nach 90 s hart beendete. Die Bridge beendet nxbt jetzt selbst (höchstens 6 s) und wartet nicht mehr
  auf hängende Hintergrundaufrufe; systemd bricht spätestens nach 15 s ab
- Bridge mit `install.sh` aktualisieren

## 1.0.16

- Die Bridge hat Vorrang auf dem Pi (`priority: -10` im Abschnitt `system`): Einlesen, Kodieren und Eingaben warten
  nicht mehr, wenn andere Programme rechnen. Gilt auch für Bluetooth (nxbt) und Ton
- Kurze Hänger alle 2 Sekunden beseitigt: Die Abfrage von Temperatur und Drosselung (`vcgencmd`, seit 1.0.11)
  lief im Hauptablauf der Bridge und hielt dabei Bilder und Eingaben an. Sie läuft jetzt nebenher
- Weniger Pausen durch Pythons Speicherbereinigung (Objekte vom Start werden nicht mehr jedes Mal durchsucht)
- Messwerte → **Pi** zeigt „Hänger … ms“, wenn die Bridge selbst ins Stocken gerät (gelb ab 17 ms, rot ab 33 ms)
- Bridge mit `install.sh` aktualisieren

## 1.0.15

- Messwerte: Eine einzelne Spitze bei **Kodieren + Senden** wird erst rot, wenn sie über der doppelten Zeit pro Bild
  liegt (darunter überbrückt das nächste Bild, es fällt keins aus) – bis dahin gelb
- **Ping über Home Assistant** ohne Ampel, solange die Eingaben direkt über den Datenkanal gehen (dann laufen über
  Home Assistant nur Status und Messwerte, z. B. von unterwegs über Nabu Casa)
- Nur das Add-on aktualisieren, die Bridge bleibt

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
