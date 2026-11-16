# BolliSoft Kamera (ESP32-CAM)

Firmware für die günstige **ESP32-CAM (AI-Thinker)** als WLAN-Kamera, von BolliSoft. Sie liefert Fotos für
[WerWarDas](../werwardas-apps/) und das Add-on [Vorrat](../vorrat/), dazu einen Livestream, und aktualisiert
sich selbst von GitHub.

| Adresse | Inhalt |
|---|---|
| `http://<Name>.local/` | Statusseite mit Vorschau, Version, Update-Stand |
| `http://<Name>.local/capture` | aktuelles Foto (JPEG) |
| `…/capture?led=60` | Foto mit LED, Helligkeit 0–255 (im Kühlschrank etwa 40–80, voll überstrahlt es) |
| `…/capture?flash=1` | Foto mit LED in voller Helligkeit |
| `…/capture?settle=6` | mehr Bilder vorher verwerfen, falls das Foto zu dunkel ist (Standard 4 mit LED, sonst 1) |
| `http://<Name>.local:81/stream` | Livestream (MJPEG), `?led=60` hält die LED an, `?fps=5` begrenzt die Bildrate |
| `http://<Name>.local/settings` | Name, automatische Updates, Passwort, WLAN neu einrichten |

Die Kamera nimmt in voller Auflösung auf (1600×1200). Der Livestream schafft damit etwa 3–6 Bilder pro Sekunde
– genug zum Ausrichten und für die Ansicht in Home Assistant. Es kann immer nur einer gleichzeitig zuschauen.

## Erstes Flashen (einmal per USB)

Am einfachsten mit dem Programmier-Adapter **ESP32-CAM-MB** (USB). Zwei Wege:

**A) Fertige Firmware, ohne PlatformIO:** Aus den [Releases](https://github.com/Devilwitha/HAaddons/releases)
(Tag `esp32cam-v…`) die Datei `bollisoft-cam-komplett.bin` laden und an Adresse `0x0` schreiben, z. B. mit

```powershell
pip install esptool
esptool --chip esp32 --port COM41 write_flash 0x0 bollisoft-cam-komplett.bin
```

(oder im Browser mit dem [ESP Web Flasher](https://espressif.github.io/esptool-js/), Adresse `0x0`).

**B) Selbst bauen mit [PlatformIO](https://platformio.org)** (`pip install platformio`):

```powershell
cd esp32cam
copy include\secrets.example.h include\secrets.h   # optional: WLAN, Name, Passwort schon eintragen
pio run -t upload --upload-port COM41               # COM-Port im Geräte-Manager nachsehen
```

Mit `secrets.h` übernimmt die Kamera die Werte beim ersten Start und verbindet sich gleich mit dem WLAN.

## WLAN einrichten

Ohne gespeichertes WLAN öffnet die Kamera den Hotspot **BolliSoft-Kamera-XXXX**:

1. Mit dem Handy oder PC damit verbinden – die Einrichtungsseite öffnet sich (sonst `http://192.168.4.1`).
2. *Configure WiFi* → WLAN wählen (nur 2,4 GHz), Passwort eingeben.
3. **Name im Netz** vergeben, bei mehreren Kameras je einen eigenen, z. B. `vorrat-kuehlschrank-oben`.
4. **Passwort** für Einstellungen und Updates festlegen (leer = `werwardas`).

Danach ist die Kamera unter `http://<Name>.local` erreichbar. Findet sie ihr WLAN einmal nicht (Router aus),
öffnet sie den Hotspot für 5 Minuten und versucht es danach von selbst wieder. Am besten der Kamera im Router
eine feste IP geben – Home Assistant und Add-ons erreichen `.local`-Namen nicht immer.

## Updates

- **Automatisch:** Die Kamera sucht 2 Minuten nach dem Start und danach alle 12 Stunden (einstellbar unter
  *Einstellungen*, auch „nie“) auf GitHub nach einem neueren Release `esp32cam-v…` und spielt es selbst ein.
  WLAN, Name und Passwort bleiben erhalten. Die Verbindung zu GitHub läuft verschlüsselt mit geprüftem
  Zertifikat.
- **Von Hand:** *Einstellungen → Jetzt nach Update suchen und einspielen*.
- **Selbst gebaut per WLAN:** `pio run -e ota -t upload --upload-port <Name>.local` (in `platformio.ini` steht
  bei `upload_flags` das Passwort; Standard `werwardas`). Selbst gebaute Firmware hat die Version 0.0.0 und wird
  beim nächsten automatischen Update durch das neueste Release ersetzt – für eigene Änderungen die Updates unter
  *Einstellungen* auf „nie“ stellen.

Neue Releases entstehen von selbst, sobald sich etwas in `esp32cam/` auf `main` ändert
(Workflow [esp32cam.yml](../.github/workflows/esp32cam.yml)).

## In Home Assistant

- **Vorrat:** als Kamera-Quelle `http://<IP>/capture?led=60` eintragen.
- **WerWarDas:** `camera_url: http://<IP>/capture` (mit LED: `?flash=1` oder `?led=…`).
- **Livestream als Kamera-Entität:** *Einstellungen → Geräte & Dienste → Integration hinzufügen →
  MJPEG IP Camera*, MJPEG-URL `http://<IP>:81/stream`, Standbild-URL `http://<IP>/capture`.
  So lässt sich die Kamera im Dashboard ansehen und auch als `camera.…` im Add-on Vorrat verwenden.

## Im Kühlschrank

- **WLAN:** Der Kühlschrank schirmt stark ab. Signal auf der Statusseite prüfen (besser als −75 dBm).
  Hilft oft: die Kamera innen oben an der Tür, oder eine ESP32-CAM mit Anschluss für eine externe Antenne.
- **Strom:** Ein flaches USB-Kabel passt meist durch die Türdichtung. Ein Netzteil mit mindestens 1 A
  verwenden – die LED zieht viel Strom, ein schwaches Netzteil führt zu Neustarts (dann `led` niedriger).
- **Licht:** Bei geschlossener Tür ist es dunkel. Die LED (`led=40…80`) reicht für ein Fach; zu hell
  überstrahlt helle Verpackungen.
- **Kälte:** 4 °C machen der ESP32-CAM nichts. Beschlägt die Linse beim Öffnen, hilft ein Foto ein paar
  Sekunden nach dem Schließen (siehe Automation in der [Vorrat-Anleitung](../vorrat/DOCS.md)).
