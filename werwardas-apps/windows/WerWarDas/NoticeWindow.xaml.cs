using System;
using System.Windows;
using System.Windows.Threading;

namespace WerWarDas;

/// <summary>Hinweis-Fenster unten rechts: "Räum bitte dein Zimmer auf!" mit Foto und Countdown bis zur Sperre.</summary>
public partial class NoticeWindow : Window
{
    private readonly Controller _controller;
    private readonly DispatcherTimer _clock;

    public NoticeWindow(Controller controller)
    {
        InitializeComponent();
        _controller = controller;
        _controller.Changed += Refresh;
        _clock = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _clock.Tick += (_, _) => UpdateDeadline();
        _clock.Start();
        Loaded += (_, _) =>
        {
            var area = SystemParameters.WorkArea;
            Left = area.Right - ActualWidth - 16;
            Top = area.Bottom - ActualHeight - 16;
        };
        Closed += (_, _) =>
        {
            _clock.Stop();
            _controller.Changed -= Refresh;
        };
        Refresh();
    }

    public void Refresh()
    {
        var cam = _controller.LastCamState;
        ReasonText.Text = string.IsNullOrWhiteSpace(cam?.Reason) ? "Dein Zimmer ist zu unordentlich." : cam!.Reason;
        Photo.Source = _controller.LastPhoto;
        CheckButton.IsEnabled = !_controller.CheckPending && _controller.IsConnected;
        CheckButton.Content = _controller.CheckPending ? "Prüfe …" : "Aufgeräumt – jetzt prüfen";
        UpdateDeadline();
    }

    private void UpdateDeadline()
    {
        if (_controller.LockDeadline is { } deadline)
        {
            var left = deadline - DateTimeOffset.Now;
            DeadlineText.Text = left > TimeSpan.Zero
                ? $"🔒 Der PC wird in {LockWindow.Format(left)} gesperrt."
                : "🔒 Der PC wird gleich gesperrt.";
        }
        else
        {
            DeadlineText.Text = "";
        }
    }

    private void CheckButton_Click(object sender, RoutedEventArgs e) => _controller.RequestCheck();

    private void OkButton_Click(object sender, RoutedEventArgs e) => Close();
}
