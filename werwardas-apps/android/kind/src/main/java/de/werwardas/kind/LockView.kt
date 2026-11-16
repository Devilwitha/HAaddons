package de.werwardas.kind

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import de.werwardas.common.Colors
import de.werwardas.common.dp

/** Vollbild-Sperre: Aufforderung zum Aufräumen, Foto, Countdown und PIN-Eingabe über einen eigenen Ziffernblock. */
@SuppressLint("ViewConstructor", "SetTextI18n")
class LockView(context: Context, private val actions: GuardActions?) : LinearLayout(context) {
    private val headline = TextView(context)
    private val reason = TextView(context)
    private val scoreText = TextView(context)
    private val photo = ImageView(context)
    private val pinDots = TextView(context)
    private val checkButton = Button(context)
    private var pin = StringBuilder()
    private val ticker = Handler(Looper.getMainLooper())
    private var lockAt: Long? = null

    init {
        orientation = VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        setBackgroundColor(0xFF0B0F14.toInt())
        isFocusableInTouchMode = true
        isFocusable = true
        setPadding(context.dp(24), context.dp(48), context.dp(24), context.dp(24))

        headline.apply {
            text = "🧹 Zimmer aufräumen!"
            textSize = 28f; setTypeface(typeface, Typeface.BOLD); setTextColor(0xFFFFFFFF.toInt())
            gravity = Gravity.CENTER
        }
        addView(headline)

        scoreText.apply {
            textSize = 17f; setTextColor(Colors.YELLOW); gravity = Gravity.CENTER
        }
        addView(scoreText, lp(top = 8))

        photo.apply {
            adjustViewBounds = true; scaleType = ImageView.ScaleType.FIT_CENTER
        }
        addView(photo, LayoutParams(LayoutParams.MATCH_PARENT, context.dp(180)).apply { topMargin = context.dp(12) })

        reason.apply {
            textSize = 15f; setTextColor(0xFFB8C0CC.toInt()); gravity = Gravity.CENTER
        }
        addView(reason, lp(top = 10))

        pinDots.apply {
            textSize = 26f; setTextColor(0xFFFFFFFF.toInt()); gravity = Gravity.CENTER; letterSpacing = 0.3f
            setPadding(0, context.dp(10), 0, context.dp(6))
        }
        addView(pinDots, lp(top = 16))

        addView(buildKeypad(), lp(top = 4))

        checkButton.apply {
            text = "Ich habe aufgeräumt – neu prüfen"
            isAllCaps = false; textSize = 15f
            setOnClickListener {
                actions?.requestCheck()
                isEnabled = false; text = "Wird geprüft …"
            }
        }
        addView(checkButton, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
            topMargin = context.dp(16)
        })

        startTicker()
    }

    private fun lp(top: Int) = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply {
        topMargin = context.dp(top); gravity = Gravity.CENTER_HORIZONTAL
    }

    private fun buildKeypad(): View {
        val grid = LinearLayout(context).apply { orientation = VERTICAL }
        val rows = listOf(listOf("1", "2", "3"), listOf("4", "5", "6"), listOf("7", "8", "9"), listOf("⌫", "0", "OK"))
        for (r in rows) {
            val row = LinearLayout(context).apply { orientation = HORIZONTAL }
            for (key in r) row.addView(keyButton(key), LinearLayout.LayoutParams(context.dp(76), context.dp(58)).apply {
                setMargins(context.dp(5), context.dp(5), context.dp(5), context.dp(5))
            })
            grid.addView(row)
        }
        return grid
    }

    private fun keyButton(key: String) = Button(context).apply {
        text = key; textSize = 20f; isAllCaps = false
        background = GradientDrawable().apply {
            setColor(if (key == "OK") Colors.GREEN else 0xFF2A313A.toInt())
            cornerRadius = context.dp(10).toFloat()
        }
        setTextColor(0xFFFFFFFF.toInt())
        setOnClickListener { onKey(key) }
    }

    private fun onKey(key: String) {
        when (key) {
            "⌫" -> if (pin.isNotEmpty()) pin.deleteCharAt(pin.length - 1)
            "OK" -> {
                if (actions?.tryPin(pin.toString()) == false) {
                    pinDots.text = "Falsche PIN"
                    pinDots.setTextColor(Colors.RED)
                }
                pin = StringBuilder()
            }
            else -> if (pin.length < 12) pin.append(key)
        }
        if (key != "OK") {
            pinDots.setTextColor(0xFFFFFFFF.toInt())
            pinDots.text = "●".repeat(pin.length)
        }
    }

    fun update(info: GuardInfo) {
        val cam = info.cam
        scoreText.text = cam?.let { "Ordnung: ${it.scoreText}" + (it.threshold?.let { t -> " (nötig: ${t.toInt()} %)" } ?: "") } ?: ""
        reason.text = cam?.reason ?: ""
        photo.visibility = if (info.photo != null) View.VISIBLE else View.GONE
        info.photo?.let { photo.setImageBitmap(it) }
        pinDots.visibility = if (info.hasPin) View.VISIBLE else View.GONE
        if (!info.checkPending) {
            checkButton.isEnabled = true
            checkButton.text = "Ich habe aufgeräumt – neu prüfen"
        }
        lockAt = info.lockAt
    }

    private fun startTicker() {
        val run = object : Runnable {
            override fun run() {
                ticker.postDelayed(this, 1000)
            }
        }
        ticker.post(run)
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        requestFocus()
        // Status- und Navigationsleiste ausblenden (sticky), damit die Sperre den Bildschirm füllt.
        @Suppress("DEPRECATION")
        systemUiVisibility = View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
            View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
            View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
            View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
    }

    /** Zurück-Taste schluckt die Sperre, statt das Kind zur vorherigen App zu lassen. */
    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean {
        if (event.keyCode == android.view.KeyEvent.KEYCODE_BACK) return true
        return super.dispatchKeyEvent(event)
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        ticker.removeCallbacksAndMessages(null)
    }
}
