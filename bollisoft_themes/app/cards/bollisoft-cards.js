/*
 * BolliSoft Karten für Home Assistant – von BolliSoft (Add-on „BolliSoft Themes“)
 *
 *   custom:bollisoft-glow-card  Sensor mit leuchtendem Rand, Symbol und Verlauf im Hintergrund
 *   custom:bollisoft-room-card  Raum mit Temperatur, Luftfeuchte, Verlauf und runden Schaltknöpfen
 *   custom:bollisoft-person-card  Wer ist da? Personen mit Ort, Akku und Handy-Status
 *
 * Die Datei wird vom Add-on nach /config/www/bollisoft-themes/ kopiert und als Dashboard-Ressource
 * eingetragen. Keine weiteren Abhängigkeiten.
 */
(() => {
  "use strict";

  const VERSION = "1.0.0";
  const COLORS = {
    orange: "#fb923c",
    red: "#f87171",
    yellow: "#facc15",
    green: "#4ade80",
    teal: "#2dd4bf",
    cyan: "#22d3ee",
    blue: "#60a5fa",
    purple: "#a78bfa",
    pink: "#f472b6",
    grey: "#94a3b8",
  };
  const COLOR_OPTIONS = [
    { value: "auto", label: "Automatisch" },
    { value: "orange", label: "Orange" },
    { value: "red", label: "Rot" },
    { value: "yellow", label: "Gelb" },
    { value: "green", label: "Grün" },
    { value: "teal", label: "Türkis" },
    { value: "cyan", label: "Cyan" },
    { value: "blue", label: "Blau" },
    { value: "purple", label: "Lila" },
    { value: "pink", label: "Pink" },
    { value: "grey", label: "Grau" },
    { value: "theme", label: "Theme-Farbe" },
  ];
  const AUTO_COLORS = {
    temperature: "orange",
    humidity: "blue",
    moisture: "blue",
    aqi: "green",
    pm25: "green",
    pm10: "green",
    pm1: "green",
    carbon_dioxide: "green",
    volatile_organic_compounds: "green",
    power: "yellow",
    energy: "yellow",
    current: "yellow",
    voltage: "yellow",
    illuminance: "yellow",
    battery: "green",
    pressure: "purple",
    atmospheric_pressure: "purple",
    wind_speed: "cyan",
    precipitation: "cyan",
    gas: "red",
    water: "cyan",
  };
  const AUTO_ICONS = {
    temperature: "mdi:thermometer",
    humidity: "mdi:water-percent",
    moisture: "mdi:water",
    aqi: "mdi:air-filter",
    pm25: "mdi:air-filter",
    pm10: "mdi:air-filter",
    carbon_dioxide: "mdi:molecule-co2",
    volatile_organic_compounds: "mdi:air-filter",
    power: "mdi:flash",
    energy: "mdi:lightning-bolt",
    illuminance: "mdi:brightness-5",
    battery: "mdi:battery",
    pressure: "mdi:gauge",
    atmospheric_pressure: "mdi:gauge",
  };
  const TOGGLE_DOMAINS = ["light", "switch", "fan", "input_boolean", "automation", "siren", "humidifier"];
  const ON_STATES = ["on", "open", "opening", "home", "playing", "unlocked", "active", "heat", "cool", "detected"];
  const REFRESH_MS = 5 * 60 * 1000;

  function resolveColor(name, fallback) {
    if (!name || name === "auto") name = fallback;
    if (name === "theme" || !name) return "var(--primary-color)";
    if (COLORS[name]) return COLORS[name];
    return /^#[0-9a-f]{3,8}$/i.test(name) || /^(rgb|hsl)a?\(/i.test(name) ? name : "var(--primary-color)";
  }

  function esc(text) {
    return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function fireEvent(node, type, detail) {
    node.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
  }

  function formatState(hass, stateObj) {
    if (!stateObj) return "–";
    if (["unavailable", "unknown"].includes(stateObj.state)) return "–";
    if (hass.formatEntityState) return hass.formatEntityState(stateObj);
    const unit = stateObj.attributes.unit_of_measurement;
    return unit ? `${stateObj.state} ${unit}` : stateObj.state;
  }

  function formatNumber(hass, stateObj, decimals) {
    if (!stateObj || Number.isNaN(Number(stateObj.state))) return "–";
    const value = Number(stateObj.state);
    return value.toLocaleString(hass.locale?.language || navigator.language, {
      maximumFractionDigits: decimals, minimumFractionDigits: 0,
    });
  }

  // ---------------------------------------------------------------- Verlauf
  async function fetchHistory(hass, entityIds, hours) {
    const end = new Date();
    const start = new Date(end.getTime() - hours * 3600 * 1000);
    const result = await hass.callWS({
      type: "history/history_during_period",
      start_time: start.toISOString(),
      end_time: end.toISOString(),
      entity_ids: entityIds,
      minimal_response: true,
      no_attributes: true,
      significant_changes_only: false,
    });
    const out = {};
    for (const id of entityIds) {
      out[id] = (result?.[id] || [])
        .map((item) => [((item.lu ?? item.lc ?? 0) * 1000), Number(item.s)])
        .filter(([t, v]) => t > 0 && Number.isFinite(v));
    }
    return { start: start.getTime(), end: end.getTime(), series: out };
  }

  /** Messwerte in gleich breite Abschnitte mitteln: Liste von [x 0…1, Wert]. */
  function bucket(points, start, end, current, buckets = 60) {
    const data = points.slice();
    if (Number.isFinite(current)) data.push([end, current]);
    if (data.length < 2) {
      const v = data.length ? data[0][1] : current;
      return Number.isFinite(v) ? [[0, v], [1, v]] : [];
    }
    const span = end - start;
    const sums = new Array(buckets).fill(0);
    const counts = new Array(buckets).fill(0);
    for (const [t, v] of data) {
      const i = Math.min(buckets - 1, Math.max(0, Math.floor(((t - start) / span) * buckets)));
      sums[i] += v;
      counts[i] += 1;
    }
    const out = [];
    let last = data[0][1];
    for (let i = 0; i < buckets; i++) {
      if (counts[i]) last = sums[i] / counts[i];
      out.push([i / (buckets - 1), last]);
    }
    return out;
  }

  /** Geglättete SVG-Pfade (Linie und Fläche) in einem Rechteck. */
  function paths(points, box, range) {
    if (points.length < 2) return { line: "", area: "" };
    let [min, max] = range || [Math.min(...points.map((p) => p[1])), Math.max(...points.map((p) => p[1]))];
    if (max - min < 1e-6) {
      min -= 1;
      max += 1;
    }
    const pad = (max - min) * 0.12;
    min -= pad;
    max += pad;
    const xy = points.map(([x, v]) => [box.x + x * box.w, box.y + box.h - ((v - min) / (max - min)) * box.h]);
    let d = `M${xy[0][0].toFixed(1)},${xy[0][1].toFixed(1)}`;
    for (let i = 1; i < xy.length; i++) {
      const [x0, y0] = xy[i - 1];
      const [x1, y1] = xy[i];
      const mx = (x0 + x1) / 2;
      d += ` C${mx.toFixed(1)},${y0.toFixed(1)} ${mx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
    const bottom = box.y + box.h + 40;
    const area = `${d} L${xy[xy.length - 1][0].toFixed(1)},${bottom} L${xy[0][0].toFixed(1)},${bottom} Z`;
    return { line: d, area };
  }

  // ---------------------------------------------------------------- Grundklasse
  class BolliSoftCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._history = null;
      this._historyAt = 0;
      this._loading = false;
    }

    connectedCallback() {
      this._timer = setInterval(() => this._loadHistory(true), REFRESH_MS);
      if (this._hass && this._config) this._loadHistory(false);
    }

    disconnectedCallback() {
      clearInterval(this._timer);
    }

    set hass(hass) {
      const old = this._hass;
      this._hass = hass;
      if (!this._config) return;
      if (!old || this._watched().some((id) => old.states[id] !== hass.states[id])) this._render();
      if (!this._history && !this._loading) this._loadHistory(false);
    }

    get hass() {
      return this._hass;
    }

    _historyIds() {
      return [];
    }

    async _loadHistory(force) {
      const ids = this._historyIds().filter((id) => id && this._hass?.states[id]);
      if (!ids.length || this._loading) return;
      if (!force && this._history && Date.now() - this._historyAt < REFRESH_MS) return;
      this._loading = true;
      try {
        this._history = await fetchHistory(this._hass, ids, this._config.hours || 24);
        this._historyAt = Date.now();
        this._render();
      } catch (err) {
        console.warn("BolliSoft Karte: Verlauf nicht geladen", err);
      } finally {
        this._loading = false;
      }
    }

    _series(id) {
      if (!this._history) return [];
      const stateObj = this._hass.states[id];
      return bucket(this._history.series[id] || [], this._history.start, Date.now(), Number(stateObj?.state));
    }

    _moreInfo(entityId) {
      if (entityId) fireEvent(this, "hass-more-info", { entityId });
    }
  }

  // ---------------------------------------------------------------- Glow-Karte
  const GLOW_CSS = `
    :host {
      display: block;
      --bs-c: var(--primary-color);
    }
    ha-card {
      display: block;
      position: relative;
      overflow: hidden;
      height: 100%;
      min-height: 112px;
      cursor: pointer;
      border-radius: var(--ha-card-border-radius, 12px);
      border: 1px solid color-mix(in srgb, var(--bs-c) 75%, transparent);
      background:
        linear-gradient(100deg, color-mix(in srgb, var(--bs-c) 30%, transparent) 0%, transparent 58%),
        var(--ha-card-background, var(--card-background-color, #1c1c1c));
      box-shadow: 0 0 14px color-mix(in srgb, var(--bs-c) 55%, transparent);
      transition: transform 0.15s ease;
    }
    ha-card.pulse {
      animation: bs-glow 3.2s ease-in-out infinite;
    }
    ha-card:active {
      transform: scale(0.985);
    }
    @keyframes bs-glow {
      0%, 100% { box-shadow: 0 0 8px color-mix(in srgb, var(--bs-c) 40%, transparent); }
      50% { box-shadow: 0 0 22px color-mix(in srgb, var(--bs-c) 80%, transparent), 0 0 3px color-mix(in srgb, var(--bs-c) 90%, transparent); }
    }
    @media (prefers-reduced-motion: reduce) {
      ha-card.pulse { animation: none; }
    }
    .graph {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }
    .graph .line {
      fill: none;
      stroke: color-mix(in srgb, var(--bs-c) 55%, #ff3b30 25%);
      stroke-width: 2;
      vector-effect: non-scaling-stroke;
      opacity: 0.75;
    }
    .graph .area {
      fill: url(#bs-fill);
      opacity: 0.5;
    }
    .bubble {
      position: absolute;
      left: -38px;
      top: -38px;
      width: 120px;
      height: 120px;
      border-radius: 50%;
      background: color-mix(in srgb, var(--primary-background-color, #000) 72%, transparent);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-text-color, #fff) 5%, transparent);
    }
    .bubble ha-icon {
      position: absolute;
      left: 48px;
      top: 46px;
      --mdc-icon-size: 32px;
      color: var(--bs-c);
      filter: drop-shadow(0 0 6px color-mix(in srgb, var(--bs-c) 60%, transparent));
    }
    .text {
      position: absolute;
      left: 104px;
      top: 50%;
      transform: translateY(-62%);
      right: 12px;
    }
    .value {
      font-size: 22px;
      font-weight: 600;
      color: var(--primary-text-color);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .name {
      font-size: 13.5px;
      color: var(--secondary-text-color);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .warn {
      padding: 16px;
      color: var(--error-color, #db4437);
    }
  `;

  class BolliSoftGlowCard extends BolliSoftCard {
    static getConfigElement() {
      return document.createElement("bollisoft-glow-card-editor");
    }

    static getStubConfig(hass) {
      const states = Object.values(hass?.states || {});
      const pick = states.find((s) => s.entity_id.startsWith("sensor.") && s.attributes.device_class === "temperature")
        || states.find((s) => s.entity_id.startsWith("sensor.") && !Number.isNaN(Number(s.state)));
      return { entity: pick ? pick.entity_id : "", color: "auto", hours: 24, animation: true };
    }

    setConfig(config) {
      if (!config || !config.entity) throw new Error("Bitte einen Sensor wählen (entity)");
      const changed = this._config?.entity !== config.entity || this._config?.hours !== config.hours;
      this._config = { hours: 24, animation: true, color: "auto", ...config };
      if (changed) {
        this._history = null;
        if (this._hass) this._loadHistory(false);
      }
      this._render();
    }

    getCardSize() {
      return 2;
    }

    getGridOptions() {
      return { columns: 6, rows: 2, min_columns: 4, min_rows: 2 };
    }

    _watched() {
      return [this._config.entity];
    }

    _historyIds() {
      return this._config.graph === false ? [] : [this._config.entity];
    }

    _render() {
      if (!this._config || !this._hass) return;
      const cfg = this._config;
      const stateObj = this._hass.states[cfg.entity];
      if (!stateObj) {
        this.shadowRoot.innerHTML = `<style>${GLOW_CSS}</style><ha-card><div class="warn">Nicht gefunden: ${esc(cfg.entity)}</div></ha-card>`;
        return;
      }
      const deviceClass = stateObj.attributes.device_class;
      const color = resolveColor(cfg.color, AUTO_COLORS[deviceClass]);
      const icon = cfg.icon || stateObj.attributes.icon || AUTO_ICONS[deviceClass] || "mdi:chart-bell-curve";
      const name = cfg.name || stateObj.attributes.friendly_name || cfg.entity;
      const series = cfg.graph === false ? [] : this._series(cfg.entity);
      const p = paths(series, { x: 0, y: 54, w: 300, h: 38 });
      this.style.setProperty("--bs-c", color);
      this.shadowRoot.innerHTML = `
        <style>${GLOW_CSS}</style>
        <ha-card class="${cfg.animation === false ? "" : "pulse"}">
          <svg class="graph" viewBox="0 0 300 100" preserveAspectRatio="none" aria-hidden="true">
            <defs><linearGradient id="bs-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="${color}" stop-opacity="0.35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/>
            </linearGradient></defs>
            ${p.area ? `<path class="area" d="${p.area}"/><path class="line" d="${p.line}"/>` : ""}
          </svg>
          <div class="bubble"><ha-icon icon="${esc(icon)}"></ha-icon></div>
          <div class="text">
            <div class="value">${esc(formatState(this._hass, stateObj))}</div>
            <div class="name">${esc(name)}</div>
          </div>
        </ha-card>`;
      this.shadowRoot.querySelector("ha-card").addEventListener("click", () => this._moreInfo(cfg.entity));
    }
  }

  // ---------------------------------------------------------------- Raum-Karte
  const ROOM_CSS = `
    :host {
      display: block;
      --bs-c: #60a5fa;
    }
    ha-card {
      display: block;
      position: relative;
      overflow: hidden;
      height: 100%;
      min-height: 168px;
      border-radius: var(--ha-card-border-radius, 28px);
      background: var(--ha-card-background, var(--card-background-color, #2b2b2e));
    }
    .graph {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
    }
    .graph .line {
      fill: none;
      stroke: color-mix(in srgb, var(--bs-c) 85%, white 15%);
      stroke-width: 2;
      vector-effect: non-scaling-stroke;
    }
    .graph .area {
      fill: color-mix(in srgb, var(--bs-c) 16%, transparent);
    }
    .graph .hum {
      fill: none;
      stroke: color-mix(in srgb, var(--primary-text-color, #fff) 45%, transparent);
      stroke-width: 1.4;
      stroke-dasharray: 4 4;
      vector-effect: non-scaling-stroke;
    }
    .head {
      position: absolute;
      left: 22px;
      right: 20px;
      top: 14px;
      display: flex;
      align-items: baseline;
      gap: 12px;
      cursor: pointer;
    }
    .name {
      flex: 1;
      min-width: 0;
      font-size: 22px;
      font-weight: 400;
      color: var(--primary-text-color);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .temp {
      font-size: 30px;
      font-weight: 400;
      color: var(--primary-text-color);
      white-space: nowrap;
    }
    .hum-text {
      font-size: 15px;
      color: var(--secondary-text-color);
      white-space: nowrap;
    }
    .room {
      position: absolute;
      left: -22px;
      bottom: -44px;
      width: 150px;
      height: 150px;
      border-radius: 50%;
      background: color-mix(in srgb, var(--bs-c) 42%, var(--ha-card-background, var(--card-background-color, #2b2b2e)));
      cursor: pointer;
    }
    .room ha-icon {
      position: absolute;
      left: 52px;
      top: 34px;
      --mdc-icon-size: 46px;
      color: color-mix(in srgb, var(--bs-c) 55%, var(--primary-text-color, #fff));
    }
    .buttons {
      position: absolute;
      right: 16px;
      bottom: 14px;
      display: flex;
      gap: 10px;
      max-width: calc(100% - 150px);
      overflow-x: auto;
      scrollbar-width: none;
      padding: 2px;
    }
    .buttons::-webkit-scrollbar {
      display: none;
    }
    .btn {
      flex: none;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      border: 0;
      padding: 0;
      display: grid;
      place-items: center;
      cursor: pointer;
      background: color-mix(in srgb, var(--bs-c) 24%, var(--ha-card-background, var(--card-background-color, #2b2b2e)));
      color: color-mix(in srgb, var(--bs-c) 30%, var(--secondary-text-color, #999));
      transition: background 0.2s, color 0.2s, transform 0.1s;
      -webkit-tap-highlight-color: transparent;
    }
    .btn:active {
      transform: scale(0.92);
    }
    .btn.on {
      background: color-mix(in srgb, var(--bs-c) 75%, white 25%);
      color: #1b1b1f;
    }
    .btn.off-line {
      opacity: 0.4;
    }
    .btn ha-icon {
      --mdc-icon-size: 24px;
    }
  `;

  function hashColor(text) {
    const names = ["blue", "green", "orange", "purple", "teal", "pink", "yellow", "cyan"];
    let h = 0;
    for (const ch of String(text || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return names[h % names.length];
  }

  function entityConf(entry) {
    return typeof entry === "string" ? { entity: entry } : (entry || {});
  }

  class BolliSoftRoomCard extends BolliSoftCard {
    static getConfigElement() {
      return document.createElement("bollisoft-room-card-editor");
    }

    static getStubConfig(hass) {
      const states = Object.values(hass?.states || {});
      const temp = states.find((s) => s.entity_id.startsWith("sensor.") && s.attributes.device_class === "temperature");
      const hum = states.find((s) => s.entity_id.startsWith("sensor.") && s.attributes.device_class === "humidity");
      const lights = states.filter((s) => s.entity_id.startsWith("light.")).slice(0, 3).map((s) => s.entity_id);
      return { name: "Wohnzimmer", icon: "mdi:sofa", color: "auto", temperature: temp?.entity_id || "",
        humidity: hum?.entity_id || "", entities: lights, hours: 24 };
    }

    setConfig(config) {
      if (!config) throw new Error("Konfiguration fehlt");
      const changed = this._config?.temperature !== config.temperature || this._config?.humidity !== config.humidity
        || this._config?.hours !== config.hours;
      this._config = { hours: 24, color: "auto", icon: "mdi:sofa", ...config, entities: (config.entities || []).map(entityConf) };
      if (changed) {
        this._history = null;
        if (this._hass) this._loadHistory(false);
      }
      this._render();
    }

    getCardSize() {
      return 3;
    }

    getGridOptions() {
      return { columns: 12, rows: 3, min_columns: 6, min_rows: 3 };
    }

    _watched() {
      return [this._config.temperature, this._config.humidity, ...this._config.entities.map((e) => e.entity)].filter(Boolean);
    }

    _historyIds() {
      return [this._config.temperature, this._config.humidity].filter(Boolean);
    }

    _button(conf) {
      const stateObj = this._hass.states[conf.entity];
      if (!stateObj) return `<button class="btn off-line" title="${esc(conf.entity)} nicht gefunden"><ha-icon icon="mdi:help"></ha-icon></button>`;
      const on = ON_STATES.includes(stateObj.state);
      const icon = conf.icon || stateObj.attributes.icon || "";
      const title = `${conf.name || stateObj.attributes.friendly_name || conf.entity}: ${formatState(this._hass, stateObj)}`;
      const iconHtml = icon ? `<ha-icon icon="${esc(icon)}"></ha-icon>` : `<ha-state-icon></ha-state-icon>`;
      const cls = ["unavailable", "unknown"].includes(stateObj.state) ? "off-line" : on ? "on" : "";
      return `<button class="btn ${cls}" data-entity="${esc(conf.entity)}" title="${esc(title)}">${iconHtml}</button>`;
    }

    _render() {
      if (!this._config || !this._hass) return;
      const cfg = this._config;
      const color = resolveColor(cfg.color, hashColor(cfg.name));
      const temp = cfg.temperature ? this._hass.states[cfg.temperature] : null;
      const hum = cfg.humidity ? this._hass.states[cfg.humidity] : null;
      const tempUnit = temp?.attributes.unit_of_measurement || "°C";
      const tempText = temp ? `${formatNumber(this._hass, temp, cfg.decimals ?? 0)}${tempUnit}` : "";
      const humText = hum ? `${formatNumber(this._hass, hum, 0)}%` : "";
      const box = { x: 28, y: 64, w: 272, h: 52 };
      const tp = cfg.temperature ? paths(this._series(cfg.temperature), box) : { line: "", area: "" };
      const hp = cfg.humidity ? paths(this._series(cfg.humidity), { x: 28, y: 112, w: 272, h: 34 }) : { line: "" };
      this.style.setProperty("--bs-c", color);
      this.shadowRoot.innerHTML = `
        <style>${ROOM_CSS}</style>
        <ha-card>
          <svg class="graph" viewBox="0 0 300 168" preserveAspectRatio="none" aria-hidden="true">
            ${tp.area ? `<path class="area" d="${tp.area}"/><path class="line" d="${tp.line}"/>` : ""}
            ${hp.line ? `<path class="hum" d="${hp.line}"/>` : ""}
          </svg>
          <div class="head">
            <div class="name">${esc(cfg.name || "")}</div>
            ${tempText ? `<div class="temp">${esc(tempText)}</div>` : ""}
            ${humText ? `<div class="hum-text">${esc(humText)}</div>` : ""}
          </div>
          <div class="room"><ha-icon icon="${esc(cfg.icon)}"></ha-icon></div>
          <div class="buttons">${cfg.entities.map((e) => this._button(e)).join("")}</div>
        </ha-card>`;
      // Symbole ohne eigene Angabe übernimmt Home Assistant (passend zum Zustand)
      this.shadowRoot.querySelectorAll(".btn[data-entity]").forEach((btn) => {
        const stateIcon = btn.querySelector("ha-state-icon");
        if (stateIcon) {
          stateIcon.hass = this._hass;
          stateIcon.stateObj = this._hass.states[btn.dataset.entity];
        }
        this._bindButton(btn);
      });
      const open = () => {
        if (cfg.navigation_path) {
          history.pushState(null, "", cfg.navigation_path);
          fireEvent(window, "location-changed", { replace: false });
        } else {
          this._moreInfo(cfg.temperature || cfg.humidity);
        }
      };
      this.shadowRoot.querySelector(".head").addEventListener("click", open);
      this.shadowRoot.querySelector(".room").addEventListener("click", open);
    }

    _bindButton(btn) {
      const entityId = btn.dataset.entity;
      let timer = 0;
      let held = false;
      const start = () => {
        held = false;
        timer = setTimeout(() => {
          held = true;
          this._moreInfo(entityId);
        }, 500);
      };
      const cancel = () => clearTimeout(timer);
      btn.addEventListener("pointerdown", start);
      btn.addEventListener("pointerup", cancel);
      btn.addEventListener("pointerleave", cancel);
      btn.addEventListener("contextmenu", (ev) => ev.preventDefault());
      btn.addEventListener("click", () => {
        if (held) return;
        const domain = entityId.split(".")[0];
        if (TOGGLE_DOMAINS.includes(domain)) {
          this._hass.callService("homeassistant", "toggle", { entity_id: entityId });
        } else if (domain === "scene" || domain === "script") {
          this._hass.callService(domain, "turn_on", { entity_id: entityId });
        } else if (domain === "button" || domain === "input_button") {
          this._hass.callService(domain, "press", { entity_id: entityId });
        } else {
          this._moreInfo(entityId);
        }
      });
    }
  }

  // ---------------------------------------------------------------- Personen-Karte
  const PERSON_CSS = `
    :host {
      display: block;
    }
    ha-card {
      display: block;
      padding: 14px;
      height: 100%;
      box-sizing: border-box;
    }
    .title {
      text-align: center;
      font-size: 22px;
      font-weight: 400;
      color: var(--ha-card-header-color, var(--primary-text-color));
      margin: 2px 0 12px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 10px;
    }
    .person {
      position: relative;
      border-radius: calc(var(--ha-card-border-radius, 12px) * 0.75);
      background: color-mix(in srgb, var(--primary-text-color, #fff) 5%, transparent);
      padding: 14px 12px 12px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      cursor: pointer;
      min-width: 0;
      transition: transform 0.12s ease;
    }
    .person:active {
      transform: scale(0.98);
    }
    .avatar {
      width: 62px;
      height: 62px;
      border-radius: 50%;
      box-shadow: 0 0 0 3px var(--bs-ring), 0 0 16px color-mix(in srgb, var(--bs-ring) 45%, transparent);
      background: color-mix(in srgb, var(--primary-color) 30%, transparent);
      display: grid;
      place-items: center;
      font-size: 22px;
      font-weight: 600;
      color: var(--primary-text-color);
      overflow: hidden;
      margin-bottom: 6px;
      flex: none;
    }
    .avatar img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .away .avatar {
      filter: grayscale(0.75);
      opacity: 0.75;
    }
    .pname {
      font-size: 16px;
      font-weight: 500;
      color: var(--primary-text-color);
      max-width: 100%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .status {
      font-size: 13px;
      color: var(--bs-ring);
      font-weight: 500;
      text-align: center;
    }
    .since {
      font-size: 11.5px;
      color: var(--secondary-text-color);
    }
    .battery {
      width: 100%;
      margin-top: 8px;
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--secondary-text-color);
    }
    .bar {
      flex: 1;
      height: 6px;
      border-radius: 3px;
      background: color-mix(in srgb, var(--primary-text-color, #fff) 12%, transparent);
      overflow: hidden;
    }
    .bar i {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: var(--bs-bat);
    }
    .battery ha-icon {
      --mdc-icon-size: 16px;
      color: var(--bs-bat);
    }
    .details {
      width: 100%;
      margin-top: 4px;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .detail {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--secondary-text-color);
      min-width: 0;
    }
    .detail ha-state-icon {
      --mdc-icon-size: 15px;
      flex: none;
    }
    .detail span {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .warn {
      color: var(--error-color, #db4437);
      padding: 8px;
    }
  `;
  const PERSON_DETAIL_SUFFIXES = ["wifi_connection", "bluetooth_connection"];

  function relativeSince(iso, lang) {
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!Number.isFinite(diff) || diff < 0) return "";
    const rtf = new Intl.RelativeTimeFormat(lang || "de", { numeric: "auto" });
    if (diff < 60) return rtf.format(-Math.round(diff), "second");
    if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
    if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
    return rtf.format(-Math.round(diff / 86400), "day");
  }

  class BolliSoftPersonCard extends BolliSoftCard {
    static getConfigElement() {
      return document.createElement("bollisoft-person-card-editor");
    }

    static getStubConfig(hass) {
      const persons = Object.keys(hass?.states || {}).filter((id) => id.startsWith("person.")).slice(0, 4);
      return { title: "Wer ist da?", persons };
    }

    setConfig(config) {
      if (!config || !Array.isArray(config.persons) || !config.persons.length) {
        throw new Error("Bitte mindestens eine Person wählen (persons)");
      }
      this._config = { ...config, persons: config.persons.map(entityConf) };
      this._render();
    }

    getCardSize() {
      return 1 + Math.ceil(this._config.persons.length / 2) * 3;
    }

    getGridOptions() {
      return { columns: 12, min_columns: 6 };
    }

    connectedCallback() {
      super.connectedCallback();
      // „vor … Minuten“ aktuell halten
      this._clock = setInterval(() => this._render(), 60 * 1000);
    }

    disconnectedCallback() {
      super.disconnectedCallback();
      clearInterval(this._clock);
    }

    /** Sensoren des Handys über den Tracker der Person finden (Namen der Companion-App). */
    _resolve(conf) {
      const states = this._hass.states;
      const stateObj = states[conf.entity];
      const trackers = stateObj?.attributes.device_trackers || [];
      const tracker = conf.device
        || trackers.find((t) => t.startsWith("device_tracker.") && states[`sensor.${t.split(".")[1]}_battery_level`])
        || trackers.find((t) => t.startsWith("device_tracker."));
      const base = tracker ? tracker.split(".")[1] : "";
      const first = (...ids) => ids.find((id) => id && states[id]);
      const battery = conf.battery === false ? null
        : first(conf.battery, base && `sensor.${base}_battery_level`, base && `sensor.${base}_battery`);
      const charging = conf.charging === false ? null
        : first(conf.charging, base && `binary_sensor.${base}_is_charging`, base && `sensor.${base}_battery_state`);
      const details = conf.details !== undefined
        ? (conf.details || []).filter((id) => states[id])
        : PERSON_DETAIL_SUFFIXES.map((s) => base && `sensor.${base}_${s}`).filter((id) => id && states[id]);
      return { stateObj, battery, charging, details, room: conf.room };
    }

    _watched() {
      const ids = [];
      for (const conf of this._config.persons) {
        const r = this._hass ? this._resolve(conf) : { details: [] };
        ids.push(conf.entity, r.battery, r.charging, r.room, ...r.details);
      }
      return ids.filter(Boolean);
    }

    _status(stateObj, roomId) {
      const s = stateObj.state;
      if (s === "home") {
        const room = roomId ? this._hass.states[roomId] : null;
        const roomOk = room && !["unknown", "unavailable", "not_home", "away", ""].includes(room.state);
        return { home: true, text: roomOk ? `Zuhause · ${formatState(this._hass, room)}` : "Zuhause" };
      }
      if (s === "not_home") return { home: false, text: "Unterwegs" };
      if (["unknown", "unavailable"].includes(s)) return { home: false, text: "Unbekannt" };
      return { home: false, text: s, zone: true };
    }

    _person(conf) {
      const r = this._resolve(conf);
      const stateObj = r.stateObj;
      if (!stateObj) return `<div class="person"><div class="warn">Nicht gefunden: ${esc(conf.entity)}</div></div>`;
      const name = conf.name || stateObj.attributes.friendly_name || conf.entity;
      const status = this._status(stateObj, r.room);
      const ring = status.home ? "var(--success-color, #4ade80)"
        : status.zone ? "var(--warning-color, #fbbf24)" : "var(--disabled-text-color, #8a8a8a)";
      const picture = stateObj.attributes.entity_picture;
      const initials = name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
      const avatar = picture ? `<img src="${esc(picture)}" alt="">` : esc(initials);
      let battery = "";
      if (r.battery) {
        const level = Number(this._hass.states[r.battery].state);
        if (Number.isFinite(level)) {
          const ch = r.charging ? this._hass.states[r.charging] : null;
          const charging = Boolean(ch && (ch.state === "on" || /^charging$/i.test(ch.state)));
          const color = charging || level >= 50 ? "var(--success-color, #4ade80)"
            : level < 20 ? "var(--error-color, #f87171)" : "var(--warning-color, #fbbf24)";
          battery = `<div class="battery" style="--bs-bat:${color}" title="${charging ? "Lädt" : "Akku"}">
            <ha-icon icon="${charging ? "mdi:battery-charging" : "mdi:battery"}"></ha-icon>
            <div class="bar"><i style="width:${Math.max(3, Math.min(100, level))}%"></i></div><span>${Math.round(level)} %</span></div>`;
        }
      }
      const details = r.details.map((id) => {
        const d = this._hass.states[id];
        return `<div class="detail" title="${esc(d.attributes.friendly_name || id)}"><ha-state-icon data-entity="${esc(id)}"></ha-state-icon><span>${esc(formatState(this._hass, d))}</span></div>`;
      }).join("");
      return `
        <div class="person ${status.home ? "home" : "away"}" data-entity="${esc(conf.entity)}" style="--bs-ring:${ring}">
          <div class="avatar">${avatar}</div>
          <div class="pname">${esc(name)}</div>
          <div class="status">${esc(status.text)}</div>
          <div class="since">${esc(relativeSince(stateObj.last_changed, this._hass.locale?.language))}</div>
          ${battery}
          ${details ? `<div class="details">${details}</div>` : ""}
        </div>`;
    }

    _render() {
      if (!this._config || !this._hass) return;
      const cfg = this._config;
      this.shadowRoot.innerHTML = `
        <style>${PERSON_CSS}</style>
        <ha-card>
          ${cfg.title ? `<div class="title">${esc(cfg.title)}</div>` : ""}
          <div class="grid">${cfg.persons.map((p) => this._person(p)).join("")}</div>
        </ha-card>`;
      this.shadowRoot.querySelectorAll("ha-state-icon[data-entity]").forEach((el) => {
        el.hass = this._hass;
        el.stateObj = this._hass.states[el.dataset.entity];
      });
      this.shadowRoot.querySelectorAll(".person[data-entity]").forEach((el) => {
        el.addEventListener("click", () => this._moreInfo(el.dataset.entity));
      });
    }
  }

  // ---------------------------------------------------------------- Editoren
  class BolliSoftEditor extends HTMLElement {
    setConfig(config) {
      this._config = config;
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      if (this._form) this._form.hass = hass;
      else this._render();
    }

    _render() {
      if (!this._hass || !this._config) return;
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.computeLabel = (s) => this.constructor.labels[s.name] || s.name;
        this._form.computeHelper = (s) => this.constructor.helpers?.[s.name] || "";
        this._form.addEventListener("value-changed", (ev) => {
          ev.stopPropagation();
          this._config = this._toConfig(ev.detail.value);
          fireEvent(this, "config-changed", { config: this._config });
        });
        this.appendChild(this._form);
      }
      this._form.hass = this._hass;
      this._form.schema = this.constructor.schema;
      this._form.data = this._toData(this._config);
    }

    _toData(config) {
      return config;
    }

    _toConfig(data) {
      const out = { ...data };
      for (const key of Object.keys(out)) {
        if (out[key] === "" || out[key] === undefined) delete out[key];
      }
      return out;
    }
  }

  class GlowEditor extends BolliSoftEditor {}
  GlowEditor.schema = [
    { name: "entity", required: true, selector: { entity: { domain: "sensor" } } },
    { name: "name", selector: { text: {} } },
    {
      type: "grid", name: "", schema: [
        { name: "icon", selector: { icon: {} } },
        { name: "color", selector: { select: { mode: "dropdown", options: COLOR_OPTIONS } } },
      ],
    },
    {
      type: "grid", name: "", schema: [
        { name: "hours", selector: { number: { min: 1, max: 168, mode: "box", unit_of_measurement: "h" } } },
        { name: "animation", selector: { boolean: {} } },
      ],
    },
  ];
  GlowEditor.labels = { entity: "Sensor", name: "Name", icon: "Symbol", color: "Farbe", hours: "Verlauf (Stunden)", animation: "Leuchten pulsiert" };

  class RoomEditor extends BolliSoftEditor {
    _toData(config) {
      return { ...config, entities: (config.entities || []).map((e) => entityConf(e).entity).filter(Boolean) };
    }

    _toConfig(data) {
      const out = super._toConfig(data);
      // eigene Angaben (Symbol, Name) zu Knöpfen behalten, die noch in der Liste sind
      const old = new Map((this._config.entities || []).map((e) => [entityConf(e).entity, e]));
      out.entities = (data.entities || []).map((id) => old.get(id) || id);
      return out;
    }
  }
  RoomEditor.schema = [
    {
      type: "grid", name: "", schema: [
        { name: "name", required: true, selector: { text: {} } },
        { name: "icon", selector: { icon: {} } },
      ],
    },
    {
      type: "grid", name: "", schema: [
        { name: "temperature", selector: { entity: { domain: "sensor", device_class: "temperature" } } },
        { name: "humidity", selector: { entity: { domain: "sensor", device_class: "humidity" } } },
      ],
    },
    { name: "entities", selector: { entity: { multiple: true } } },
    {
      type: "grid", name: "", schema: [
        { name: "color", selector: { select: { mode: "dropdown", options: COLOR_OPTIONS } } },
        { name: "hours", selector: { number: { min: 1, max: 168, mode: "box", unit_of_measurement: "h" } } },
      ],
    },
    { name: "navigation_path", selector: { text: {} } },
  ];
  RoomEditor.labels = {
    name: "Raum", icon: "Symbol", temperature: "Temperatur", humidity: "Luftfeuchte", entities: "Knöpfe (Licht, Schalter, Fenster …)",
    color: "Farbe", hours: "Verlauf (Stunden)", navigation_path: "Beim Antippen öffnen (z. B. /dashboard-haus/buero)",
  };
  RoomEditor.helpers = { entities: "Antippen schaltet um, lange drücken zeigt Details" };

  class PersonEditor extends BolliSoftEditor {
    _toData(config) {
      return { ...config, persons: (config.persons || []).map((e) => entityConf(e).entity).filter(Boolean) };
    }

    _toConfig(data) {
      const out = super._toConfig(data);
      const old = new Map((this._config.persons || []).map((e) => [entityConf(e).entity, e]));
      out.persons = (data.persons || []).map((id) => old.get(id) || id);
      return out;
    }
  }
  PersonEditor.schema = [
    { name: "title", selector: { text: {} } },
    { name: "persons", required: true, selector: { entity: { domain: "person", multiple: true } } },
  ];
  PersonEditor.labels = { title: "Überschrift", persons: "Personen" };
  PersonEditor.helpers = { persons: "Akku, Laden, WLAN und Bluetooth kommen automatisch aus der Companion-App des Handys" };

  const define = (name, cls) => {
    if (!customElements.get(name)) customElements.define(name, cls);
  };
  define("bollisoft-glow-card", BolliSoftGlowCard);
  define("bollisoft-room-card", BolliSoftRoomCard);
  define("bollisoft-person-card", BolliSoftPersonCard);
  define("bollisoft-glow-card-editor", GlowEditor);
  define("bollisoft-room-card-editor", RoomEditor);
  define("bollisoft-person-card-editor", PersonEditor);

  window.customCards = window.customCards || [];
  for (const card of [
    { type: "bollisoft-glow-card", name: "BolliSoft Glow-Karte", description: "Sensor mit leuchtendem Rand und Verlauf" },
    { type: "bollisoft-room-card", name: "BolliSoft Raum-Karte", description: "Raum mit Temperatur, Luftfeuchte, Verlauf und Schaltknöpfen" },
    { type: "bollisoft-person-card", name: "BolliSoft Personen-Karte", description: "Wer ist da? Personen mit Ort, Akku und Handy-Status" },
  ]) {
    if (!window.customCards.some((c) => c.type === card.type)) {
      window.customCards.push({ ...card, preview: true, documentationURL: "https://github.com/Devilwitha/HAaddons/tree/main/bollisoft_themes" });
    }
  }
  console.info(`%c BolliSoft Karten %c ${VERSION} `, "background:#a855f7;color:#fff;border-radius:4px 0 0 4px;padding:2px 4px",
    "background:#38bdf8;color:#0b1120;border-radius:0 4px 4px 0;padding:2px 4px");
})();
