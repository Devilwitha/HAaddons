package de.werwardas.kind

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import de.werwardas.common.Colors
import de.werwardas.common.dp
import java.util.concurrent.TimeUnit

/** Kleines Kärtchen oben am Rand: Hinweis, dass bald gesperrt wird, solange nicht aufgeräumt ist. */
@SuppressLint("ViewConstructor", "SetTextI18n")
class NoticeView(context: Context, private val actions: GuardActions?) : LinearLayout(context) {
    private val line = TextView(context)
    private val countdown = TextView(context)

    init {
        orientation = VERTICAL
        setPadding(context.dp(16), context.dp(12), context.dp(16), context.dp(12))
        background = GradientDrawable().apply {
            setColor(Colors.YELLOW); cornerRadius = context.dp(14).toFloat()
        }
        line.apply {
            text = "🧹 Bitte das Zimmer aufräumen"
            textSize = 16f; setTypeface(typeface, Typeface.BOLD); setTextColor(0xFF20262C.toInt())
        }
        addView(line)
        countdown.apply { textSize = 13f; setTextColor(0xFF4A3B10.toInt()) }
        addView(countdown)
        setOnClickListener { actions?.openApp() }
    }

    fun update(info: GuardInfo) {
        val cam = info.cam
        line.text = "🧹 Bitte aufräumen" + (cam?.let { " (${it.scoreText})" } ?: "")
        val lockAt = info.lockAt
        countdown.text = if (lockAt != null) {
            val left = lockAt - System.currentTimeMillis()
            if (left > 0) "Gesperrt in ${TimeUnit.MILLISECONDS.toMinutes(left) + 1} Min., wenn nicht aufgeräumt"
            else "Wird gleich gesperrt …"
        } else {
            "Zum Öffnen tippen"
        }
    }
}
