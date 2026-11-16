package de.werwardas.eltern

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Startet den Benachrichtigungsdienst nach einem Neustart wieder. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            if (ParentPrefs(context).isLoggedIn) ParentService.start(context)
        }
    }
}
