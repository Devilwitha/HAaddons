package de.werwardas.kind

import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.Toast
import android.app.Activity
import de.werwardas.common.Colors
import de.werwardas.common.Form
import de.werwardas.common.Pin

/** Einstellungen der Kinder-App (per PIN geschützt geöffnet). */
class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = KidPrefs(this)
        val form = Form(this)
        form.title("Einstellungen")

        form.section("Verhalten")
        val grace = form.field("Sperren nach (Minuten)", prefs.graceMinutes.toString(), InputType.TYPE_CLASS_NUMBER)
        val emergency = form.field("Notfall-Entsperrung (Minuten)", prefs.emergencyMinutes.toString(), InputType.TYPE_CLASS_NUMBER)
        val failOpen = form.field("Bei Verbindungsverlust freigeben nach (Minuten)", prefs.failOpenMinutes.toString(), InputType.TYPE_CLASS_NUMBER)
        val update = form.field("Nach Updates suchen alle … Stunden (0 = nie)", prefs.updateHours.toString(), InputType.TYPE_CLASS_NUMBER)

        form.section("Notfall-PIN")
        val pin = form.field("Neue PIN (leer = unverändert)", type = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD)
        val removePin = form.switch("PIN entfernen", false)

        form.section("Home Assistant")
        val local = form.field("Adresse zu Hause", prefs.localUrl)
        val remote = form.field("Adresse über Nabu Casa (unterwegs)", prefs.remoteUrl)
        form.text("Die Nabu-Casa-Adresse wird bei der Anmeldung automatisch gesetzt.", color = Colors.GRAY, size = 13f)

        form.section("Entitäten (nur bei abweichenden Namen)")
        val sensor = form.field("Ordnungs-Sensor", prefs.camSensor)
        val camera = form.field("Kamera", prefs.cameraEntity)
        val checkBtn = form.field("„Jetzt prüfen“-Button", prefs.checkButton)

        form.button("Speichern") {
            fun num(e: EditText, def: Int, min: Int, max: Int): Int =
                e.text.toString().toIntOrNull()?.coerceIn(min, max) ?: def
            prefs.graceMinutes = num(grace, prefs.graceMinutes, 0, 1440)
            prefs.emergencyMinutes = num(emergency, prefs.emergencyMinutes, 1, 1440)
            prefs.failOpenMinutes = num(failOpen, prefs.failOpenMinutes, 0, 120)
            prefs.updateHours = num(update, prefs.updateHours, 0, 168)
            prefs.localUrl = local.text.toString().trim().trimEnd('/')
            prefs.remoteUrl = remote.text.toString().trim().trimEnd('/')
            prefs.camSensor = sensor.text.toString().trim()
            prefs.cameraEntity = camera.text.toString().trim()
            prefs.checkButton = checkBtn.text.toString().trim()
            if (removePin.isChecked) prefs.pinHash = ""
            else if (pin.text.toString().isNotEmpty()) prefs.pinHash = Pin.hash(pin.text.toString())

            startService(Intent(this, GuardService::class.java).setAction(GuardService.ACTION_REEVALUATE))
            Toast.makeText(this, "Gespeichert", Toast.LENGTH_SHORT).show()
            finish()
        }

        form.button("Abmelden (Gerät trennen)") {
            prefs.raw.edit().remove("refresh_token").remove("webhook_id").apply()
            stopService(Intent(this, GuardService::class.java))
            Toast.makeText(this, "Abgemeldet", Toast.LENGTH_SHORT).show()
            finish()
        }

        setContentView(form.scroll)
    }
}
