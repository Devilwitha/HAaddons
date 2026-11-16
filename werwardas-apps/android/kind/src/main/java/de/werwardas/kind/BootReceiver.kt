package de.werwardas.kind

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Startet den Wächter-Dienst nach einem Neustart des Handys wieder. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            if (KidPrefs(context).isRegistered) GuardService.start(context)
        }
    }
}
