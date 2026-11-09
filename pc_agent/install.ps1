# HA PC Agent – Einrichtung: virtuelle Umgebung, Abhängigkeiten, Autostart bei Anmeldung.
# Ausführen in PowerShell:  powershell -ExecutionPolicy Bypass -File install.ps1
# Es werden KEINE Administratorrechte benötigt (der Agent steuert nur die eigene Sitzung).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "HA PC Agent – Einrichtung in $here" -ForegroundColor Cyan

# 1) Python finden
$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue) }
if (-not $py) { throw "Python wurde nicht gefunden. Bitte Python 3 installieren (python.org)." }

# 2) Virtuelle Umgebung
if (-not (Test-Path "$here\venv")) {
    Write-Host "Erstelle virtuelle Umgebung ..."
    python -m venv "$here\venv"
}
$venvPy  = "$here\venv\Scripts\python.exe"
$venvPyw = "$here\venv\Scripts\pythonw.exe"
& $venvPy -m pip install --quiet --upgrade pip
Write-Host "Installiere Abhängigkeiten ..."
& $venvPy -m pip install --quiet -r "$here\requirements.txt"

# 3) Konfiguration anlegen
if (-not (Test-Path "$here\config.json")) {
    Copy-Item "$here\config.example.json" "$here\config.json"
    Write-Host "config.json angelegt – bitte MQTT-Host, Benutzer und Passwort eintragen." -ForegroundColor Yellow
}

# 4) Autostart: Verknüpfung im Autostart-Ordner (ohne Konsolenfenster, ohne Admin)
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup "HA PC Agent.lnk"
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($lnkPath)
$sc.TargetPath = $venvPyw
$sc.Arguments = "`"$here\agent.py`""
$sc.WorkingDirectory = $here
$sc.Description = "HA PC Agent"
$sc.Save()
Write-Host "Autostart eingerichtet (Verknuepfung im Autostart-Ordner)." -ForegroundColor Green

Write-Host ""
Write-Host "Fertig. Nächste Schritte:" -ForegroundColor Cyan
Write-Host "  1. config.json ausfüllen (MQTT-Zugang)."
Write-Host "  2. Jetzt starten:  & `"$venvPyw`" `"$here\agent.py`""
Write-Host "  3. Beim nächsten Anmelden startet der Agent automatisch."
Write-Host "  In Home Assistant erscheint das Gerät automatisch unter Einstellungen -> Geräte (MQTT)."
