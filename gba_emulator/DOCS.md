# GBA Emulator

*Ein Add-on von BolliSoft.*

Spiele Game-Boy-Advance-Spiele (auch Game Boy und Game Boy Color) direkt in Home Assistant –
in der Seitenleiste unter **GBA**, am PC, Tablet oder Handy. Der Emulator (mGBA über EmulatorJS)
läuft im Browser. Home Assistant speichert nur die Dateien. Deshalb spielst du auf jedem Gerät
am selben Spielstand weiter.

## Spiele hinzufügen
- **Im Browser:** **Spiel hinzufügen** tippen oder die Datei in die Bibliothek ziehen.
- **Per Samba:** Dateien in den Ordner `gba` der Freigabe `share` kopieren (`/share/gba`).
  Die Bibliothek zeigt sie beim nächsten Öffnen an.

Erlaubt sind `.gba`, `.gbc`, `.gb` sowie `.zip` und `.7z` mit einer ROM darin, bis 64 MB.
Lade nur Spiele hoch, die du besitzt. Ein BIOS ist nicht nötig.

## Steuerung
| Konsole | Tastatur (Standard) |
|---|---|
| Steuerkreuz | Pfeiltasten |
| A / B | Z / X |
| L / R | Q / E |
| Start / Select | Enter / V |

Belegung ändern: im Emulator unten auf das Controller-Symbol. USB- und Bluetooth-Controller werden
erkannt, sobald du eine Taste drückst.

### Controller (Xbox, PlayStation …)
USB- oder Bluetooth-Controller am PC, Tablet oder Android-Handy verbinden und im Spiel eine Taste
drücken. Oben auf **🎮 Controller** tippen:
- **Vorlage:** *Nach Beschriftung* (Xbox A = A, B = B bzw. PlayStation ✕ = A) oder
  *Wie Nintendo-Anordnung* (die rechte Taste ist A, wie am Original)
- **Tasten-Beschriftung:** Xbox oder PlayStation – wird am Controller erkannt, lässt sich umstellen
- **Einzelne Tasten ändern:** öffnet das Steuerungsmenü des Emulators; dort jede Taste neu belegen

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

Bedienen lässt sich das Gamepad mit Finger, Stift oder Maus.

Die Einstellungen gelten pro Gerät und Browser, so kann das Handy andere Werte haben als das Tablet.

**Tipp:** In der HA-Seitenleiste läuft der Emulator in einem eingebetteten Fenster. Manche Browser
erlauben dort kein Vollbild und keinen Controller. Dann **↗ Eigener Tab** oben rechts antippen.

## Spielstände
- **Spielstand des Spiels** (wenn du im Spiel speicherst): wird beim Start vom Server geladen
  und danach alle 30 Sekunden sowie beim Verlassen gesichert. Der Punkt oben rechts zeigt den
  Zustand: grün = gesichert, gelb = wird gesichert, rot = Fehler.
- **Speicherstände** (Slot 1–9, im Emulator-Menü speichern/laden): liegen ebenfalls auf dem
  Server, mit Vorschaubild in der Bibliothek. Den Slot wählst du in den Emulator-Einstellungen.
- **Vor dem Gerätewechsel** über **← Bibliothek** zurückgehen: Dann wartet die Seite, bis der
  Spielstand gesichert ist.

Alles liegt im Spiele-Ordner:

```
/share/gba/            ROMs
/share/gba/saves/      <Spiel>.sav   (kompatibel mit mGBA am PC, die vorherige Fassung als .sav.bak)
/share/gba/states/     <Spiel>.slot<N>.state und .png
```

Einen Spielstand von einem anderen Emulator übernimmst du so: die `.sav` mit demselben Namen wie
die ROM nach `saves` kopieren. Beim Löschen eines Spiels bleiben seine Spielstände erhalten.

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
Das Add-on legt ein Gerät **GBA Emulator** mit diesen Sensoren an (mit eingerichtetem MQTT-Broker,
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
| `rom_folder` | Spiele-Ordner, muss unter `/share` oder `/media` liegen. Standard: `/share/gba` |
| `use_mqtt` | Sensoren per MQTT als Gerät anlegen (Standard: an), sonst über die REST-API |
| `idle_minutes` | Minuten ohne Eingabe bis zum automatischen Speichern und Pausieren (Standard 10, 0 = aus) |

## Datenschutz
Die Seite ist nur über Home Assistant erreichbar (Ingress), es wird kein Port geöffnet. Emulator
und Kern sind im Add-on enthalten, beim Spielen werden keine externen Server angefragt.

## Lizenzen
EmulatorJS und mGBA stehen unter freien Lizenzen (GPL-3.0 bzw. MPL-2.0). Die Lizenz von EmulatorJS
liegt im Container unter `/opt/emulatorjs/LICENSE`.
