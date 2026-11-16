# Vorrat

Zählt Vorräte mit Kameras und KI und führt die Liste von selbst – von BolliSoft.

Kameras schauen in den Kühlschrank, ins Vorratsregal oder in den Getränkekeller. Bei jedem Durchlauf
fotografiert das Add-on alle Kameras eines Ortes, ein Vision-Modell über **Ollama** zählt, was zu sehen
ist, und die Liste wird angepasst. Aus den Unterschieden entsteht der Verlauf:

```
Kühlschrank: Dose Cola  0 → 2   (+2, zwei Dosen hineingestellt)
Kühlschrank: Dose Cola  2 → 1   (−1, eine herausgenommen)  →  noch 1 im Kühlschrank
```

Das Add-on zählt also, was **gerade da ist**, statt Bewegungen hinein und heraus zu verfolgen. Das ist
deutlich zuverlässiger: Ein verpasstes Foto bringt die Liste nicht dauerhaft durcheinander, der nächste
Durchlauf stimmt wieder.

## Was du brauchst

- **Kameras**, die ein Foto per Adresse liefern:
  - **ESP32-CAM** mit der Firmware aus [`werwardas-apps/esp32cam`](../werwardas-apps/esp32cam):
    `http://<IP>/capture?flash=1` (mit `flash=1` schaltet sie die LED dazu – im dunklen Kühlschrank nötig)
  - **Raspberry Pi** mit Kamera, z. B. mit dem WerWarDas-Kameradienst oder jedem Programm, das ein JPEG
    unter einer Adresse anbietet
  - **Jede Kamera, die schon in Home Assistant ist** (ESPHome, Generic Camera, Frigate …): einfach die
    Entität eintragen, z. B. `camera.kuehlschrank`
- **Ollama** mit einem Modell, das Bilder versteht:
  - am schnellsten auf einem PC mit Grafikkarte (`qwen2.5vl:7b`, wenige Sekunden pro Foto)
  - oder das **Ollama-Add-on** aus diesem Repository (`qwen2.5vl:3b`; auf einem Raspberry Pi dauert ein
    Foto eine bis mehrere Minuten – für den Kühlschrank reicht das)
- Optional **Mosquitto broker** (MQTT): dann gibt es ein Gerät „Vorrat“ mit Sensoren und Knöpfen.

## Einrichten

1. Add-on installieren.
2. Unter **Konfiguration** (oben rechts ⋮ → *Als YAML bearbeiten*) die Kameras eintragen, zum Beispiel:

   ```yaml
   cameras:
     - name: Kühlschrank oben
       source: "http://192.168.1.170/capture?flash=1"
       location: Kühlschrank
     - name: Kühlschrank unten
       source: "http://192.168.1.171/capture?flash=1"
       location: Kühlschrank
     - name: Vorratsregal
       source: camera.vorratsregal
       location: Vorratsregal
       rotation: 90
   ollama_servers:
     - url: "http://192.168.1.139:11434"
       model: "qwen2.5vl:7b"
     - url: "http://ac854b7e-ollama:11434"
       model: "qwen2.5vl:3b"
       timeout_seconds: 1800
   ```

   Kameras mit demselben `location` bilden einen Ort. Ihre Zahlen werden **addiert** – jede Kamera
   sollte also einen anderen Bereich zeigen (z. B. oberes und unteres Fach).
3. Add-on starten und in der Seitenleiste **Vorrat** öffnen. Mit **Jetzt zählen** den ersten Durchlauf
   starten.

## Optionen

| Option | Bedeutung |
|---|---|
| `cameras` | Name, Quelle (`http://…` oder `camera.…`), Ort, optional `rotation` (0/90/180/270) |
| `ollama_servers` | Server der Reihe nach; der erste erreichbare mit geladenem Modell zählt. `timeout_seconds`, `max_width` (Bildbreite für die KI, Standard 1024) |
| `interval_minutes` | regelmäßig zählen, 0 = nur per Knopf |
| `active_hours` | Zeitraum fürs regelmäßige Zählen, z. B. `06:00-23:00` |
| `items_hint` | Hinweis für die KI, z. B. welche Produkte es meistens gibt |
| `todo_entity` | To-do-Liste für fehlende Vorräte, z. B. `todo.einkaufsliste` |
| `use_mqtt` | Gerät „Vorrat“ per MQTT (Standard: an), sonst Sensoren über die REST-API |

## Die Liste

- **− / +** korrigiert die Anzahl von Hand. Der nächste Durchlauf der Kamera überschreibt sie wieder.
- **Auf einen Namen tippen:**
  - umbenennen; gibt es den Namen schon, werden beide zusammengeführt (z. B. „Cola Dose“ in „Dose Cola“).
    Die KI bekommt ab dann den neuen Namen vorgegeben.
  - **Mindestens:** fällt die Anzahl darunter, steht der Vorrat auf „fehlt“ und kommt auf die Einkaufsliste
  - **Ignorieren:** die Kamera zählt ihn nicht mehr (z. B. Ketchup, der immer da ist)
  - **Löschen**
- Vorräte, die nur die Kamera erkannt hat und die seit drei Tagen auf 0 stehen, verschwinden von selbst.
  Mit Mindestbestand oder nach einer Änderung von Hand bleiben sie.

## In Home Assistant

Mit MQTT entsteht das Gerät **Vorrat**:

| Entität | Inhalt |
|---|---|
| `sensor.vorrat_<ort>` | Anzahl aller Vorräte am Ort; Attribute: alle Vorräte, fehlende, letzte Zählung, Fehler |
| `sensor.vorrat_<ort>_<vorrat>` | Anzahl eines Vorrats, z. B. `sensor.vorrat_kuehlschrank_dose_cola` |
| `button.vorrat_<ort>_zaehlen` | Ort jetzt zählen |
| `button.vorrat_alle_zaehlen` | alle Orte zählen |
| `sensor.vorrat_status` | Bereit, Zählt …, Fehler |
| `sensor.vorrat_letzte_aenderung` | z. B. „Kühlschrank: Dose Cola 2 → 1“ (Attribut: die letzten 10) |

Ohne MQTT gibt es dieselben Sensoren über die REST-API, aber keine Knöpfe.

**Beispiel: zählen, wenn die Kühlschranktür zugeht** (mit Türkontakt):

```yaml
automation:
  - alias: Kühlschrank nach dem Schließen zählen
    trigger:
      - platform: state
        entity_id: binary_sensor.kuehlschrank_tuer
        to: "off"
        for: "00:00:05"
    action:
      - service: button.press
        target:
          entity_id: button.vorrat_kuehlschrank_zaehlen
```

Dann reicht `interval_minutes: 0`. Die ESP32-CAM leuchtet mit `flash=1` selbst, auch bei geschlossener Tür.

## Gut zu wissen

- **Genauigkeit:** Die KI erkennt Verpackungen, die gut zu sehen sind. Verdeckte Dinge in der zweiten
  Reihe oder in undurchsichtigen Dosen kann sie nicht zählen. Am besten klappt es mit Kameras von vorn
  auf übersichtliche Fächer. `items_hint` und das Zusammenführen von Namen helfen, dass dieselben Produkte
  immer gleich heißen.
- **Größeres Modell = genauer:** `qwen2.5vl:7b` zählt merklich besser als `3b`.
- **AI HAT+ (Hailo):** Ein Add-on kann den KI-Beschleuniger unter Home Assistant OS nicht nutzen (es gibt
  dort keinen Treiber dafür). Seine üblichen Modelle erkennen außerdem nur allgemeine Klassen wie
  „Flasche“ oder „Tasse“, keine Produkte. Gezählt wird deshalb über Ollama.
- **Datenschutz:** Fotos bleiben im Heimnetz (Kamera → Home Assistant → Ollama). Gespeichert wird nur das
  letzte Foto je Kamera in `/data` (im Backup des Add-ons).

## Fehlersuche

| Anzeige | Ursache / Lösung |
|---|---|
| „Kamera … nicht erreichbar“ | Adresse im Browser öffnen – es muss direkt ein Bild kommen |
| „Modell … nicht geladen“ | Auf dem Ollama-Server `ollama pull qwen2.5vl:3b` (bzw. das eingetragene Modell) |
| „Kein Ollama-Server konnte zählen“ | Server aus oder Zeit zu kurz: `timeout_seconds` erhöhen |
| Namen ändern sich ständig | Namen zusammenführen, `items_hint` ergänzen, größeres Modell |
