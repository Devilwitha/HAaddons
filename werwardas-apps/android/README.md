# WerWarDas – Android-Apps

Zwei Apps, die wie die Windows-App über Home Assistant funktionieren – auch unterwegs über **Nabu Casa**:

- **WerWarDas (Kind)** – läuft auf dem Handy des Kindes. Verfolgt den Kamera-Status und zeigt bei Unordnung
  erst einen Hinweis, dann eine Vollbild-Sperre, die sich per Notfall-PIN aufheben lässt. Grundsatz wie bei der
  Windows-App: Geht etwas nicht (keine Verbindung, Kamera weg, Fehler), ist das Handy frei.
- **WerWarDas Eltern** – läuft auf dem Eltern-Handy. Zeigt Zimmerfoto, Ordnung in %, den Status aller Geräte
  (PC, Kinderhandy) und steuert sie (Hinweis, Sperren, Freigeben, Herunterfahren). Enthält die Kamera-Einstellungen
  (Intervall, Grenzwert, Prüfzeit, Überwachung) und meldet, wenn ein Gerät gesperrt oder per PIN entsperrt wird.

Beide verbinden sich **nicht per MQTT**, sondern direkt mit Home Assistant (WebSocket). Zu Hause über die lokale
Adresse, unterwegs automatisch über die Nabu-Casa-Adresse. Es genügt ein Home-Assistant-Benutzer **ohne Admin-Rechte**.

## Grenzen der Sperre (Kinder-App)

Die Sperre ist ein Vollbild-Fenster über den Apps (Berechtigung „Über anderen Apps einblenden"). Sie **blockiert
bewusst nicht** die Einstellungen, den abgesicherten Modus oder das Deinstallieren – das Kind behält die Kontrolle
über sein Gerät, Notrufe gehen jederzeit. Ein entschlossenes Kind kann die Sperre also umgehen. Für eine härtere
Sperre bräuchte es den Geräteinhaber-Modus (Device Owner), der das Gerät einmal zurücksetzen würde.

## Einrichten

1. In Home Assistant einen Benutzer ohne Admin-Rechte anlegen (z. B. `werwardas-handy`).
2. APK aufs Handy laden (aus den [Releases](https://github.com/Devilwitha/werwardas/releases), Tag `android-v…`).
3. App öffnen → **Einrichten**: Adresse von Home Assistant, Benutzer, Passwort, Gerätename. Die Nabu-Casa-Adresse
   wird dabei automatisch übernommen.
4. Kinder-App: die beiden Berechtigungen erlauben („Über anderen Apps einblenden", „Unbekannte Apps installieren")
   und den Akkusparen-Modus für die App ausschalten, damit sie im Hintergrund laufen darf.
5. Optional eine Notfall-PIN festlegen.

## Automatische Updates

Jeder Push auf `main`, der etwas unter `android/` ändert, baut per GitHub Actions beide APKs und veröffentlicht sie
unter dem Tag `android-v1.0.<Laufnummer>`. Die Apps suchen regelmäßig danach (einstellbar, Standard 1 Stunde),
prüfen Größe, SHA-256 und Versionsnummer und installieren das Update, sobald das Handy frei ist. Android zeigt dabei
einmalig eine Bestätigung; ab Android 12 laufen weitere Updates ohne Nachfrage.

**Damit Updates installiert werden können, müssen alle APKs mit demselben Schlüssel signiert sein.** Dafür zwei
GitHub-Secrets im Repo anlegen (Settings → Secrets and variables → Actions):

- `WERWARDAS_KEYSTORE_BASE64` – der Signaturschlüssel als Base64 (Inhalt der Datei `werwardas.jks.b64`).
- `WERWARDAS_KEYSTORE_PASSWORD` – das Keystore-Passwort.

Ohne diese Secrets baut CI mit dem Debug-Schlüssel; die Apps laufen dann, können sich aber nicht selbst aktualisieren.

## Lokal bauen

```bash
cd android
./gradlew :kind:assembleRelease :eltern:assembleRelease
# Ergebnis: kind/build/outputs/apk/release/kind-release.apk und eltern/.../eltern-release.apk
```

Zum lokalen Signieren mit dem echten Schlüssel eine `android/keystore.properties` anlegen (nicht im Repo):

```properties
storeFile=/pfad/zu/werwardas.jks
password=DAS_KEYSTORE_PASSWORT
```

## Aufbau

| Modul | Inhalt |
|-------|--------|
| `common` | Anmeldung und WebSocket-Verbindung zu Home Assistant, Kamera-Status, Selbst-Updater, UI-Bausteine |
| `kind`   | Wächter-Dienst, Sperr- und Hinweis-Overlay, Einrichtung, Einstellungen |
| `eltern` | Dashboard, Steuerung der Geräte, Benachrichtigungen |
