package de.werwardas.common

import android.content.SharedPreferences
import okhttp3.FormBody
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

class HaException(message: String) : IOException(message)

/** Die Anmeldung ist abgelaufen oder wurde in Home Assistant gelöscht – neu anmelden nötig. */
class HaAuthExpired(message: String) : IOException(message)

/**
 * Anmeldung bei Home Assistant mit Benutzername und Passwort (wie die offizielle App).
 * Gespeichert wird nur das Refresh-Token; das Zugriffstoken (30 Minuten gültig) wird bei Bedarf erneuert.
 */
class HaAuth(private val prefs: SharedPreferences) {
    companion object {
        // HA verlangt eine URL als Client-ID; die Rücksprungadresse muss auf demselben Host liegen.
        const val CLIENT_ID = "https://github.com/Devilwitha/werwardas"
        private const val REDIRECT_URI = "$CLIENT_ID/auth"
        private val JSON = "application/json".toMediaType()
    }

    private var accessToken: String? = null
    private var expiresAt = 0L

    val isLoggedIn: Boolean get() = !prefs.getString("refresh_token", null).isNullOrEmpty()

    /** Blockierend – nicht auf dem UI-Thread aufrufen. */
    fun login(http: OkHttpClient, base: String, user: String, password: String) {
        val flow = postJson(http, "$base/auth/login_flow", JSONObject()
            .put("client_id", CLIENT_ID)
            .put("handler", JSONArray().put("homeassistant").put(JSONObject.NULL))
            .put("redirect_uri", REDIRECT_URI))
        val step = postJson(http, "$base/auth/login_flow/${flow.getString("flow_id")}", JSONObject()
            .put("client_id", CLIENT_ID)
            .put("username", user)
            .put("password", password))
        if (step.optString("type") != "create_entry") {
            val errors = step.optJSONObject("errors")
            throw HaException(
                if (errors?.optString("base") == "invalid_auth") "Benutzername oder Passwort falsch"
                else "Anmeldung nicht möglich (${step.optString("type")} ${errors ?: ""})"
            )
        }
        val tokens = postForm(http, "$base/auth/token", mapOf(
            "grant_type" to "authorization_code",
            "code" to step.getString("result"),
            "client_id" to CLIENT_ID,
        ))
        prefs.edit().putString("refresh_token", tokens.getString("refresh_token")).apply()
        remember(tokens)
    }

    /** Gültiges Zugriffstoken, notfalls erneuert. Blockierend. */
    @Synchronized
    fun accessToken(http: OkHttpClient, base: String): String {
        accessToken?.let { if (System.currentTimeMillis() < expiresAt) return it }
        val refresh = prefs.getString("refresh_token", null) ?: throw HaAuthExpired("Nicht angemeldet")
        val body = FormBody.Builder()
            .add("grant_type", "refresh_token")
            .add("refresh_token", refresh)
            .add("client_id", CLIENT_ID)
            .build()
        http.newCall(Request.Builder().url("$base/auth/token").post(body).build()).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (resp.code == 400 || resp.code == 403) throw HaAuthExpired("Anmeldung abgelaufen – bitte neu anmelden")
            if (!resp.isSuccessful) throw HaException("Token-Erneuerung fehlgeschlagen (HTTP ${resp.code})")
            remember(JSONObject(text))
        }
        return accessToken!!
    }

    @Synchronized
    fun invalidateAccessToken() {
        accessToken = null
    }

    fun logout() {
        prefs.edit().remove("refresh_token").apply()
        invalidateAccessToken()
    }

    private fun remember(tokens: JSONObject) {
        accessToken = tokens.getString("access_token")
        expiresAt = System.currentTimeMillis() + (tokens.optLong("expires_in", 1800) - 60) * 1000
    }

    private fun postJson(http: OkHttpClient, url: String, body: JSONObject): JSONObject {
        val req = Request.Builder().url(url).post(body.toString().toRequestBody(JSON)).build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (!resp.isSuccessful) throw HaException("HTTP ${resp.code} bei ${url.substringAfter("/auth/")}: ${text.take(120)}")
            return JSONObject(text)
        }
    }

    private fun postForm(http: OkHttpClient, url: String, fields: Map<String, String>): JSONObject {
        val form = FormBody.Builder().apply { fields.forEach { (k, v) -> add(k, v) } }.build()
        http.newCall(Request.Builder().url(url).post(form).build()).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (!resp.isSuccessful) throw HaException("Token-Abruf fehlgeschlagen (HTTP ${resp.code})")
            return JSONObject(text)
        }
    }
}
