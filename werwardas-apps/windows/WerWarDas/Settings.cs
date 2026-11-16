using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Win32;

namespace WerWarDas;

public enum ControlMode
{
    /// <summary>Die App entscheidet selbst anhand des Kamera-Status (Hinweis -> Sperre -> optional Herunterfahren).</summary>
    Automatic,
    /// <summary>Nur Befehle aus Home Assistant (Automationen/Blueprint) werden ausgeführt.</summary>
    HomeAssistant,
}

public sealed class Settings
{
    public string BrokerHost { get; set; } = "homeassistant.local";
    public int BrokerPort { get; set; } = 1883;
    public string BrokerUser { get; set; } = "werwardas";
    /// <summary>Mit Windows-DPAPI verschlüsselt (nur dieser Benutzer kann es lesen).</summary>
    public string BrokerPasswordProtected { get; set; } = "";
    public string TopicPrefix { get; set; } = "werwardas";
    public string PcName { get; set; } = Environment.MachineName;

    public ControlMode Mode { get; set; } = ControlMode.Automatic;
    /// <summary>Minuten zwischen dem ersten Hinweis und der Sperre (0 = sofort sperren).</summary>
    public int GraceMinutes { get; set; } = 15;
    /// <summary>Minuten gesperrt, bevor der PC herunterfährt (0 = nie herunterfahren).</summary>
    public int ShutdownAfterMinutes { get; set; } = 0;
    /// <summary>Wie lange eine Notfall-Entsperrung per PIN gilt.</summary>
    public int EmergencyUnlockMinutes { get; set; } = 60;
    /// <summary>Nach so vielen Minuten ohne Verbindung zu Broker oder Kamera wird der PC freigegeben.</summary>
    public int FailOpenMinutes { get; set; } = 2;
    public string PinHash { get; set; } = "";
    public bool Autostart { get; set; } = true;
    /// <summary>Alle so viele Stunden auf GitHub nach einer neuen Version suchen (0 = nie automatisch).</summary>
    public int UpdateCheckHours { get; set; } = 1;

    public static string Folder =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "WerWarDas");

    private static string FilePath => Path.Combine(Folder, "settings.json");

    public static bool Exists => File.Exists(FilePath);

    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public static Settings Load()
    {
        try
        {
            if (File.Exists(FilePath))
            {
                return JsonSerializer.Deserialize<Settings>(File.ReadAllText(FilePath)) ?? new Settings();
            }
        }
        catch (Exception ex)
        {
            Log.Write($"Einstellungen nicht lesbar: {ex.Message}");
        }
        return new Settings();
    }

    public void Save()
    {
        Directory.CreateDirectory(Folder);
        File.WriteAllText(FilePath, JsonSerializer.Serialize(this, JsonOptions));
        ApplyAutostart();
    }

    public Settings Clone() => JsonSerializer.Deserialize<Settings>(JsonSerializer.Serialize(this))!;

    /// <summary>Eindeutiger, MQTT-tauglicher Name des PCs (z. B. "gaming_pc").</summary>
    [JsonIgnore]
    public string PcId
    {
        get
        {
            var sb = new StringBuilder();
            foreach (var c in PcName.ToLowerInvariant())
            {
                sb.Append(char.IsAsciiLetterOrDigit(c) ? c : '_');
            }
            return sb.Length > 0 ? sb.ToString() : "pc";
        }
    }

    [JsonIgnore]
    public string BrokerPassword
    {
        get
        {
            if (string.IsNullOrEmpty(BrokerPasswordProtected)) return "";
            try
            {
                var bytes = ProtectedData.Unprotect(
                    Convert.FromBase64String(BrokerPasswordProtected), null, DataProtectionScope.CurrentUser);
                return Encoding.UTF8.GetString(bytes);
            }
            catch (Exception)
            {
                return "";
            }
        }
        set
        {
            BrokerPasswordProtected = string.IsNullOrEmpty(value)
                ? ""
                : Convert.ToBase64String(ProtectedData.Protect(
                    Encoding.UTF8.GetBytes(value), null, DataProtectionScope.CurrentUser));
        }
    }

    [JsonIgnore]
    public bool HasPin => !string.IsNullOrEmpty(PinHash);

    public void SetPin(string pin) => PinHash = string.IsNullOrEmpty(pin) ? "" : HashPin(pin);

    public bool CheckPin(string pin) => HasPin && CryptographicOperations.FixedTimeEquals(
        Encoding.ASCII.GetBytes(HashPin(pin)), Encoding.ASCII.GetBytes(PinHash));

    private static string HashPin(string pin) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes("werwardas:" + pin)));

    private void ApplyAutostart()
    {
        const string runKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(runKey, writable: true);
            if (key == null) return;
            if (Autostart && Environment.ProcessPath is { } exe)
            {
                key.SetValue("WerWarDas", $"\"{exe}\"");
            }
            else
            {
                key.DeleteValue("WerWarDas", throwOnMissingValue: false);
            }
        }
        catch (Exception ex)
        {
            Log.Write($"Autostart konnte nicht gesetzt werden: {ex.Message}");
        }
    }
}

public static class Log
{
    private static readonly object Gate = new();

    public static void Write(string message)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(Settings.Folder);
                var path = Path.Combine(Settings.Folder, "werwardas.log");
                if (File.Exists(path) && new FileInfo(path).Length > 1_000_000)
                {
                    File.Move(path, path + ".old", overwrite: true);
                }
                File.AppendAllText(path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
            }
        }
        catch (Exception)
        {
            // Logging darf die App nie zum Absturz bringen.
        }
    }
}
