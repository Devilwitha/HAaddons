// Kopieren nach secrets.h und ausfüllen. secrets.h wird nicht ins Git eingecheckt.
#pragma once

#define WIFI_SSID "MeinWLAN"
#define WIFI_PASSWORD "geheim"
// Passwort für Firmware-Updates per WLAN (OTA) – muss zu upload_flags in platformio.ini passen
#define OTA_PASSWORD "werwardas"
// Name im Netz (http://<Name>.local). Bei mehreren Kameras je Gerät einen eigenen, z. B.
// "vorrat-kuehlschrank-oben". Ohne diese Zeile heißt die Kamera wie bisher "werwardas-cam".
#define CAM_HOSTNAME "werwardas-cam"
