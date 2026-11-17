# BolliSoft Browser

Ein Webbrowser in der Home-Assistant-Seitenleiste – von BolliSoft. Damit öffnest du die Oberflächen von Geräten im
Heimnetz (Router, Drucker, NAS, ESPHome, Tasmota, Shelly, Kameras …) überall dort, wo du Home Assistant erreichst,
also auch von unterwegs in der HA-App, ohne VPN und ohne Portfreigabe.

## So funktioniert es

Im Add-on läuft ein echter Chromium ohne Fenster. Das Add-on schickt das Bild der Seite in die Seitenleiste, Klicks,
Wischen und Tastatur gehen zurück an den Browser. Die Seiten selbst lädt also das Add-on im Heimnetz – dein Handy
oder PC spricht nur mit Home Assistant. Dadurch funktionieren auch Oberflächen, die sich nicht hinter einen Proxy
stecken lassen.

Alle, die das Add-on gleichzeitig offen haben, sehen denselben Browser mit denselben Tabs.

## Startseite

- **Adresse eingeben:** IP-Adresse (`192.168.178.1`), Name (`fritz.box`, `drucker.local`) oder ganze Adresse.
  Alles mit Leerzeichen oder ohne Punkt wird bei DuckDuckGo gesucht.
- **Lesezeichen:** Stern in der Adresszeile oder **+ Hinzufügen**. Mit dem Stift lassen sie sich umbenennen,
  verschieben und entfernen.
- **Geräte aus Home Assistant:** alle Geräte, bei denen Home Assistant eine Weboberfläche kennt („Gerät
  konfigurieren“ auf der Geräteseite), nach Bereich sortiert.
- **Zuletzt besucht:** die letzten Seiten, mit **Leeren** löschbar.

Soll stattdessen immer eine bestimmte Seite kommen, trage sie in der Konfiguration als **Startseite** ein.

## Bedienung

| | PC | Handy und Tablet |
|---|---|---|
| Klicken | Maus (auch rechts und Doppelklick) | Tippen, doppelt tippen |
| Scrollen | Mausrad | Wischen (mit Schwung) |
| Ziehen (Regler, Karten) | Maustaste halten | lange drücken, dann ziehen |
| Tippen | Tastatur, Strg+V fügt ein, Strg+C kopiert | Tastatur öffnet sich bei Eingabefeldern selbst, sonst Tastatur-Knopf |

Weitere Tastenkürzel: F5 / Strg+R neu laden, Alt+← / Alt+→ zurück und vor, Strg+L Adresszeile, mittlere Maustaste
auf einem Tab schließt ihn. Am Handy öffnet der Knopf mit der Zahl die Liste der Tabs.

### Menü (⋮)

- **Text senden:** fügt Text dort ein, wo auf der Seite der Cursor steht – praktisch für Passwörter und lange Texte,
  wenn die Handy-Tastatur zickt.
- **Markierten Text kopieren:** holt die Markierung der Seite in die Zwischenablage deines Geräts.
- **Seite direkt öffnen:** öffnet die Adresse in deinem eigenen Browser (klappt nur, wenn du im Heimnetz bist).
- **Ansicht:** *Handy* zeigt Seiten wie auf einem Smartphone, *Desktop* wie am PC. Am Handy ist *Handy* voreingestellt.
- **Zoom:** macht die Seite größer oder kleiner (50–200 %).
- **Bild:** *Schnell* für langsame Verbindungen, *Scharf* für hochauflösende Bildschirme.
- **Downloads**, **Browser neu starten** und **Browserdaten löschen** (Cookies, Anmeldungen, Verlauf, offene Tabs;
  Lesezeichen und Downloads bleiben).

Diese Einstellungen merkt sich jedes Gerät selbst.

## Dialoge der Seiten

- **Auswahllisten** öffnen sich als Liste in der Oberfläche.
- **Meldungen** der Seite (Hinweise, Rückfragen, Eingaben) erscheinen als Dialog.
- **Passwortabfragen** (z. B. bei Druckern und älteren Routern): Benutzername und Passwort eingeben. Mit **Anmeldung im
  Add-on merken** meldet sich der Browser künftig selbst an. Die Daten liegen nur im Add-on (`/data/logins.json`).
- **Datei auswählen** (Firmware-Update, Sicherung einspielen): Datei auf deinem Gerät wählen, das Add-on übergibt sie
  an die Seite. Große Dateien werden in Teilen hochgeladen.
- **Downloads** (z. B. Sicherungen des Routers) landen in `/share/browser/downloads` – über das Menü herunterladen
  oder per Samba abholen.

## Konfiguration

| Option | Bedeutung |
|---|---|
| Startseite | Adresse für neue Browser und den Haus-Knopf. Leer = eigene Startseite. |
| Zertifikatsfehler ignorieren | Geräte im Heimnetz haben meist selbst signierte Zertifikate. Eingeschaltet (Standard) öffnen sich solche Seiten ohne Warnung. |
| Pausieren nach (Minuten) | Chromium beenden, wenn so lange niemand zusieht (Standard 30). Die Tabs kommen beim nächsten Öffnen wieder. 0 = immer laufen lassen. |

## Gut zu wissen

- Das Add-on steht nur **Administratoren** zur Verfügung: Wer es öffnen kann, erreicht alles im Heimnetz.
- Chromium braucht etwa 200–400 MB Arbeitsspeicher, solange er läuft. Auf einem Raspberry Pi 4 mit 2 GB besser die
  Pause eingeschaltet lassen.
- Ton gibt es nicht, Videos laufen stumm.
- Chromium läuft im Add-on als eigener Benutzer ohne Root-Rechte und ohne Zugriff auf den Home-Assistant-Schlüssel.
  Adressen wie `file:` und `chrome:` sind gesperrt.
- Namen wie `drucker.local` löst Home Assistant über mDNS auf. Klappt ein Name nicht, die IP-Adresse nehmen.
