"use strict";
// Karten-Bereich: Status der Installation und Vorschau der Karten mit Beispieldaten.

const DEMO = { theme: "", mode: "dark", hass: null, cards: [] };

function demoEntity(id, state, attributes = {}, minutesAgo = 30) {
  const t = new Date(Date.now() - minutesAgo * 60000).toISOString();
  return { entity_id: id, state: String(state), attributes, last_changed: t, last_updated: t };
}

function demoStates() {
  const list = [
    demoEntity("sensor.temperatur", 21.2, { device_class: "temperature", unit_of_measurement: "°C", friendly_name: "Temperatur" }),
    demoEntity("sensor.luftfeuchte", 50.1, { device_class: "humidity", unit_of_measurement: "%", friendly_name: "Luftfeuchte" }),
    demoEntity("sensor.luftqualitaet", 20, { device_class: "aqi", unit_of_measurement: "AQI", friendly_name: "Luftqualität" }),
    demoEntity("sensor.buero_temperatur", 27.2, { device_class: "temperature", unit_of_measurement: "°C" }),
    demoEntity("sensor.buero_luftfeuchte", 48, { device_class: "humidity", unit_of_measurement: "%" }),
    demoEntity("binary_sensor.buero_anwesenheit", "on", { device_class: "occupancy", friendly_name: "Anwesenheit" }),
    demoEntity("binary_sensor.buero_fenster", "off", { device_class: "window", friendly_name: "Fenster" }),
    demoEntity("light.buero", "off", { friendly_name: "Licht" }),
    demoEntity("switch.webcam", "off", { friendly_name: "Webcam", icon: "mdi:webcam" }),
    demoEntity("sensor.wohnzimmer_temperatur", 24.6, { device_class: "temperature", unit_of_measurement: "°C" }),
    demoEntity("sensor.wohnzimmer_luftfeuchte", 51, { device_class: "humidity", unit_of_measurement: "%" }),
    demoEntity("light.wohnzimmer", "on", { friendly_name: "Deckenlicht" }),
    demoEntity("switch.fernseher", "off", { friendly_name: "Fernseher", icon: "mdi:television" }),
    demoEntity("binary_sensor.wohnzimmer_fenster", "on", { device_class: "window", friendly_name: "Fenster" }),
    demoEntity("person.anna", "home", { friendly_name: "Anna", device_trackers: ["device_tracker.annas_handy"] }, 95),
    demoEntity("sensor.annas_handy_battery_level", 100, { unit_of_measurement: "%", device_class: "battery" }),
    demoEntity("sensor.annas_handy_battery_state", "charging", {}),
    demoEntity("sensor.annas_handy_wifi_connection", "Heimnetz", { friendly_name: "WLAN" }),
    demoEntity("sensor.annas_handy_bluetooth_connection", "2 Geräte", { friendly_name: "Bluetooth" }),
    demoEntity("sensor.anna_raum", "Küche", {}),
    demoEntity("person.ben", "not_home", { friendly_name: "Ben", device_trackers: ["device_tracker.bens_handy"] }, 190),
    demoEntity("sensor.bens_handy_battery_level", 51, { unit_of_measurement: "%", device_class: "battery" }),
    demoEntity("sensor.bens_handy_battery_state", "discharging", {}),
    demoEntity("sensor.bens_handy_wifi_connection", "<not connected>", { friendly_name: "WLAN" }),
    demoEntity("person.mia", "Schule", { friendly_name: "Mia", device_trackers: ["device_tracker.mias_handy"] }, 240),
    demoEntity("sensor.mias_handy_battery_level", 14, { unit_of_measurement: "%", device_class: "battery" }),
  ];
  return Object.fromEntries(list.map((s) => [s.entity_id, s]));
}

const DEMO_TEXT = { on: "An", off: "Aus", home: "Zuhause", not_home: "Unterwegs", charging: "Lädt", discharging: "Entlädt", "<not connected>": "Nicht verbunden" };

function demoHass(states) {
  return {
    states,
    locale: { language: "de" },
    formatEntityState(obj) {
      const num = Number(obj.state);
      const unit = obj.attributes.unit_of_measurement;
      if (obj.state !== "" && Number.isFinite(num)) {
        const text = num.toLocaleString("de", { maximumFractionDigits: 1 });
        return unit ? `${text} ${unit}` : text;
      }
      if (obj.entity_id.startsWith("binary_sensor.") && obj.attributes.device_class === "window") return obj.state === "on" ? "Offen" : "Geschlossen";
      return DEMO_TEXT[obj.state] || obj.state;
    },
    async callWS(msg) {
      // Verlauf mit gleichmäßigen Schwankungen, damit die Kurven nach echten Messwerten aussehen
      const start = new Date(msg.start_time).getTime();
      const end = new Date(msg.end_time).getTime();
      const out = {};
      for (const id of msg.entity_ids) {
        const base = Number(states[id]?.state) || 0;
        const amp = id.includes("luftfeuchte") ? 6 : id.includes("luftqualitaet") ? 14 : 2.2;
        const seed = [...id].reduce((a, c) => a + c.charCodeAt(0), 0);
        const points = [];
        for (let i = 0; i <= 96; i++) {
          const x = i / 96;
          const wave = Math.sin(x * 5.5 + seed) * 0.6 + Math.sin(x * 13 + seed * 0.3) * 0.25 + (x > 0.8 ? Math.sin((x - 0.8) * 15) * 0.9 : 0);
          points.push({ s: String((base + wave * amp - amp * 0.3).toFixed(2)), lu: (start + x * (end - start)) / 1000 });
        }
        out[id] = points;
      }
      return out;
    },
    async callService(domain, service, data) {
      const id = data.entity_id;
      const old = DEMO.hass.states[id];
      if (!old) return;
      const next = { ...old, state: old.state === "on" ? "off" : "on", last_changed: new Date().toISOString() };
      DEMO.hass = demoHass({ ...DEMO.hass.states, [id]: next });
      for (const card of DEMO.cards) card.hass = DEMO.hass;
      toast(`Vorschau: ${next.attributes.friendly_name || id} ${next.state === "on" ? "an" : "aus"}`);
    },
  };
}

function demoCard(stage, type, config) {
  const card = document.createElement(type);
  card.setConfig(config);
  stage.appendChild(card);
  card.hass = DEMO.hass;
  card.addEventListener("hass-more-info", (ev) => toast(`In Home Assistant öffnen sich hier die Details zu ${ev.detail.entityId}`));
  DEMO.cards.push(card);
}

function buildDemo() {
  if (DEMO.cards.length || !customElements.get("bollisoft-glow-card")) return;
  DEMO.hass = demoHass(demoStates());
  const glow = $("demo-glow");
  demoCard(glow, "bollisoft-glow-card", { entity: "sensor.temperatur" });
  demoCard(glow, "bollisoft-glow-card", { entity: "sensor.luftfeuchte" });
  demoCard(glow, "bollisoft-glow-card", { entity: "sensor.luftqualitaet", icon: "mdi:air-filter" });
  const room = $("demo-room");
  demoCard(room, "bollisoft-room-card", {
    name: "Büro", icon: "mdi:desktop-classic", color: "blue", temperature: "sensor.buero_temperatur",
    humidity: "sensor.buero_luftfeuchte",
    entities: ["binary_sensor.buero_anwesenheit", "binary_sensor.buero_fenster", "light.buero", "switch.webcam"],
  });
  demoCard(room, "bollisoft-room-card", {
    name: "Wohnzimmer", icon: "mdi:sofa", color: "green", temperature: "sensor.wohnzimmer_temperatur",
    humidity: "sensor.wohnzimmer_luftfeuchte",
    entities: ["binary_sensor.wohnzimmer_fenster", "switch.fernseher", "light.wohnzimmer"],
  });
  demoCard($("demo-person"), "bollisoft-person-card", {
    title: "Wer ist da?",
    persons: [{ entity: "person.anna", room: "sensor.anna_raum" }, "person.ben", "person.mia"],
  });
}

function applyDemoTheme() {
  const themes = S.state?.themes || [];
  const entry = themes.find((t) => t.id === DEMO.theme) || themes.find((t) => t.id === "graphit") || themes[0];
  if (!entry) return;
  const modes = modesOf(entry.theme);
  if (!modes.includes(DEMO.mode)) DEMO.mode = modes[0];
  $("demo-mode").querySelectorAll("button").forEach((b) => {
    b.disabled = !modes.includes(b.dataset.mode);
    b.classList.toggle("on", b.dataset.mode === DEMO.mode);
  });
  applyVars($("demo"), entry.theme.modes[DEMO.mode], null);
}

function renderCardsTab() {
  const st = S.state;
  if (!st) return;
  const c = st.cards;
  const out = [];
  if (c.state === "ok") {
    out.push(`<div class="banner ok"><p><b>Karten sind installiert</b> (Dashboard-Ressource ${esc(c.detail)}).
      <small>Nach dem ersten Installieren oder einem Update die Home-Assistant-Seite einmal neu laden (in der App: nach unten ziehen).</small></p></div>`);
  } else if (c.state === "manual") {
    out.push(`<div class="banner"><p><b>Ressource bitte von Hand eintragen</b> (${esc(c.detail)}):
      <small>Einstellungen → Dashboards → ⋮ → Ressourcen → Ressource hinzufügen: <code>${esc(c.url)}</code>, Typ JavaScript-Modul.
      Bei Dashboards im YAML-Modus unter <code>lovelace: resources:</code> eintragen.</small></p>
      <button class="btn" data-cards="install">Erneut versuchen</button></div>`);
  } else if (c.state === "error") {
    out.push(`<div class="banner err"><p>${esc(c.detail)}</p><button class="btn" data-cards="install">Erneut versuchen</button></div>`);
  }
  if (c.www_created) {
    out.push(`<div class="banner"><p><b>Home Assistant einmal neu starten:</b> Der Ordner <code>/config/www</code> war neu,
      erst nach einem Neustart liefert Home Assistant die Karten unter <code>/local/</code> aus.</p>
      <button class="btn primary" data-act="restart">Jetzt neu starten</button></div>`);
  }
  $("cards-status").innerHTML = out.join("");
  const select = $("demo-theme");
  const current = DEMO.theme || "graphit";
  select.innerHTML = (st.themes || []).map((t) => `<option value="${esc(t.id)}" ${t.id === current ? "selected" : ""}>${esc(t.name)}</option>`).join("");
  DEMO.theme = select.value;
  buildDemo();
  applyDemoTheme();
}

$("cards-status").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.act === "restart") {
    // gleicher Ablauf wie im Hinweis oben
    if (!confirm("Home Assistant jetzt neu starten? Das dauert etwa eine Minute.")) return;
    try {
      await api("api/setup/restart", {});
      toast("Home Assistant startet neu …");
      waitForRestart();
    } catch (err) {
      toast(err.message, true);
    }
    return;
  }
  b.disabled = true;
  try {
    S.state.cards = await api("api/cards/install", {});
    toast(S.state.cards.state === "ok" ? "Karten eingetragen" : "Noch nicht geklappt", S.state.cards.state !== "ok");
  } catch (err) {
    toast(err.message, true);
  }
  renderCardsTab();
});
$("demo-theme").addEventListener("change", (ev) => {
  DEMO.theme = ev.target.value;
  applyDemoTheme();
});
$("demo-mode").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b || b.disabled) return;
  DEMO.mode = b.dataset.mode;
  applyDemoTheme();
});

function showTab(tab) {
  $("tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $("tab-themes").hidden = tab !== "themes";
  $("tab-cards").hidden = tab !== "cards";
  if (tab === "cards") renderCardsTab();
  try {
    localStorage.setItem("bollisoft-themes-tab", tab);
  } catch (err) {
    // ohne Speicher bleibt es beim Standard
  }
}

$("tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-tab]");
  if (b) showTab(b.dataset.tab);
});

try {
  if (localStorage.getItem("bollisoft-themes-tab") === "cards") showTab("cards");
} catch (err) {
  // ohne Speicher bleibt es beim Standard
}
