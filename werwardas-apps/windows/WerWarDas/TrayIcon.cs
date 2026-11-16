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

    private static Icon MakeIcon(Color color)
    {
        using var bmp = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            using var fill = new SolidBrush(color);
            g.FillEllipse(fill, 1, 1, 30, 30);
            using var pen = new Pen(Color.White, 3.5f);
            g.DrawLine(pen, 21, 6, 13, 19);                 // Besenstiel
            using var head = new SolidBrush(Color.FromArgb(255, 235, 150));
            g.FillPolygon(head, new[] { new PointF(9, 16), new PointF(17, 21), new PointF(13, 27), new PointF(5, 22) });
        }
        return Icon.FromHandle(bmp.GetHicon());
    }

    public void Dispose()
    {
        _controller.Changed -= Update;
        _icon.Visible = false;
        _icon.Dispose();
    }
}
