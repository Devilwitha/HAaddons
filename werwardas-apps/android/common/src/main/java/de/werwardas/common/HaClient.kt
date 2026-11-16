package de.werwardas.common

import android.content.Context
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.Network
import android.os.Handler
import android.os.HandlerThread
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Zustand einer Home-Assistant-Entität (aus subscribe_entities). */
data class Entity(val id: String, val state: String, val attrs: JSONObject, val lastChanged: Double, val lastUpdated: Double) {
    val name: String get() = attrs.optString("friendly_name", id)
    val available: Boolean get() = state != "unavailable" && state != "unknown"
}

enum class ConnState(val text: String) {
    DISCONNECTED("Keine Verbindung"),
    CONNECTING("Verbinde …"),
    LOCAL("Verbunden (zu Hause)"),
    REMOTE("Verbunden (über Nabu Casa)"),
    NOT_CONFIGURED("Nicht eingerichtet"),
    AUTH_FAILED("Anmeldung abgelaufen – bitte neu anmelden"),
}

/**
 * WebSocket-Verbindung zu Home Assistant. Zu Hause wird die lokale Adresse benutzt, sonst die Nabu-Casa-Adresse.
 * Alle Rückrufe an den [Listener] kommen auf einem eigenen Hintergrund-Thread.
 *
 * Einstellungen (SharedPreferences): local_url, remote_url, refresh_token (über [HaAuth]).
 */
class HaClient(context: Context, private val prefs: SharedPreferences, private val listener: Listener) {

    interface Listener {
        fun onConnection(state: ConnState) {}
        fun onEntities(changed: Set<String>) {}
        fun onPush(data: JSONObject) {}
        fun onEvent(event: JSONObject) {}
        /** Nach jeder erfolgreichen Anmeldung (z. B. um Dinge einmalig nachzuladen). */
        fun onReady() {}
    }

    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()
    private val probeHttp = http.newBuilder().connectTimeout(3, TimeUnit.SECONDS).readTimeout(3, TimeUnit.SECONDS).build()

    val auth = HaAuth(prefs)
    val states = ConcurrentHashMap<String, Entity>()

    /** null = alle Entitäten abonnieren. */
    var entityFilter: List<String>? = null
    /** Webhook-ID der Mobile-App-Registrierung: dann kommen Befehle per Push über diese Verbindung. */
    var pushWebhookId: String? = null
    var eventTypes: List<String> = emptyList()

    @Volatile var state: ConnState = ConnState.DISCONNECTED
        private set
    @Volatile var baseUrl: String? = null
        private set
    @Volatile var lastError: String = ""
        private set

    private val thread = HandlerThread("ha").apply { start() }
    private val handler = Handler(thread.looper)
    private val ids = AtomicInteger(1)
    private val pending = ConcurrentHashMap<Int, (JSONObject) -> Unit>()
    private var socket: WebSocket? = null
    private var generation = 0
    private var entitiesSubId = -1
    private var pushSubId = -1
    private val eventSubIds = mutableSetOf<Int>()
    private var retryDelay = 5_000L
    private var running = false
    private var authRetried = false

    private val connectivity = context.getSystemService(ConnectivityManager::class.java)
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            // Netzwechsel (z. B. WLAN zu Hause): neu verbinden, damit die richtige Adresse gewählt wird.
            handler.postDelayed({ if (running) reconnectNow() }, 1500)
        }
    }

    fun start() = handler.post {
        if (running) return@post
        running = true
        try {
            connectivity?.registerDefaultNetworkCallback(networkCallback)
        } catch (_: Exception) {
        }
        connect()
    }

    fun stop() = handler.post {
        running = false
        try {
            connectivity?.unregisterNetworkCallback(networkCallback)
        } catch (_: Exception) {
        }
        closeSocket()
        setState(ConnState.DISCONNECTED)
    }

    fun reconnectNow() = handler.post {
        if (!running) return@post
        handler.removeCallbacksAndMessages(RETRY)
        retryDelay = 5_000L
        closeSocket()
        connect()
    }

    val isConnected: Boolean get() = state == ConnState.LOCAL || state == ConnState.REMOTE

    // ------------------------------------------------------------------ Verbindungsaufbau

    private fun connect() {
        closeSocket()
        val local = prefs.getString("local_url", "")!!.trimEnd('/')
        val remote = prefs.getString("remote_url", "")!!.trimEnd('/')
        if (local.isEmpty() && remote.isEmpty() || !auth.isLoggedIn) {
            setState(ConnState.NOT_CONFIGURED)
            return
        }
        setState(ConnState.CONNECTING)
        val base = when {
            local.isNotEmpty() && probe(local) -> local
            remote.isNotEmpty() && probe(remote) -> remote
            else -> null
        }
        if (base == null) {
            lastError = "Home Assistant nicht erreichbar"
            scheduleRetry()
            return
        }
        val token = try {
            auth.accessToken(http, base)
        } catch (e: HaAuthExpired) {
            lastError = e.message ?: ""
            FileLog.write("Anmeldung abgelaufen: ${e.message}")
            setState(ConnState.AUTH_FAILED)
            return
        } catch (e: IOException) {
            lastError = e.message ?: "Fehler"
            scheduleRetry()
            return
        }
        baseUrl = base
        val gen = ++generation
        val wsUrl = base.replaceFirst("http", "ws") + "/api/websocket"
        socket = http.newWebSocket(Request.Builder().url(wsUrl).build(), object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                handler.post { if (gen == generation) handleMessage(text, token, base, local) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                handler.post {
                    if (gen != generation) return@post
                    lastError = t.message ?: t.javaClass.simpleName
                    FileLog.write("Verbindung zu Home Assistant verloren: $lastError")
                    socket = null
                    scheduleRetry()
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                handler.post {
                    if (gen != generation) return@post
                    socket = null
                    scheduleRetry()
                }
            }
        })
    }

    private fun probe(base: String): Boolean = try {
        probeHttp.newCall(Request.Builder().url("$base/manifest.json").build()).execute().use { it.isSuccessful }
    } catch (_: Exception) {
        false
    }

    private fun handleMessage(text: String, token: String, base: String, local: String) {
        val msg = try {
            JSONObject(text)
        } catch (_: Exception) {
            return
        }
        when (msg.optString("type")) {
            "auth_required" -> socket?.send(JSONObject().put("type", "auth").put("access_token", token).toString())
            "auth_ok" -> {
                authRetried = false
                retryDelay = 5_000L
                lastError = ""
                setState(if (base == local) ConnState.LOCAL else ConnState.REMOTE)
                subscribe()
                listener.onReady()
            }
            "auth_invalid" -> {
                auth.invalidateAccessToken()
                if (!authRetried) {
                    authRetried = true
                    handler.post { connect() }
                } else {
                    setState(ConnState.AUTH_FAILED)
                    closeSocket()
                }
            }
            "result" -> pending.remove(msg.optInt("id"))?.invoke(msg)
            "event" -> {
                val id = msg.optInt("id")
                val event = msg.optJSONObject("event") ?: return
                when (id) {
                    entitiesSubId -> applyEntities(event)
                    pushSubId -> {
                        event.optString("hass_confirm_id").takeIf { it.isNotEmpty() }?.let { confirm ->
                            send(JSONObject().put("type", "mobile_app/push_notification_confirm")
                                .put("webhook_id", pushWebhookId).put("confirm_id", confirm))
                        }
                        listener.onPush(event)
                    }
                    in eventSubIds -> listener.onEvent(event)
                }
            }
        }
    }

    private fun subscribe() {
        states.clear()
        val sub = JSONObject().put("type", "subscribe_entities")
        entityFilter?.let { sub.put("entity_ids", JSONArray(it)) }
        entitiesSubId = send(sub)
        pushWebhookId?.let {
            pushSubId = send(JSONObject().put("type", "mobile_app/push_notification_channel")
                .put("webhook_id", it).put("support_confirm", true)) { r ->
                if (!r.optBoolean("success")) FileLog.write("Push-Kanal nicht möglich: ${r.optJSONObject("error")}")
            }
        }
        eventSubIds.clear()
        for (type in eventTypes) {
            eventSubIds += send(JSONObject().put("type", "subscribe_events").put("event_type", type))
        }
    }

    /** Wendet das kompakte Format von subscribe_entities an ("a" neu, "c" geändert, "r" entfernt). */
    private fun applyEntities(ev: JSONObject) {
        val changed = mutableSetOf<String>()
        ev.optJSONObject("a")?.let { added ->
            for (id in added.keys()) {
                val e = added.getJSONObject(id)
                val lc = e.optDouble("lc", 0.0)
                states[id] = Entity(id, e.optString("s"), e.optJSONObject("a") ?: JSONObject(), lc, e.optDouble("lu", lc))
                changed += id
            }
        }
        ev.optJSONObject("c")?.let { diffs ->
            for (id in diffs.keys()) {
                val old = states[id] ?: continue
                val d = diffs.getJSONObject(id)
                var s = old.state
                val attrs = JSONObject(old.attrs.toString())
                var lc = old.lastChanged
                var lu = old.lastUpdated
                d.optJSONObject("+")?.let { p ->
                    if (p.has("s")) s = p.optString("s")
                    p.optJSONObject("a")?.let { na -> for (k in na.keys()) attrs.put(k, na.get(k)) }
                    if (p.has("lc")) {
                        lc = p.optDouble("lc"); lu = lc
                    }
                    if (p.has("lu")) lu = p.optDouble("lu")
                }
                d.optJSONObject("-")?.optJSONArray("a")?.let { gone ->
                    for (i in 0 until gone.length()) attrs.remove(gone.getString(i))
                }
                states[id] = Entity(id, s, attrs, lc, lu)
                changed += id
            }
        }
        ev.optJSONArray("r")?.let { removed ->
            for (i in 0 until removed.length()) {
                states.remove(removed.getString(i)); changed += removed.getString(i)
            }
        }
        if (changed.isNotEmpty()) listener.onEntities(changed)
    }

    private fun scheduleRetry() {
        setState(ConnState.DISCONNECTED)
        if (!running) return
        handler.removeCallbacksAndMessages(RETRY)
        handler.postAtTime({ connect() }, RETRY, android.os.SystemClock.uptimeMillis() + retryDelay)
        retryDelay = (retryDelay * 2).coerceAtMost(60_000L)
    }

    private fun closeSocket() {
        generation++
        socket?.close(1000, null)
        socket = null
        pending.clear()
    }

    private fun setState(s: ConnState) {
        if (state == s) return
        state = s
        listener.onConnection(s)
    }

    // ------------------------------------------------------------------ Befehle

    /** Sendet eine Nachricht, gibt die ID zurück (-1 = nicht verbunden). Muss nicht auf dem HA-Thread laufen. */
    fun send(msg: JSONObject, onResult: ((JSONObject) -> Unit)? = null): Int {
        val ws = socket ?: return -1
        val id = ids.getAndIncrement()
        msg.put("id", id)
        if (onResult != null) pending[id] = onResult
        if (!ws.send(msg.toString())) {
            pending.remove(id)
            return -1
        }
        return id
    }

    fun callService(domain: String, service: String, data: JSONObject = JSONObject(), entityId: String? = null,
                    onResult: ((Boolean, String) -> Unit)? = null) {
        val msg = JSONObject().put("type", "call_service").put("domain", domain).put("service", service)
            .put("service_data", data)
        entityId?.let { msg.put("target", JSONObject().put("entity_id", it)) }
        val id = send(msg) { r ->
            onResult?.invoke(r.optBoolean("success"), r.optJSONObject("error")?.optString("message") ?: "")
        }
        if (id < 0) onResult?.invoke(false, "Keine Verbindung zu Home Assistant")
    }

    /** Bild einer Kamera-Entität laden (asynchron). */
    fun fetchCameraImage(entityId: String, onResult: (ByteArray?) -> Unit) {
        val base = baseUrl ?: return onResult(null)
        handler.post {
            try {
                val token = auth.accessToken(http, base)
                val req = Request.Builder().url("$base/api/camera_proxy/$entityId")
                    .header("Authorization", "Bearer $token").build()
                http.newCall(req).enqueue(object : Callback {
                    override fun onFailure(call: Call, e: IOException) = onResult(null)
                    override fun onResponse(call: Call, response: Response) {
                        response.use { onResult(if (it.isSuccessful) it.body?.bytes() else null) }
                    }
                })
            } catch (_: Exception) {
                onResult(null)
            }
        }
    }

    /** HTTP-Aufruf mit Anmeldung (z. B. Mobile-App-Registrierung). Blockierend – nicht auf dem UI-Thread. */
    fun postApi(base: String, path: String, body: JSONObject): JSONObject {
        val token = auth.accessToken(http, base)
        val req = Request.Builder().url("$base$path").header("Authorization", "Bearer $token")
            .post(body.toString().toRequestBody(JSON)).build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (!resp.isSuccessful) throw HaException("HTTP ${resp.code}: ${text.take(150)}")
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        }
    }

    fun post(r: Runnable) = handler.post(r)

    companion object {
        private val RETRY = Any()
        private val JSON = "application/json".toMediaType()
    }
}
