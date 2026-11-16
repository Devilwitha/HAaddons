<#
.SYNOPSIS
  Installiert den WerWarDas-Kameradienst (Foto + Bewertung) auf einem Windows-PC.

.DESCRIPTION
  Für alle ohne eigenen Raspberry Pi: Der Dienst holt das Foto von einer ESP32-CAM (oder Webcam),
  bewertet es (reference / ollama / claude) und meldet das Ergebnis per MQTT an Home Assistant.
  Er läuft als geplante Aufgabe bei der Anmeldung, unsichtbar und ohne Admin-Rechte.

  Installiert nach %LOCALAPPDATA%\WerWarDas\kamera:
    werwardas\   Programm (Kopie aus pi\werwardas)
    venv\        Python-Umgebung
    config.yaml  Konfiguration (wird bei Updates nicht überschrieben)
    data\        Fotos, Referenzbilder, runtime.json
    werwardas-kamera.log

.EXAMPLE
  .\install-windows.ps1 -MqttHost 192.168.1.201 -MqttUser werwardas -MqttPassword geheim `
      -CameraUrl http://werwardas-cam.local/capture -Analyzer ollama
  .\install-windows.ps1            # Update: Programm erneuern, Konfiguration behalten, neu starten
  .\install-windows.ps1 -Uninstall
#>
param(
    [string]$MqttHost = "homeassistant.local",
    [string]$MqttUser = "werwardas",
    [string]$MqttPassword = "",
    [string]$CameraUrl = "",
    [ValidateSet("reference", "ollama", "claude")][string]$Analyzer = "reference",
    [string]$OllamaUrl = "http://localhost:11434",
    [string]$OllamaModel = "qwen2.5vl:7b",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "WerWarDas Kamera"
$AppDir = Join-Path $env:LOCALAPPDATA "WerWarDas\kamera"
$Config = Join-Path $AppDir "config.yaml"
$LogFile = Join-Path $AppDir "werwardas-kamera.log"

function Stop-Dienst {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$AppDir*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall) {
    Stop-Dienst
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
    Write-Host "WerWarDas-Kameradienst entfernt."
    return
}

# --- Python finden
$py = $null
foreach ($cand in @("py -3", "python")) {
    $exe, $arg = $cand -split " ", 2
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        $ver = & $exe $arg -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        if ($ver -eq "True") { $py = @($exe) + @($arg | Where-Object { $_ }); break }
    }
}
if (-not $py) { throw "Python 3.10 oder neuer fehlt: https://www.python.org/downloads/ (Haken bei 'Add to PATH')" }

Stop-Dienst
New-Item -ItemType Directory -Force (Join-Path $AppDir "data") | Out-Null

# --- Programm kopieren
$src = Join-Path $PSScriptRoot "werwardas"
Remove-Item -Recurse -Force (Join-Path $AppDir "werwardas") -ErrorAction SilentlyContinue
Copy-Item -Recurse $src (Join-Path $AppDir "werwardas")
Get-ChildItem -Recurse -Directory -Filter __pycache__ (Join-Path $AppDir "werwardas") | Remove-Item -Recurse -Force

# --- Python-Umgebung
$venvPy = Join-Path $AppDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Python-Umgebung anlegen ..."
    & $py[0] $py[1..9] -m venv (Join-Path $AppDir "venv")
}
Write-Host "Pakete installieren ..."
& $venvPy -m pip install -q --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.txt") Pillow numpy
if ($Analyzer -eq "claude") {
    & $venvPy -m pip install -q --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements-claude.txt")
}

# --- Konfiguration (nur beim ersten Mal)
if (-not (Test-Path $Config)) {
    if (-not $MqttPassword) { $MqttPassword = Read-Host "MQTT-Passwort für '$MqttUser'" }
    if (-not $CameraUrl) { $CameraUrl = Read-Host "Snapshot-URL der Kamera (z. B. http://werwardas-cam.local/capture)" }
    $dataDir = (Join-Path $AppDir "data") -replace "\\", "/"
    @"
# WerWarDas-Kameradienst (Windows). Alle Optionen: pi/config.example.yaml
mqtt:
  host: "$MqttHost"
  port: 1883
  username: "$MqttUser"
  password: "$MqttPassword"

camera:
  type: http
  url: "$CameraUrl"
  rotation: 0       # 0, 90, 180, 270

interval_minutes: 30
active_hours: "08:00-22:00"
threshold: 60
analyzer: $Analyzer
prompt_hint: ""

ollama:
  url: "$OllamaUrl"
  model: "$OllamaModel"
  timeout_seconds: 600

storage:
  dir: "$dataDir"
  keep_days: 7
"@ | Set-Content -Encoding utf8 $Config
    Write-Host "Konfiguration geschrieben: $Config"
}

# --- Testfoto
Write-Host "Testfoto ..."
$testJpg = Join-Path $AppDir "data\testfoto.jpg"
Push-Location $AppDir
try { & $venvPy -m werwardas -c $Config --test-camera $testJpg } finally { Pop-Location }

# --- Geplante Aufgabe: bei Anmeldung starten, bei Absturz neu starten
$pyw = Join-Path $AppDir "venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $pyw -WorkingDirectory $AppDir `
    -Argument "-m werwardas -c `"$Config`" --log-file `"$LogFile`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "WerWarDas: Foto der Kamera bewerten und an Home Assistant melden" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Fertig. Der Kameradienst läuft und startet bei jeder Anmeldung."
Write-Host "  Log:            Get-Content -Wait `"$LogFile`""
Write-Host "  Konfiguration:  notepad `"$Config`"   (danach: Stop-/Start-ScheduledTask '$TaskName')"
Write-Host "  Entfernen:      .\install-windows.ps1 -Uninstall"
