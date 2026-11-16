package de.werwardas.kind

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import de.werwardas.common.AppUpdater
import de.werwardas.common.CamState
import de.werwardas.common.ConnState
import de.werwardas.common.Entity
import de.werwardas.common.FileLog
import de.werwardas.common.HaClient
import de.werwardas.common.Pin
import org.json.JSONObject
import java.util.concurrent.TimeUnit

enum class PhoneState { FREE, WARNED, LOCKED, EMERGENCY }

/**
 * Läuft dauerhaft im Hintergrund, hält die Verbindung zu Home Assistant und entscheidet – wie die Windows-App –,
 * ob das Handy frei ist, einen Hinweis zeigt oder gesperrt wird. Grundsatz: Geht etwas nicht (keine Verbindung,
 * Kamera weg, Fehler), ist das Handy frei.
 */
class GuardService : Service(), HaClient.Listener, GuardActions {
    companion object {
        private const val CHANNEL = "werwardas"
        private const val ONGOING_ID = 1
        const val ACTION_CHECK_UPDATE = "de.werwardas.kind.CHECK_UPDATE"
        const val ACTION_REEVALUATE = "de.werwardas.kind.REEVALUATE"

        @Volatile var instance: GuardService? = null

        fun start(context: Context) {
            val intent = Intent(context, GuardService::class.java)
            context.startForegroundService(intent)
        }
    }

    private lateinit var prefs: KidPrefs
    private lateinit var client: HaClient
    private lateinit var overlay: OverlayHost
    private lateinit var mobile: MobileApp
    private val main = Handler(Looper.getMainLooper())

    private var state = PhoneState.FREE
    private var cam: CamState? = null
    private var photo: Bitmap? = null
    private var checkPending = false
    private var messySince = 0L
    private var disconnectedSince = System.currentTimeMillis()
    private var camAvailable = false
    private var lastPhotoFetch = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    /** Grobe Statusanzeige für die Activity (Text + Ampelfarbe). */
    fun statusForUi(): Pair<String, Int> = when {
        !::client.isInitialized || !client.isConnected ->
            (if (::client.isInitialized) client.state.text else "Startet …") to 0xFF828C96.toInt()
        !camAvailable -> "Kamera offline" to 0xFFF0B428.toInt()
        state == PhoneState.LOCKED -> "Gesperrt" to 0xFFDC3C3C.toInt()
        state == PhoneState.WARNED -> "Hinweis: aufräumen" to 0xFFF0B428.toInt()
        state == PhoneState.EMERGENCY -> "Notfall-Entsperrung" to 0xFFF0B428.toInt()
        else -> (cam?.let { "Alles ordentlich (${it.scoreText})" } ?: "Alles ordentlich") to 0xFF2EB85C.toInt()
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        FileLog.init(this)
        prefs = KidPrefs(this)
        overlay = OverlayHost(this)
        overlay.actions = this
        mobile = MobileApp(this, prefs)

        createChannel()
        startForeground(ONGOING_ID, buildNotification("WerWarDas läuft", "Verbinde mit Home Assistant …"))

        client = HaClient(this, prefs.raw, this).apply {
            entityFilter = null
            pushWebhookId = prefs.webhookId.ifEmpty { null }
        }
        client.start()

        // Alle 15 Sekunden neu bewerten (für Schonfrist, Notfall-Ablauf, Ausfallsicherung).
        main.post(object : Runnable {
            override fun run() {
                evaluate()
                if (state == PhoneState.LOCKED) overlay.reassertLock()
                maybeCheckForUpdate()
                main.postDelayed(this, if (state == PhoneState.LOCKED) 3_000 else 15_000)
            }
        })
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CHECK_UPDATE -> checkForUpdate(manual = true)
            ACTION_REEVALUATE -> {
                client.pushWebhookId = prefs.webhookId.ifEmpty { null }
                client.reconnectNow()
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        client.stop()
        overlay.hideAll()
        main.removeCallbacksAndMessages(null)
    }

    // ------------------------------------------------------------------ HaClient.Listener

    override fun onConnection(state: ConnState) = main.post {
        updateNotification()
        if (state == ConnState.LOCAL || state == ConnState.REMOTE) {
            disconnectedSince = 0L
        } else if (disconnectedSince == 0L) {
            disconnectedSince = System.currentTimeMillis()
        }
        evaluate()
    }.let {}

    override fun onReady() {
        // Beim Verbinden: Sensor-Entität suchen, falls sie anders heißt.
        main.post {
            CamState.findSensor(client.states)?.let { if (it != prefs.camSensor) prefs.camSensor = it }
        }
    }

    override fun onEntities(changed: Set<String>) = main.post {
        val sensor = prefs.camSensor
        if (changed.contains(sensor) || cam == null) {
            val newCam = CamState.from(client.states[sensor])
            val entity = client.states[sensor]
            camAvailable = entity?.available == true
            onCamState(newCam)
        }
    }.let {}

    override fun onPush(data: JSONObject) {
        // Befehle aus Home Assistant (notify-Dienst mit data.werwardas = lock/unlock/warn).
        val cmd = data.optJSONObject("data")?.optString("werwardas")
            ?: data.optString("message").takeIf { it.isNotEmpty() }
        main.post { if (!cmd.isNullOrEmpty()) onCommand(cmd.lowercase()) }
    }

    // ------------------------------------------------------------------ Logik (wie Windows-Controller)

    private fun onCamState(newCam: CamState?) {
        cam = newCam
        checkPending = false
        if (newCam != null && newCam.isMessy) {
            val since = if (newCam.timestamp in 1 until System.currentTimeMillis()) newCam.timestamp else System.currentTimeMillis()
            if (messySince == 0L || since < messySince) messySince = since
            fetchPhoto()
        } else {
            messySince = 0L
        }
        evaluate(newState = true)
        pushStatus()
        refreshOverlay()
    }

    private fun onCommand(cmd: String) {
        FileLog.write("Befehl aus Home Assistant: $cmd")
        when (cmd) {
            "lock" -> lock()
            "unlock" -> { if (messySince != 0L) messySince = System.currentTimeMillis(); unlock() }
            "warn" -> showNotice()
            "check" -> requestCheck()
        }
    }

    private fun evaluate(newState: Boolean = false) {
        val now = System.currentTimeMillis()
        val offline = !client.isConnected || !camAvailable

        if (state == PhoneState.EMERGENCY) {
            if (now < prefs.emergencyUntil) { updateNotification(); return }
            if (messySince != 0L) messySince = now
            setState(PhoneState.FREE)
        }

        // Ausfallsicher: keine Verbindung lange genug -> freigeben.
        if (offline && now - disconnectedSince > TimeUnit.MINUTES.toMillis(prefs.failOpenMinutes.toLong())) {
            if (state == PhoneState.LOCKED || state == PhoneState.WARNED) {
                FileLog.write("Keine Verbindung zu Home Assistant/Kamera – Handy wird freigegeben")
                unlock()
            }
            return
        }

        if (!offline) {
            val c = cam
            if (c == null || !c.isMessy) {
                if (state != PhoneState.FREE) unlock()
            } else if (messySince != 0L) {
                val graceOver = now - messySince >= TimeUnit.MINUTES.toMillis(prefs.graceMinutes.toLong())
                if (graceOver && state != PhoneState.LOCKED) lock()
                else if (!graceOver && (state == PhoneState.FREE || newState)) showNotice()
            }
        }
        updateNotification()
    }

    private val lockAt: Long?
        get() = if (state == PhoneState.WARNED && messySince != 0L)
            messySince + TimeUnit.MINUTES.toMillis(prefs.graceMinutes.toLong()) else null

    private fun setState(s: PhoneState) {
        if (state == s) return
        state = s
        pushStatus()
        updateNotification()
    }

    // ------------------------------------------------------------------ Aktionen

    private fun showNotice() {
        if (state == PhoneState.LOCKED) return
        setState(PhoneState.WARNED)
        overlay.showNotice(info())
    }

    private fun lock() {
        if (state == PhoneState.LOCKED) return
        FileLog.write("Handy wird gesperrt")
        setState(PhoneState.LOCKED)
        mobile.fireEvent(client, "locked")
        fetchPhoto(force = true)
        overlay.showLock(info())
    }

    private fun unlock() {
        val wasLocked = state == PhoneState.LOCKED
        overlay.hideAll()
        setState(PhoneState.FREE)
        if (wasLocked) {
            FileLog.write("Handy entsperrt")
            mobile.fireEvent(client, "unlocked")
        }
    }

    override fun tryPin(pin: String): Boolean {
        if (!Pin.check(pin, prefs.pinHash)) return false
        FileLog.write("Notfall-Entsperrung per PIN")
        prefs.emergencyUntil = System.currentTimeMillis() + TimeUnit.MINUTES.toMillis(prefs.emergencyMinutes.toLong())
        overlay.hideAll()
        setState(PhoneState.EMERGENCY)
        mobile.fireEvent(client, "emergency_unlock")
        return true
    }

    override fun requestCheck() {
        checkPending = true
        refreshOverlay()
        if (prefs.checkButton.isNotEmpty()) {
            client.callService("button", "press", entityId = prefs.checkButton) { ok, err ->
                if (!ok) FileLog.write("'Jetzt prüfen' fehlgeschlagen: $err")
            }
        }
        main.postDelayed({ if (checkPending) { checkPending = false; refreshOverlay() } }, TimeUnit.MINUTES.toMillis(3))
    }

    override fun openApp() {
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    // ------------------------------------------------------------------ Darstellung / Status

    private fun info() = GuardInfo(cam, photo, checkPending, prefs.pinHash.isNotEmpty(), lockAt)

    private fun refreshOverlay() {
        if (state == PhoneState.LOCKED) overlay.showLock(info())
        else if (state == PhoneState.WARNED) overlay.showNotice(info())
        else overlay.update(info())
    }

    private fun fetchPhoto(force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && now - lastPhotoFetch < 10_000) return
        lastPhotoFetch = now
        val entity = prefs.cameraEntity.ifEmpty { return }
        client.fetchCameraImage(entity) { bytes ->
            if (bytes != null) {
                val bmp = runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }.getOrNull()
                if (bmp != null) main.post { photo = bmp; refreshOverlay() }
            }
        }
    }

    private fun pushStatus() {
        if (prefs.webhookId.isEmpty()) return
        val text = when (state) {
            PhoneState.WARNED -> "hinweis"
            PhoneState.LOCKED -> "gesperrt"
            PhoneState.EMERGENCY -> "notfall"
            PhoneState.FREE -> "frei"
        }
        val extra = JSONObject()
            .put("score", cam?.score ?: JSONObject.NULL)
            .put("reason", cam?.reason ?: "")
            .put("connection", client.state.text)
        mobile.updateStatus(client, text, state == PhoneState.LOCKED, extra)
    }

    // ------------------------------------------------------------------ Updates

    private fun maybeCheckForUpdate() {
        val hours = prefs.updateHours
        if (hours <= 0) return
        val due = prefs.lastUpdateCheck + TimeUnit.HOURS.toMillis(hours.toLong())
        if (System.currentTimeMillis() >= due && state != PhoneState.LOCKED) checkForUpdate(manual = false)
    }

    private fun checkForUpdate(manual: Boolean) {
        prefs.lastUpdateCheck = System.currentTimeMillis()
        Thread {
            val msg = runCatching { AppUpdater(this, "WerWarDas-Kind.apk").checkAndInstall() }
                .getOrElse { "Update-Suche fehlgeschlagen: ${it.message}" }
            if (manual) main.post {
                getSystemService(NotificationManager::class.java).notify(42,
                    buildNotification("WerWarDas-Update", msg))
            }
        }.start()
    }

    // ------------------------------------------------------------------ Benachrichtigung

    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "WerWarDas", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Hält die Verbindung zu Home Assistant"
        })
    }

    private fun buildNotification(title: String, text: String): Notification {
        val pi = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification() {
        val status = when {
            !client.isConnected -> client.state.text
            !camAvailable -> "Kamera offline"
            state == PhoneState.LOCKED -> "Gesperrt – Zimmer aufräumen"
            state == PhoneState.WARNED -> "Hinweis: Zimmer aufräumen"
            state == PhoneState.EMERGENCY -> "Notfall-Entsperrung aktiv"
            else -> cam?.let { "Alles ordentlich (${it.scoreText})" } ?: "Alles ordentlich"
        }
        getSystemService(NotificationManager::class.java).notify(ONGOING_ID, buildNotification("WerWarDas", status))
    }
}
