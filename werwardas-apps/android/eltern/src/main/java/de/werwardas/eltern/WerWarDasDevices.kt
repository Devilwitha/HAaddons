package de.werwardas.eltern

import de.werwardas.common.Entity

/** Ein überwachtes Gerät (PC über die Windows-App oder ein Handy über die Kinder-App). */
data class Device(
    val key: String,
    val name: String,
    val statusEntity: String,
    val status: String,
    val available: Boolean,
    val kind: Kind,
    /** Für den PC: switch-Entität der Sperre. Für das Handy: notify-Dienst für Befehle. */
    val lockSwitch: String?,
    val warnButton: String?,
    val shutdownButton: String?,
    val notifyService: String?,
) {
    enum class Kind { PC, PHONE }

    val locked: Boolean get() = status == "gesperrt"
    val statusLabel: String get() = when (status) {
        "frei" -> "Frei"
        "hinweis" -> "Hinweis"
        "gesperrt" -> "Gesperrt"
        "notfall" -> "Notfall-Entsperrung"
        else -> if (!available) "Offline" else status.replaceFirstChar { it.uppercase() }
    }

    companion object {
        /** Findet alle WerWarDas-Geräte aus den HA-Zuständen. */
        fun findAll(states: Map<String, Entity>): List<Device> {
            val devices = mutableListOf<Device>()
            // PCs: sensor.werwardas_pc_<id>_status + switch ..._sperre
            for (e in states.values) {
                val m = Regex("^sensor\\.werwardas_pc_(.+)_status$").find(e.id) ?: continue
                val id = m.groupValues[1]
                devices += Device(
                    key = "pc_$id",
                    name = e.name.removeSuffix(" Status").ifEmpty { "PC $id" },
                    statusEntity = e.id,
                    status = e.state,
                    available = e.available,
                    kind = Kind.PC,
                    lockSwitch = "switch.werwardas_pc_${id}_sperre".takeIf { states.containsKey(it) },
                    warnButton = "button.werwardas_pc_${id}_hinweis_anzeigen".takeIf { states.containsKey(it) },
                    shutdownButton = "button.werwardas_pc_${id}_herunterfahren".takeIf { states.containsKey(it) },
                    notifyService = null,
                )
            }
            // Handys: sensor.*_werwardas_status (von der Kinder-App), Befehle über notify.<gerät>
            for (e in states.values) {
                if (!e.id.endsWith("_werwardas_status")) continue
                if (e.attrs.optString("werwardas") != "phone") continue
                val device = e.attrs.optString("device_name").ifEmpty { e.name.removeSuffix(" WerWarDas Status") }
                val notifyBase = e.id.removePrefix("sensor.").removeSuffix("_werwardas_status")
                // Befehle an das Handy laufen über den mobile_app-Notify-Dienst (notify.mobile_app_<gerät>).
                // Die notify.<gerät>-Entität existiert zwar, ist aber kein aufrufbarer Dienst.
                devices += Device(
                    key = "phone_$notifyBase",
                    name = device,
                    statusEntity = e.id,
                    status = e.state,
                    available = e.available,
                    kind = Kind.PHONE,
                    lockSwitch = null,
                    warnButton = null,
                    shutdownButton = null,
                    notifyService = "notify.mobile_app_$notifyBase",
                )
            }
            return devices.sortedBy { it.name }
        }
    }
}
