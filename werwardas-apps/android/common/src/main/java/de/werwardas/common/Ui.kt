package de.werwardas.common

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import java.security.MessageDigest

fun Context.dp(value: Int): Int =
    TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

object Colors {
    // Dunkles Design mit Grün als Akzent.
    const val GREEN = 0xFF2FD96B.toInt()
    const val GREEN_DEEP = 0xFF1F8F49.toInt()
    const val BLUE = GREEN            // frühere Akzentstellen nutzen jetzt ebenfalls Grün
    const val YELLOW = 0xFFF0B428.toInt()
    const val RED = 0xFFF0564B.toInt()
    const val GRAY = 0xFF8A94A2.toInt()
    const val BG = 0xFF11161C.toInt()
    const val TEXT = 0xFFE6EAF0.toInt()
    const val CARD = 0xFF1B222B.toInt()
}

/** Kleiner Baukasten für Formulare ohne XML-Layouts. */
class Form(private val ctx: Context) {
    val root = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(ctx.dp(16), ctx.dp(12), ctx.dp(16), ctx.dp(24))
    }
    val scroll = ScrollView(ctx).apply { addView(root); isFillViewport = true }

    private fun add(v: View, top: Int = 6): View {
        root.addView(v, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = ctx.dp(top) })
        return v
    }

    fun title(text: String) = add(TextView(ctx).apply {
        this.text = text; textSize = 24f; setTypeface(typeface, Typeface.BOLD); setTextColor(Colors.TEXT)
    }, 4) as TextView

    fun section(text: String) = add(TextView(ctx).apply {
        this.text = text; textSize = 13f; setTypeface(typeface, Typeface.BOLD); setTextColor(Colors.GREEN)
        isAllCaps = true
    }, 22) as TextView

    fun text(text: String = "", size: Float = 15f, color: Int = Colors.TEXT) = add(TextView(ctx).apply {
        this.text = text; textSize = size; setTextColor(color)
    }) as TextView

    fun card(): LinearLayout {
        val card = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(ctx.dp(14), ctx.dp(12), ctx.dp(14), ctx.dp(12))
            background = GradientDrawable().apply { setColor(Colors.CARD); cornerRadius = ctx.dp(12).toFloat() }
        }
        add(card, 8)
        return card
    }

    fun field(label: String, value: String = "", type: Int = InputType.TYPE_CLASS_TEXT, hint: String = ""): EditText {
        add(TextView(ctx).apply { text = label; textSize = 13f; setTextColor(Colors.GRAY) }, 10)
        return add(EditText(ctx).apply {
            setText(value); inputType = type; this.hint = hint; isSingleLine = true
        }, 0) as EditText
    }

    fun switch(label: String, checked: Boolean = false) = add(Switch(ctx).apply {
        text = label; isChecked = checked; textSize = 15f
    }, 10) as Switch

    fun button(text: String, onClick: () -> Unit): Button {
        val b = greenButton(ctx, text, onClick)
        add(b, 8)
        return b
    }

    fun row(vararg views: View): LinearLayout {
        val row = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        for (v in views) row.addView(v, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginEnd = ctx.dp(6)
        })
        add(row, 6)
        return row
    }

    fun custom(v: View, top: Int = 8) = add(v, top)
}

/** Grün gefüllter Knopf mit weißer Schrift, passend zum dunklen Design. */
fun greenButton(ctx: Context, text: String, onClick: () -> Unit) = Button(ctx).apply {
    this.text = text; isAllCaps = false; setTextColor(0xFFFFFFFF.toInt())
    background = GradientDrawable().apply { setColor(Colors.GREEN_DEEP); cornerRadius = ctx.dp(10).toFloat() }
    setOnClickListener { onClick() }
}

fun Context.smallButton(text: String, onClick: () -> Unit) = Button(this).apply {
    this.text = text; isAllCaps = false; textSize = 13f; setTextColor(0xFFFFFFFF.toInt())
    background = GradientDrawable().apply { setColor(Colors.GREEN_DEEP); cornerRadius = dp(10).toFloat() }
    setOnClickListener { onClick() }
}

fun badge(view: TextView, text: String, color: Int) {
    view.text = text
    view.setTextColor(Color.WHITE)
    view.setPadding(view.context.dp(10), view.context.dp(4), view.context.dp(10), view.context.dp(4))
    view.background = GradientDrawable().apply { setColor(color); cornerRadius = view.context.dp(20).toFloat() }
}

/** PIN wie in der Windows-App: SHA-256 von "werwardas:" + PIN, als Großbuchstaben-Hex. */
object Pin {
    fun hash(pin: String): String = MessageDigest.getInstance("SHA-256")
        .digest("werwardas:$pin".toByteArray()).joinToString("") { "%02X".format(it) }

    fun check(pin: String, storedHash: String?): Boolean = !storedHash.isNullOrEmpty() && hash(pin) == storedHash

    /** Fragt nach der PIN; ohne gespeicherte PIN geht es direkt weiter. */
    fun ask(activity: Activity, storedHash: String?, purpose: String, onOk: () -> Unit) {
        if (storedHash.isNullOrEmpty()) return onOk()
        val input = EditText(activity).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
            hint = "PIN"
        }
        AlertDialog.Builder(activity)
            .setTitle("PIN eingeben")
            .setMessage("Um $purpose")
            .setView(input)
            .setPositiveButton("OK") { _, _ ->
                if (check(input.text.toString(), storedHash)) onOk()
                else AlertDialog.Builder(activity).setMessage("Falsche PIN").setPositiveButton("OK", null).show()
            }
            .setNegativeButton("Abbrechen", null)
            .setCancelable(false)
            .show()
    }
}

/** Wie Home Assistant Namen in Entitäts-IDs umwandelt (vereinfacht). */
fun haSlug(name: String): String {
    val replaced = name.lowercase()
        .replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return replaced.replace(Regex("[^a-z0-9]+"), "_").trim('_')
}
