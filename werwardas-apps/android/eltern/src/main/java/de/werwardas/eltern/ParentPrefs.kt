package de.werwardas.eltern

import android.content.Context
import android.content.SharedPreferences

/** Einstellungen der Eltern-App. */
class ParentPrefs(context: Context) {
    val raw: SharedPreferences = context.getSharedPreferences("werwardas", Context.MODE_PRIVATE)

    private fun str(key: String, def: String = "") = raw.getString(key, def) ?: def
    private fun put(key: String, value: String) = raw.edit().putString(key, value).apply()
    private fun int(key: String, def: Int) = raw.getInt(key, def)
    private fun putInt(key: String, value: Int) = raw.edit().putInt(key, value).apply()

    var localUrl: String get() = str("local_url", "http://homeassistant.local:8123"); set(v) = put("local_url", v)
    var remoteUrl: String get() = str("remote_url"); set(v) = put("remote_url", v)
    var user: String get() = str("user"); set(v) = put("user", v)
    var updateHours: Int get() = int("update_hours", 1); set(v) = putInt("update_hours", v)

    var camSensor: String get() = str("cam_sensor", "sensor.werwardas_kamera_ordnung"); set(v) = put("cam_sensor", v)
    var cameraEntity: String get() = str("camera_entity", "camera.werwardas_kamera_zimmer"); set(v) = put("camera_entity", v)

    val isLoggedIn: Boolean get() = raw.getString("refresh_token", null) != null
}
