# Claude für Home Assistant

*Ein Add-on von BolliSoft.*

Chatte direkt in Home Assistant mit Claude – so wie auf claude.ai. Claude kann
Automationen anlegen, deine Konfiguration ändern, prüfen und neu laden. Jede
Änderung musst du vorher mit **Erlauben** bestätigen.

Über **Remote Control** erscheint dein Home Assistant zusätzlich in der Claude App
und auf claude.ai/code – so kannst du auch von unterwegs Änderungen machen.

## Erste Schritte
1. Add-on starten und **Claude** in der Seitenleiste öffnen.
2. Auf **Anmelden** tippen, im neuen Fenster mit deinem Claude-Konto anmelden.
3. Den angezeigten Code kopieren, einfügen, **Verbinden** – fertig.

Die Anmeldung bleibt gespeichert, auch nach Updates und Neustarts.
Voraussetzung ist ein Claude-Abo (Pro, Max, Team oder Enterprise).

## Automatik
Unten links im Eingabefeld sitzt der Schalter **Nachfragen / Automatisch**.
Steht er auf **Automatisch**, führt Claude Befehle und Dateiänderungen ohne Rückfrage
aus. Das gilt auch für Remote Control in der Claude App. Nur wenn Claude dir eine
echte Frage stellt (Auswahl), wartet es auf deine Antwort. Die Einstellung bleibt
gespeichert.

## Bilder anhängen
Mit der **Büroklammer** im Eingabefeld hängst du Bilder an, am Handy auch direkt mit der Kamera. Am PC geht
es auch mit **Strg+V** (z. B. ein Screenshot) oder per Ziehen und Ablegen. Bis zu 10 Bilder pro Nachricht,
JPG, PNG, WebP oder GIF.

Claude sieht die Bilder und kann sie beschreiben oder auswerten. Beispiele: „Was bedeutet diese
Fehlermeldung?“ mit einem Screenshot, „Baue mir eine Karte wie auf dem Bild“ oder „Nimm das Foto als
Hintergrund fürs Dashboard“.

Für Claude wird jedes Bild auf höchstens 1568 Pixel verkleinert. Das Original liegt unter
`/share/claude/uploads/`, damit Claude es auch weiterverwenden kann, z. B. nach `/config/www` kopieren.
Originale werden nach 30 Tagen gelöscht.

## Optionen
| Option | Bedeutung |
|---|---|
| Remote Control | Home Assistant in der Claude App / auf claude.ai/code verfügbar machen |
| Session-Name | Name, unter dem es dort erscheint (Standard: „HAOS“) |
| Modell | Optional, z. B. `sonnet` oder `opus`. Leer = Standard |
| Berechtigungsmodus | `default`: jede Änderung bestätigen · `acceptEdits`: Dateiänderungen ohne Rückfrage · `plan`: Claude macht erst einen Plan |

## Tipps
- Mach vor größeren Änderungen ein Backup (Einstellungen → System → Backups).
- Beispiele: „Schalte das Flurlicht bei Bewegung nach Sonnenuntergang ein“,
  „Welche Batterien sind fast leer?“, „Prüfe meine Konfiguration“.
