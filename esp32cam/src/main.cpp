// BolliSoft Kamera – ESP32-CAM (AI-Thinker) für WerWarDas und Vorrat
//
//   http://<Name>.local/                Statusseite mit Vorschau, Version, Update-Stand
//   http://<Name>.local/capture         aktuelles Foto als JPEG
//       ?flash=1       LED in voller Helligkeit dazuschalten
//       ?led=0..255    LED mit dieser Helligkeit (z. B. 60 im Kühlschrank, sonst überstrahlt es)
//       ?settle=0..15  so viele Bilder vorher verwerfen, damit sich die Belichtung einstellt
//                      (Standard: 4 mit LED, sonst 1)
//   http://<Name>.local:81/stream       Livestream (MJPEG), ?led=0..255, ?fps=1..20 (Standard 10)
//   http://<Name>.local/settings        Name, automatische Updates, WLAN neu einrichten
//
// WLAN: Ohne gespeichertes WLAN öffnet die Kamera den Hotspot „BolliSoft-Kamera-XXXX“ – verbinden,
// WLAN auswählen, Name und Passwort eintragen. Eine vorhandene include/secrets.h wird beim ersten
// Start übernommen (für Firmware, die selbst gebaut und per USB geflasht wird).
//
// Updates: Die Kamera sucht regelmäßig auf GitHub nach Releases mit dem Tag „esp32cam-v…“ und spielt
// die neue Firmware (bollisoft-cam.bin) selbst ein. Einstellungen und WLAN bleiben dabei erhalten.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WiFiManager.h>

#include "esp_camera.h"
#include "esp_http_server.h"
#include "github_ca.h"

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#ifndef FW_VERSION
#define FW_VERSION "0.0.0"  // selbst gebaut: das erste Release gilt als neuer
#endif
#ifndef CAM_HOSTNAME
#define CAM_HOSTNAME "werwardas-cam"  // bisheriger Name: bestehende Kameras bleiben erreichbar
#endif
#ifndef OTA_PASSWORD
#define OTA_PASSWORD "werwardas"
#endif

static const char *RELEASES_URL = "https://api.github.com/repos/Devilwitha/HAaddons/releases?per_page=20";
static const char *TAG_PREFIX = "esp32cam-v";  // nie releases/latest: im Repository liegen mehrere Programme
static const char *ASSET_NAME = "bollisoft-cam.bin";
static const unsigned long FIRST_CHECK_MS = 2UL * 60 * 1000;

// AI-Thinker ESP32-CAM
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22
#define FLASH_LED_PIN 4

// LED gedimmt per PWM. Kanal 7 nutzt Timer 3 – die Kamera belegt Kanal 0 / Timer 0 für ihren Takt.
#define LED_CHANNEL 7
#define LED_FREQ 5000
#define LED_BITS 8
#define LED_WARMUP_MS 120
#define MAX_SETTLE 15

WebServer server(80);
Preferences prefs;
static httpd_handle_t streamServer = nullptr;
static volatile bool cameraOk = false;
static volatile bool stopStreams = false;
static volatile int streamClients = 0;
static String hostName;
static String otaPassword;
static int updateHours = 12;
static unsigned long lastCheck = 0;
static String updateStatus = "noch nicht gesucht";
static String latestVersion = "";

// ------------------------------------------------------------------ Hilfen
static void setLed(int level) { ledcWrite(LED_CHANNEL, constrain(level, 0, 255)); }

static String cleanHost(String name) {
  name.toLowerCase();
  String out;
  for (size_t i = 0; i < name.length() && out.length() < 32; i++) {
    char ch = name[i];
    if ((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-') out += ch;
    else if (ch == ' ' || ch == '_') out += '-';
  }
  return out.length() ? out : String(CAM_HOSTNAME);
}

static String htmlEscape(const String &text) {
  String out;
  for (size_t i = 0; i < text.length(); i++) {
    char ch = text[i];
    if (ch == '<') out += "&lt;";
    else if (ch == '>') out += "&gt;";
    else if (ch == '&') out += "&amp;";
    else if (ch == '"') out += "&quot;";
    else out += ch;
  }
  return out;
}

// Version „1.0.12“ als Zahl zum Vergleichen
static uint32_t versionNumber(const String &v) {
  uint32_t parts[3] = {0, 0, 0};
  int index = 0;
  for (size_t i = 0; i < v.length() && index < 3; i++) {
    if (isdigit(v[i])) parts[index] = parts[index] * 10 + (v[i] - '0');
    else if (v[i] == '.') index++;
    else break;
  }
  return parts[0] * 1000000 + parts[1] * 1000 + parts[2];
}

// ------------------------------------------------------------------ Kamera
static bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 10000000;  // 20 MHz erzeugt Streifen und EV-EOF-OVF
  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode = CAMERA_GRAB_LATEST;

  if (psramFound()) {
    c.frame_size = FRAMESIZE_UXGA;  // 1600x1200
    c.jpeg_quality = 10;
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    c.frame_size = FRAMESIZE_SVGA;
    c.jpeg_quality = 12;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
  }

  // Nach einem Software-Neustart (OTA, WLAN-Verlust) hängt der Sensor manchmal noch:
  // kurz stromlos schalten und mehrfach versuchen.
  esp_err_t err = ESP_FAIL;
  for (int attempt = 1; attempt <= 3 && err != ESP_OK; attempt++) {
    esp_camera_deinit();
    pinMode(PWDN_GPIO_NUM, OUTPUT);
    digitalWrite(PWDN_GPIO_NUM, HIGH);
    delay(200);
    digitalWrite(PWDN_GPIO_NUM, LOW);
    delay(200);
    err = esp_camera_init(&c);
    if (err != ESP_OK) Serial.printf("Kamera-Fehler 0x%x (Versuch %d)\n", err, attempt);
  }
  if (err != ESP_OK) return false;
  sensor_t *s = esp_camera_sensor_get();
  s->set_whitebal(s, 1);
  s->set_awb_gain(s, 1);
  s->set_exposure_ctrl(s, 1);
  s->set_aec2(s, 1);  // bessere Belichtung bei wenig Licht
  s->set_gain_ctrl(s, 1);
  s->set_lenc(s, 1);
  return true;
}

static int argInt(const char *name, int fallback, int lo, int hi) {
  if (!server.hasArg(name) || server.arg(name).length() == 0) return fallback;
  return constrain(server.arg(name).toInt(), lo, hi);
}

static void handleCapture() {
  if (!cameraOk) {
    server.send(503, "text/plain", "Kamera nicht bereit");
    return;
  }
  int led = server.arg("flash") == "1" ? 255 : 0;
  led = argInt("led", led, 0, 255);
  int settle = argInt("settle", led > 0 ? 4 : 1, 0, MAX_SETTLE);

  if (led > 0) {
    setLed(led);
    delay(LED_WARMUP_MS);
  }
  // Ältere Bilder verwerfen: das erste stammt noch von vor dem Licht, die weiteren geben der
  // Belichtungsautomatik Zeit, sich auf die LED einzustellen.
  for (int i = 0; i < settle; i++) {
    camera_fb_t *old = esp_camera_fb_get();
    if (old) esp_camera_fb_return(old);
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (led > 0 && streamClients == 0) setLed(0);
  if (!fb) {
    server.send(500, "text/plain", "Foto fehlgeschlagen");
    return;
  }
  server.sendHeader("Cache-Control", "no-store");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.client().write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

// ------------------------------------------------------------------ Livestream (Port 81)
// Eigener Server, damit der Stream die Fotos und die Statusseite auf Port 80 nicht blockiert.
static esp_err_t streamHandler(httpd_req_t *req) {
  char query[64] = "";
  char value[8];
  int led = 0;
  int fps = 10;
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK) {
    if (httpd_query_key_value(query, "led", value, sizeof(value)) == ESP_OK) led = constrain(atoi(value), 0, 255);
    if (httpd_query_key_value(query, "fps", value, sizeof(value)) == ESP_OK) fps = constrain(atoi(value), 1, 20);
  }
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  streamClients++;
  if (led > 0) setLed(led);
  char part[96];
  const unsigned long period = 1000UL / fps;
  esp_err_t res = ESP_OK;
  while (res == ESP_OK && cameraOk && !stopStreams) {
    unsigned long started = millis();
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) break;
    int len = snprintf(part, sizeof(part), "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                       (unsigned)fb->len);
    res = httpd_resp_send_chunk(req, part, len);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, "\r\n", 2);
    esp_camera_fb_return(fb);
    unsigned long spent = millis() - started;
    if (spent < period) delay(period - spent);
  }
  streamClients--;
  if (streamClients <= 0) setLed(0);
  return res;
}

static void startStreamServer() {
  if (streamServer) return;
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 81;
  config.ctrl_port = 32769;
  config.max_open_sockets = 3;
  if (httpd_start(&streamServer, &config) != ESP_OK) {
    streamServer = nullptr;
    Serial.println("Livestream-Server konnte nicht starten");
    return;
  }
  httpd_uri_t uri = {};
  uri.uri = "/stream";
  uri.method = HTTP_GET;
  uri.handler = streamHandler;
  httpd_register_uri_handler(streamServer, &uri);
}

static void stopStreamServer() {
  stopStreams = true;
  if (streamServer) {
    httpd_stop(streamServer);
    streamServer = nullptr;
  }
  setLed(0);
}

// ------------------------------------------------------------------ Updates von GitHub
static bool syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
  for (int i = 0; i < 20; i++) {
    if (time(nullptr) > 1700000000) return true;
    delay(500);
  }
  return false;
}

// Sucht das neueste Release „esp32cam-v…“. install = true spielt es auch ein (Neustart bei Erfolg).
static void checkForUpdate(bool install) {
  if (WiFi.status() != WL_CONNECTED) return;
  if (!syncTime()) {
    updateStatus = "Uhrzeit nicht verfügbar (NTP) – nötig für die sichere Verbindung zu GitHub";
    return;
  }
  WiFiClientSecure client;
  client.setCACert(GITHUB_CA);
  HTTPClient http;
  http.useHTTP10(true);  // ohne „chunked“: die Antwort lässt sich direkt als JSON lesen
  http.setTimeout(15000);
  if (!http.begin(client, RELEASES_URL)) {
    updateStatus = "Verbindung zu GitHub fehlgeschlagen";
    return;
  }
  http.addHeader("Accept", "application/vnd.github+json");
  http.setUserAgent("BolliSoft-Kamera/" FW_VERSION);
  int code = http.GET();
  if (code != 200) {
    updateStatus = "GitHub antwortet mit " + String(code);
    http.end();
    return;
  }
  // Nur die nötigen Felder einlesen (die Antwort ist groß)
  JsonDocument filter;
  filter[0]["tag_name"] = true;
  filter[0]["draft"] = true;
  filter[0]["prerelease"] = true;
  filter[0]["assets"][0]["name"] = true;
  filter[0]["assets"][0]["browser_download_url"] = true;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, http.getStream(), DeserializationOption::Filter(filter));
  http.end();
  if (err) {
    updateStatus = String("Antwort von GitHub nicht lesbar: ") + err.c_str();
    return;
  }
  uint32_t best = versionNumber(FW_VERSION);
  String bestVersion, bestUrl;
  for (JsonObject release : doc.as<JsonArray>()) {
    String tag = release["tag_name"] | "";
    if (!tag.startsWith(TAG_PREFIX) || (release["draft"] | false) || (release["prerelease"] | false)) continue;
    String version = tag.substring(strlen(TAG_PREFIX));
    if (versionNumber(version) <= best) continue;
    for (JsonObject asset : release["assets"].as<JsonArray>()) {
      if (String(asset["name"] | "") == ASSET_NAME) {
        best = versionNumber(version);
        bestVersion = version;
        bestUrl = asset["browser_download_url"] | "";
      }
    }
  }
  if (bestUrl.isEmpty()) {
    latestVersion = FW_VERSION;
    updateStatus = "aktuell (" FW_VERSION ")";
    return;
  }
  latestVersion = bestVersion;
  if (!install) {
    updateStatus = "Version " + bestVersion + " verfügbar";
    return;
  }

  Serial.printf("Update auf %s: %s\n", bestVersion.c_str(), bestUrl.c_str());
  // Kamera (DMA) und Livestream stören das Schreiben in den Flash: für das Update abschalten.
  stopStreamServer();
  cameraOk = false;
  esp_camera_deinit();
  WiFiClientSecure updateClient;
  updateClient.setCACert(GITHUB_CA);
  httpUpdate.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);  // GitHub leitet auf release-assets… um
  httpUpdate.rebootOnUpdate(true);
  t_httpUpdate_return result = httpUpdate.update(updateClient, bestUrl);
  // Hier geht es nur weiter, wenn das Update fehlgeschlagen ist
  updateStatus = "Update auf " + bestVersion + " fehlgeschlagen: " + httpUpdate.getLastErrorString();
  Serial.println(updateStatus);
  (void)result;
  cameraOk = initCamera();
  stopStreams = false;
  startStreamServer();
}

// ------------------------------------------------------------------ Seiten
static const char PAGE_HEAD[] PROGMEM =
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
    "<title>BolliSoft Kamera</title><style>body{font-family:system-ui;margin:16px;max-width:760px}"
    "code{background:#eee;padding:1px 5px}input,select{font:inherit;padding:4px}label{display:block;margin:8px 0}"
    "button{font:inherit;padding:6px 14px}</style>";

static void handleRoot() {
  String host = hostName + ".local";
  String html = FPSTR(PAGE_HEAD);
  html += F("<h1>BolliSoft Kamera</h1><p>Name: <b>");
  html += htmlEscape(hostName);
  html += F("</b><br>Firmware: ");
  html += FW_VERSION;
  html += F(" &ndash; Update: ");
  html += htmlEscape(updateStatus);
  html += F("<br>Kamera: ");
  html += cameraOk ? "ok" : "FEHLER";
  html += F("<br>WLAN-Signal: ");
  html += WiFi.RSSI();
  html += F(" dBm (besser als -75 ist gut)<br>IP: ");
  html += WiFi.localIP().toString();
  html += F("<br>Laufzeit: ");
  html += millis() / 60000;
  html += F(" min</p><p>Foto-Adresse für WerWarDas / Vorrat:<br><code>http://");
  html += host;
  html += F("/capture</code> &ndash; mit LED: <code>?led=60</code> (0&ndash;255) oder <code>?flash=1</code>"
            "<br>Livestream: <code>http://");
  html += host;
  html += F(":81/stream</code></p><p><a href=/capture>Foto</a> &middot; <a href='/capture?led=60'>Foto mit LED</a>"
            " &middot; <a href='http://");
  html += WiFi.localIP().toString();
  html += F(":81/stream'>Livestream</a> &middot; <a href=/settings>Einstellungen</a></p>"
            "<img src='/capture' style='max-width:100%'>");
  server.send(200, "text/html", html);
}

static void handleSettings() {
  String html = FPSTR(PAGE_HEAD);
  html += F("<h1>Einstellungen</h1><form method=post action=/settings>"
            "<label>Name im Netz (http://&lt;Name&gt;.local)<br><input name=host maxlength=32 value='");
  html += htmlEscape(hostName);
  html += F("'></label><label>Nach Updates suchen<br><select name=hours>");
  const int choices[] = {0, 1, 6, 12, 24};
  for (int h : choices) {
    html += "<option value=" + String(h) + (h == updateHours ? " selected>" : ">");
    html += h == 0 ? String("nie (nur von Hand)") : (h == 1 ? String("jede Stunde") : "alle " + String(h) + " Stunden");
    html += "</option>";
  }
  html += F("</select></label><label>Neues Passwort (leer = unverändert)<br><input name=newpw type=password "
            "maxlength=32></label><label>Aktuelles Passwort (zum Speichern nötig)<br><input name=pw type=password "
            "required></label><button>Speichern und neu starten</button></form><hr><p>Firmware ");
  html += FW_VERSION;
  html += F(" &ndash; ");
  html += htmlEscape(updateStatus);
  html += F("</p><form method=post action=/update><input name=pw type=password placeholder=Passwort required> "
            "<button>Jetzt nach Update suchen und einspielen</button></form><hr>"
            "<form method=post action=/wifi onsubmit=\"return confirm('WLAN-Zugang löschen? Danach öffnet die "
            "Kamera wieder den Hotspot zum Einrichten.')\"><input name=pw type=password placeholder=Passwort required> "
            "<button>WLAN neu einrichten</button></form><p><a href=/>Zurück</a></p>"
            "<p style='color:#666'>Das Passwort ist dasselbe wie für Updates per WLAN (OTA). Ab Werk: werwardas.</p>");
  server.send(200, "text/html", html);
}

static bool passwordOk() {
  if (server.arg("pw") == otaPassword) return true;
  server.send(403, "text/html", String(FPSTR(PAGE_HEAD)) + "<p>Falsches Passwort.</p><p><a href=/settings>Zurück</a></p>");
  return false;
}

static void restartPage(const String &text) {
  server.send(200, "text/html", String(FPSTR(PAGE_HEAD)) + "<p>" + text + "</p><p><a href=/>Zur Statusseite</a></p>");
  delay(800);
  ESP.restart();
}

static void handleSettingsSave() {
  if (!passwordOk()) return;
  prefs.putString("host", cleanHost(server.arg("host")));
  prefs.putInt("hours", constrain(server.arg("hours").toInt(), 0, 24));
  String newPw = server.arg("newpw");
  if (newPw.length() >= 4) prefs.putString("otapw", newPw);
  restartPage("Gespeichert. Die Kamera startet neu und ist gleich unter http://" + cleanHost(server.arg("host")) +
              ".local erreichbar.");
}

static void handleUpdateNow() {
  if (!passwordOk()) return;
  server.send(200, "text/html", String(FPSTR(PAGE_HEAD)) +
                                    "<p>Suche nach Updates … Gibt es eins, wird es eingespielt und die Kamera startet "
                                    "neu (etwa eine Minute).</p><p><a href=/>Zur Statusseite</a></p>");
  delay(300);
  checkForUpdate(true);
}

static void handleWifiReset() {
  if (!passwordOk()) return;
  server.send(200, "text/html", String(FPSTR(PAGE_HEAD)) +
                                    "<p>WLAN gelöscht. Mit dem Hotspot „BolliSoft-Kamera-…“ verbinden und neu einrichten.</p>");
  delay(800);
  WiFiManager wm;
  wm.resetSettings();
  ESP.restart();
}

// ------------------------------------------------------------------ WLAN
static void loadSettings() {
  prefs.begin("cam", false);
#if defined(WIFI_SSID)
  // Selbst gebaute Firmware mit secrets.h: Werte beim ersten Start übernehmen
  if (!prefs.getBool("seeded", false)) {
    prefs.putString("host", cleanHost(CAM_HOSTNAME));
    prefs.putString("otapw", OTA_PASSWORD);
    WiFi.mode(WIFI_STA);
    WiFi.persistent(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);  // speichert das WLAN dauerhaft
    prefs.putBool("seeded", true);
  }
#endif
  hostName = prefs.getString("host", cleanHost(CAM_HOSTNAME));
  otaPassword = prefs.getString("otapw", OTA_PASSWORD);
  updateHours = prefs.getInt("hours", 12);
}

static void connectWifi() {
  WiFiManager wm;
  char apName[32];
  snprintf(apName, sizeof(apName), "BolliSoft-Kamera-%04X", (unsigned)(ESP.getEfuseMac() & 0xFFFF));
  WiFiManagerParameter hostParam("host", "Name im Netz (z. B. vorrat-kuehlschrank)", hostName.c_str(), 32);
  WiFiManagerParameter pwParam("otapw", "Passwort für Einstellungen und Updates (leer = werwardas)", "", 32);
  wm.addParameter(&hostParam);
  wm.addParameter(&pwParam);
  wm.setSaveParamsCallback([&]() {
    prefs.putString("host", cleanHost(hostParam.getValue()));
    if (strlen(pwParam.getValue()) >= 4) prefs.putString("otapw", pwParam.getValue());
  });
  wm.setHostname(hostName.c_str());
  wm.setConnectTimeout(30);
  wm.setConfigPortalTimeout(300);  // ohne Einrichtung nach 5 min neu versuchen (z. B. Router war aus)
  wm.setTitle("BolliSoft Kamera");
  if (!wm.autoConnect(apName)) {
    Serial.println("Kein WLAN – Neustart");
    delay(1000);
    ESP.restart();
  }
  hostName = prefs.getString("host", hostName);
  otaPassword = prefs.getString("otapw", otaPassword);
  WiFi.setHostname(hostName.c_str());
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
}

void setup() {
  Serial.begin(115200);
  ledcSetup(LED_CHANNEL, LED_FREQ, LED_BITS);
  ledcAttachPin(FLASH_LED_PIN, LED_CHANNEL);
  setLed(0);

  cameraOk = initCamera();
  Serial.printf("BolliSoft Kamera %s – Kamera: %s, PSRAM: %s\n", FW_VERSION, cameraOk ? "ok" : "FEHLER",
                psramFound() ? "ja" : "nein");

  loadSettings();
  connectWifi();
  Serial.printf("WLAN ok, IP: %s, Name: %s.local\n", WiFi.localIP().toString().c_str(), hostName.c_str());

  MDNS.begin(hostName.c_str());
  MDNS.addService("http", "tcp", 80);

  ArduinoOTA.setHostname(hostName.c_str());
  ArduinoOTA.setPassword(otaPassword.c_str());
  ArduinoOTA.onStart([]() {
    stopStreamServer();
    cameraOk = false;
    esp_camera_deinit();
  });
  ArduinoOTA.onError([](ota_error_t) { ESP.restart(); });
  ArduinoOTA.begin();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/capture", HTTP_GET, handleCapture);
  server.on("/settings", HTTP_GET, handleSettings);
  server.on("/settings", HTTP_POST, handleSettingsSave);
  server.on("/update", HTTP_POST, handleUpdateNow);
  server.on("/wifi", HTTP_POST, handleWifiReset);
  server.begin();
  startStreamServer();
  Serial.printf("Bereit: http://%s/capture, Livestream http://%s:81/stream\n", WiFi.localIP().toString().c_str(),
                WiFi.localIP().toString().c_str());
}

void loop() {
  static unsigned long lostSince = 0;
  server.handleClient();
  ArduinoOTA.handle();

  // Kamera ausgefallen: einmal pro Minute neu initialisieren, nach 5 Fehlversuchen neu starten.
  static unsigned long lastCamRetry = 0;
  static int camFailures = 0;
  if (!cameraOk && millis() - lastCamRetry > 60000) {
    lastCamRetry = millis();
    cameraOk = initCamera();
    camFailures = cameraOk ? 0 : camFailures + 1;
    if (camFailures >= 5) ESP.restart();
  }

  // Regelmäßig nach Updates suchen (0 = nie); nicht während jemand den Livestream schaut
  unsigned long wait = lastCheck == 0 ? FIRST_CHECK_MS : updateHours * 3600UL * 1000UL;
  if (updateHours > 0 && millis() - lastCheck >= wait && streamClients == 0) {
    lastCheck = millis() | 1;  // nie 0, damit die erste Suche nur einmal kurz nach dem Start kommt
    checkForUpdate(true);
  }

  // Bleibt das WLAN länger als 2 Minuten weg, neu starten.
  if (WiFi.status() != WL_CONNECTED) {
    if (lostSince == 0) lostSince = millis();
    if (millis() - lostSince > 120000) ESP.restart();
  } else {
    lostSince = 0;
  }
  delay(2);
}
