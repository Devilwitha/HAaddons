package de.werwardas.kind

import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.provider.Settings
import de.werwardas.common.AppUpdater
import de.werwardas.common.FileLog
import de.werwardas.common.HaAuth
import de.werwardas.common.HaClient
import de.werwardas.common.HaException
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

/**
 * Das Handy als Gerät in Home Assistant (Integration "Mobile App", wie die offizielle HA-App).
 * Status geht per Webhook an HA; Befehle kommen als Push über die WebSocket-Verbindung ([HaClient.pushWebhookId]).
 * Dafür reicht ein HA-Benutzer ohne Admin-Rechte.
 */
class MobileApp(private val context: Context, private val prefs: KidPrefs) {
    companion object {
        const val SENSOR_ID = "werwardas_status"
        private val JSON = "application/json".toMediaType()

        /** Anmelden und Gerät registrieren. Blockierend – nicht auf dem UI-Thread. */
        @SuppressLint("HardwareIds")
        fun loginAndRegister(context: Context, prefs: KidPrefs, base: String, user: String, password: String) {
            val http = OkHttpClient()
            val auth = HaAuth(prefs.raw)
            auth.login(http, base, user, password)
            val token = auth.accessToken(http, base)

            val existing = prefs.webhookId
            if (existing.isNotEmpty() && updateRegistration(context, http, base, existing, prefs)) {
                FileLog.write("Bestehende Registrierung aktualisiert")
            } else {
                val body = JSONObject()
                    .put("device_id", Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID))
                    .put("app_id", context.packageName)
                    .put("app_name", "WerWarDas")
                    .put("app_version", AppUpdater.currentVersionName(context))
                    .put("device_name", prefs.deviceName)
                    .put("manufacturer", Build.MANUFACTURER)
                    .put("model", Build.MODEL)
                    .put("os_name", "Android")
                    .put("os_version", Build.VERSION.RELEASE)
                    .put("supports_encryption", false)
                    .put("app_data", JSONObject().put("push_websocket_channel", true))
                val req = Request.Builder().url("$base/api/mobile_app/registrations")
                    .header("Authorization", "Bearer $token")
                    .post(body.toString().toRequestBody(JSON)).build()
                val reg = http.newCall(req).execute().use { resp ->
                    val text = resp.body?.string() ?: ""
                    if (!resp.isSuccessful) throw HaException("Registrierung fehlgeschlagen (HTTP ${resp.code}): ${text.take(120)}")
                    JSONObject(text)
                }
                prefs.webhookId = reg.getString("webhook_id")
                prefs.cloudhookUrl = reg.optString("cloudhook_url").takeIf { it != "null" } ?: ""
                FileLog.write("Handy in Home Assistant registriert")
                // Nabu-Casa-Adresse übernehmen, damit die App auch unterwegs verbunden bleibt.
                val remote = reg.optString("remote_ui_url").takeIf { it.isNotEmpty() && it != "null" }
                if (remote != null) prefs.remoteUrl = remote.trimEnd('/')
            }
            prefs.localUrl = base
            prefs.user = user

            // Status-Sensor anlegen (bei einer bestehenden Registrierung wird er einfach aktualisiert).
            val sensor = sensorJson("frei", locked = false, extra = JSONObject())
                .put("name", "WerWarDas Status")
            postWebhook(http, "$base/api/webhook/${prefs.webhookId}", JSONObject()
                .put("type", "register_sensor").put("data", sensor))
        }

        private fun updateRegistration(context: Context, http: OkHttpClient, base: String, webhookId: String, prefs: KidPrefs): Boolean = try {
            postWebhook(http, "$base/api/webhook/$webhookId", JSONObject().put("type", "update_registration")
                .put("data", JSONObject().put("device_name", prefs.deviceName).put("app_version", AppUpdater.currentVersionName(context))
                    .put("manufacturer", Build.MANUFACTURER).put("model", Build.MODEL)
                    .put("os_version", Build.VERSION.RELEASE)))
            true
        } catch (_: Exception) {
            false
        }

        private fun postWebhook(http: OkHttpClient, url: String, body: JSONObject) {
            http.newCall(Request.Builder().url(url).post(body.toString().toRequestBody(JSON)).build()).execute().use { resp ->
                // 410 = Registrierung wurde in HA gelöscht
                if (!resp.isSuccessful) throw HaException("Webhook HTTP ${resp.code}")
            }
        }

        fun sensorJson(state: String, locked: Boolean, extra: JSONObject): JSONObject {
            val attrs = JSONObject(extra.toString()).put("werwardas", "phone").put("locked", locked)
            return JSONObject()
                .put("unique_id", SENSOR_ID)
                .put("type", "sensor")
                .put("state", state)
                .put("icon", if (locked) "mdi:cellphone-lock" else "mdi:cellphone-check")
                .put("attributes", attrs)
        }
    }

    private val http = OkHttpClient()

    /** Webhook-Adresse: zu Hause direkt, unterwegs über Nabu Casa. */
    private fun webhookUrl(client: HaClient): String? {
        val id = prefs.webhookId.ifEmpty { return null }
        val base = client.baseUrl
        return when {
            base != null && base == prefs.localUrl.trimEnd('/') -> "$base/api/webhook/$id"
            prefs.cloudhookUrl.isNotEmpty() -> prefs.cloudhookUrl
            base != null -> "$base/api/webhook/$id"
            else -> null
        }
    }

    fun updateStatus(client: HaClient, state: String, locked: Boolean, extra: JSONObject) {
        val extraWithInfo = JSONObject(extra.toString())
            .put("device_name", prefs.deviceName)
            .put("version", AppUpdater.currentVersionName(context))
        send(client, JSONObject().put("type", "update_sensor_states")
            .put("data", JSONArray().put(sensorJson(state, locked, extraWithInfo))))
    }

    fun fireEvent(client: HaClient, event: String) {
        send(client, JSONObject().put("type", "fire_event").put("data", JSONObject()
            .put("event_type", "werwardas_event")
            .put("event_data", JSONObject().put("device", prefs.deviceName).put("event", event))))
    }

    private fun send(client: HaClient, body: JSONObject) {
        val url = webhookUrl(client) ?: return
        http.newCall(Request.Builder().url(url).post(body.toString().toRequestBody(JSON)).build())
            .enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {}
                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        if (it.code == 410) FileLog.write("Das Handy wurde in Home Assistant entfernt – bitte neu anmelden")
                    }
                }
            })
    }
}
