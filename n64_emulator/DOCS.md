# N64 Emulator

*Ein Add-on von BolliSoft.*

Spiele Nintendo-64-Spiele direkt in Home Assistant – in der Seitenleiste unter **N64**, am PC,
Tablet oder Handy. Der Emulator (mupen64plus über EmulatorJS) läuft im Browser. Home Assistant
speichert nur die Dateien. Deshalb spielst du auf jedem Gerät am selben Spielstand weiter.

## Spiele hinzufügen
- **Im Browser:** **Spiel hinzufügen** tippen oder die Datei in die Bibliothek ziehen.
- **Per Samba:** Dateien in den Ordner `n64` der Freigabe `share` kopieren (`/share/n64`).
  Die Bibliothek zeigt sie beim nächsten Öffnen an.

Erlaubt sind `.z64`, `.n64`, `.v64` sowie `.zip` und `.7z` mit einer ROM darin, bis 512 MB. Große Dateien werden in Teilen hochgeladen, weil Home Assistant pro Anfrage höchstens 16 MB annimmt.
Lade nur Spiele hoch, die du besitzt.

## Leistung
N64-Emulation ist deutlich aufwendiger als GBA. Am PC und auf aktuellen Tablets und Handys läuft
das meiste flüssig, auf älteren Geräten kann es ruckeln. Der Browser braucht WebGL 2 (alle
aktuellen Browser). Auf iPhone und iPad nimmt EmulatorJS automatisch den Kern `parallel_n64`,
überall sonst `mupen64plus_next`. Wechseln kannst du im Emulator unter Einstellungen → Kern.

## Steuerung
| Konsole | Tastatur (Standard) |
|---|---|
| Analogstick | T / F / G / H |
| Steuerkreuz | Pfeiltasten |
| A / B | X / S |
| C-Tasten oben / unten / links / rechts | I / K / J / L |
| L / R / Z | Q / E / Tab |
| Start | Enter |

Belegung ändern: im Emulator unten auf das Controller-Symbol. USB- und Bluetooth-Controller werden
erkannt, sobald du eine Taste drückst – mit Controller spielt es sich am besten.

### Controller (Xbox, PlayStation …)
USB- oder Bluetooth-Controller am PC, Tablet oder Android-Handy verbinden und im Spiel eine Taste
drücken. Oben auf **🎮 Controller** tippen:
- **Vorlage Standard:** A = A, X = B (PlayStation ✕ / □), LT = Z, LB/RB = L/R, linker Stick =
  Analogstick, rechter Stick = C-Tasten
- **Vorlage Alternative:** wie Standard, aber B auf der rechten Taste und Z auf RT
- **Tasten-Beschriftung:** Xbox oder PlayStation – wird am Controller erkannt, lässt sich umstellen
- **Einzelne Tasten ändern:** öffnet das Steuerungsmenü des Emulators

Die Vorlage gilt für alle Spiele auf diesem Gerät. Wer in einem Spiel einzelne Tasten ändert,
behält diese Änderung für dieses Spiel.

### Touch-Steuerung
Oben rechts auf **✋ Touch** tippen:
- **Automatisch:** erscheint, wenn du das Spiel mit dem Finger startest
- **Immer an:** z. B. für Tablets oder Touch-Laptops, die nicht erkannt werden
- **Aus:** wenn du mit Controller oder Tastatur spielst
- **Größe, Abstand unten, Abstand seitlich, Deckkraft:** mit den Schiebereglern, die Änderung ist
  sofort zu sehen. Ist der Bildschirm zu klein, verkleinert sich die Steuerung von selbst so weit,
  dass sich nichts überdeckt (im Feld steht dann „mehr passt hier nicht“).

Links liegen Steuerkreuz (oben) und Analogstick (unten), darüber L und Z. Rechts die C-Tasten,
A und B, darüber R. Bedienen lässt sich das Gamepad mit Finger, Stift oder Maus. Die Einstellungen
gelten pro Gerät und Browser, so kann das Handy andere Werte haben als das Tablet. Im Querformat
ist mehr Platz für eine große Steuerung.

**Tipp:** In der HA-Seitenleiste läuft der Emulator in einem eingebetteten Fenster. Manche Browser
erlauben dort kein Vollbild und keinen Controller. Dann **↗ Eigener Tab** oben rechts antippen.

## Spielstände
- **Spielstand des Spiels** (wenn du im Spiel speicherst, inkl. Controller Pak): wird beim Start
  vom Server geladen und danach alle 30 Sekunden sowie beim Verlassen gesichert. Der Punkt oben
  rechts zeigt den Zustand: grün = gesichert, gelb = wird gesichert, rot = Fehler.
- **Speicherstände** (Slot 1–9, im Emulator-Menü speichern/laden): liegen ebenfalls auf dem
  Server, mit Vorschaubild in der Bibliothek. Ein N64-Speicherstand ist etwa 16 MB groß, das
  Speichern dauert daher einen Moment.
- **Vor dem Gerätewechsel** über **← Bibliothek** zurückgehen: Dann wartet die Seite, bis der
  Spielstand gesichert ist.

Alles liegt im Spiele-Ordner:

```
/share/n64/            ROMs
/share/n64/saves/      <Spiel>.sav   (Format wie RetroArch-.srm von mupen64plus-next, vorherige Fassung als .sav.bak)
/share/n64/states/     <Spiel>.slot<N>.state und .png
```

Beim Löschen eines Spiels bleiben seine Spielstände erhalten.

## Automatisch speichern und ein Gerät pro Benutzer
- **Inaktivität:** Nach 10 Minuten ohne Eingabe (Tastatur, Touch, Maus oder Controller) sichert die
  Seite den Spielstand und einen **Auto-Speicherstand**, pausiert das Spiel und beendet die Sitzung.
  Mit **Weiterspielen** geht es an derselben Stelle weiter. Die Minuten stellst du mit der Option
  `idle_minutes` ein (0 = aus).
- **Auto-Speicherstand:** liegt in einem eigenen Slot und überschreibt deine Slots 1–9 nicht. Beim
  nächsten Start des Spiels – auch auf einem anderen Gerät – bietet ein Hinweis oben an, ihn zu laden.
- **Ein Gerät pro Benutzer:** Startest du ein Spiel, während du (derselbe Home-Assistant-Benutzer) schon
  auf einem anderen Gerät spielst, fragt die Seite nach. Mit **Hier spielen** speichert das andere Gerät
  sofort (Spielstand und Auto-Speicherstand), pausiert und meldet sich ab; erst dann startet das Spiel
  hier mit dem frischen Stand. Antwortet das andere Gerät nicht (Tab zu, Akku leer), wird es nach
  15 Sekunden abgemeldet.

## Sensoren in Home Assistant
Das Add-on legt ein Gerät **N64 Emulator** mit diesen Sensoren an (mit eingerichtetem MQTT-Broker,
z. B. Mosquitto, als Gerät unter *Einstellungen → Geräte & Dienste → MQTT*; sonst als einzelne
Entitäten):

| Sensor | Inhalt |
|---|---|
| Wird gespielt | Titel des laufenden Spiels oder „Ruhend“ |
| Spieler | Home-Assistant-Benutzer, der gerade spielt |
| Sitzung | Dauer der laufenden Sitzung |
| Gesamtzeit | gesamte Spielzeit in Stunden |
| Sitzungen | Anzahl gestarteter Spiele; Attribut `starts_pro_spiel` zählt jedes Spiel einzeln |
| Zuletzt gespielt | Zeitpunkt des letzten Starts (Attribute: Spiel, Spieler) |
| Letztes Spiel | Titel des zuletzt gestarteten Spiels |
| Meistgespielt | Spiel mit der meisten Spielzeit; Attribut `rangliste` mit den Top 10 |
| Spiele | Anzahl Spiele in der Bibliothek |
| Aktiv | an, solange jemand spielt – praktisch für Automationen |

Eine Sitzung endet beim Verlassen des Spiels oder spätestens 3 Minuten nach dem letzten
Lebenszeichen (Tab geschlossen, Gerät aus). Die Statistik liegt in `/data/stats.json` und ist im
Backup des Add-ons enthalten. Mit der Option `use_mqtt: false` gehen die Sensoren immer über die
REST-API.

## Konfiguration
| Option | Bedeutung |
|---|---|
| `rom_folder` | Spiele-Ordner, muss unter `/share` oder `/media` liegen. Standard: `/share/n64` |
| `use_mqtt` | Sensoren per MQTT als Gerät anlegen (Standard: an), sonst über die REST-API |
| `idle_minutes` | Minuten ohne Eingabe bis zum automatischen Speichern und Pausieren (Standard 10, 0 = aus) |

## Datenschutz
Die Seite ist nur über Home Assistant erreichbar (Ingress), es wird kein Port geöffnet. Emulator
und Kerne sind im Add-on enthalten, beim Spielen werden keine externen Server angefragt.

## Lizenzen
EmulatorJS (GPL-3.0), mupen64plus-next und parallel-n64 (GPL-2.0) sind freie Software. Die Lizenz
von EmulatorJS liegt im Container unter `/opt/emulatorjs/LICENSE`.
