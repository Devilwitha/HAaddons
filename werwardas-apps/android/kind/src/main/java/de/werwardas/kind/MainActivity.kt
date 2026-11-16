package de.werwardas.kind

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.TextView
import android.app.Activity
import de.werwardas.common.AppUpdater
import de.werwardas.common.Colors
import de.werwardas.common.ConnState
import de.werwardas.common.Form
import de.werwardas.common.Pin
import de.werwardas.common.badge

/** Startseite der Kinder-App: zeigt den Status und führt durch die Einrichtung. */
class MainActivity : Activity() {
    private lateinit var prefs: KidPrefs
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var statusBadge: TextView
    private lateinit var connText: TextView
    private lateinit var hint: TextView
    private val refresh = object : Runnable {
        override fun run() { render(); ui.postDelayed(this, 2000) }
    }

    @SuppressLint("SetTextI18n")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = KidPrefs(this)

        val form = Form(this)
        form.title("WerWarDas")
        form.text("Aufräum-Wächter fürs Handy", color = Colors.GRAY)

        statusBadge = form.text("", size = 18f)
        connText = form.text("", color = Colors.GRAY, size = 14f)
        hint = form.text("", color = Colors.RED, size = 14f)

        form.button("Einrichten / Anmelden") { startActivity(Intent(this, SetupActivity::class.java)) }
        form.button("Einstellungen") {
            Pin.ask(this, prefs.pinHash, "die Einstellungen zu öffnen") {
                startActivity(Intent(this, SettingsActivity::class.java))
            }
        }
        form.button("Jetzt nach Update suchen") {
            GuardService.instance?.let {
                startService(Intent(this, GuardService::class.java).setAction(GuardService.ACTION_CHECK_UPDATE))
            }
        }
        form.text("Version ${AppUpdater.currentVersionName(this)}", color = Colors.GRAY, size = 12f)

        setContentView(form.scroll)
        ensurePermissions()
    }

    override fun onResume() {
        super.onResume()
        if (prefs.isRegistered) GuardService.start(this)
        ui.post(refresh)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(refresh)
    }

    @SuppressLint("SetTextI18n")
    private fun render() {
        if (!prefs.isRegistered) {
            badge(statusBadge, "Noch nicht eingerichtet", Colors.GRAY)
            connText.text = "Tippe auf „Einrichten / Anmelden“."
        } else {
            val st = GuardService.instance?.statusForUi() ?: ("Dienst startet …" to Colors.GRAY)
            badge(statusBadge, st.first, st.second)
            connText.text = "Gerät: ${prefs.deviceName}"
        }
        hint.text = buildList {
            if (!Settings.canDrawOverlays(this@MainActivity))
                add("⚠ Berechtigung „Über anderen Apps einblenden“ fehlt – ohne sie kann nicht gesperrt werden.")
            if (!packageManager.canRequestPackageInstalls())
                add("⚠ „Unbekannte Apps installieren“ ist aus – dann klappen automatische Updates nicht.")
        }.joinToString("\n")
    }

    private fun ensurePermissions() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }
}
