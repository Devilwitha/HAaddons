using System;
using System.Threading.Tasks;
using System.Threading;
using System.Windows;
using System.Windows.Threading;

namespace WerWarDas;

public partial class App : Application
{
    private Mutex? _singleInstance;
    private MqttService? _mqtt;
    private Controller? _controller;
    private TrayIcon? _tray;
    private Updater? _updater;

    public static new App Current => (App)Application.Current;
    public static Settings Settings { get; private set; } = new();

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        _singleInstance = new Mutex(true, @"Local\WerWarDas.SingleInstance", out var isNew);
        if (!isNew && Array.IndexOf(e.Args, Updater.UpdatedArg) >= 0)
        {
            // Nach einem Update beendet sich die alte Version gerade noch.
            try { isNew = _singleInstance.WaitOne(TimeSpan.FromSeconds(30)); }
            catch (AbandonedMutexException) { isNew = true; }
        }
        if (!isNew)
        {
            MessageBox.Show("WerWarDas läuft bereits (siehe Symbol unten rechts in der Taskleiste).", "WerWarDas");
            Shutdown();
            return;
        }

        DispatcherUnhandledException += OnUnhandledException;
        Log.Write($"WerWarDas {Updater.CurrentVersion.ToString(3)} gestartet");
        Updater.CleanupAfterUpdate();

        var firstStart = !Settings.Exists;
        Settings = Settings.Load();
        if (firstStart)
        {
            var dlg = new SettingsWindow(Settings);
            if (dlg.ShowDialog() != true || dlg.Result == null)
            {
                Shutdown();
                return;
            }
            Settings = dlg.Result;
        }

        StartServices();
    }

    private void StartServices()
    {
        _mqtt = new MqttService(Settings);
        _controller = new Controller(Settings, _mqtt, Dispatcher);
        _tray ??= new TrayIcon(_controller);
        _mqtt.Start();
        _updater = new Updater(Settings.UpdateCheckHours, Dispatcher,
            canRestart: () => _controller?.State == PcState.Free,
            beforeRestart: () =>
            {
                StopServices();
                _singleInstance?.ReleaseMutex();
                _singleInstance?.Dispose();
                _singleInstance = null;
            });
    }

    public async void CheckForUpdates()
    {
        if (_updater == null) return;
        var message = await _updater.CheckAsync();
        _tray?.ShowBalloon("WerWarDas-Update", message);
    }

    private void StopServices()
    {
        _updater?.Dispose();
        _updater = null;
        _controller?.Stop();
        _tray?.Dispose();
        _tray = null;
        _mqtt?.Dispose();
        _mqtt = null;
        _controller = null;
    }

    public void OpenSettings()
    {
        if (_controller?.State == PcState.Locked) return;
        if (!PinDialog.Ask(Settings, "die Einstellungen zu öffnen")) return;
        var mqtt = _mqtt;
        var dlg = new SettingsWindow(Settings, mqtt?.IsConnected == true ? mqtt.CamSettings : null);
        if (dlg.ShowDialog() == true && dlg.Result != null)
        {
            // Kamera-Einstellungen noch über die bestehende Verbindung senden, bevor sie neu aufgebaut wird.
            foreach (var (key, value) in dlg.CamChanges)
            {
                Log.Write($"Kamera-Einstellung {key} = {value}");
                if (mqtt != null) Task.Run(() => mqtt.SetCamSetting(key, value)).Wait(2000);
            }
            Settings = dlg.Result;
            StopServices();
            StartServices();
        }
    }

    public void RequestExit()
    {
        if (_controller?.State == PcState.Locked) return;
        if (!PinDialog.Ask(Settings, "WerWarDas zu beenden")) return;
        StopServices();
        Log.Write("WerWarDas beendet");
        Shutdown();
    }

    private void OnUnhandledException(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        Log.Write($"Unbehandelter Fehler: {e.Exception}");
        e.Handled = true; // App weiterlaufen lassen
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _mqtt?.Dispose();
        _singleInstance?.Dispose();
        base.OnExit(e);
    }
}
