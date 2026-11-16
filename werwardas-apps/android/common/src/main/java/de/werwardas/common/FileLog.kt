package de.werwardas.common

import android.content.Context
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Kleines Logbuch in den App-Dateien (zum Nachsehen in der App), zusätzlich im Android-Log. */
object FileLog {
    private var file: File? = null
    private val format = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.GERMANY)

    fun init(context: Context) {
        file = File(context.filesDir, "werwardas.log")
    }

    @Synchronized
    fun write(message: String) {
        Log.i("WerWarDas", message)
        val f = file ?: return
        try {
            if (f.length() > 300_000) f.renameTo(File(f.parentFile, "werwardas.log.old"))
            f.appendText("${format.format(Date())} $message\n")
        } catch (_: Exception) {
            // Logging darf nie stören.
        }
    }

    fun tail(lines: Int = 80): String = try {
        file?.takeIf { it.exists() }?.readLines()?.takeLast(lines)?.joinToString("\n") ?: ""
    } catch (_: Exception) {
        ""
    }
}
