package de.werwardas.kind

import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.app.Activity
import de.werwardas.common.Colors
import de.werwardas.common.Form
import de.werwardas.common.FileLog

/**
 * Richtet die App ein: Adresse von Home Assistant, Anmeldung mit einem (Nicht-Admin-)Benutzer, Gerätename.
 * Danach die beiden Berechtigungen für Sperre und Updates.
 */
class SetupActivity : Activity() {
    private lateinit var prefs: KidPrefs
    private val ui = Handler(Looper.getMainLooper())

    private lateinit var urlField: EditText
    private lateinit var userField: EditText
    private lateinit var passField: EditText
    private lateinit var nameField: EditText
    private lateinit var status: TextView
    private lateinit var loginButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = KidPrefs(this)

        val form = Form(this)
        form.title("Einrichten")
        form.text("Mit Home Assistant verbinden. Am besten ein eigener Benutzer ohne Admin-Rechte.", color = Colors.GRAY)

        form.section("Home Assistant")
        urlField = form.field("Adresse (zu Hause)", prefs.localUrl, hint = "http://homeassistant.local:8123")
        userField = form.field("Benutzer", prefs.user)
        passField = form.field("Passwort", type = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        nameField = form.field("Name dieses Handys", prefs.deviceName)

        status = form.text("", color = Colors.BLUE, size = 14f)
        loginButton = form.button("Anmelden und Gerät registrieren") { doLogin() }

        form.section("Berechtigungen")
        form.text("Für die Sperre und die automatischen Updates:", color = Colors.GRAY, size = 14f)
        form.button("„Über anderen Apps einblenden“ erlauben") {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        form.button("„Unbekannte Apps installieren“ erlauben") {
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
        }
        form.button("Akkusparen für diese App ausschalten") {
            runCatching {
                startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
            }
        }
        form.text("Ohne Akku-Ausnahme kann Android die App im Hintergrund beenden; dann greift die Sperre erst wieder beim nächsten Öffnen.",
            color = Colors.GRAY, size = 13f)

        setContentView(form.scroll)
    }

    private fun doLogin() {
        val base = urlField.text.toString().trim().trimEnd('/')
        val user = userField.text.toString().trim()
        val pass = passField.text.toString()
        if (base.isEmpty() || user.isEmpty() || pass.isEmpty()) {
            status.setText("Bitte Adresse, Benutzer und Passwort ausfüllen."); return
        }
        prefs.deviceName = nameField.text.toString().trim().ifEmpty { prefs.deviceName }
        loginButton.isEnabled = false
        status.setTextColor(Colors.BLUE)
        status.text = "Melde an …"
        Thread {
            val result = runCatching {
                MobileApp.loginAndRegister(this, prefs, base, user, pass)
            }
            ui.post {
                loginButton.isEnabled = true
                result.onSuccess {
                    status.setTextColor(Colors.GREEN)
                    status.text = "✓ Angemeldet und registriert. Das Handy erscheint jetzt in Home Assistant."
                    startService(Intent(this, GuardService::class.java).setAction(GuardService.ACTION_REEVALUATE))
                    GuardService.start(this)
                    maybeAskPin()
                }.onFailure {
                    FileLog.write("Anmeldung fehlgeschlagen: ${it.message}")
                    status.setTextColor(Colors.RED)
                    status.text = "Fehlgeschlagen: ${it.message}"
                }
            }
        }.start()
    }

    private fun maybeAskPin() {
        if (prefs.pinHash.isNotEmpty()) return
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
            hint = "PIN (Ziffern)"
        }
        AlertDialog.Builder(this)
            .setTitle("Notfall-PIN festlegen")
            .setMessage("Mit dieser PIN lässt sich die Sperre aufheben und die Einstellungen öffnen. Leer lassen = keine PIN.")
            .setView(input)
            .setPositiveButton("Speichern") { _, _ ->
                val pin = input.text.toString()
                if (pin.isNotEmpty()) {
                    prefs.pinHash = de.werwardas.common.Pin.hash(pin)
                    status.append("\nPIN gespeichert.")
                }
            }
            .setNegativeButton("Später", null)
            .show()
    }
}
