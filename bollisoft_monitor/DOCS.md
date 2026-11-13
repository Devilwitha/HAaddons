# Bollisoft Server Monitor

Zeigt den Zustand des Bollisoft-Servers (Windows) in Home Assistant und kann ihn neu starten.
Die Daten schickt der Agent `bollisoft-agent.exe` vom Server alle 10 Sekunden an dieses Add-on.

## Was angezeigt wird

- CPU-Auslastung (auch je Kern), Takt, RAM, Auslagerung
- Laufwerke (belegt in %, frei, gesamt)
- Laufzeit, Startzeit, „Neustart ausstehend“ nach Windows-Updates
- Netzwerk (empfangen, gesendet)
- Ollama: online, Version, installierte Modelle, geladene Modelle mit Grösse, Kontext,
  Anteil auf der GPU, CPU und Speicher der Ollama-Prozesse
- Erreichbarkeit der Dienste (Webshop, Blackbox-Viewer, Sim-Live …) mit Antwortzeit
- Die fünf Prozesse mit der meisten CPU, Betriebssystem, Prozessor, Grafik

## Knöpfe

- **Server neu starten** – mit Vorlauf (Standard 60 s), in der Zeit abbrechbar
- **Neustart abbrechen**
- **Bollisoft-Server neu starten** – nur die geplante Aufgabe (Webseite, Spieleserver, Ollama)
- **Ollama-Modelle entladen** – gibt den Arbeitsspeicher frei

Herunterfahren und Energiesparen gibt es absichtlich nicht. Welche Knöpfe erscheinen, legt der
Server in `bollisoft-agent.ini` unter `[commands]` fest. Befehle gehen nur an einen verbundenen
Server und verfallen nach 2 Minuten. Der Agent führt jeden Befehl höchstens einmal aus, auch über
einen Neustart hinweg.

## Einrichtung

1. *Einstellungen → Add-ons → Add-on-Store*, oben rechts ⋮ → *Repositories*, dort
   `https://github.com/Devilwitha/HAaddons` hinzufügen. Das Add-on erscheint unter
   „Devilwitha HA Add-ons“. (Ohne GitHub: den Ordner `bollisoft_monitor` nach `/addons/` kopieren,
   dann steht es unter „Lokale Add-ons“, der Dienstname ist dann `local_bollisoft_monitor`.)
2. Installieren, unter *Konfiguration* einen langen, zufälligen **Token** eintragen, starten.
   (Ohne Token schlägt das Log einen vor.)
3. Auf dem Server `bollisoft-agent.ini` anlegen: `url = http://<Home-Assistant>:8765` und
   derselbe `token`. Dann `install.ps1` als Administrator ausführen (Agent und Skripte liegen im
   Bollisoft-Repo unter `server-monitor/agent/`).
4. In der Seitenleiste erscheint **Server**. Mit eingerichtetem MQTT-Broker (Mosquitto-Add-on)
   legt das Add-on zusätzlich ein Gerät mit allen Sensoren und Knöpfen an.

## Ohne MQTT

Die Sensoren heissen dann `sensor.bollisoft_…` bzw. `binary_sensor.bollisoft_…` (über die
REST-API, ohne eindeutige ID). Befehle gehen über die Seitenleiste oder einen Dienst, z. B. in
einem Skript:

```yaml
action: hassio.addon_stdin
data:
  addon: 1a2b3c4d_bollisoft_monitor   # Slug aus der Adresse der Add-on-Seite
  input: restart_task
```

Mögliche Werte: `restart_server`, `cancel_restart`, `restart_task`, `ollama_unload`.

## Sicherheit

Der Server öffnet keinen Port: der Agent meldet sich, das Add-on antwortet mit den Befehlen.
Die Verbindung ist einfaches HTTP mit Token. Liegen Server und Home Assistant nicht im selben
Netz, die Verbindung über ein VPN führen statt Port 8765 ins Internet freizugeben.
