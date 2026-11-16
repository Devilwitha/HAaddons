package de.werwardas.common

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * Sucht im GitHub-Repo nach dem neuesten Android-Release (Tag "android-v1.0.N") und installiert die passende APK.
 * Die Versionsnummer N ist der versionCode. Android fragt beim ersten Update nach einer Bestätigung;
 * ab Android 12 laufen weitere Updates ohne Nachfrage, sobald die App sich einmal selbst installiert hat.
 */
class AppUpdater(private val context: Context, private val assetName: String) {
    companion object {
        const val REPO = "Devilwitha/werwardas"
        private const val TAG_PREFIX = "android-v"
        private const val CHANNEL = "updates"
        private const val NOTIFY_ID = 4711
        private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()

        fun currentVersionCode(context: Context): Long =
            context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode

        fun currentVersionName(context: Context): String =
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"
    }

    /** Blockierend. Gibt eine Meldung für den Benutzer zurück. */
    fun checkAndInstall(): String {
        if (!context.packageManager.canRequestPackageInstalls()) {
            return "Updates: Bitte „Unbekannte Apps installieren“ für diese App erlauben."
        }
        val releases = getJson("https://api.github.com/repos/$REPO/releases?per_page=30")
        var best: JSONObject? = null
        var bestCode = 0L
        for (i in 0 until releases.length()) {
            val r = releases.getJSONObject(i)
            if (r.optBoolean("draft") || r.optBoolean("prerelease")) continue
            val tag = r.optString("tag_name")
            if (!tag.startsWith(TAG_PREFIX)) continue
            val code = tag.substringAfterLast('.').toLongOrNull() ?: continue
            if (code > bestCode && findAsset(r) != null) {
                best = r; bestCode = code
            }
        }
        val current = currentVersionCode(context)
        if (best == null || bestCode <= current) return "Die App ist aktuell (Version ${currentVersionName(context)})."

        val asset = findAsset(best)!!
        val dir = File(context.cacheDir, "updates").apply { mkdirs() }
        val apk = File(dir, assetName)
        FileLog.write("Update ${best.optString("tag_name")} gefunden, lade herunter …")
        http.newCall(Request.Builder().url(asset.getString("browser_download_url")).build()).execute().use { resp ->
            if (!resp.isSuccessful) return "Download fehlgeschlagen (HTTP ${resp.code})."
            apk.outputStream().use { out -> resp.body!!.byteStream().copyTo(out) }
        }
        verify(apk, asset, bestCode)?.let { problem ->
            apk.delete()
            FileLog.write("Update verworfen: $problem")
            return "Update verworfen: $problem"
        }
        install(apk)
        return "Update ${best.optString("tag_name").removePrefix(TAG_PREFIX)} wird installiert …"
    }

    private fun findAsset(release: JSONObject): JSONObject? {
        val assets = release.optJSONArray("assets") ?: return null
        for (i in 0 until assets.length()) {
            val a = assets.getJSONObject(i)
            if (a.optString("name").equals(assetName, ignoreCase = true)) return a
        }
        return null
    }

    private fun verify(apk: File, asset: JSONObject, expectedCode: Long): String? {
        if (apk.length() != asset.optLong("size")) return "Download unvollständig"
        val digest = asset.optString("digest")
        if (digest.startsWith("sha256:")) {
            val md = MessageDigest.getInstance("SHA-256")
            apk.inputStream().use { input ->
                val buf = ByteArray(65536)
                while (true) {
                    val n = input.read(buf); if (n < 0) break; md.update(buf, 0, n)
                }
            }
            val hex = md.digest().joinToString("") { "%02x".format(it) }
            if (!hex.equals(digest.removePrefix("sha256:"), ignoreCase = true)) return "Prüfsumme stimmt nicht"
        }
        val info = context.packageManager.getPackageArchiveInfo(apk.path, 0) ?: return "Keine gültige APK"
        if (info.packageName != context.packageName) return "Falsches Paket ${info.packageName}"
        if (info.longVersionCode != expectedCode) return "APK hat Version ${info.longVersionCode} statt $expectedCode"
        return null
    }

    private fun install(apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            session.openWrite("app.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val intent = Intent(context, InstallResultReceiver::class.java)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val pending = PendingIntent.getBroadcast(context, sessionId, intent, flags)
            session.commit(pending.intentSender)
        }
        FileLog.write("Update an Android übergeben")
    }

    private fun getJson(url: String): org.json.JSONArray {
        val req = Request.Builder().url(url)
            .header("Accept", "application/vnd.github+json")
            .header("User-Agent", "WerWarDas-Android")
            .build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw HaException("GitHub antwortet mit HTTP ${resp.code}")
            return org.json.JSONArray(resp.body!!.string())
        }
    }

    /** Ergebnis der Installation. Braucht Android eine Bestätigung, kommt eine Benachrichtigung zum Antippen. */
    class InstallResultReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            FileLog.init(context)
            when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    @Suppress("DEPRECATION")
                    val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                    confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    val nm = context.getSystemService(NotificationManager::class.java)
                    nm.createNotificationChannel(NotificationChannel(CHANNEL, "Updates", NotificationManager.IMPORTANCE_HIGH))
                    val pi = PendingIntent.getActivity(context, 0, confirm,
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
                    nm.notify(NOTIFY_ID, Notification.Builder(context, CHANNEL)
                        .setSmallIcon(android.R.drawable.stat_sys_download_done)
                        .setContentTitle("WerWarDas-Update bereit")
                        .setContentText("Zum Installieren antippen")
                        .setContentIntent(pi)
                        .setAutoCancel(true)
                        .build())
                    // Wenn die App gerade offen ist, klappt auch der direkte Start.
                    try {
                        context.startActivity(confirm)
                    } catch (_: Exception) {
                    }
                }
                PackageInstaller.STATUS_SUCCESS -> FileLog.write("Update installiert")
                else -> FileLog.write("Update fehlgeschlagen ($status): ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
            }
        }
    }
}
