package de.werwardas.kind

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import de.werwardas.common.CamState
import de.werwardas.common.FileLog

/** Was auf Sperre und Hinweis angezeigt wird. */
data class GuardInfo(
    val cam: CamState?,
    val photo: Bitmap?,
    val checkPending: Boolean,
    val hasPin: Boolean,
    val lockAt: Long?,
)

interface GuardActions {
    fun requestCheck()
    fun tryPin(pin: String): Boolean
    fun openApp()
}

/**
 * Zeigt Sperre und Hinweis über den Apps. Die Sperre ist ein Vollbild-Fenster, das zum Aufräumen auffordert
 * und per PIN aufgeht. Es greift bewusst NICHT in andere Apps, die Einstellungen oder das Deinstallieren ein:
 * das Kind behält die Kontrolle über sein Gerät (Notrufe bleiben jederzeit möglich).
 *
 * Grundlage ist die Berechtigung "Über anderen Apps einblenden" (SYSTEM_ALERT_WINDOW).
 */
class OverlayHost(private val app: Context) {
    private val main = Handler(Looper.getMainLooper())
    private val wm = app.getSystemService(WindowManager::class.java)

    private var lockView: LockView? = null
    private var noticeView: NoticeView? = null
    private var info = GuardInfo(null, null, false, false, null)

    var lockWanted = false
        private set
    var actions: GuardActions? = null

    val canShow: Boolean get() = Settings.canDrawOverlays(app)

    // ------------------------------------------------------------------ Sperre

    fun showLock(newInfo: GuardInfo) = main.post {
        info = newInfo
        lockWanted = true
        hideNoticeInternal()
        if (!canShow) {
            FileLog.write("Sperre nicht möglich: Berechtigung 'Über anderen Apps einblenden' fehlt")
            return@post
        }
        if (lockView == null) {
            val view = LockView(app, actions)
            wm.addView(view, overlayParams())
            lockView = view
            FileLog.write("Sperre angezeigt")
        }
        lockView?.update(info)
    }

    fun update(newInfo: GuardInfo) = main.post {
        info = newInfo
        lockView?.update(info)
        noticeView?.update(info)
    }

    fun hideLock() = main.post {
        lockWanted = false
        lockView?.let { runCatching { wm.removeView(it) } }
        lockView = null
    }

    // ------------------------------------------------------------------ Hinweis

    fun showNotice(newInfo: GuardInfo) = main.post {
        info = newInfo
        if (lockView != null || !canShow) return@post
        if (noticeView == null) {
            val view = NoticeView(app, actions)
            wm.addView(view, noticeParams())
            noticeView = view
        }
        noticeView?.update(info)
    }

    fun hideNotice() = main.post { hideNoticeInternal() }

    private fun hideNoticeInternal() {
        noticeView?.let { runCatching { wm.removeView(it) } }
        noticeView = null
    }

    fun hideAll() = main.post {
        lockWanted = false
        hideNoticeInternal()
        lockView?.let { runCatching { wm.removeView(it) } }
        lockView = null
    }

    private fun overlayParams() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        // Fokussierbar (fängt die Zurück-Taste ab) und über die ganze Fläche inkl. Systemleisten.
        WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
            WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.OPAQUE,
    )

    /** Falls die Sperre angezeigt sein soll, aber das Fenster verschwunden ist (z. B. nach Home), neu einblenden. */
    fun reassertLock() = main.post {
        if (lockWanted && lockView == null && canShow) {
            val view = LockView(app, actions)
            wm.addView(view, overlayParams())
            lockView = view
            lockView?.update(info)
            FileLog.write("Sperre wieder in den Vordergrund geholt")
        } else {
            lockView?.bringToFront()
        }
    }

    private fun noticeParams() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP
        y = app.dpPx(16)
    }

    private fun Context.dpPx(v: Int) = (v * resources.displayMetrics.density).toInt()
}
