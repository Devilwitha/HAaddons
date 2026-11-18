using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace WerWarDas;

/// <summary>Ein Vision-Modell für die Kamera-Bewertung.</summary>
public sealed record OllamaModel(string Name, string Title, string Description, string Size, double MinVramGb);

/// <summary>
/// Ollama auf diesem PC: erkennen, installieren (offizieller Installer, ohne Admin-Rechte), im Netzwerk freigeben
/// (OLLAMA_HOST + Firewall-Regel), Modelle laden und testen.
/// </summary>
public static class Ollama
{
    public const int Port = 11434;
    public const string FirewallRule = "Ollama (WerWarDas)";

    // Zum Testen umleitbar (Ersatz-Server statt echtem Ollama)
    public static string ApiUrl => Environment.GetEnvironmentVariable("WERWARDAS_OLLAMA_URL") ?? $"http://127.0.0.1:{Port}";
    private static string SetupUrl => Environment.GetEnvironmentVariable("WERWARDAS_OLLAMA_SETUP")
                                      ?? "https://ollama.com/download/OllamaSetup.exe";

    public static readonly OllamaModel[] Models =
    {
        new("qwen2.5vl:7b", "Qwen2.5-VL 7B", "Genaue Bewertung – für PCs mit Grafikkarte ab 8 GB", "ca. 6 GB", 7),
        new("qwen2.5vl:3b", "Qwen2.5-VL 3B", "Schneller und kleiner – für PCs ohne starke Grafikkarte", "ca. 3,2 GB", 0),
    };

    private static readonly HttpClient Http = new() { Timeout = Timeout.InfiniteTimeSpan };

    // ------------------------------------------------------------------ Zustand
    public static async Task<string?> GetVersionAsync()
    {
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(3));
            var json = await Http.GetStringAsync(ApiUrl + "/api/version", cts.Token);
            using var doc = JsonDocument.Parse(json);
            return doc.RootElement.TryGetProperty("version", out var v) ? v.GetString() ?? "?" : "?";
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException)
        {
            return null;
        }
    }

    /// <summary>Namen der geladenen Modelle („qwen2.5vl:7b“, ohne „:latest“).</summary>
    public static async Task<HashSet<string>> GetModelsAsync()
    {
        var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            var json = await Http.GetStringAsync(ApiUrl + "/api/tags", cts.Token);
            using var doc = JsonDocument.Parse(json);
            foreach (var m in doc.RootElement.GetProperty("models").EnumerateArray())
            {
                var name = m.GetProperty("name").GetString() ?? "";
                result.Add(name.EndsWith(":latest", StringComparison.Ordinal) ? name[..^7] : name);
            }
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException
                                      or KeyNotFoundException or InvalidOperationException)
        {
        }
        return result;
    }

    private static string? _lastRunningPath;

    /// <summary>
    /// Pfad der Ollama-App (startet den Server und sitzt im Infobereich) oder von ollama.exe, null = nicht installiert.
    /// Sucht im Standardordner, unter „Programme“, im PATH und beim gerade laufenden Prozess.
    /// </summary>
    public static string? AppPath
    {
        get
        {
            var dirs = new List<string>
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Ollama"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Ollama"),
            };
            dirs.AddRange((Environment.GetEnvironmentVariable("PATH") ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries));
            foreach (var dir in dirs)
            {
                foreach (var name in new[] { "ollama app.exe", "ollama.exe" })
                {
                    try
                    {
                        var path = Path.Combine(dir.Trim(), name);
                        if (File.Exists(path)) return path;
                    }
                    catch (ArgumentException)
                    {
                    }
                }
            }
            var running = RunningPath();
            if (running != null) _lastRunningPath = running;
            return running ?? (_lastRunningPath != null && File.Exists(_lastRunningPath) ? _lastRunningPath : null);
        }
    }

    /// <summary>Pfad eines laufenden Ollama-Prozesses (null, wenn keiner läuft oder er einem Dienst gehört).</summary>
    private static string? RunningPath()
    {
        foreach (var name in new[] { "ollama app", "ollama" })
        {
            foreach (var p in Process.GetProcessesByName(name))
            {
                try
                {
                    var path = p.MainModule?.FileName;
                    if (!string.IsNullOrEmpty(path)) return path;
                }
                catch (Exception e) when (e is Win32Exception or InvalidOperationException)
                {
                }
                finally
                {
                    p.Dispose();
                }
            }
        }
        return null;
    }

    /// <summary>Für das Netzwerk freigegeben = OLLAMA_HOST lauscht auf allen Adressen.</summary>
    public static bool NetworkEnabled
    {
        get
        {
            var host = Environment.GetEnvironmentVariable("OLLAMA_HOST", EnvironmentVariableTarget.User)
                       ?? Environment.GetEnvironmentVariable("OLLAMA_HOST", EnvironmentVariableTarget.Machine) ?? "";
            return host.StartsWith("0.0.0.0", StringComparison.Ordinal) || host.StartsWith("[::]", StringComparison.Ordinal);
        }
    }

    /// <summary>Antwortet Ollama auch über die Netzwerkadresse dieses PCs (nicht nur über localhost)?</summary>
    public static async Task<bool> ReachableFromNetworkAsync()
    {
        if (Environment.GetEnvironmentVariable("WERWARDAS_OLLAMA_URL") != null) return NetworkEnabled;
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            using var resp = await Http.GetAsync($"{LanUrl}/api/version", cts.Token);
            return resp.IsSuccessStatusCode;
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException)
        {
            return false;
        }
    }

    public static bool FirewallRuleExists()
    {
        try
        {
            var psi = new ProcessStartInfo("netsh", $"advfirewall firewall show rule name=\"{FirewallRule}\"")
            {
                CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true,
            };
            using var p = Process.Start(psi)!;
            p.StandardOutput.ReadToEnd();
            p.WaitForExit(5000);
            return p.ExitCode == 0;
        }
        catch (Exception e) when (e is Win32Exception or InvalidOperationException)
        {
            return false;
        }
    }

    // ------------------------------------------------------------------ Hardware
    /// <summary>Größter Grafikspeicher in GB (aus der Registry, auch über 4 GB genau) und Name der Karte.</summary>
    public static (double Gb, string Name) GetGpu()
    {
        double best = 0;
        var name = "";
        try
        {
            using var cls = Registry.LocalMachine.OpenSubKey(
                @"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}");
            if (cls == null) return (0, "");
            foreach (var sub in cls.GetSubKeyNames().Where(n => n.Length == 4 && n.All(char.IsDigit)))
            {
                using var key = cls.OpenSubKey(sub);
                var raw = key?.GetValue("HardwareInformation.qwMemorySize") ?? key?.GetValue("HardwareInformation.MemorySize");
                double bytes = raw switch
                {
                    long l => l,
                    int i => (uint)i,
                    byte[] b when b.Length >= 8 => BitConverter.ToInt64(b, 0),
                    byte[] b when b.Length >= 4 => BitConverter.ToUInt32(b, 0),
                    _ => 0,
                };
                var gb = bytes / 1024 / 1024 / 1024;
                if (gb > best)
                {
                    best = gb;
                    name = key?.GetValue("DriverDesc") as string ?? "";
                }
            }
        }
        catch (Exception e) when (e is System.Security.SecurityException or UnauthorizedAccessException or IOException)
        {
        }
        return (Math.Round(best, 1), name);
    }

    public static OllamaModel Recommended(double vramGb) => Models.First(m => vramGb >= m.MinVramGb);

    // ------------------------------------------------------------------ Adresse
    /// <summary>IPv4-Adresse dieses PCs im Heimnetz (Netzwerkkarte mit Gateway), sonst der PC-Name.</summary>
    public static string LanHost()
    {
        try
        {
            var candidates = NetworkInterface.GetAllNetworkInterfaces()
                .Where(n => n.OperationalStatus == OperationalStatus.Up
                            && n.NetworkInterfaceType is not (NetworkInterfaceType.Loopback or NetworkInterfaceType.Tunnel)
                            && !n.Description.Contains("Virtual", StringComparison.OrdinalIgnoreCase)
                            && !n.Description.Contains("Hyper-V", StringComparison.OrdinalIgnoreCase))
                .Select(n => n.GetIPProperties())
                .Where(p => p.GatewayAddresses.Any(g => g.Address.AddressFamily == AddressFamily.InterNetwork
                                                        && !g.Address.Equals(System.Net.IPAddress.Any)))
                .SelectMany(p => p.UnicastAddresses)
                .Select(a => a.Address)
                .Where(a => a.AddressFamily == AddressFamily.InterNetwork && !System.Net.IPAddress.IsLoopback(a)
                            && !a.ToString().StartsWith("169.254.", StringComparison.Ordinal))
                .ToList();
            if (candidates.Count > 0) return candidates[0].ToString();
        }
        catch (NetworkInformationException)
        {
        }
        return Environment.MachineName.ToLowerInvariant();
    }

    public static string LanUrl => $"http://{LanHost()}:{Port}";

    // ------------------------------------------------------------------ Aktionen
    /// <summary>Offiziellen Installer laden und still installieren (für den angemeldeten Benutzer, ohne Admin).</summary>
    public static async Task InstallAsync(IProgress<(string Text, double? Fraction)> progress, CancellationToken ct)
    {
        var file = Path.Combine(Path.GetTempPath(), "OllamaSetup.exe");
        progress.Report(("Ollama wird heruntergeladen …", 0));
        using (var resp = await Http.GetAsync(SetupUrl, HttpCompletionOption.ResponseHeadersRead, ct))
        {
            resp.EnsureSuccessStatusCode();
            var total = resp.Content.Headers.ContentLength ?? 0;
            await using var src = await resp.Content.ReadAsStreamAsync(ct);
            await using var dst = File.Create(file);
            var buffer = new byte[1 << 16];
            long done = 0;
            var last = DateTime.MinValue;
            int n;
            while ((n = await src.ReadAsync(buffer, ct)) > 0)
            {
                await dst.WriteAsync(buffer.AsMemory(0, n), ct);
                done += n;
                if (DateTime.Now - last > TimeSpan.FromMilliseconds(200))
                {
                    last = DateTime.Now;
                    progress.Report(($"Ollama wird heruntergeladen … {Mb(done)}{(total > 0 ? " von " + Mb(total) : "")}",
                                     total > 0 ? (double)done / total : null));
                }
            }
        }
        progress.Report(("Ollama wird installiert …", null));
        Log.Write("Ollama-Installer gestartet");
        var psi = new ProcessStartInfo(file, "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-") { UseShellExecute = false };
        if (NetworkEnabled) psi.Environment["OLLAMA_HOST"] = $"0.0.0.0:{Port}";
        using (var p = Process.Start(psi) ?? throw new InvalidOperationException("Installer startet nicht"))
        {
            await p.WaitForExitAsync(ct);
            if (p.ExitCode != 0) throw new InvalidOperationException($"Installer beendet mit Code {p.ExitCode}");
        }
        try { File.Delete(file); } catch (IOException) { }
        progress.Report(("Ollama wird gestartet …", null));
        await StartAsync(ct);
    }

    /// <summary>Ollama starten (falls nicht schon erreichbar) und warten, bis die API antwortet.</summary>
    public static async Task StartAsync(CancellationToken ct)
    {
        if (await GetVersionAsync() != null) return;
        var app = AppPath ?? throw new InvalidOperationException("Ollama ist nicht installiert");
        var psi = new ProcessStartInfo(app) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(app)! };
        if (Path.GetFileName(app).Equals("ollama.exe", StringComparison.OrdinalIgnoreCase))
        {
            psi.Arguments = "serve";
            psi.CreateNoWindow = true;
        }
        // Der eigene Prozess kennt eine eben gesetzte Benutzer-Variable noch nicht
        var host = Environment.GetEnvironmentVariable("OLLAMA_HOST", EnvironmentVariableTarget.User);
        if (!string.IsNullOrEmpty(host)) psi.Environment["OLLAMA_HOST"] = host;
        Process.Start(psi)?.Dispose();
        for (var i = 0; i < 60; i++)
        {
            await Task.Delay(1000, ct);
            if (await GetVersionAsync() != null) return;
        }
        throw new TimeoutException("Ollama antwortet nicht");
    }

    /// <summary>Ollama beenden (App und Server), damit eine geänderte OLLAMA_HOST greift.</summary>
    public static async Task StopAsync()
    {
        _ = AppPath; // Pfad merken, solange der Prozess noch läuft
        foreach (var name in new[] { "ollama app", "ollama" })
        {
            foreach (var p in Process.GetProcessesByName(name))
            {
                try { p.Kill(entireProcessTree: true); }
                catch (Exception e) when (e is Win32Exception or InvalidOperationException) { }
                finally { p.Dispose(); }
            }
        }
        for (var i = 0; i < 20 && await GetVersionAsync() != null; i++) await Task.Delay(250);
    }

    /// <summary>
    /// Für Geräte im Netzwerk freigeben: OLLAMA_HOST=0.0.0.0 für den Benutzer, Ollama neu starten und eine
    /// Firewall-Regel für Port 11434 (privates Netz) anlegen – dafür fragt Windows einmal nach Admin-Rechten.
    /// Gibt einen Hinweis zurück, falls die Firewall-Regel nicht angelegt wurde.
    /// </summary>
    public static async Task<string?> EnableNetworkAsync(IProgress<(string Text, double? Fraction)> progress, CancellationToken ct)
    {
        string? warning = null;
        if (!NetworkEnabled)
        {
            progress.Report(("Freigabe im Netzwerk wird eingerichtet …", null));
            Environment.SetEnvironmentVariable("OLLAMA_HOST", $"0.0.0.0:{Port}", EnvironmentVariableTarget.User);
            Log.Write("OLLAMA_HOST=0.0.0.0 gesetzt");
        }
        if (!await ReachableFromNetworkAsync() && AppPath != null)
        {
            progress.Report(("Ollama wird neu gestartet …", null));
            await StopAsync();
            await StartAsync(ct);
            if (!await ReachableFromNetworkAsync())
                warning = "Ollama ist weiter nur auf diesem PC erreichbar. Läuft es als Dienst, dort OLLAMA_HOST=0.0.0.0 eintragen.";
        }
        if (!FirewallRuleExists())
        {
            progress.Report(("Windows fragt nach Admin-Rechten für die Firewall-Freigabe …", null));
            try
            {
                var psi = new ProcessStartInfo("netsh",
                    $"advfirewall firewall add rule name=\"{FirewallRule}\" dir=in action=allow protocol=TCP localport={Port} profile=private,domain")
                {
                    UseShellExecute = true, Verb = "runas", WindowStyle = ProcessWindowStyle.Hidden,
                };
                using var p = Process.Start(psi);
                if (p != null) await p.WaitForExitAsync(ct);
                if (!FirewallRuleExists()) warning ??= "Die Firewall-Freigabe hat nicht geklappt.";
                else Log.Write("Firewall-Regel für Ollama angelegt");
            }
            catch (Win32Exception)
            {
                warning ??= "Firewall-Freigabe abgebrochen – andere Geräte erreichen Ollama eventuell nicht.";
            }
        }
        return warning;
    }

    /// <summary>Modell laden; meldet Fortschritt aus dem Stream von /api/pull.</summary>
    public static async Task PullAsync(string model, IProgress<(string Text, double? Fraction)> progress, CancellationToken ct)
    {
        var body = JsonSerializer.Serialize(new { model, stream = true });
        using var req = new HttpRequestMessage(HttpMethod.Post, ApiUrl + "/api/pull")
        {
            Content = new StringContent(body, Encoding.UTF8, "application/json"),
        };
        using var resp = await Http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        resp.EnsureSuccessStatusCode();
        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream);
        string? line;
        var lastStatus = "";
        while ((line = await reader.ReadLineAsync(ct)) != null)
        {
            if (line.Length == 0) continue;
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            if (root.TryGetProperty("error", out var err)) throw new InvalidOperationException(err.GetString());
            var status = root.TryGetProperty("status", out var s) ? s.GetString() ?? "" : "";
            lastStatus = status;
            if (root.TryGetProperty("total", out var t) && t.GetInt64() > 0 && root.TryGetProperty("completed", out var c))
            {
                var total = t.GetInt64();
                var done = c.GetInt64();
                progress.Report(($"{model}: {Mb(done)} von {Mb(total)}", (double)done / total));
            }
            else
            {
                progress.Report(($"{model}: {Translate(status)}", null));
            }
        }
        if (lastStatus != "success") throw new InvalidOperationException($"{model}: Download unvollständig ({lastStatus})");
        Log.Write($"Ollama-Modell {model} geladen");
    }

    /// <summary>Kurzer Test: Modell laden und eine Antwort holen. Gibt die Dauer zurück.</summary>
    public static async Task<TimeSpan> TestAsync(string model, CancellationToken ct)
    {
        var sw = Stopwatch.StartNew();
        var body = JsonSerializer.Serialize(new
        {
            model, prompt = "Antworte nur mit dem Wort OK.", stream = false, options = new { num_predict = 8 },
        });
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(TimeSpan.FromMinutes(10));
        using var resp = await Http.PostAsync(ApiUrl + "/api/generate",
            new StringContent(body, Encoding.UTF8, "application/json"), cts.Token);
        var text = await resp.Content.ReadAsStringAsync(cts.Token);
        if (!resp.IsSuccessStatusCode)
        {
            var msg = text;
            try { msg = JsonDocument.Parse(text).RootElement.GetProperty("error").GetString() ?? text; }
            catch (Exception e) when (e is JsonException or KeyNotFoundException or InvalidOperationException) { }
            throw new InvalidOperationException(msg);
        }
        return sw.Elapsed;
    }

    private static string Translate(string status) => status switch
    {
        "pulling manifest" => "Modell wird gesucht …",
        "verifying sha256 digest" => "wird geprüft …",
        "writing manifest" => "wird gespeichert …",
        "removing any unused layers" => "wird aufgeräumt …",
        "success" => "fertig",
        _ => status,
    };

    private static string Mb(long bytes) =>
        bytes >= 1L << 30 ? $"{bytes / (double)(1L << 30):0.0} GB" : $"{bytes / (double)(1L << 20):0} MB";
}
