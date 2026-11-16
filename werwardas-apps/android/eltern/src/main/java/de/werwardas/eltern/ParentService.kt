package de.werwardas.eltern

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import de.werwardas.common.ConnState
import de.werwardas.common.FileLog
import de.werwardas.common.HaClient
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

/**
 * Hält im Hintergrund die Verbindung zu Home Assistant und meldet, wenn ein Gerät gesperrt wird oder
 * jemand die Notfall-PIN benutzt. Die Dashboard-Activity benutzt denselben Client über [instance].
 */
class ParentService : Service(), HaClient.Listener {
    companion object {
        private const val ONGOING_CHANNEL = "werwardas"
        private const val ALERT_CHANNEL = "werwardas_alerts"
        private const val ONGOING_ID = 1
        @Volatile var instance: ParentService? = null
        private val alertId = AtomicInteger(100)

        fun start(context: Context) = context.startForegroundService(Intent(context, ParentService::class.java))
    }

    lateinit var client: HaClient
        private set
    private val main = Handler(Looper.getMainLooper())
    private var listener: (() -> Unit)? = null
    private val lastStatus = HashMap<String, String>()

    fun setDashboardListener(l: (() -> Unit)?) { listener = l }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        FileLog.init(this)
        createChannels()
        startForeground(ONGOING_ID, ongoing("Verbinde mit Home Assistant …"))
        val prefs = ParentPrefs(this)
        client = HaClient(this, prefs.raw, this).apply {
            entityFilter = null
            eventTypes = listOf("werwardas_event")
        }
        client.start()
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        client.stop()
    }

    override fun onConnection(state: ConnState) = main.post {
        getSystemService(NotificationManager::class.java).notify(ONGOING_ID, ongoing(state.text))
        listener?.invoke()
    }.let {}

    override fun onEntities(changed: Set<String>) {
        // Statuswechsel der Geräte erkennen und melden.
        for (id in changed) {
            if (!id.endsWith("_status")) continue
            val e = client.states[id] ?: continue
            if (!id.startsWith("sensor.werwardas_pc_") && e.attrs.optString("werwardas") != "phone") continue
            val prev = lastStatus.put(id, e.state)
            if (prev != null && prev != e.state) notifyStatus(e.name, prev, e.state)
        }
        main.post { listener?.invoke() }
    }

    override fun onEvent(event: JSONObject) {
        val data = event.optJSONObject("data")?.optJSONObject("event_data") ?: return
        val device = data.optString("device")
        when (data.optString("event")) {
            "emergency_unlock" -> alert("Notfall-Entsperrung", "$device wurde per PIN entsperrt.")
            "locked" -> {} // Statuswechsel meldet schon notifyStatus
        }
    }

    private fun notifyStatus(name: String, from: String, to: String) {
        val cleanName = name.removeSuffix(" Status").removeSuffix(" WerWarDas Status")
        when (to) {
            "gesperrt" -> alert("Gesperrt", "$cleanName wurde gesperrt (Zimmer unordentlich).")
            "notfall" -> alert("Notfall-Entsperrung", "$cleanName wurde per PIN entsperrt.")
            "frei" -> if (from == "gesperrt") alert("Aufgeräumt", "$cleanName ist wieder frei.")
        }
    }

    private fun alert(title: String, text: String) = main.post {
        val pi = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        getSystemService(NotificationManager::class.java).notify(alertId.incrementAndGet(),
            Notification.Builder(this, ALERT_CHANNEL)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("WerWarDas: $title")
                .setContentText(text)
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build())
    }.let {}

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(ONGOING_CHANNEL, "Verbindung", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(ALERT_CHANNEL, "Meldungen", NotificationManager.IMPORTANCE_HIGH))
    }

    private fun ongoing(text: String): Notification {
        val pi = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, ONGOING_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentTitle("WerWarDas Eltern")
            .setContentText(text)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }
}
