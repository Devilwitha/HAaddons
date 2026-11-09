# HA PC Agent – Einrichtung zum Starten aus dem Quelltext (ohne .exe-Build).
# Legt eine virtuelle Umgebung an und installiert die Abhängigkeiten.
# Autostart und alle Einstellungen regelst du danach im Fenster des Agenten.
#   powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Get-Command python.exe -ErrorAction SilentlyContinue)) {
    throw "Python wurde nicht gefunden. Bitte Python 3 installieren (python.org)."
}
if (-not (Test-Path "$here\venv")) {
    Write-Host "Erstelle virtuelle Umgebung ..."
    python -m venv "$here\venv"
}
$py = "$here\venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
Write-Host "Installiere Abhaengigkeiten ..."
& $py -m pip install --quiet -r "$here\requirements.txt"

Write-Host ""
Write-Host "Fertig. Agent starten (oeffnet das Einstellungsfenster):" -ForegroundColor Green
Write-Host "  & `"$here\venv\Scripts\pythonw.exe`" `"$here\agent.py`""
Write-Host "Im Fenster traegst du MQTT-Zugang ein und setzt bei Bedarf 'Automatisch mit Windows starten'."
