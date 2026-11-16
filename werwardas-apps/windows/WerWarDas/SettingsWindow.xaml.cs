using System;
using System.Collections.Generic;
using System.Globalization;
using System.Windows;

namespace WerWarDas;

public partial class SettingsWindow : Window
{
    private readonly Settings _settings;
    private readonly CamSettings? _cam;

    /// <param name="cam">Aktuelle Einstellungen des Kameradienstes; null = nicht erreichbar.</param>
    public SettingsWindow(Settings settings, CamSettings? cam = null)
    {
        InitializeComponent();
        _cam = cam;
        if (cam != null)
        {
            IntervalBox.Text = cam.Interval.ToString();
            ThresholdBox.Text = cam.Threshold.ToString();
            ActiveHoursBox.Text = cam.ActiveHours ?? "";
        }
        else
        {
            IntervalBox.IsEnabled = ThresholdBox.IsEnabled = ActiveHoursBox.IsEnabled = false;
            CamHint.Visibility = Visibility.Visible;
        }
        _settings = settings.Clone();
        HostBox.Text = _settings.BrokerHost;
        PortBox.Text = _settings.BrokerPort.ToString();
        UserBox.Text = _settings.BrokerUser;
        PasswordBox.Password = _settings.BrokerPassword;
        PrefixBox.Text = _settings.TopicPrefix;
        PcNameBox.Text = _settings.PcName;
        ModeBox.SelectedIndex = _settings.Mode == ControlMode.Automatic ? 0 : 1;
        GraceBox.Text = _settings.GraceMinutes.ToString();
        ShutdownBox.Text = _settings.ShutdownAfterMinutes.ToString();
        EmergencyBox.Text = _settings.EmergencyUnlockMinutes.ToString();
        UpdateBox.Text = _settings.UpdateCheckHours.ToString();
        AutostartBox.IsChecked = _settings.Autostart;
        RemovePinBox.IsEnabled = _settings.HasPin;
    }

    /// <summary>Die gespeicherten Einstellungen, wenn der Benutzer auf "Speichern" geklickt hat.</summary>
    public Settings? Result { get; private set; }

    /// <summary>Geänderte Einstellungen des Kameradienstes (MQTT-Schlüssel -> Wert).</summary>
    public Dictionary<string, string> CamChanges { get; } = new();

    /// <summary>"8:00 - 22:00" -> "08:00-22:00", leer -> "". null = ungültig.</summary>
    private static string? NormalizeActiveHours(string text)
    {
        text = text.Trim();
        if (text.Length == 0) return "";
        var parts = text.Split('-');
        if (parts.Length != 2) return null;
        var times = new List<string>();
        foreach (var part in parts)
        {
            if (!TimeSpan.TryParseExact(part.Trim(), new[] { @"h\:mm", @"hh\:mm" }, CultureInfo.InvariantCulture, out var t))
                return null;
            times.Add(t.ToString(@"hh\:mm"));
        }
        return $"{times[0]}-{times[1]}";
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(HostBox.Text))
        {
            ErrorText.Text = "Bitte die Adresse von Home Assistant / dem MQTT-Broker eingeben.";
            return;
        }
        if (!int.TryParse(PortBox.Text, out var port) || port is < 1 or > 65535)
        {
            ErrorText.Text = "Ungültiger Port.";
            return;
        }
        if (!int.TryParse(GraceBox.Text, out var grace) || grace < 0
            || !int.TryParse(ShutdownBox.Text, out var shutdown) || shutdown < 0)
        {
            ErrorText.Text = "Bitte bei den Minuten nur ganze Zahlen ab 0 eingeben.";
            return;
        }
        if (!int.TryParse(EmergencyBox.Text, out var emergency) || emergency is < 1 or > 1440)
        {
            ErrorText.Text = "Notfall-Entsperrung: bitte eine ganze Zahl von 1 bis 1440 Minuten eingeben.";
            return;
        }
        if (!int.TryParse(UpdateBox.Text, out var updateHours) || updateHours is < 0 or > 168)
        {
            ErrorText.Text = "Nach Updates suchen: bitte eine ganze Zahl von 0 bis 168 Stunden eingeben.";
            return;
        }

        if (_cam != null)
        {
            if (!int.TryParse(IntervalBox.Text, out var interval) || interval is < 1 or > 240)
            {
                ErrorText.Text = "Prüfintervall: bitte eine ganze Zahl von 1 bis 240 Minuten eingeben.";
                return;
            }
            if (!int.TryParse(ThresholdBox.Text, out var threshold) || threshold is < 0 or > 100)
            {
                ErrorText.Text = "Aufgeräumt ab: bitte einen Wert von 0 bis 100 % eingeben.";
                return;
            }
            var hours = NormalizeActiveHours(ActiveHoursBox.Text);
            if (hours == null)
            {
                ErrorText.Text = "Prüfzeit: bitte in der Form 08:00-22:00 eingeben (oder leer lassen).";
                return;
            }
            if (interval != _cam.Interval) CamChanges["interval"] = interval.ToString();
            if (threshold != _cam.Threshold) CamChanges["threshold"] = threshold.ToString();
            if (hours != (_cam.ActiveHours ?? "")) CamChanges["active_hours"] = hours;
        }

        _settings.BrokerHost = HostBox.Text.Trim();
        _settings.BrokerPort = port;
        _settings.BrokerUser = UserBox.Text.Trim();
        _settings.BrokerPassword = PasswordBox.Password;
        _settings.TopicPrefix = string.IsNullOrWhiteSpace(PrefixBox.Text) ? "werwardas" : PrefixBox.Text.Trim();
        _settings.PcName = string.IsNullOrWhiteSpace(PcNameBox.Text) ? System.Environment.MachineName : PcNameBox.Text.Trim();
        _settings.Mode = ModeBox.SelectedIndex == 0 ? ControlMode.Automatic : ControlMode.HomeAssistant;
        _settings.GraceMinutes = grace;
        _settings.ShutdownAfterMinutes = shutdown;
        _settings.EmergencyUnlockMinutes = emergency;
        _settings.UpdateCheckHours = updateHours;
        _settings.Autostart = AutostartBox.IsChecked == true;
        if (RemovePinBox.IsChecked == true) _settings.SetPin("");
        else if (!string.IsNullOrEmpty(PinBox.Password)) _settings.SetPin(PinBox.Password);

        _settings.Save();
        Result = _settings;
        DialogResult = true;
    }
}
