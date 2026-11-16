using System;
using System.Collections.Generic;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using MQTTnet;
using MQTTnet.Client;
using MQTTnet.Protocol;

namespace WerWarDas;

/// <summary>Status der Kamera, wie ihn der Raspberry Pi auf "werwardas/cam/state" veröffentlicht.</summary>
public sealed class CamState
{
    [JsonPropertyName("score")] public double? Score { get; set; }
    [JsonPropertyName("tidy")] public bool? Tidy { get; set; }
    [JsonPropertyName("threshold")] public double? Threshold { get; set; }
    [JsonPropertyName("reason")] public string Reason { get; set; } = "";
    [JsonPropertyName("analyzer")] public string Analyzer { get; set; } = "";
    [JsonPropertyName("trigger")] public string Trigger { get; set; } = "";
    [JsonPropertyName("error")] public bool Error { get; set; }
    [JsonPropertyName("paused")] public bool Paused { get; set; }
    [JsonPropertyName("timestamp")] public DateTimeOffset? Timestamp { get; set; }

    /// <summary>Nur ein eindeutig "unordentlich" führt zu Hinweis/Sperre. Fehler, Pause, unbekannt = frei.</summary>
    public bool IsMessy => Tidy == false && !Error && !Paused;
}

/// <summary>Einstellungen des Kameradienstes, veröffentlicht auf "werwardas/cam/settings" (retained).</summary>
public sealed class CamSettings
{
    [JsonPropertyName("threshold")] public int Threshold { get; set; }
    [JsonPropertyName("interval")] public int Interval { get; set; }
    [JsonPropertyName("active_hours")] public string? ActiveHours { get; set; }
}

/// <summary>MQTT-Verbindung zu Home Assistant / Mosquitto inkl. automatischer Wiederverbindung.</summary>
public sealed class MqttService : IDisposable
{
    private readonly Settings _settings;
    private readonly IMqttClient _client;
    private readonly MqttFactory _factory = new();
    private readonly CancellationTokenSource _cts = new();

    public event Action<CamState>? CamStateReceived;
    public event Action<byte[]>? ImageReceived;
    public event Action<bool>? CamAvailabilityChanged;
    public event Action<string>? CommandReceived;
    public event Action<bool>? ConnectionChanged;

    /// <summary>Zuletzt empfangene Einstellungen des Kameradienstes (null = noch nichts empfangen).</summary>
    public CamSettings? CamSettings { get; private set; }

    public bool IsConnected => _client.IsConnected;
    public string LastError { get; private set; } = "";

    private string CamBase => $"{_settings.TopicPrefix}/cam";
    private string PcBase => $"{_settings.TopicPrefix}/pc/{_settings.PcId}";

    public MqttService(Settings settings)
    {
        _settings = settings;
        _client = _factory.CreateMqttClient();
        _client.ApplicationMessageReceivedAsync += OnMessage;
        _client.ConnectedAsync += async _ =>
        {
            LastError = "";
            ConnectionChanged?.Invoke(true);
            await OnConnected();
        };
        _client.DisconnectedAsync += e =>
        {
            if (e.ClientWasConnected)
            {
                Log.Write($"MQTT getrennt: {e.Reason}");
                ConnectionChanged?.Invoke(false);
            }
            return Task.CompletedTask;
        };
    }

    public void Start() => _ = Task.Run(ReconnectLoop);

    private async Task ReconnectLoop()
    {
        while (!_cts.IsCancellationRequested)
        {
            if (!_client.IsConnected)
            {
                try
                {
                    var options = new MqttClientOptionsBuilder()
                        .WithTcpServer(_settings.BrokerHost, _settings.BrokerPort)
                        .WithClientId($"werwardas_pc_{_settings.PcId}")
                        .WithCredentials(_settings.BrokerUser, _settings.BrokerPassword)
                        .WithCleanSession()
                        .WithKeepAlivePeriod(TimeSpan.FromSeconds(30))
                        .WithWillTopic($"{PcBase}/availability")
                        .WithWillPayload("offline")
                        .WithWillRetain(true)
                        .Build();
                    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
                    await _client.ConnectAsync(options, timeout.Token);
                    Log.Write($"MQTT verbunden mit {_settings.BrokerHost}:{_settings.BrokerPort}");
                }
                catch (Exception ex)
                {
                    LastError = ex.Message;
                }
            }
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(5), _cts.Token);
            }
            catch (TaskCanceledException)
            {
                break;
            }
        }
    }

    private async Task OnConnected()
    {
        var subscribe = _factory.CreateSubscribeOptionsBuilder()
            .WithTopicFilter($"{CamBase}/state")
            .WithTopicFilter($"{CamBase}/image")
            .WithTopicFilter($"{CamBase}/availability")
            .WithTopicFilter($"{CamBase}/settings")
            .WithTopicFilter($"{PcBase}/cmd")
            .WithTopicFilter("homeassistant/status")
            .Build();
        await _client.SubscribeAsync(subscribe);
        await PublishDiscovery();
        await Publish($"{PcBase}/availability", "online", retain: true);
    }

    private Task OnMessage(MqttApplicationMessageReceivedEventArgs e)
    {
        var topic = e.ApplicationMessage.Topic;
        var payload = e.ApplicationMessage.PayloadSegment.ToArray();
        try
        {
            if (topic == $"{CamBase}/state")
            {
                var state = JsonSerializer.Deserialize<CamState>(payload);
                if (state != null) CamStateReceived?.Invoke(state);
            }
            else if (topic == $"{CamBase}/image")
            {
                ImageReceived?.Invoke(payload);
            }
            else if (topic == $"{CamBase}/availability")
            {
                CamAvailabilityChanged?.Invoke(Encoding.UTF8.GetString(payload) == "online");
            }
            else if (topic == $"{CamBase}/settings")
            {
                CamSettings = JsonSerializer.Deserialize<CamSettings>(payload);
            }
            else if (topic == $"{PcBase}/cmd")
            {
                CommandReceived?.Invoke(Encoding.UTF8.GetString(payload).Trim().ToLowerInvariant());
            }
            else if (topic == "homeassistant/status" && Encoding.UTF8.GetString(payload) == "online")
            {
                _ = PublishDiscovery();
            }
        }
        catch (Exception ex)
        {
            Log.Write($"Fehler bei Nachricht auf {topic}: {ex.Message}");
        }
        return Task.CompletedTask;
    }

    public async Task Publish(string topic, string payload, bool retain = false)
    {
        if (!_client.IsConnected) return;
        try
        {
            var msg = new MqttApplicationMessageBuilder()
                .WithTopic(topic)
                .WithPayload(payload)
                .WithRetainFlag(retain)
                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                .Build();
            await _client.PublishAsync(msg, _cts.Token);
        }
        catch (Exception ex)
        {
            Log.Write($"Senden fehlgeschlagen ({topic}): {ex.Message}");
        }
    }

    /// <summary>Bittet die Kamera, sofort ein Foto zu machen und zu bewerten.</summary>
    /// <summary>Eine Einstellung des Kameradienstes ändern (interval, threshold, active_hours).</summary>
    public Task SetCamSetting(string key, string value) => Publish($"{CamBase}/set/{key}", value);

    public Task RequestCheck() => Publish($"{CamBase}/cmd/check", $"pc:{_settings.PcId}");

    public Task PublishPcState(string state, bool locked) => Task.WhenAll(
        Publish($"{PcBase}/state", state, retain: true),
        Publish($"{PcBase}/locked", locked ? "ON" : "OFF", retain: true));

    public Task PublishEvent(string evt) => Publish($"{PcBase}/event", evt);

    private async Task PublishDiscovery()
    {
        var id = $"werwardas_pc_{_settings.PcId}";
        var device = new Dictionary<string, object>
        {
            ["identifiers"] = new[] { id },
            ["name"] = $"WerWarDas PC {_settings.PcName}",
            ["manufacturer"] = "WerWarDas",
            ["model"] = "Windows-App",
            ["sw_version"] = typeof(MqttService).Assembly.GetName().Version?.ToString(3) ?? "",
        };
        var entities = new Dictionary<string, Dictionary<string, object>>
        {
            ["sensor/status"] = new()
            {
                ["name"] = "Status",
                ["icon"] = "mdi:monitor-lock",
                ["state_topic"] = $"{PcBase}/state",
            },
            ["switch/lock"] = new()
            {
                ["name"] = "Sperre",
                ["icon"] = "mdi:lock",
                ["command_topic"] = $"{PcBase}/cmd",
                ["payload_on"] = "lock",
                ["payload_off"] = "unlock",
                ["state_topic"] = $"{PcBase}/locked",
                ["state_on"] = "ON",
                ["state_off"] = "OFF",
            },
            ["button/warn"] = new()
            {
                ["name"] = "Hinweis anzeigen",
                ["icon"] = "mdi:message-alert",
                ["command_topic"] = $"{PcBase}/cmd",
                ["payload_press"] = "warn",
            },
            ["button/shutdown"] = new()
            {
                ["name"] = "Herunterfahren",
                ["icon"] = "mdi:power",
                ["command_topic"] = $"{PcBase}/cmd",
                ["payload_press"] = "shutdown",
            },
        };
        foreach (var (key, conf) in entities)
        {
            var parts = key.Split('/');
            conf["unique_id"] = $"{id}_{parts[1]}";
            conf["device"] = device;
            conf["availability_topic"] = $"{PcBase}/availability";
            await Publish($"homeassistant/{parts[0]}/{id}/{parts[1]}/config", JsonSerializer.Serialize(conf), retain: true);
        }
    }

    public void Dispose()
    {
        try
        {
            // Erst "offline" senden, dann abbrechen: Publish nutzt _cts.Token und würde sonst sofort scheitern.
            // (Bei einer sauberen Trennung verschickt der Broker den Last Will nicht.)
            if (_client.IsConnected)
            {
                Task.Run(() => Publish($"{PcBase}/availability", "offline", retain: true)).Wait(2000);
            }
            _cts.Cancel();
            if (_client.IsConnected)
            {
                Task.Run(() => _client.DisconnectAsync()).Wait(2000);
            }
        }
        catch (Exception)
        {
            // Beim Beenden egal
        }
        _cts.Cancel();
        _client.Dispose();
    }
}
