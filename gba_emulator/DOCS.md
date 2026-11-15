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

### Touch-Steuerung
Oben rechts auf **🎮 Touch** tippen:
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

## Konfiguration
| Option | Bedeutung |
|---|---|
| `rom_folder` | Spiele-Ordner, muss unter `/share` oder `/media` liegen. Standard: `/share/gba` |

## Datenschutz
Die Seite ist nur über Home Assistant erreichbar (Ingress), es wird kein Port geöffnet. Emulator
und Kern sind im Add-on enthalten, beim Spielen werden keine externen Server angefragt.

## Lizenzen
EmulatorJS und mGBA stehen unter freien Lizenzen (GPL-3.0 bzw. MPL-2.0). Die Lizenz von EmulatorJS
liegt im Container unter `/opt/emulatorjs/LICENSE`.
