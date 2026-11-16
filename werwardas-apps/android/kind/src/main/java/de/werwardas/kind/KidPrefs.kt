package de.werwardas.kind

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import de.werwardas.common.CamState

/** Alle Einstellungen der Kinder-App. */
class KidPrefs(context: Context) {
    val raw: SharedPreferences = context.getSharedPreferences("werwardas", Context.MODE_PRIVATE)

    private fun str(key: String, def: String = "") = raw.getString(key, def) ?: def
    private fun put(key: String, value: String) = raw.edit().putString(key, value).apply()
    private fun int(key: String, def: Int) = raw.getInt(key, def)
    private fun putInt(key: String, value: Int) = raw.edit().putInt(key, value).apply()
    private fun long(key: String) = raw.getLong(key, 0L)
    private fun putLong(key: String, value: Long) = raw.edit().putLong(key, value).apply()

    var localUrl: String get() = str("local_url", "http://homeassistant.local:8123"); set(v) = put("local_url", v)
    var remoteUrl: String get() = str("remote_url"); set(v) = put("remote_url", v)
    var cloudhookUrl: String get() = str("cloudhook_url"); set(v) = put("cloudhook_url", v)
    var webhookId: String get() = str("webhook_id"); set(v) = put("webhook_id", v)
    var user: String get() = str("user", "werwardas-handy"); set(v) = put("user", v)
    var deviceName: String get() = str("device_name", "Handy ${Build.MODEL}"); set(v) = put("device_name", v)

    var graceMinutes: Int get() = int("grace_minutes", 15); set(v) = putInt("grace_minutes", v)
    var emergencyMinutes: Int get() = int("emergency_minutes", 60); set(v) = putInt("emergency_minutes", v)
    var failOpenMinutes: Int get() = int("fail_open_minutes", 2); set(v) = putInt("fail_open_minutes", v)
    var updateHours: Int get() = int("update_hours", 1); set(v) = putInt("update_hours", v)
    var pinHash: String get() = str("pin_hash"); set(v) = put("pin_hash", v)

    var camSensor: String get() = str("cam_sensor", CamState.DEFAULT_SENSOR); set(v) = put("cam_sensor", v)
    var cameraEntity: String get() = str("camera_entity", "camera.werwardas_kamera_zimmer"); set(v) = put("camera_entity", v)
    var checkButton: String get() = str("check_button", "button.werwardas_kamera_jetzt_prufen"); set(v) = put("check_button", v)

    var emergencyUntil: Long get() = long("emergency_until"); set(v) = putLong("emergency_until", v)
    var protectPausedUntil: Long get() = long("protect_paused_until"); set(v) = putLong("protect_paused_until", v)
    var lastUpdateCheck: Long get() = long("last_update_check"); set(v) = putLong("last_update_check", v)

    val isRegistered: Boolean get() = webhookId.isNotEmpty() && raw.getString("refresh_token", null) != null

    /** Der Schutz (Einstellungen/Deinstallieren blockieren) gilt nur mit PIN und wenn er nicht ausgesetzt ist. */
    val protectionActive: Boolean get() = pinHash.isNotEmpty() && System.currentTimeMillis() > protectPausedUntil
}
