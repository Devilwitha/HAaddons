using System;
using System.Drawing;
using System.Windows;
using Forms = System.Windows.Forms;

namespace WerWarDas;

/// <summary>Symbol im Infobereich der Taskleiste mit Status und Menü.</summary>
public sealed class TrayIcon : IDisposable
{
    public static TrayIcon? Instance { get; private set; }

    private readonly Forms.NotifyIcon _icon;
    private readonly Controller _controller;
    private readonly Forms.ToolStripMenuItem _statusItem;
    private readonly Forms.ToolStripMenuItem _settingsItem;
    private readonly Forms.ToolStripMenuItem _exitItem;
    private readonly Forms.ToolStripMenuItem _ollamaItem;
    private readonly Icon _green = MakeIcon(Color.FromArgb(46, 160, 90));
    private readonly Icon _yellow = MakeIcon(Color.FromArgb(240, 180, 40));
    private readonly Icon _red = MakeIcon(Color.FromArgb(220, 60, 60));
    private readonly Icon _gray = MakeIcon(Color.FromArgb(130, 130, 130));

    public TrayIcon(Controller controller)
    {
        Instance = this;
        _controller = controller;

        var menu = new Forms.ContextMenuStrip();
        _statusItem = new Forms.ToolStripMenuItem { Enabled = false };
        menu.Items.Add(_statusItem);
        menu.Items.Add(new Forms.ToolStripSeparator());
        menu.Items.Add("Jetzt prüfen", null, (_, _) => _controller.RequestCheck());
        menu.Items.Add("Letztes Foto anzeigen", null, (_, _) => _controller.ShowNotice());
        _settingsItem = new Forms.ToolStripMenuItem("Einstellungen …", null, (_, _) => App.Current.OpenSettings());
        menu.Items.Add(_settingsItem);
        _ollamaItem = new Forms.ToolStripMenuItem("KI auf diesem PC (Ollama) …", null, (_, _) => App.Current.OpenOllama());
        menu.Items.Add(_ollamaItem);
        menu.Items.Add("Logdatei öffnen", null, (_, _) => OpenLog());
        menu.Items.Add("Nach Updates suchen", null, (_, _) => App.Current.CheckForUpdates());
        menu.Items.Add(new Forms.ToolStripSeparator());
        menu.Items.Add(new Forms.ToolStripMenuItem($"Version {Updater.CurrentVersion.ToString(3)}") { Enabled = false });
        _exitItem = new Forms.ToolStripMenuItem("Beenden", null, (_, _) => App.Current.RequestExit());
        menu.Items.Add(_exitItem);

        _icon = new Forms.NotifyIcon
        {
            ContextMenuStrip = menu,
            Visible = true,
            Text = "WerWarDas",
            Icon = _gray,
        };
        _icon.DoubleClick += (_, _) => _controller.ShowNotice();
        _controller.Changed += Update;
        Update();
    }

    private void Update()
    {
        var cam = _controller.LastCamState;
        string status;
        Icon icon;
        if (!_controller.IsConnected)
        {
            status = "Keine Verbindung zu Home Assistant";
            icon = _gray;
        }
        else if (!_controller.CamOnline)
        {
            status = "Kamera offline";
            icon = _gray;
        }
        else
        {
            status = _controller.State switch
            {
                PcState.Locked => "Gesperrt – Zimmer aufräumen!",
                PcState.Warned => "Hinweis: Zimmer aufräumen",
                PcState.EmergencyUnlocked => "Notfall-Entsperrung aktiv",
                _ => cam?.Paused == true ? "Überwachung pausiert" : "Alles ordentlich",
            };
            icon = _controller.State switch
            {
                PcState.Locked => _red,
                PcState.Warned or PcState.EmergencyUnlocked => _yellow,
                _ => _green,
            };
        }
        if (cam?.Score is { } score) status += $" ({score:0} %)";

        _statusItem.Text = status;
        _icon.Icon = icon;
        _icon.Text = Truncate("WerWarDas – " + status, 127);
        var locked = _controller.State == PcState.Locked;
        _settingsItem.Enabled = !locked;
        _ollamaItem.Enabled = !locked;
        _exitItem.Enabled = !locked;
    }

    public void ShowBalloon(string title, string text) =>
        _icon.ShowBalloonTip(5000, title, text, Forms.ToolTipIcon.Info);

    private static void OpenLog()
    {
        var path = System.IO.Path.Combine(Settings.Folder, "werwardas.log");
        if (!System.IO.File.Exists(path)) return;
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
    }

    private static string Truncate(string s, int max) => s.Length <= max ? s : s[..max];

    /// <summary>Webcam mit Glanz-Sternchen (wie das App-Symbol); die Hintergrundfarbe zeigt den Zustand.</summary>
    private static Icon MakeIcon(Color color)
    {
        using var bmp = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            using (var bg = new SolidBrush(color))
            using (var path = RoundedRect(new RectangleF(0.5f, 0.5f, 31, 31), 7))
                g.FillPath(bg, path);
            using var white = new SolidBrush(Color.White);
            g.FillRectangle(white, 13.2f, 20, 3.6f, 6);              // Hals
            using (var foot = RoundedRect(new RectangleF(8, 25, 14, 3.5f), 1.7f))
                g.FillPath(white, foot);                              // Fuß
            g.FillEllipse(white, 4.5f, 4.5f, 21, 21);                 // Kamerakopf
            using var lens = new SolidBrush(Color.FromArgb(15, 32, 74));
            g.FillEllipse(lens, 9, 9, 12, 12);                        // Objektiv
            using var glass = new SolidBrush(Color.FromArgb(37, 99, 235));
            g.FillEllipse(glass, 11.8f, 11.8f, 6.4f, 6.4f);
            using var shine = new SolidBrush(Color.FromArgb(219, 234, 254));
            g.FillEllipse(shine, 11.5f, 11.5f, 2.6f, 2.6f);
            using var star = new SolidBrush(Color.FromArgb(253, 224, 71));
            g.FillPolygon(star, Star(27, 5, 4.6f));                   // Glanz: sauber
        }
        return Icon.FromHandle(bmp.GetHicon());
    }

    private static System.Drawing.Drawing2D.GraphicsPath RoundedRect(RectangleF r, float radius)
    {
        var d = radius * 2;
        var path = new System.Drawing.Drawing2D.GraphicsPath();
        path.AddArc(r.X, r.Y, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    private static PointF[] Star(float cx, float cy, float r)
    {
        var k = r * 0.3f;
        return new[]
        {
            new PointF(cx, cy - r), new PointF(cx + k, cy - k), new PointF(cx + r, cy), new PointF(cx + k, cy + k),
            new PointF(cx, cy + r), new PointF(cx - k, cy + k), new PointF(cx - r, cy), new PointF(cx - k, cy - k),
        };
    }

    public void Dispose()
    {
        _controller.Changed -= Update;
        _icon.Visible = false;
        _icon.Dispose();
    }
}
