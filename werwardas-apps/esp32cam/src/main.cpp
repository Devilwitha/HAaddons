// WerWarDas – ESP32-CAM (AI-Thinker)
//
//   GET /            kurze Statusseite
//   GET /capture     aktuelles Foto als JPEG (?flash=1 schaltet die LED dazu ein)
//
// Die WerWarDas-Software holt sich das Foto über camera.url = http://<IP>/capture.

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WiFi.h>

#include "esp_camera.h"
#include "secrets.h"

static const char *HOSTNAME = "werwardas-cam";

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

WebServer server(80);
static bool cameraOk = false;

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
  s->set_exposure_ctrl(s, 1);
  s->set_gain_ctrl(s, 1);
  s->set_lenc(s, 1);
  return true;
}

static void handleCapture() {
  if (!cameraOk) {
    server.send(503, "text/plain", "Kamera nicht bereit");
    return;
  }
  bool flash = server.arg("flash") == "1";
  if (flash) {
    digitalWrite(FLASH_LED_PIN, HIGH);
    delay(150);
  }
  // Ein altes Bild aus dem Puffer verwerfen, damit das Foto wirklich aktuell ist.
  camera_fb_t *fb = esp_camera_fb_get();
  if (fb) esp_camera_fb_return(fb);
  fb = esp_camera_fb_get();
  if (flash) digitalWrite(FLASH_LED_PIN, LOW);
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

static void handleRoot() {
  String html = F("<!doctype html><meta charset=utf-8><title>WerWarDas Kamera</title>"
                  "<h1>WerWarDas Kamera</h1><p>Kamera: ");
  html += cameraOk ? "ok" : "FEHLER";
  html += F("<br>WLAN-Signal: ");
  html += WiFi.RSSI();
  html += F(" dBm<br>Laufzeit: ");
  html += millis() / 60000;
  html += F(" min</p><p><a href=/capture>Foto</a></p><img src=/capture style='max-width:100%'>");
  server.send(200, "text/html", html);
}

static void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Verbinde mit WLAN \"%s\"", WIFI_SSID);
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  cameraOk = initCamera();
  Serial.printf("Kamera: %s, PSRAM: %s\n", cameraOk ? "ok" : "FEHLER", psramFound() ? "ja" : "nein");

  connectWifi();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WLAN nicht erreichbar – Neustart");
    delay(1000);
    ESP.restart();
  }
  Serial.printf("WLAN ok, IP: %s\n", WiFi.localIP().toString().c_str());

  MDNS.begin(HOSTNAME);
  MDNS.addService("http", "tcp", 80);

  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  // Die laufende Kamera (DMA) stört das Schreiben in den Flash: für das Update abschalten.
  ArduinoOTA.onStart([]() {
    cameraOk = false;
    esp_camera_deinit();
  });
  ArduinoOTA.onError([](ota_error_t) { ESP.restart(); });
  ArduinoOTA.begin();

  server.on("/", handleRoot);
  server.on("/capture", handleCapture);
  server.begin();
  Serial.printf("Bereit: http://%s/capture\n", WiFi.localIP().toString().c_str());
}

void loop() {
  static unsigned long lostSince = 0;
  server.handleClient();
  ArduinoOTA.handle();

  // Bleibt das WLAN länger als 2 Minuten weg, neu starten.
  // Kamera ausgefallen: einmal pro Minute neu initialisieren, nach 5 Fehlversuchen neu starten.
  static unsigned long lastCamRetry = 0;
  static int camFailures = 0;
  if (!cameraOk && millis() - lastCamRetry > 60000) {
    lastCamRetry = millis();
    cameraOk = initCamera();
    camFailures = cameraOk ? 0 : camFailures + 1;
    if (camFailures >= 5) ESP.restart();
  }

  if (WiFi.status() != WL_CONNECTED) {
    if (lostSince == 0) lostSince = millis();
    if (millis() - lostSince > 120000) ESP.restart();
  } else {
    lostSince = 0;
  }
  delay(2);
}
