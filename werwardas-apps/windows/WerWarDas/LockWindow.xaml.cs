using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

namespace WerWarDas;

/// <summary>Sperrbildschirm über allen Monitoren. Geht erst wieder weg, wenn aufgeräumt ist (oder per Notfall-PIN).</summary>
public sealed class LockScreen
{
    private readonly Controller _controller;
    private readonly List<LockWindow> _windows = new();
    private readonly KeyboardBlocker _keyboard = new();
    private readonly DispatcherTimer _focusTimer;

    public LockScreen(Controller controller)
    {
        _controller = controller;
        _focusTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(700) };
        _focusTimer.Tick += (_, _) => KeepOnTop();
        _controller.Changed += Refresh;
    }

    public void Show()
    {
        if (_windows.Count > 0) return;
        foreach (var screen in Forms.Screen.AllScreens)
        {
            var w = new LockWindow(_controller, screen.Bounds, screen.Primary);
            _windows.Add(w);
            w.Show();
        }
        _keyboard.Enable();
        _focusTimer.Start();
        Refresh();
    }

    public void Refresh()
    {
        foreach (var w in _windows) w.Refresh();
    }

    private void KeepOnTop()
    {
        var anyActive = false;
        foreach (var w in _windows)
        {
            w.Topmost = true;
            anyActive |= w.IsActive;
        }
        if (!anyActive && _windows.Count > 0)
        {
            _windows.Find(w => w.IsPrimary)?.Activate();
        }
    }

    public void Close()
    {
        _controller.Changed -= Refresh;
        _focusTimer.Stop();
        _keyboard.Disable();
        foreach (var w in _windows) w.ForceClose();
        _windows.Clear();
    }
}

public partial class LockWindow : Window
{
    private readonly Controller _controller;
    private readonly System.Drawing.Rectangle _bounds;
    private readonly DispatcherTimer _clock;
    private bool _allowClose;

    public bool IsPrimary { get; }

    public LockWindow(Controller controller, System.Drawing.Rectangle bounds, bool primary)
    {
        InitializeComponent();
        _controller = controller;
        _bounds = bounds;
        IsPrimary = primary;
        // Grobe Position, die genaue setzt SourceInitialized in physischen Pixeln (wegen DPI).
        Left = bounds.Left;
        Top = bounds.Top;
        Width = 800;
        Height = 600;
        SourceInitialized += OnSourceInitialized;
        Loaded += (_, _) =>
        {
            WindowState = WindowState.Maximized;
            if (IsPrimary)
            {
                Activate();
                CheckButton.Focus();
            }
        };
        _clock = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _clock.Tick += (_, _) => UpdateStatus();
        _clock.Start();
    }

    private void OnSourceInitialized(object? sender, EventArgs e)
    {
        var hwnd = new WindowInteropHelper(this).Handle;
        SetWindowPos(hwnd, IntPtr.Zero, _bounds.Left, _bounds.Top, _bounds.Width, _bounds.Height,
            SWP_NOZORDER | SWP_NOACTIVATE);
    }

    public void Refresh()
    {
        var cam = _controller.LastCamState;
        ReasonText.Text = string.IsNullOrWhiteSpace(cam?.Reason) ? "Dein Zimmer ist zu unordentlich." : cam!.Reason;
        ScoreText.Text = cam?.Score is { } score
            ? $"Ordnung: {score:0} %   (nötig: {cam.Threshold ?? 0:0} %)"
            : "";
        Photo.Source = _controller.LastPhoto;
        PinPanel.Visibility = App.Settings.HasPin ? Visibility.Visible : Visibility.Collapsed;
        UpdateStatus();
    }

    private void UpdateStatus()
    {
        CheckButton.IsEnabled = !_controller.CheckPending && _controller.IsConnected && _controller.CamOnline;
        CheckButton.Content = _controller.CheckPending
            ? "📷 Foto wird gemacht und bewertet …"
            : "✔ Ich habe aufgeräumt – jetzt prüfen";

        var lines = new List<string>();
        if (!_controller.IsConnected) lines.Add("Keine Verbindung zu Home Assistant – der PC wird in Kürze automatisch freigegeben.");
        else if (!_controller.CamOnline) lines.Add("Kamera ist offline – der PC wird in Kürze automatisch freigegeben.");
        if (_controller.ShutdownDeadline is { } deadline)
        {
            var left = deadline - DateTimeOffset.Now;
            lines.Add(left > TimeSpan.Zero
                ? $"⏻ Der PC fährt in {Format(left)} herunter, wenn bis dahin nicht aufgeräumt ist."
                : "⏻ Der PC fährt herunter …");
        }
        StatusText.Text = string.Join(Environment.NewLine, lines);
    }

    internal static string Format(TimeSpan t) =>
        t.TotalHours >= 1 ? $"{(int)t.TotalHours}:{t.Minutes:00}:{t.Seconds:00} h" : $"{t.Minutes}:{t.Seconds:00} min";

    private void CheckButton_Click(object sender, RoutedEventArgs e) => _controller.RequestCheck();

    private void PinButton_Click(object sender, RoutedEventArgs e) => TryPin();

    private void PinBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) TryPin();
    }

    private void TryPin()
    {
        if (!_controller.TryEmergencyUnlock(PinBox.Password))
        {
            PinError.Text = "Falsche PIN";
            PinBox.Clear();
        }
    }

    public void ForceClose()
    {
        _allowClose = true;
        _clock.Stop();
        Close();
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        if (!_allowClose) e.Cancel = true; // Alt+F4 & Co. ignorieren
        base.OnClosing(e);
    }

    private const uint SWP_NOZORDER = 0x0004;
    private const uint SWP_NOACTIVATE = 0x0010;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint flags);
}
