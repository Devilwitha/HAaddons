package de.werwardas.eltern

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import de.werwardas.common.CamState
import de.werwardas.common.Colors
import de.werwardas.common.ConnState
import de.werwardas.common.FileLog
import de.werwardas.common.HaAuth
import de.werwardas.common.badge
import de.werwardas.common.dp
import de.werwardas.common.smallButton
import okhttp3.OkHttpClient
import org.json.JSONObject

/** Eltern-Dashboard: Raumstatus, Kamera-Einstellungen und Steuerung aller Geräte. */
class MainActivity : Activity() {
    private lateinit var prefs: ParentPrefs
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var root: LinearLayout
    private var lastPhotoKey = ""
    private var photoView: ImageView? = null
    private var resumed = false

    // Sicherheits-Aktualisierung (langsam), falls kein Ereignis kommt. Baut nur neu auf, wenn nichts bearbeitet wird.
    private val slowRefresh = object : Runnable {
        override fun run() { renderSafely(); ui.postDelayed(this, 8000) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = ParentPrefs(this)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(24))
        }
        setContentView(ScrollView(this).apply { addView(root); isFillViewport = true })
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        if (prefs.isLoggedIn) ParentService.start(this)
        ParentService.instance?.setDashboardListener { ui.post { renderSafely() } }
        render()
        ui.postDelayed(slowRefresh, 8000)
    }

    override fun onPause() {
        super.onPause()
        resumed = false
        ui.removeCallbacks(slowRefresh)
        ParentService.instance?.setDashboardListener(null)
    }

    // ------------------------------------------------------------------ Aufbau

    /** Baut den Bildschirm neu auf – aber nicht, während ein Eingabefeld bearbeitet wird (sonst ginge der Text verloren). */
    private fun renderSafely() {
        if (!resumed) return
        // Login-Bildschirm nie automatisch neu aufbauen (sonst gingen Eingaben verloren).
        if (!prefs.isLoggedIn) return
        if (root.findFocus() is EditText) return
        render()
    }

    @SuppressLint("SetTextI18n")
    private fun render() {
        root.removeAllViews()
        photoView = null
        heading("WerWarDas Eltern")

        if (!prefs.isLoggedIn) {
            buildLogin()
            return
        }
        val svc = ParentService.instance
        if (svc == null) {
            text("Dienst startet …", Colors.GRAY)
            return
        }
        val client = svc.client
        val conn = client.state
        val connColor = if (conn == ConnState.LOCAL || conn == ConnState.REMOTE) Colors.GREEN
        else if (conn == ConnState.CONNECTING) Colors.YELLOW else Colors.GRAY
        badge(text("", size = 14f), conn.text, connColor)

        if (conn == ConnState.AUTH_FAILED) {
            text("Bitte neu anmelden.", Colors.RED)
            button("Neu anmelden") { prefs.raw.edit().remove("refresh_token").apply(); render() }
            return
        }
        if (conn != ConnState.LOCAL && conn != ConnState.REMOTE) {
            text("Warte auf Verbindung …", Colors.GRAY)
        }

        buildRoomCard(client)
        buildCamSettings(client)
        buildDevices(client)

        section("App")
        button("Einstellungen") { startActivity(Intent(this, SettingsActivity::class.java)) }
    }

    private fun buildRoomCard(client: de.werwardas.common.HaClient) {
        section("Zimmer")
        val cam = CamState.from(client.states[prefs.camSensor])
        val card = card()
        val scoreColor = when {
            cam == null -> Colors.GRAY
            cam.paused -> Colors.GRAY
            cam.tidy == true -> Colors.GREEN
            cam.isMessy -> Colors.RED
            else -> Colors.YELLOW
        }
        val badgeView = TextView(this)
        card.addView(badgeView)
        badge(badgeView, cam?.let { "Ordnung: ${it.scoreText}" } ?: "–", scoreColor)
        val reason = TextView(this).apply {
            text = cam?.reason ?: "Noch keine Bewertung."
            setTextColor(Colors.TEXT); textSize = 15f
        }
        card.addView(reason, cardLp())

        val img = ImageView(this).apply {
            adjustViewBounds = true; scaleType = ImageView.ScaleType.FIT_CENTER
        }
        photoView = img
        card.addView(img, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(200)).apply { topMargin = dp(10) })
        fetchPhoto(client)

        val btnRow = LinearLayout(this)
        btnRow.addView(smallButton("Jetzt prüfen") {
            press(client, "button.werwardas_kamera_jetzt_prufen")
        })
        card.addView(btnRow, cardLp())
    }

    private fun buildCamSettings(client: de.werwardas.common.HaClient) {
        section("Kamera-Prüfung")
        val interval = client.states["number.werwardas_kamera_intervall"]
        val threshold = client.states["number.werwardas_kamera_grenzwert"]
        val activeHours = client.states["text.werwardas_kamera_prufzeit"]
        val monitoring = client.states["switch.werwardas_kamera_uberwachung"]
        if (interval == null && monitoring == null) {
            text("Keine Kamera-Einstellungen gefunden (Add-on nicht verbunden?).", Colors.GRAY, 13f)
            return
        }
        val card = card()
        val intervalField = fieldIn(card, "Prüfen alle (Minuten)", interval?.state?.substringBefore(".") ?: "", InputType.TYPE_CLASS_NUMBER)
        val thresholdField = fieldIn(card, "Aufgeräumt ab (% Ordnung)", threshold?.state?.substringBefore(".") ?: "", InputType.TYPE_CLASS_NUMBER)
        val hoursField = fieldIn(card, "Prüfzeit (z. B. 08:00-22:00, leer = immer)", activeHours?.state ?: "")
        val monSwitch = android.widget.Switch(this).apply {
            text = "Überwachung aktiv"; isChecked = monitoring?.state == "on"; textSize = 15f
        }
        card.addView(monSwitch, cardLp())
        val save = de.werwardas.common.greenButton(this, "Speichern") {
                intervalField.text.toString().toIntOrNull()?.coerceIn(1, 240)?.let {
                    setNumber(client, "number.werwardas_kamera_intervall", it)
                }
                thresholdField.text.toString().toIntOrNull()?.coerceIn(0, 100)?.let {
                    setNumber(client, "number.werwardas_kamera_grenzwert", it)
                }
                if (activeHours != null) client.callService("text", "set_value",
                    JSONObject().put("value", hoursField.text.toString().trim()), "text.werwardas_kamera_prufzeit")
                if (monitoring != null) client.callService("switch", if (monSwitch.isChecked) "turn_on" else "turn_off",
                    entityId = "switch.werwardas_kamera_uberwachung")
                Toast.makeText(this@MainActivity, "Gespeichert", Toast.LENGTH_SHORT).show()
        }
        card.addView(save, cardLp())
    }

    private fun buildDevices(client: de.werwardas.common.HaClient) {
        section("Geräte")
        val devices = Device.findAll(client.states)
        if (devices.isEmpty()) {
            text("Noch keine Geräte gefunden.", Colors.GRAY, 13f)
            return
        }
        for (d in devices) {
            val card = card()
            val head = TextView(this).apply {
                text = "${if (d.kind == Device.Kind.PC) "🖥" else "📱"} ${d.name}"
                setTypeface(typeface, Typeface.BOLD); textSize = 16f; setTextColor(Colors.TEXT)
            }
            card.addView(head)
            val statusBadge = TextView(this)
            card.addView(statusBadge, cardLp())
            badge(statusBadge, d.statusLabel, statusColor(d))

            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            if (d.locked || d.status == "notfall") {
                row.addView(smallButton("Freigeben") { command(client, d, "unlock") })
            } else {
                row.addView(smallButton("Hinweis") { command(client, d, "warn") })
                row.addView(smallButton("Sperren") { command(client, d, "lock") })
            }
            if (d.kind == Device.Kind.PC && d.shutdownButton != null) {
                row.addView(smallButton("Herunterfahren") { press(client, d.shutdownButton) })
            }
            card.addView(row, cardLp())
        }
    }

    // ------------------------------------------------------------------ Anmeldung

    private fun buildLogin() {
        text("Mit Home Assistant verbinden.", Colors.GRAY)
        val url = fieldIn(root, "Adresse (zu Hause)", prefs.localUrl, hint = "http://homeassistant.local:8123")
        val user = fieldIn(root, "Benutzer", prefs.user)
        val pass = fieldIn(root, "Passwort", type = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        val status = text("", Colors.BLUE, 14f)
        val btn = de.werwardas.common.greenButton(this, "Anmelden") {}
        btn.setOnClickListener {
            val base = url.text.toString().trim().trimEnd('/')
            if (base.isEmpty() || user.text.isEmpty() || pass.text.isEmpty()) {
                status.text = "Bitte alles ausfüllen."; return@setOnClickListener
            }
            btn.isEnabled = false; status.text = "Melde an …"
            Thread {
                val result = runCatching {
                    HaAuth(prefs.raw).login(OkHttpClient(), base, user.text.toString().trim(), pass.text.toString())
                }
                ui.post {
                    btn.isEnabled = true
                    result.onSuccess {
                        prefs.localUrl = base; prefs.user = user.text.toString().trim()
                        // Nabu-Casa-Adresse wird aus HA gelesen, sobald verbunden.
                        ParentService.start(this)
                        render()
                    }.onFailure {
                        FileLog.write("Anmeldung fehlgeschlagen: ${it.message}")
                        status.text = "Fehlgeschlagen: ${it.message}"
                    }
                }
            }.start()
        }
        root.addView(btn, rowLp())
    }

    // ------------------------------------------------------------------ Befehle

    private fun command(client: de.werwardas.common.HaClient, d: Device, action: String) {
        when (d.kind) {
            Device.Kind.PC -> when (action) {
                "lock" -> client.callService("switch", "turn_on", entityId = d.lockSwitch)
                "unlock" -> client.callService("switch", "turn_off", entityId = d.lockSwitch)
                "warn" -> d.warnButton?.let { press(client, it) }
            }
            Device.Kind.PHONE -> {
                val svc = d.notifyService ?: return toast("Für dieses Handy ist kein Befehlskanal verfügbar.")
                client.callService("notify", svc.removePrefix("notify."),
                    JSONObject().put("message", "command").put("data", JSONObject().put("werwardas", action))) { ok, err ->
                    if (!ok) ui.post { toast("Befehl fehlgeschlagen: $err") }
                }
            }
        }
    }

    private fun press(client: de.werwardas.common.HaClient, entity: String?) {
        entity ?: return
        client.callService("button", "press", entityId = entity) { ok, err -> if (!ok) ui.post { toast(err) } }
    }

    private fun setNumber(client: de.werwardas.common.HaClient, entity: String, value: Int) {
        client.callService("number", "set_value", JSONObject().put("value", value), entity)
    }

    private var lastPhotoFetch = 0L
    private var cachedPhoto: android.graphics.Bitmap? = null

    private fun fetchPhoto(client: de.werwardas.common.HaClient) {
        cachedPhoto?.let { photoView?.setImageBitmap(it) }
        val now = System.currentTimeMillis()
        if (now - lastPhotoFetch < 10_000) return
        lastPhotoFetch = now
        val entity = prefs.cameraEntity.ifEmpty { return }
        client.fetchCameraImage(entity) { bytes ->
            if (bytes == null) return@fetchCameraImage
            val key = bytes.size.toString()
            val bmp = runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }.getOrNull() ?: return@fetchCameraImage
            ui.post { lastPhotoKey = key; cachedPhoto = bmp; photoView?.setImageBitmap(bmp) }
        }
    }

    // ------------------------------------------------------------------ kleine UI-Helfer

    private fun statusColor(d: Device) = when {
        !d.available -> Colors.GRAY
        d.locked -> Colors.RED
        d.status == "hinweis" || d.status == "notfall" -> Colors.YELLOW
        else -> Colors.GREEN
    }

    private fun heading(t: String) = root.addView(TextView(this).apply {
        text = t; textSize = 24f; setTypeface(typeface, Typeface.BOLD); setTextColor(Colors.TEXT)
    })

    private fun section(t: String) = root.addView(TextView(this).apply {
        text = t.uppercase(); textSize = 13f; setTypeface(typeface, Typeface.BOLD); setTextColor(Colors.BLUE)
    }, rowLp(top = 20))

    private fun text(t: String, color: Int = Colors.TEXT, size: Float = 15f): TextView {
        val tv = TextView(this).apply { text = t; setTextColor(color); textSize = size }
        root.addView(tv, rowLp())
        return tv
    }

    private fun button(t: String, onClick: () -> Unit) =
        root.addView(de.werwardas.common.greenButton(this, t, onClick), rowLp())

    private fun card(): LinearLayout {
        val c = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = android.graphics.drawable.GradientDrawable().apply {
                setColor(Colors.CARD); cornerRadius = dp(12).toFloat()
            }
        }
        root.addView(c, rowLp(top = 8))
        return c
    }

    private fun fieldIn(parent: LinearLayout, label: String, value: String = "", type: Int = InputType.TYPE_CLASS_TEXT, hint: String = ""): EditText {
        parent.addView(TextView(this).apply { text = label; textSize = 13f; setTextColor(Colors.GRAY) }, rowLp(top = 8))
        val e = EditText(this).apply { setText(value); inputType = type; this.hint = hint; isSingleLine = true }
        parent.addView(e, rowLp())
        return e
    }

    private fun rowLp(top: Int = 6) = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(top) }

    private fun cardLp() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) }

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_SHORT).show()
}
