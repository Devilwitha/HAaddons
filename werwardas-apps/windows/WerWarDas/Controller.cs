using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace WerWarDas;

public enum PcState
{
    Free,
    Warned,
    Locked,
    EmergencyUnlocked,
}

/// <summary>
/// Entscheidet, ob der PC frei ist, einen Hinweis zeigt, gesperrt wird oder herunterfährt.
/// Alles läuft auf dem UI-Thread (Dispatcher).
/// Grundsatz: Wenn etwas nicht funktioniert (Kamera/Broker weg, Fehler), ist der PC frei.
/// </summary>
public sealed class Controller
{
    private readonly Settings _settings;
    private readonly MqttService _mqtt;
    private readonly Dispatcher _ui;
    private readonly DispatcherTimer _timer;

    private LockScreen? _lockScreen;
    private NoticeWindow? _notice;

    private DateTimeOffset? _messySince;
    private DateTimeOffset? _lockedSince;
    private DateTimeOffset _emergencyUntil = DateTimeOffset.MinValue;
    private DateTimeOffset _disconnectedSince = DateTimeOffset.Now;
    private bool _camOnline;
    private bool _noticeIsAutomatic;
    private bool _shutdownStarted;
    private bool _checkPending;

    public PcState State { get; private set; } = PcState.Free;
    public CamState? LastCamState { get; private set; }
    public BitmapImage? LastPhoto { get; private set; }

    public event Action? Changed;

    public Controller(Settings settings, MqttService mqtt, Dispatcher ui)
    {
        _settings = settings;
        _mqtt = mqtt;
        _ui = ui;

        _mqtt.CamStateReceived += s => _ui.BeginInvoke(() => OnCamState(s));
        _mqtt.ImageReceived += bytes => _ui.BeginInvoke(() => OnImage(bytes));
        _mqtt.CamAvailabilityChanged += online => _ui.BeginInvoke(() => OnCamAvailability(online));
        _mqtt.CommandReceived += cmd => _ui.BeginInvoke(() => OnCommand(cmd));
        _mqtt.ConnectionChanged += connected => _ui.BeginInvoke(() => OnConnection(connected));

        _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(15) };
        _timer.Tick += (_, _) => Evaluate();
        _timer.Start();
    }

    public bool IsConnected => _mqtt.IsConnected;
    public bool CamOnline => _camOnline;
    public string ConnectionError => _mqtt.LastError;
    public bool CheckPending => _checkPending;

    // ------------------------------------------------------------ Eingänge

    private void OnConnection(bool connected)
    {
        if (!connected)
        {
            _disconnectedSince = DateTimeOffset.Now;
        }
        else
        {
            PublishState();
        }
        Changed?.Invoke();
        Evaluate();
    }

    private void OnCamAvailability(bool online)
    {
        if (_camOnline && !online) _disconnectedSince = DateTimeOffset.Now;
        _camOnline = online;
        Changed?.Invoke();
        Evaluate();
    }

    private void OnImage(byte[] bytes)
    {
        try
        {
            var img = new BitmapImage();
            img.BeginInit();
            img.CacheOption = BitmapCacheOption.OnLoad;
            img.StreamSource = new MemoryStream(bytes);
            img.EndInit();
            img.Freeze();
            LastPhoto = img;
            Changed?.Invoke();
        }
        catch (Exception ex)
        {
            Log.Write($"Foto nicht lesbar: {ex.Message}");
        }
    }

    private void OnCamState(CamState state)
    {
        LastCamState = state;
        _checkPending = false;
        if (state.IsMessy)
        {
            // Beim Start kann ein älterer (retained) Status ankommen: dann zählt dessen Zeitpunkt.
            var since = state.Timestamp is { } ts && ts < DateTimeOffset.Now ? ts : DateTimeOffset.Now;
            if (_messySince == null || since < _messySince) _messySince = since;
        }
        else
        {
            _messySince = null;
        }
        Changed?.Invoke();

        if (_settings.Mode == ControlMode.Automatic)
        {
            Evaluate(newState: true);
        }
    }

    private void OnCommand(string cmd)
    {
        Log.Write($"Befehl aus Home Assistant: {cmd}");
        switch (cmd)
        {
            case "lock":
                Lock();
                break;
            case "unlock":
                // Im Automatik-Modus startet danach die Schonfrist neu.
                if (_messySince != null) _messySince = DateTimeOffset.Now;
                Unlock(thanks: false);
                break;
            case "warn":
                ShowNotice();
                break;
            case "shutdown":
                StartShutdown();
                break;
            case "cancel_shutdown":
                CancelShutdown();
                break;
        }
    }

    // ------------------------------------------------------------ Logik

    private void Evaluate(bool newState = false)
    {
        var now = DateTimeOffset.Now;
        // Die Kamera zählt nur im Automatik-Modus; im HA-Modus entscheidet Home Assistant.
        var offline = !_mqtt.IsConnected || (_settings.Mode == ControlMode.Automatic && !_camOnline);

        if (State == PcState.EmergencyUnlocked)
        {
            if (now < _emergencyUntil) return;
            // Nach der Notfall-Entsperrung beginnt die Schonfrist neu.
            if (_messySince != null) _messySince = now;
            SetState(PcState.Free);
        }

        // Ausfallsicher: keine Verbindung -> PC freigeben
        if (offline && now - _disconnectedSince > TimeSpan.FromMinutes(_settings.FailOpenMinutes))
        {
            // Von Hand geöffnete Hinweise (Tray, HA-Button) schränken nichts ein und bleiben offen.
            if (State == PcState.Locked || (State == PcState.Warned && _noticeIsAutomatic))
            {
                Log.Write("Keine Verbindung zu Broker/Kamera – PC wird freigegeben");
                Unlock(thanks: false);
            }
            return;
        }

        if (_settings.Mode == ControlMode.Automatic && !offline)
        {
            var cam = LastCamState;
            if (cam == null || !cam.IsMessy)
            {
                if (State != PcState.Free) Unlock(thanks: cam?.Tidy == true && State == PcState.Locked);
            }
            else if (_messySince is { } since)
            {
                var graceOver = now - since >= TimeSpan.FromMinutes(_settings.GraceMinutes);
                if (graceOver && State != PcState.Locked)
                {
                    Lock();
                }
                else if (!graceOver && (State == PcState.Free || newState))
                {
                    ShowNotice(automatic: true);
                }
                else if (State == PcState.Locked && newState)
                {
                    _lockScreen?.Refresh();
                }
            }
        }

        if (State == PcState.Locked && _lockedSince is { } locked && _settings.ShutdownAfterMinutes > 0
            && now - locked >= TimeSpan.FromMinutes(_settings.ShutdownAfterMinutes))
        {
            StartShutdown();
        }
    }

    public DateTimeOffset? LockDeadline =>
        _messySince is { } since && State == PcState.Warned && _settings.Mode == ControlMode.Automatic
            ? since + TimeSpan.FromMinutes(_settings.GraceMinutes)
            : null;

    public DateTimeOffset? ShutdownDeadline =>
        _lockedSince is { } locked && State == PcState.Locked && _settings.ShutdownAfterMinutes > 0
            ? locked + TimeSpan.FromMinutes(_settings.ShutdownAfterMinutes)
            : null;

    // ------------------------------------------------------------ Aktionen

    public void ShowNotice(bool automatic = false)
    {
        if (State == PcState.Locked) return;
        _noticeIsAutomatic = automatic;
        SetState(PcState.Warned);
        if (_notice == null)
        {
            _notice = new NoticeWindow(this);
            _notice.Closed += (_, _) => _notice = null;
            _notice.Show();
        }
        else
        {
            _notice.Refresh();
            _notice.Activate();
        }
    }

    public void Lock()
    {
        if (State == PcState.Locked) return;
        Log.Write("PC wird gesperrt");
        _notice?.Close();
        _lockedSince = DateTimeOffset.Now;
        SetState(PcState.Locked);
        _lockScreen ??= new LockScreen(this);
        _lockScreen.Show();
        _ = _mqtt.PublishEvent("locked");
    }

    public void Unlock(bool thanks)
    {
        var wasLocked = State == PcState.Locked;
        _lockScreen?.Close();
        _lockScreen = null;
        _notice?.Close();
        _lockedSince = null;
        CancelShutdown();
        SetState(PcState.Free);
        if (wasLocked)
        {
            Log.Write("PC entsperrt");
            _ = _mqtt.PublishEvent("unlocked");
            if (thanks) TrayIcon.Instance?.ShowBalloon("Danke!", "Zimmer ist aufgeräumt – viel Spaß am PC. 👍");
        }
    }

    public bool TryEmergencyUnlock(string pin)
    {
        if (!_settings.CheckPin(pin)) return false;
        Log.Write("Notfall-Entsperrung per PIN");
        Unlock(thanks: false);
        _emergencyUntil = DateTimeOffset.Now + TimeSpan.FromMinutes(_settings.EmergencyUnlockMinutes);
        SetState(PcState.EmergencyUnlocked);
        _ = _mqtt.PublishEvent("emergency_unlock");
        return true;
    }

    public void RequestCheck()
    {
        _checkPending = true;
        Changed?.Invoke();
        _ = _mqtt.RequestCheck();
        // Falls keine Antwort kommt, den Knopf nach 3 Minuten wieder freigeben.
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMinutes(3) };
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            if (_checkPending)
            {
                _checkPending = false;
                Changed?.Invoke();
            }
        };
        timer.Start();
    }

    private void StartShutdown()
    {
        if (_shutdownStarted) return;
        _shutdownStarted = true;
        Log.Write("PC wird heruntergefahren");
        _ = _mqtt.PublishEvent("shutdown");
        RunShutdown("/s /t 60 /c \"WerWarDas: Zimmer nicht aufgeräumt – der PC fährt in 60 Sekunden herunter.\"");
    }

    private void CancelShutdown()
    {
        if (!_shutdownStarted) return;
        _shutdownStarted = false;
        RunShutdown("/a");
    }

    private static void RunShutdown(string args)
    {
        try
        {
            Process.Start(new ProcessStartInfo("shutdown.exe", args) { CreateNoWindow = true, UseShellExecute = false });
        }
        catch (Exception ex)
        {
            Log.Write($"shutdown.exe fehlgeschlagen: {ex.Message}");
        }
    }

    private void SetState(PcState state)
    {
        if (State == state) return;
        State = state;
        PublishState();
        Changed?.Invoke();
    }

    public void Stop()
    {
        _timer.Stop();
        Unlock(thanks: false);
    }

    private void PublishState()
    {
        var text = State switch
        {
            PcState.Warned => "hinweis",
            PcState.Locked => "gesperrt",
            PcState.EmergencyUnlocked => "notfall",
            _ => "frei",
        };
        _ = _mqtt.PublishPcState(text, State == PcState.Locked);
    }
}
