using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Threading;

namespace WerWarDas;

/// <summary>
/// Sucht im GitHub-Release nach einer neueren WerWarDas.exe, lädt sie herunter, tauscht die laufende Exe aus
/// und startet die App neu. Ausgetauscht wird nur, wenn der PC gerade frei ist (nicht gesperrt/gewarnt).
/// </summary>
public sealed class Updater : IDisposable
{
    public const string Repo = "Devilwitha/werwardas";
    private const string AssetName = "WerWarDas.exe";
    public const string UpdatedArg = "--updated";

    private static readonly HttpClient Http = CreateClient();

    private readonly Dispatcher _dispatcher;
    private readonly Func<bool> _canRestart;
    private readonly Action _beforeRestart;
    private readonly TimeSpan _interval;
    private readonly Timer _timer;
    private readonly SemaphoreSlim _busy = new(1, 1);
    private string? _readyFile;
    private Version? _readyVersion;
    private bool _waitLogged;

    /// <param name="intervalHours">Abstand der automatischen Suche; 0 = nur von Hand.</param>
    /// <param name="canRestart">Wird auf dem UI-Thread gefragt, ob gerade neu gestartet werden darf.</param>
    /// <param name="beforeRestart">Läuft auf dem UI-Thread direkt vor dem Neustart (Dienste stoppen, Mutex freigeben).</param>
    public Updater(int intervalHours, Dispatcher dispatcher, Func<bool> canRestart, Action beforeRestart)
    {
        _dispatcher = dispatcher;
        _canRestart = canRestart;
        _beforeRestart = beforeRestart;
        _interval = TimeSpan.FromHours(Math.Max(0, intervalHours));
        _timer = new Timer(_ => _ = Tick(), null, Timeout.Infinite, Timeout.Infinite);
        if (_interval > TimeSpan.Zero) _timer.Change(TimeSpan.FromMinutes(2), Timeout.InfiniteTimeSpan);
    }

    public static Version CurrentVersion => Normalize(Assembly.GetEntryAssembly()?.GetName().Version ?? new Version(0, 0));

    /// <summary>Nur die veröffentlichte Einzeldatei-Exe kann sich selbst austauschen, nicht der Entwicklungsbuild.</summary>
    private static bool CanSelfUpdate =>
        string.IsNullOrEmpty(Assembly.GetEntryAssembly()?.Location) && Environment.ProcessPath != null;

    private async Task Tick()
    {
        await CheckAsync();
        // Liegt ein Update bereit, aber der PC ist gerade nicht frei: jede Minute erneut versuchen.
        var next = _readyFile != null ? TimeSpan.FromMinutes(1) : _interval;
        try
        {
            if (next > TimeSpan.Zero) _timer.Change(next, Timeout.InfiniteTimeSpan);
        }
        catch (ObjectDisposedException)
        {
            // Einstellungen wurden geändert, ein neuer Updater hat übernommen.
        }
    }

    /// <summary>Sucht nach einem Update und installiert es, wenn möglich. Gibt eine Statusmeldung zurück.</summary>
    public async Task<string> CheckAsync()
    {
        if (!CanSelfUpdate) return "Updates gibt es nur für die veröffentlichte WerWarDas.exe.";
        if (!await _busy.WaitAsync(0)) return "Die Update-Suche läuft bereits.";
        try
        {
            if (_readyFile == null)
            {
                var message = await DownloadIfNewer();
                if (message != null) return message;
            }
            return await _dispatcher.InvokeAsync(TryInstall);
        }
        catch (Exception ex)
        {
            Log.Write($"Update-Suche fehlgeschlagen: {ex.Message}");
            return $"Update-Suche fehlgeschlagen: {ex.Message}";
        }
        finally
        {
            _busy.Release();
        }
    }

    /// <summary>Lädt eine neuere Version herunter. null = Update liegt bereit, sonst der Grund, warum nicht.</summary>
    private async Task<string?> DownloadIfNewer()
    {
        using var resp = await Http.GetAsync($"https://api.github.com/repos/{Repo}/releases/latest");
        if (resp.StatusCode == HttpStatusCode.NotFound) return "Auf GitHub gibt es noch kein Release.";
        resp.EnsureSuccessStatusCode();
        using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
        var root = doc.RootElement;
        var tag = root.GetProperty("tag_name").GetString() ?? "";
        if (!Version.TryParse(tag.TrimStart('v', 'V'), out var parsed))
            return $"Unbekannte Release-Version „{tag}“.";
        var latest = Normalize(parsed);
        if (latest <= CurrentVersion) return $"WerWarDas ist aktuell (Version {CurrentVersion}).";

        JsonElement? asset = null;
        foreach (var a in root.GetProperty("assets").EnumerateArray())
        {
            if (string.Equals(a.GetProperty("name").GetString(), AssetName, StringComparison.OrdinalIgnoreCase)) asset = a;
        }
        if (asset is not { } found) return $"Im Release {tag} fehlt die {AssetName}.";

        var url = found.GetProperty("browser_download_url").GetString()!;
        var size = found.GetProperty("size").GetInt64();
        var digest = found.TryGetProperty("digest", out var d) ? d.GetString() : null;

        var dir = Path.GetDirectoryName(Environment.ProcessPath)!;
        var target = Path.Combine(dir, "WerWarDas.update.exe");
        Log.Write($"Update {latest} gefunden, lade herunter …");
        using (var download = await Http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead))
        {
            download.EnsureSuccessStatusCode();
            await using var file = File.Create(target);
            await download.Content.CopyToAsync(file);
        }

        // Prüfen, ob die Datei vollständig und unverändert ist und wirklich die erwartete Version enthält.
        var problem = Verify(target, size, digest, latest);
        if (problem != null)
        {
            File.Delete(target);
            Log.Write($"Update verworfen: {problem}");
            return $"Update verworfen: {problem}";
        }
        _readyFile = target;
        _readyVersion = latest;
        Log.Write($"Update {latest} heruntergeladen und geprüft");
        return null;
    }

    private static string? Verify(string path, long size, string? digest, Version expected)
    {
        if (new FileInfo(path).Length != size) return "Download unvollständig";
        if (digest != null && digest.StartsWith("sha256:", StringComparison.OrdinalIgnoreCase))
        {
            using var stream = File.OpenRead(path);
            var hash = Convert.ToHexString(SHA256.HashData(stream));
            if (!hash.Equals(digest[7..], StringComparison.OrdinalIgnoreCase)) return "Prüfsumme stimmt nicht";
        }
        var info = FileVersionInfo.GetVersionInfo(path);
        if (!Version.TryParse(info.FileVersion, out var fileVersion) || Normalize(fileVersion) != expected)
            return $"Exe hat Version {info.FileVersion} statt {expected}";
        return null;
    }

    /// <summary>Läuft auf dem UI-Thread: Exe austauschen und neu starten, sobald der PC frei ist.</summary>
    private string TryInstall()
    {
        if (_readyFile == null || _readyVersion == null) return "Kein Update bereit.";
        if (!_canRestart())
        {
            if (!_waitLogged) Log.Write($"Update {_readyVersion} wartet, bis der PC wieder frei ist");
            _waitLogged = true;
            return $"Update {_readyVersion} ist geladen und wird installiert, sobald der PC frei ist.";
        }

        var exe = Environment.ProcessPath!;
        var old = exe + ".old";
        try
        {
            if (File.Exists(old)) File.Delete(old);
            // Eine laufende Exe darf umbenannt (aber nicht überschrieben) werden.
            File.Move(exe, old);
            try
            {
                File.Move(_readyFile, exe);
            }
            catch
            {
                File.Move(old, exe);
                throw;
            }
        }
        catch (Exception ex)
        {
            Log.Write($"Update konnte nicht installiert werden: {ex.Message}");
            _readyFile = null;
            return $"Update konnte nicht installiert werden: {ex.Message}";
        }

        Log.Write($"Update auf {_readyVersion} installiert – starte neu");
        _beforeRestart();
        Process.Start(new ProcessStartInfo(exe, UpdatedArg)
        {
            UseShellExecute = false,
            WorkingDirectory = Path.GetDirectoryName(exe)!,
        });
        System.Windows.Application.Current.Shutdown();
        return $"Update auf {_readyVersion} installiert.";
    }

    /// <summary>Reste eines vorherigen Updates aufräumen (die alte Exe ist evtl. noch kurz gesperrt).</summary>
    public static void CleanupAfterUpdate()
    {
        if (Environment.ProcessPath is not { } exe) return;
        var dir = Path.GetDirectoryName(exe)!;
        var files = new[] { exe + ".old", Path.Combine(dir, "WerWarDas.update.exe") };
        Task.Run(async () =>
        {
            for (var attempt = 0; attempt < 30; attempt++)
            {
                var pending = false;
                foreach (var file in files)
                {
                    try { if (File.Exists(file)) File.Delete(file); }
                    catch (IOException) { pending = true; }
                    catch (UnauthorizedAccessException) { pending = true; }
                }
                if (!pending) return;
                await Task.Delay(1000);
            }
        });
    }

    /// <summary>"1.0.5" und "1.0.5.0" sollen als gleich gelten.</summary>
    private static Version Normalize(Version v) => new(v.Major, v.Minor, Math.Max(v.Build, 0), Math.Max(v.Revision, 0));

    private static HttpClient CreateClient()
    {
        var client = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
        client.DefaultRequestHeaders.UserAgent.ParseAdd($"WerWarDas-Updater/{CurrentVersion}");
        client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
        return client;
    }

    public void Dispose()
    {
        _timer.Dispose();
    }
}
