# BolliSoft Themes

Fertige Designs für Home Assistant, ein Editor für eigene Themes und drei Karten fürs Dashboard – von BolliSoft.

## Was drin ist

- **14 Designs:** PicoDesk, Nordlicht, Mitternacht Gold, Abendrot, Waldlichtung, Graphit, OLED Schwarz, Neonnacht,
  Gletscher, Papier, Kirschblüte, Ozean, Mokka und Minze. Jedes lässt sich einzeln ein- und ausschalten.
- **Editor mit Live-Vorschau:** eigene Themes ohne YAML zusammenklicken.
- **Karten fürs Dashboard:** Glow-Karte, Raum-Karte und Personen-Karte (siehe unten).
- **Import und Export:** vorhandene Themes als YAML übernehmen und weiterbearbeiten, eigene herunterladen und
  weitergeben.

## Einrichten

1. Add-on installieren und starten, in der Seitenleiste **Themes** öffnen.
2. Steht oben „Themes sind noch nicht eingeschaltet“: **Automatisch eintragen** drücken. Das Add-on schreibt
   `themes: !include_dir_merge_named themes` unter `frontend:` in die `configuration.yaml`. Vorher legt es eine
   Sicherung an (`configuration.yaml.vor-themes-…`). Danach **Jetzt neu starten**; das Add-on prüft die
   Konfiguration vor dem Neustart.
3. Ein Theme wählen: in Home Assistant unten links auf dein **Profil** → **Design**.
   Für alle Geräte auf einmal: im Add-on bei einem Theme **Als Standard**. Im Profil muss dafür
   „Backend-ausgewählt“ eingestellt sein (das ist die Voreinstellung).

Die Themes landen in `/config/themes/bollisoft/`. Andere Dateien in `/config/themes` fasst das Add-on nicht an.
Gibt es ein Theme mit gleichem Namen schon in einer alten Datei (z. B. `themes/picodesk.yaml`), zeigt das Add-on
einen Hinweis. **Alte Datei abschalten** benennt diese Datei in `.bak` um, gelöscht wird nichts.

## Eigenes Theme erstellen

**Neues Theme** öffnet den Editor, **Als Vorlage** startet mit einem der Designs. Die Vorschau zeigt sofort, wie
Seitenleiste, Kopfzeile, Kacheln, Schalter, Regler und Diagramme aussehen.

| Bereich | Einstellungen |
|---|---|
| Grundlagen | Name, Modus (Dunkel, Hell oder beides), **Zufallsdesign**, **Flächen aus Hauptfarbe** |
| Akzentfarben | Hauptfarbe (Schalter, Regler, aktive Symbole) und Zweitfarbe |
| Flächen | Hintergrund, Karten und Schrift, bei „Beides“ getrennt für Hell und Dunkel |
| Hintergrund | Einfarbig, Leuchten, Verlauf oder **Bild** (hochladen oder `/local/…`), Stärke |
| Karten | Deckkraft, Rundung, Rahmen (auch in der Hauptfarbe), Schatten |
| Schrift und Leisten | Schriftart, Kopfzeile und Seitenleiste durchsichtig, wie Karten oder in der Hauptfarbe |
| Effekte | Glas-Weichzeichner, Anheben beim Darüberfahren, Knöpfe mit Farbverlauf (brauchen card-mod) |
| Für Profis | einzelne Theme-Variablen überschreiben, z. B. `ha-card-header-font-size` = `22px` |

Ist eine Akzentfarbe auf dem Hintergrund schlecht zu sehen, dunkelt der Editor sie automatisch ab oder hellt sie
auf. **Speichern** schreibt das Theme und lädt die Themes in Home Assistant neu, ein Neustart ist nicht nötig.

**Effekte** brauchen [card-mod](https://github.com/thomasloven/lovelace-card-mod) aus HACS. Ohne card-mod
funktioniert das Theme genauso, nur ohne diese Effekte.

**Hintergrundbilder** speichert das Add-on in `/config/www/bollisoft-themes/`. War der Ordner `/config/www` vorher
nicht da, muss Home Assistant einmal neu starten, bevor `/local/` funktioniert.

## Karten fürs Dashboard

Das Add-on kopiert die Karten nach `/config/www/bollisoft-themes/bollisoft-cards.js` und trägt sie als
Dashboard-Ressource ein. Die Seite einmal neu laden (in der App nach unten ziehen), dann: Dashboard **bearbeiten** →
**Karte hinzufügen** → nach **BolliSoft** suchen. Jede Karte hat einen eigenen Editor. Farben und Rundung kommen
aus dem gewählten Theme. Im Add-on unter **Karten fürs Dashboard** gibt es eine Vorschau mit Beispieldaten in
jedem Theme.

### Glow-Karte

Ein Sensor mit leuchtendem, langsam pulsierendem Rand, dem Symbol in einem Kreis und dem Verlauf als Kurve im
Hintergrund. Die Farbe richtet sich nach der Art des Sensors: Temperatur orange, Luftfeuchte blau, Luftqualität
grün, Strom gelb.

```yaml
type: custom:bollisoft-glow-card
entity: sensor.wohnzimmer_temperatur
name: Temperatur          # optional
icon: mdi:thermometer     # optional
color: auto               # orange, red, yellow, green, teal, cyan, blue, purple, pink, grey, theme oder #hex
hours: 24                 # Verlauf der letzten Stunden
animation: true           # false = Rand leuchtet ohne Pulsieren
```

### Raum-Karte

Der Raum mit Temperatur, Luftfeuchte und deren Verlauf (Temperatur als Fläche, Luftfeuchte gestrichelt), einem
großen Kreis mit Raumsymbol und runden Knöpfen. **Antippen** schaltet Licht, Schalter und Ventilatoren um und
startet Szenen und Skripte. **Lange drücken** zeigt die Details.

```yaml
type: custom:bollisoft-room-card
name: Büro
icon: mdi:desktop-classic
temperature: sensor.buero_temperatur
humidity: sensor.buero_luftfeuchtigkeit
entities:
  - binary_sensor.buero_anwesenheit
  - binary_sensor.buero_fenster
  - light.buero
  - entity: switch.webcam
    icon: mdi:webcam
color: auto                      # ohne Angabe bekommt jeder Raum eine eigene Farbe
navigation_path: /dashboard-haus/buero   # optional: beim Antippen des Raums öffnen
```

### Personen-Karte

„Wer ist da?“ zeigt je Person:

- Bild mit grünem Ring, wenn sie zu Hause ist (gelb in einer Zone, grau unterwegs)
- den Ort („Zuhause · Küche“, Zonenname oder „Unterwegs“) und seit wann
- den Akku des Handys mit Ladeanzeige, dazu WLAN und Bluetooth

Akku, Laden, WLAN und Bluetooth findet die Karte automatisch über den Tracker der Person. Dafür braucht es die
Companion-App. Im Editor wählst du nur die Personen.

```yaml
type: custom:bollisoft-person-card
title: Wer ist da?
persons:
  - person.anna
  - entity: person.ben
    room: sensor.ben_raum          # optional, z. B. aus ESPresense oder Bermuda
    battery: sensor.ben_handy_akku # nur nötig, wenn nicht automatisch gefunden
    details:                       # eigene Zeilen statt WLAN/Bluetooth
      - sensor.ben_handy_wlan
```

**Dashboards im YAML-Modus:** Dort kann das Add-on keine Ressource eintragen. Stattdessen von Hand unter
`lovelace: resources:` die Adresse eintragen, die das Add-on anzeigt (Typ `module`).

## Fehlersuche

| Anzeige | Ursache / Lösung |
|---|---|
| Theme fehlt im Profil | Seite neu laden. Steht im Add-on „noch nicht geladen“: Home Assistant einmal neu starten |
| „Theme-Ordner nicht gefunden“ | `frontend:` steht in einer eigenen Datei oder `themes:` zeigt woanders hin – dort `themes: !include_dir_merge_named themes` eintragen |
| Theme wirkt nicht auf allen Geräten | Im Profil ist ein festes Design gewählt – auf „Backend-ausgewählt“ stellen |
| Karte „Custom element doesn't exist“ | Seite neu laden. War `/config/www` neu: Home Assistant neu starten. Ressource prüfen unter Einstellungen → Dashboards → ⋮ → Ressourcen |
| Kein Verlauf in den Karten | Der Sensor wird nicht aufgezeichnet (Recorder) oder hat keine Zahlenwerte |
