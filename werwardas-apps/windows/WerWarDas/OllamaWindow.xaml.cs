using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace WerWarDas;

/// <summary>Ollama und die Bildmodelle auf diesem PC einrichten und die Adresse für die Kamera anzeigen.</summary>
public partial class OllamaWindow : Window
{
    private readonly CancellationTokenSource _cts = new();
    private readonly Dictionary<string, CheckBox> _boxes = new();
    private readonly Dictionary<string, TextBlock> _states = new();
    private readonly IProgress<(string Text, double? Fraction)> _progress;
    private HashSet<string> _installed = new();
    private string? _version;
    private bool _busy;

    public OllamaWindow()
    {
        InitializeComponent();
        _progress = new Progress<(string Text, double? Fraction)>(p =>
        {
            ProgressPanel.Visibility = Visibility.Visible;
            ProgressText.Text = p.Text;
            Progress.IsIndeterminate = p.Fraction == null;
            if (p.Fraction is { } f) Progress.Value = f;
        });

        var (vram, gpu) = Ollama.GetGpu();
        var recommended = Ollama.Recommended(vram);
        GpuText.Text = gpu.Length > 0
            ? $"Grafikkarte: {gpu} ({vram:0.#} GB) – empfohlen: {recommended.Title}"
            : $"Keine Grafikkarte erkannt – empfohlen: {recommended.Title}";
        foreach (var model in Ollama.Models)
        {
            var state = new TextBlock { Foreground = Brushes.SeaGreen, FontWeight = FontWeights.SemiBold, Margin = new Thickness(8, 0, 0, 0) };
            var title = new StackPanel { Orientation = Orientation.Horizontal };
            title.Children.Add(new TextBlock { Text = $"{model.Title}  ({model.Size})", FontWeight = FontWeights.SemiBold });
            if (model == recommended)
                title.Children.Add(new TextBlock { Text = "empfohlen", Foreground = Brushes.RoyalBlue, Margin = new Thickness(8, 0, 0, 0) });
            title.Children.Add(state);
            var content = new StackPanel();
            content.Children.Add(title);
            content.Children.Add(new TextBlock { Text = model.Description, Foreground = Brushes.Gray, TextWrapping = TextWrapping.Wrap });
            var box = new CheckBox { Content = content, IsChecked = model == recommended, Margin = new Thickness(0, 3, 0, 3) };
            box.Checked += (_, _) => UpdateButtons();
            box.Unchecked += (_, _) => UpdateButtons();
            ModelList.Children.Add(box);
            _boxes[model.Name] = box;
            _states[model.Name] = state;
        }
        Loaded += async (_, _) => await RefreshAsync();
        Closing += (_, e) =>
        {
            if (_busy && MessageBox.Show(this, "Die Einrichtung läuft noch. Abbrechen und schließen?", "WerWarDas",
                    MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
            {
                e.Cancel = true;
                return;
            }
            _cts.Cancel();
        };
    }

    // ------------------------------------------------------------------ Anzeige
    private async Task RefreshAsync()
    {
        _version = await Ollama.GetVersionAsync();
        _installed = _version != null ? await Ollama.GetModelsAsync() : new HashSet<string>();
        var appInstalled = Ollama.AppPath != null || _version != null;

        if (_version != null)
        {
            StatusDot.Fill = Brushes.SeaGreen;
            StatusText.Text = $"Ollama {_version} ist installiert und läuft.";
        }
        else if (appInstalled)
        {
            StatusDot.Fill = Brushes.Orange;
            StatusText.Text = "Ollama ist installiert, läuft aber gerade nicht.";
        }
        else
        {
            StatusDot.Fill = Brushes.Gray;
            StatusText.Text = "Ollama ist noch nicht installiert.";
        }
        InstallButton.Content = appInstalled ? "Starten" : "Installieren";
        InstallButton.Visibility = _version != null ? Visibility.Collapsed : Visibility.Visible;

        foreach (var model in Ollama.Models)
        {
            var have = _installed.Contains(model.Name);
            _states[model.Name].Text = have ? "✓ geladen" : "";
            if (have) _boxes[model.Name].IsChecked = true;
        }

        var network = _version != null ? await Ollama.ReachableFromNetworkAsync() : Ollama.NetworkEnabled;
        var firewall = network && Ollama.FirewallRuleExists();
        NetworkText.Text = network && firewall
            ? "Freigegeben – Kamera, Raspberry Pi und Home Assistant erreichen Ollama auf diesem PC."
            : network
                ? "Ollama ist im Netzwerk erreichbar, die Windows-Firewall lässt Zugriffe aber eventuell nicht durch."
                : Ollama.NetworkEnabled && _version != null
                    ? "Freigabe eingestellt, Ollama muss aber neu gestartet werden."
                    : "Noch nicht freigegeben – Ollama ist nur auf diesem PC selbst erreichbar.";
        NetworkButton.Visibility = network && firewall ? Visibility.Collapsed : Visibility.Visible;

        var ready = Ollama.Models.Where(m => _installed.Contains(m.Name)).ToList();
        CameraGroup.Visibility = _version != null && ready.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        if (ready.Count > 0)
        {
            var selected = ModelBox.SelectedItem as string;
            ModelBox.ItemsSource = ready.Select(m => m.Name).ToList();
            ModelBox.SelectedItem = selected != null && ready.Any(m => m.Name == selected) ? selected : ready[0].Name;
        }
        AddressBox.Text = network ? Ollama.LanUrl : "http://localhost:11434";
        LocalHint.Text = network
            ? "Läuft der Kameradienst auf diesem PC selbst, geht auch http://localhost:11434."
            : "Für Kamera, Raspberry Pi oder Home Assistant zuerst oben „Freigeben“ wählen.";
        UpdateSnippet();
        UpdateButtons();
    }

    private void UpdateSnippet()
    {
        var model = ModelBox.SelectedItem as string ?? Ollama.Models[0].Name;
        SnippetBox.Text = $"- url: \"{AddressBox.Text}\"\n  model: \"{model}\"\n  timeout_seconds: 300";
    }

    private void UpdateButtons()
    {
        var running = _version != null;
        var missing = _boxes.Where(b => b.Value.IsChecked == true && !_installed.Contains(b.Key)).ToList();
        InstallButton.IsEnabled = !_busy;
        PullButton.IsEnabled = !_busy && running && missing.Count > 0;
        NetworkButton.IsEnabled = !_busy;
        TestButton.IsEnabled = !_busy && running;
        AllButton.IsEnabled = !_busy;
        foreach (var box in _boxes.Values) box.IsEnabled = !_busy;
    }

    private void ShowMessage(string text, bool error)
    {
        MessageText.Text = text;
        MessageText.Foreground = error ? Brushes.Firebrick : Brushes.SeaGreen;
        MessageText.Visibility = string.IsNullOrEmpty(text) ? Visibility.Collapsed : Visibility.Visible;
    }

    // ------------------------------------------------------------------ Schritte
    private async Task RunAsync(Func<CancellationToken, Task<string?>> work)
    {
        if (_busy) return;
        _busy = true;
        ShowMessage("", false);
        UpdateButtons();
        try
        {
            var message = await work(_cts.Token);
            // Ein „!“ am Anfang markiert eine Warnung
            if (!string.IsNullOrEmpty(message))
                ShowMessage(message.TrimStart('!'), message.StartsWith('!'));
        }
        catch (OperationCanceledException)
        {
            if (!_cts.IsCancellationRequested) ShowMessage("Abgebrochen.", true);
        }
        catch (Exception e) when (e is HttpRequestException or InvalidOperationException or TimeoutException
                                      or System.ComponentModel.Win32Exception or System.IO.IOException)
        {
            Log.Write($"Ollama-Einrichtung: {e.Message}");
            ShowMessage("Fehler: " + e.Message, true);
        }
        finally
        {
            _busy = false;
            ProgressPanel.Visibility = Visibility.Collapsed;
            if (!_cts.IsCancellationRequested) await RefreshAsync();
        }
    }

    private async Task EnsureRunning(CancellationToken ct)
    {
        if (await Ollama.GetVersionAsync() != null) return;
        if (Ollama.AppPath == null) await Ollama.InstallAsync(_progress, ct);
        else
        {
            _progress.Report(("Ollama wird gestartet …", null));
            await Ollama.StartAsync(ct);
        }
    }

    private async Task<List<string>> PullSelected(CancellationToken ct)
    {
        var pulled = new List<string>();
        foreach (var model in Ollama.Models)
        {
            if (_boxes[model.Name].IsChecked != true || _installed.Contains(model.Name)) continue;
            await Ollama.PullAsync(model.Name, _progress, ct);
            _installed.Add(model.Name);
            pulled.Add(model.Name);
        }
        return pulled;
    }

    private async void Install_Click(object sender, RoutedEventArgs e) =>
        await RunAsync(async ct =>
        {
            await EnsureRunning(ct);
            return "Ollama läuft.";
        });

    private async void Pull_Click(object sender, RoutedEventArgs e) =>
        await RunAsync(async ct =>
        {
            var pulled = await PullSelected(ct);
            return pulled.Count > 0 ? $"Geladen: {string.Join(", ", pulled)}" : null;
        });

    private async void Network_Click(object sender, RoutedEventArgs e) =>
        await RunAsync(async ct =>
        {
            var warning = await Ollama.EnableNetworkAsync(_progress, ct);
            return warning != null ? "!" + warning : "Ollama ist im Netzwerk erreichbar.";
        });

    private async void Test_Click(object sender, RoutedEventArgs e)
    {
        var model = ModelBox.SelectedItem as string;
        if (model == null) return;
        await RunAsync(async ct =>
        {
            _progress.Report(($"{model} wird geladen und getestet (beim ersten Mal bis zu einer Minute) …", null));
            var time = await Ollama.TestAsync(model, ct);
            return $"Test erfolgreich: {model} hat in {time.TotalSeconds:0.0} s geantwortet.";
        });
    }

    private async void All_Click(object sender, RoutedEventArgs e) =>
        await RunAsync(async ct =>
        {
            await EnsureRunning(ct);
            var warning = await Ollama.EnableNetworkAsync(_progress, ct);
            _installed = await Ollama.GetModelsAsync();
            if (!_boxes.Values.Any(b => b.IsChecked == true))
                _boxes[Ollama.Recommended(Ollama.GetGpu().Gb).Name].IsChecked = true;
            await PullSelected(ct);
            var model = Ollama.Models.First(m => _boxes[m.Name].IsChecked == true).Name;
            _progress.Report(($"{model} wird getestet (beim ersten Mal bis zu einer Minute) …", null));
            var time = await Ollama.TestAsync(model, ct);
            var done = $"Fertig! {model} antwortet in {time.TotalSeconds:0.0} s. Jetzt die Adresse unten in der Kamera eintragen.";
            return warning != null ? $"!{done}\n{warning}" : done;
        });

    private void ModelBox_Changed(object sender, SelectionChangedEventArgs e) => UpdateSnippet();

    private void CopyAddress_Click(object sender, RoutedEventArgs e) => Copy(AddressBox.Text, "Adresse kopiert.");

    private void CopySnippet_Click(object sender, RoutedEventArgs e) => Copy(SnippetBox.Text, "Einstellungen kopiert.");

    private void Copy(string text, string message)
    {
        try
        {
            Clipboard.SetText(text);
            ShowMessage(message, false);
        }
        catch (System.Runtime.InteropServices.COMException)
        {
            ShowMessage("Zwischenablage gerade belegt – bitte noch einmal versuchen.", true);
        }
    }
}
