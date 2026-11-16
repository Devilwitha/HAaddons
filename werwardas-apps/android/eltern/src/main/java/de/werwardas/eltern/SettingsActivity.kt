package de.werwardas.eltern

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.Toast
import de.werwardas.common.AppUpdater
import de.werwardas.common.Colors
import de.werwardas.common.Form

/** Einstellungen der Eltern-App. */
class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = ParentPrefs(this)
        val form = Form(this)
        form.title("Einstellungen")

        form.section("Home Assistant")
        val local = form.field("Adresse zu Hause", prefs.localUrl)
        val remote = form.field("Adresse über Nabu Casa (unterwegs)", prefs.remoteUrl)

        form.section("Entitäten (nur bei abweichenden Namen)")
        val sensor = form.field("Ordnungs-Sensor", prefs.camSensor)
        val camera = form.field("Kamera", prefs.cameraEntity)

        form.section("App")
        val update = form.field("Nach Updates suchen alle … Stunden (0 = nie)", prefs.updateHours.toString(), InputType.TYPE_CLASS_NUMBER)
        form.text("Version ${AppUpdater.currentVersionName(this)}", color = Colors.GRAY, size = 12f)

        form.button("Speichern") {
            prefs.localUrl = local.text.toString().trim().trimEnd('/')
            prefs.remoteUrl = remote.text.toString().trim().trimEnd('/')
            prefs.camSensor = sensor.text.toString().trim()
            prefs.cameraEntity = camera.text.toString().trim()
            prefs.updateHours = update.text.toString().toIntOrNull()?.coerceIn(0, 168) ?: prefs.updateHours
            ParentService.instance?.client?.reconnectNow()
            Toast.makeText(this, "Gespeichert", Toast.LENGTH_SHORT).show()
            finish()
        }
        form.button("Jetzt nach Update suchen") {
            Thread {
                val msg = runCatching { AppUpdater(this, "WerWarDas-Eltern.apk").checkAndInstall() }
                    .getOrElse { "Fehlgeschlagen: ${it.message}" }
                runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
            }.start()
        }
        form.button("Abmelden") {
            prefs.raw.edit().remove("refresh_token").apply()
            stopService(Intent(this, ParentService::class.java))
            Toast.makeText(this, "Abgemeldet", Toast.LENGTH_SHORT).show()
            finish()
        }

        setContentView(form.scroll)
    }
}
