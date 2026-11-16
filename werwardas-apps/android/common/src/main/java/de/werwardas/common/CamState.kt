package de.werwardas.common

import java.time.OffsetDateTime

/**
 * Bewertung der Kamera aus dem Sensor "Ordnung" (sensor.werwardas_kamera_ordnung).
 * Die Attribute entsprechen dem MQTT-Status des Kameradienstes.
 */
data class CamState(
    val score: Double?,
    val tidy: Boolean?,
    val threshold: Double?,
    val reason: String,
    val error: Boolean,
    val paused: Boolean,
    /** Zeitpunkt der Prüfung in Millisekunden (0 = unbekannt). */
    val timestamp: Long,
) {
    /** Nur ein eindeutig "unordentlich" führt zu Hinweis/Sperre. Fehler, Pause, unbekannt = frei. */
    val isMessy: Boolean get() = tidy == false && !error && !paused

    val scoreText: String
        get() = score?.let { "${it.toInt()} %" } ?: "–"

    companion object {
        const val DEFAULT_SENSOR = "sensor.werwardas_kamera_ordnung"

        fun from(entity: Entity?): CamState? {
            if (entity == null || !entity.available && !entity.attrs.has("tidy")) return null
            val a = entity.attrs
            fun optBool(key: String): Boolean? = if (!a.has(key) || a.isNull(key)) null else a.optBoolean(key)
            fun optNum(key: String): Double? = if (!a.has(key) || a.isNull(key)) null else a.optDouble(key)
            val ts = try {
                OffsetDateTime.parse(a.optString("timestamp")).toInstant().toEpochMilli()
            } catch (_: Exception) {
                0L
            }
            return CamState(
                score = optNum("score"),
                tidy = optBool("tidy"),
                threshold = optNum("threshold"),
                reason = a.optString("reason"),
                error = a.optBoolean("error"),
                paused = a.optBoolean("paused"),
                timestamp = ts,
            )
        }

        /** Sucht den Ordnungs-Sensor (falls er anders heißt als üblich). */
        fun findSensor(states: Map<String, Entity>): String? =
            if (states.containsKey(DEFAULT_SENSOR)) DEFAULT_SENSOR
            else states.values.firstOrNull {
                it.id.startsWith("sensor.") && it.attrs.has("tidy") && it.attrs.has("threshold") && it.attrs.has("analyzer")
            }?.id
    }
}
