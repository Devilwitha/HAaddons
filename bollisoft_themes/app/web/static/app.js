"use strict";

const $ = (id) => document.getElementById(id);
const SWATCHES = ["#38bdf8", "#3b82f6", "#6366f1", "#8b5cf6", "#a855f7", "#ec4899", "#f43f5e", "#ef4444",
  "#f97316", "#f59e0b", "#eab308", "#84cc16", "#22c55e", "#10b981", "#14b8a6", "#06b6d4"];
const DEFAULT_SURFACES = {
  dark: { background: "#0b1120", card: "#1e293b", text: "#f1f5f9" },
  light: { background: "#f1f5f9", card: "#ffffff", text: "#0f172a" },
};
const UNITS = { intensity: "%", card_opacity: "%", radius: " px", border: " px", shadow: "%", blur: " px" };
const INTENSITY_LABEL = { glow: "Leuchtkraft", gradient: "Verlaufsstärke", image: "Abdunkeln" };
const S = { state: null, edit: null, previewMode: "dark", surfaceMode: "dark", dirty: false, lastYaml: "", timer: 0, seq: 0 };

// ------------------------------------------------------------------ Hilfen
function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function icon(name) {
  return `<svg><use href="#${name}"/></svg>`;
}

let toastTimer = 0;
function toast(text, error = false) {
  const el = $("toast");
  el.textContent = text;
  el.className = "show" + (error ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, error ? 6000 : 3200);
}

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const resp = await fetch(path, opts);
  if (!resp.ok) throw new Error((await resp.text()) || `Fehler ${resp.status}`);
  return resp.json();
}

// Farben (HSL) für Zufallsdesign und „Flächen aus Hauptfarbe“
function hexToHsl(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s, l];
}

function hsl(h, s, l) {
  h = ((h % 360) + 360) % 360;
  s = Math.max(0, Math.min(1, s));
  l = Math.max(0, Math.min(1, l));
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1))));
  return "#" + [f(0), f(8), f(4)].map((v) => v.toString(16).padStart(2, "0")).join("");
}

function surfacesFrom(primary, mode) {
  const [h, s] = hexToHsl(primary);
  const sat = Math.min(s, 0.6);
  return mode === "dark"
    ? { background: hsl(h, sat * 0.7, 0.06), card: hsl(h, sat * 0.5, 0.12), text: hsl(h, 0.35, 0.95) }
    : { background: hsl(h, sat * 0.6, 0.96), card: "#ffffff", text: hsl(h, 0.4, 0.12) };
}

// ------------------------------------------------------------------ Vorschau
const PREVIEW_HTML = `
  <aside class="pv-side">
    <div class="pv-title">Home Assistant</div>
    <div class="pv-nav sel">${icon("p-home")}Übersicht</div>
    <div class="pv-nav">${icon("p-bolt")}Energie</div>
    <div class="pv-nav">${icon("p-map")}Karte</div>
    <div class="pv-nav">${icon("p-chart")}Verlauf</div>
    <div class="pv-nav bottom">${icon("p-gear")}Einstellungen</div>
  </aside>
  <div class="pv-main">
    <div class="pv-bg"></div>
    <div class="pv-head"><b>Zuhause</b><span class="pv-tab sel">Wohnzimmer</span><span class="pv-tab">Küche</span><span class="pv-tab">Garten</span></div>
    <div class="pv-view">
      <div class="pv-badges">
        <div class="pv-badge"><span class="pv-ico on" style="--pv-c:var(--state-person-home-color);width:24px;height:24px">${icon("p-person")}</span>Zuhause</div>
        <div class="pv-badge"><span class="pv-ico" style="width:24px;height:24px">${icon("p-thermo")}</span>21,5 °C</div>
        <div class="pv-badge"><span class="pv-ico on" style="--pv-c:var(--state-light-active-color);width:24px;height:24px">${icon("p-bulb")}</span>3 Lichter an</div>
      </div>
      <div class="pv-cols">
        <div class="pv-col">
          <div class="pv-tiles">
            <div class="pv-card pv-tile"><span class="pv-ico on" style="--pv-c:var(--state-light-active-color)">${icon("p-bulb")}</span><div><div class="pv-name">Decke</div><div class="pv-state">An · 80 %</div></div></div>
            <div class="pv-card pv-tile"><span class="pv-ico">${icon("p-lamp")}</span><div><div class="pv-name">Stehlampe</div><div class="pv-state">Aus</div></div></div>
          </div>
          <div class="pv-card">
            <h4>Wohnzimmer</h4>
            <div class="pv-row"><span class="pv-ico">${icon("p-plug")}</span><span class="pv-name">Fernseher</span><span class="pv-sw on"></span></div>
            <div class="pv-row"><span class="pv-ico">${icon("p-fan")}</span><span class="pv-name">Ventilator</span><span class="pv-sw"></span></div>
            <div class="pv-row"><span class="pv-ico">${icon("p-lamp")}</span><span class="pv-name">Leselicht</span><span class="pv-sw on"></span></div>
          </div>
        </div>
        <div class="pv-col">
          <div class="pv-card">
            <h4>Heizung</h4>
            <div class="pv-temp">21,5<small>°C</small></div>
            <div class="pv-slider"><i></i></div>
            <div class="pv-chips"><span class="pv-chip ok">Fenster zu</span><span class="pv-chip warn">Akku 20 %</span><span class="pv-chip err">Offline</span></div>
          </div>
          <div class="pv-btns">
            <div class="pv-card pv-btn">${icon("p-home")}Auf</div>
            <div class="pv-card pv-btn">${icon("p-lamp")}Licht</div>
          </div>
        </div>
        <div class="pv-col">
          <div class="pv-card">
            <h4>Stromverbrauch</h4>
            <svg class="pv-graph" viewBox="0 0 200 70" preserveAspectRatio="none">
              <path d="M0 52 C20 48 30 30 50 34 S80 58 100 44 S140 12 160 22 S190 40 200 30" style="stroke:var(--graph-color-1, var(--primary-color))"/>
              <path d="M0 60 C25 58 40 50 60 54 S95 40 120 46 S165 56 200 48" style="stroke:var(--graph-color-2, var(--accent-color))"/>
            </svg>
            <div class="pv-legend"><span><i style="background:var(--graph-color-1, var(--primary-color))"></i>Haus</span><span><i style="background:var(--graph-color-2, var(--accent-color))"></i>Solar</span></div>
          </div>
          <div class="pv-card">
            <h4>Szene</h4>
            <div class="pv-input"><small>Name</small>Filmabend</div>
            <span class="pv-action">Aktivieren</span>
          </div>
        </div>
      </div>
    </div>
  </div>`;

function previewElement() {
  const el = document.createElement("div");
  el.className = "pv";
  el.innerHTML = PREVIEW_HTML;
  return el;
}

function applyVars(el, vars, params) {
  const style = [];
  for (const [key, value] of Object.entries(vars || {})) {
    style.push(`--${key}: ${value}`);
    // Home Assistant erzeugt rgb-…-Werte selbst aus Hex-Farben
    if (/^#[0-9a-f]{6}$/i.test(value) && !(`rgb-${key}` in vars)) {
      const n = parseInt(value.slice(1), 16);
      style.push(`--rgb-${key}: ${n >> 16 & 255}, ${n >> 8 & 255}, ${n & 255}`);
    }
  }
  const lovelace = vars?.["lovelace-background"];
  if (lovelace) {
    // vmax bezieht sich in der kleinen Vorschau auf ihre eigene Größe, „fixed“ würde verrutschen
    style.push(`--pv-lovelace: ${lovelace.replace(/(\d+(?:\.\d+)?)vmax/g, "$1cqmax").replace(/\bfixed\b/g, "")}`);
  }
  if (params?.blur) style.push(`--pv-blur-filter: blur(${params.blur}px)`);
  const transform = el.style.transform;
  el.setAttribute("style", style.join("; "));
  el.style.transform = transform;
  el.classList.toggle("fx-hover", Boolean(params?.hover));
  el.classList.toggle("fx-gradient", Boolean(params?.gradient_buttons));
}

const scaler = new ResizeObserver((entries) => {
  for (const entry of entries) {
    const pv = entry.target.querySelector(".pv");
    if (!pv) continue;
    pv.style.transform = `scale(${entry.contentRect.width / 960})`;
  }
});

function modesOf(theme) {
  return Object.keys(theme.modes);
}

// ------------------------------------------------------------------ Galerie
async function load() {
  try {
    S.state = await api("api/state");
  } catch (err) {
    toast(`Laden fehlgeschlagen: ${err.message}`, true);
    return;
  }
  $("version").textContent = S.state.version;
  renderBanners();
  renderGallery();
  if (!$("tab-cards").hidden && window.renderCardsTab) renderCardsTab();
}

function renderBanners() {
  const st = S.state;
  const out = [];
  if (st.setup.state === "missing") {
    out.push(`<div class="banner"><p><b>Themes sind in Home Assistant noch nicht eingeschaltet.</b>
      <small>In der configuration.yaml fehlt <code>frontend: themes: !include_dir_merge_named themes</code>. Das Add-on kann die Zeile eintragen (mit Sicherung der Datei).</small></p>
      <button class="btn primary" data-act="fix">Automatisch eintragen</button></div>`);
  } else if (st.setup.state === "unsupported") {
    out.push(`<div class="banner err"><p><b>Theme-Ordner nicht gefunden:</b> ${esc(st.setup.detail)}
      <small>Nötig ist in der configuration.yaml unter <code>frontend:</code> die Zeile <code>themes: !include_dir_merge_named themes</code>.</small></p></div>`);
  }
  if (st.restart_needed) {
    out.push(`<div class="banner"><p><b>Home Assistant einmal neu starten,</b> damit die Themes erscheinen.
      <small>Die configuration.yaml wird vorher geprüft.</small></p>
      <button class="btn primary" data-act="restart">Jetzt neu starten</button></div>`);
  }
  for (const [name, path] of Object.entries(st.conflicts || {})) {
    out.push(`<div class="banner"><p><b>„${esc(name)}“ gibt es doppelt:</b> zusätzlich in <code>themes/${esc(path)}</code> (alte Kopie).
      <small>Die beiden überschreiben sich gegenseitig. Die alte Datei wird nur umbenannt (<code>.bak</code>), nicht gelöscht.</small></p>
      <button class="btn" data-act="conflict" data-name="${esc(name)}">Alte Datei abschalten</button></div>`);
  }
  if (st.error) out.push(`<div class="banner err"><p>${esc(st.error)}</p></div>`);
  if (!st.ha.ok && st.setup.state === "ok") {
    out.push(`<div class="banner err"><p><b>Keine Verbindung zu Home Assistant:</b> ${esc(st.ha.error)}
      <small>Die Dateien werden trotzdem geschrieben; neu laden mit der Aktion <code>frontend.reload_themes</code>.</small></p></div>`);
  }
  $("banners").innerHTML = out.join("");
}

function themeCard(entry) {
  const st = S.state;
  const modes = modesOf(entry.theme);
  const loaded = st.ha.loaded.includes(entry.name);
  const isDefault = st.ha.default_theme === entry.name || st.ha.default_dark_theme === entry.name;
  const fx = entry.params && (entry.params.blur || entry.params.hover || entry.params.gradient_buttons);
  const modeLabel = modes.length === 2 ? "Hell + Dunkel" : modes[0] === "dark" ? "Dunkel" : "Hell";
  const badges = [`<span class="badge">${modeLabel}</span>`];
  if (isDefault) badges.unshift(`<span class="badge star">★ Standard</span>`);
  if (fx) badges.push(`<span class="badge" title="Effekte mit card-mod aus HACS">Effekte</span>`);
  if (entry.kind === "yaml") badges.push(`<span class="badge">YAML</span>`);
  if (entry.enabled && st.ha.ok) {
    badges.push(loaded ? `<span class="badge ok">in Home Assistant</span>` : `<span class="badge warn">noch nicht geladen</span>`);
  }
  const editLabel = entry.kind === "builtin" ? `${icon("i-copy")}Als Vorlage` : `${icon("i-edit")}Bearbeiten`;
  const el = document.createElement("article");
  el.className = "tcard" + (entry.enabled ? "" : " off");
  el.innerHTML = `
    <div class="mini" title="${entry.kind === "builtin" ? "Als Vorlage für ein eigenes Theme verwenden" : "Bearbeiten"}"></div>
    <div class="tbody">
      <div class="thead"><h3 title="${esc(entry.name)}">${esc(entry.name)}</h3>
        <label class="toggle" title="In Home Assistant installiert"><input type="checkbox" ${entry.enabled ? "checked" : ""}><span></span></label></div>
      ${entry.description ? `<p class="tdesc">${esc(entry.description)}</p>` : ""}
      <div class="badges">${badges.join("")}</div>
      <div class="tactions">
        <button class="btn small" data-a="edit">${editLabel}</button>
        ${isDefault
          ? `<button class="btn small ghost" data-a="undefault" title="Wieder das Standard-Design von Home Assistant">Standard aufheben</button>`
          : `<button class="btn small ghost" data-a="default" ${entry.enabled ? "" : "disabled"} title="Für alle Geräte mit Design „Backend-ausgewählt“">${icon("i-star")}Als Standard</button>`}
        <a class="btn small ghost icon" href="api/download/${encodeURIComponent(entry.id)}" download title="YAML herunterladen">${icon("i-download")}</a>
        ${entry.kind === "builtin" ? "" : `<button class="btn small ghost icon danger" data-a="delete" title="Löschen">${icon("i-trash")}</button>`}
      </div>
    </div>`;
  const mini = el.querySelector(".mini");
  const pv = previewElement();
  mini.appendChild(pv);
  const show = (mode) => {
    applyVars(pv, entry.theme.modes[mode], entry.params);
    mini.querySelectorAll(".mini-modes button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  };
  if (modes.length === 2) {
    const sw = document.createElement("div");
    sw.className = "mini-modes";
    sw.innerHTML = `<button data-mode="light" title="Hell">${icon("i-sun")}</button><button data-mode="dark" title="Dunkel">${icon("i-moon")}</button>`;
    sw.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const b = ev.target.closest("button");
      if (b) show(b.dataset.mode);
    });
    mini.appendChild(sw);
  }
  show(modes.includes("dark") ? "dark" : modes[0]);
  scaler.observe(mini);
  mini.addEventListener("click", () => editEntry(entry));
  el.querySelector(".toggle input").addEventListener("change", async (ev) => {
    try {
      await api("api/theme", { action: "enable", id: entry.id, value: ev.target.checked });
      toast(ev.target.checked ? `„${entry.name}“ installiert` : `„${entry.name}“ entfernt`);
    } catch (err) {
      ev.target.checked = !ev.target.checked;
      toast(err.message, true);
    }
    load();
  });
  el.querySelector(".tactions").addEventListener("click", async (ev) => {
    const b = ev.target.closest("button[data-a]");
    if (!b) return;
    const act = b.dataset.a;
    if (act === "edit") return editEntry(entry);
    try {
      if (act === "default") {
        await api("api/theme", { action: "default", id: entry.id });
        toast(`„${entry.name}“ ist jetzt Standard`);
      } else if (act === "undefault") {
        await api("api/theme", { action: "default_reset" });
        toast("Standard zurückgesetzt");
      } else if (act === "delete") {
        if (!confirm(`„${entry.name}“ löschen?`)) return;
        await api("api/theme", { action: "delete", id: entry.id });
        toast(`„${entry.name}“ gelöscht`);
      }
    } catch (err) {
      toast(err.message, true);
    }
    load();
  });
  return el;
}

function renderGallery() {
  const own = S.state.themes.filter((t) => t.kind !== "builtin");
  const builtin = S.state.themes.filter((t) => t.kind === "builtin");
  const ownGrid = $("own-grid");
  ownGrid.replaceChildren(...own.map(themeCard));
  if (!own.length) {
    ownGrid.innerHTML = `<div class="empty">Noch keine eigenen Themes. Mit <b>Neues Theme</b> anfangen oder bei einem Design unten <b>Als Vorlage</b> wählen.</div>`;
  }
  $("builtin-grid").replaceChildren(...builtin.map(themeCard));
}

$("banners").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-act]");
  if (!b) return;
  b.disabled = true;
  try {
    if (b.dataset.act === "fix") {
      const r = await api("api/setup/fix", {});
      toast(r.backup ? `Eingetragen (Sicherung: ${r.backup})` : "Eingetragen");
    } else if (b.dataset.act === "restart") {
      if (!confirm("Home Assistant jetzt neu starten? Das dauert etwa eine Minute.")) {
        b.disabled = false;
        return;
      }
      await api("api/setup/restart", {});
      toast("Home Assistant startet neu …");
      waitForRestart();
      return;
    } else if (b.dataset.act === "conflict") {
      const r = await api("api/conflict", { name: b.dataset.name });
      toast(`Umbenannt: ${r.renamed}`);
    }
  } catch (err) {
    toast(err.message, true);
  }
  load();
});

async function waitForRestart() {
  await new Promise((r) => setTimeout(r, 15000));
  for (let i = 0; i < 40; i++) {
    try {
      const st = await api("api/state");
      if (st.ha.ok && !st.restart_needed) {
        toast("Home Assistant ist wieder da");
        return load();
      }
    } catch (err) {
      // Home Assistant startet noch
    }
    await new Promise((r) => setTimeout(r, 5000));
  }
  load();
}

// ------------------------------------------------------------------ Editor
const editorPreview = previewElement();
$("preview").appendChild(editorPreview);
scaler.observe($("preview"));

function seg(id, value) {
  $(id).querySelectorAll("button").forEach((b) => b.classList.toggle("on", (b.dataset.v ?? b.dataset.mode) === value));
}

function buildColorFields() {
  document.querySelectorAll(".color-field").forEach((field) => {
    const key = field.dataset.key;
    const swatches = key === "primary" || key === "accent"
      ? `<div class="swatches">${SWATCHES.map((c) => `<button type="button" style="background:${c}" data-c="${c}" title="${c}"></button>`).join("")}</div>`
      : "<span></span>";
    field.insertAdjacentHTML("beforeend", `<input type="color"><input class="hex" maxlength="7" spellcheck="false">${swatches}`);
    const picker = field.querySelector("input[type=color]");
    const hex = field.querySelector(".hex");
    const set = (value) => {
      if (!/^#[0-9a-f]{6}$/i.test(value)) return;
      picker.value = value.toLowerCase();
      hex.value = value.toLowerCase();
      setColor(key, value.toLowerCase());
    };
    picker.addEventListener("input", () => set(picker.value));
    hex.addEventListener("input", () => set(hex.value.startsWith("#") ? hex.value : "#" + hex.value));
    field.querySelectorAll(".swatches button").forEach((b) => b.addEventListener("click", () => set(b.dataset.c)));
  });
}

function getColor(key) {
  const p = S.edit.params;
  return key === "primary" || key === "accent" ? p[key] : p[S.surfaceMode][key];
}

function setColor(key, value) {
  const p = S.edit.params;
  if (key === "primary" || key === "accent") p[key] = value;
  else p[S.surfaceMode][key] = value;
  changed();
}

function fillColors() {
  document.querySelectorAll(".color-field").forEach((field) => {
    const value = getColor(field.dataset.key);
    field.querySelector("input[type=color]").value = value;
    field.querySelector(".hex").value = value;
  });
}

const RANGES = ["intensity", "card_opacity", "radius", "border", "shadow", "blur"];
const CHECKS = ["border_accent", "hover", "gradient_buttons"];

function showRange(key) {
  const input = $(`f-${key}`);
  input.parentElement.querySelector("output").textContent = input.value + UNITS[key];
}

function fillForm() {
  const p = S.edit.params;
  $("f-name").value = p.name;
  seg("f-modes", p.modes.length === 2 ? "both" : p.modes[0]);
  seg("f-background", p.background);
  seg("f-header", p.header);
  seg("f-sidebar", p.sidebar);
  $("f-font").value = p.font;
  $("f-image").value = p.background_image || "";
  for (const key of RANGES) {
    $(`f-${key}`).value = p[key];
    showRange(key);
  }
  for (const key of CHECKS) $(`f-${key}`).checked = Boolean(p[key]);
  renderOverrides();
  updateModeUi();
  fillColors();
  updateBackgroundUi();
}

function updateModeUi() {
  const modes = S.edit.kind === "editor" ? S.edit.params.modes : (S.yamlModes || ["dark"]);
  if (!modes.includes(S.surfaceMode)) S.surfaceMode = modes[0];
  if (!modes.includes(S.previewMode)) S.previewMode = modes[0];
  for (const id of ["surface-mode", "preview-mode"]) {
    $(id).querySelectorAll("button").forEach((b) => { b.disabled = !modes.includes(b.dataset.mode); });
  }
  seg("surface-mode", S.surfaceMode);
  seg("preview-mode", S.previewMode);
  $("surface-mode").hidden = modes.length < 2;
}

function updateBackgroundUi() {
  const bg = S.edit.params.background;
  $("image-row").hidden = bg !== "image";
  $("f-intensity").parentElement.hidden = bg === "flat";
  $("intensity-label").textContent = INTENSITY_LABEL[bg] || "Stärke";
}

function renderOverrides() {
  const box = $("overrides");
  box.innerHTML = "";
  for (const [key, value] of Object.entries(S.edit.params.overrides || {})) addOverrideRow(key, value);
}

function addOverrideRow(key = "", value = "") {
  const row = document.createElement("div");
  row.className = "override";
  row.innerHTML = `<input placeholder="variable" list="var-list" value="${esc(key)}"><input placeholder="Wert" value="${esc(value)}">
    <button type="button" class="btn icon ghost" title="Entfernen">${icon("i-x")}</button>`;
  row.querySelectorAll("input").forEach((i) => i.addEventListener("input", readOverrides));
  row.querySelector("button").addEventListener("click", () => { row.remove(); readOverrides(); });
  $("overrides").appendChild(row);
  return row;
}

function readOverrides() {
  const out = {};
  $("overrides").querySelectorAll(".override").forEach((row) => {
    const [k, v] = row.querySelectorAll("input");
    if (k.value.trim() && v.value.trim()) out[k.value.trim().replace(/^--/, "")] = v.value.trim();
  });
  S.edit.params.overrides = out;
  changed();
}

function changed() {
  S.dirty = true;
  scheduleRender();
}

function scheduleRender() {
  clearTimeout(S.timer);
  S.timer = setTimeout(renderPreview, 90);
}

async function renderPreview() {
  const seq = ++S.seq;
  const note = $("preview-note");
  try {
    let result;
    if (S.edit.kind === "yaml") {
      result = await api("api/render", { yaml: $("f-yaml").value });
      if (seq !== S.seq) return;
      $("yaml-error").textContent = "";
      S.yamlModes = modesOf(result.theme);
      S.yamlName = result.theme.name;
      updateModeUi();
    } else {
      const params = { ...S.edit.params, name: S.edit.params.name.trim() || "Vorschau" };
      result = await api("api/render", { params });
      if (seq !== S.seq) return;
      S.lastYaml = result.yaml;
      $("var-list").innerHTML = Object.keys(Object.values(result.theme.modes)[0]).map((k) => `<option value="${k}">`).join("");
    }
    const mode = result.theme.modes[S.previewMode] ? S.previewMode : modesOf(result.theme)[0];
    applyVars(editorPreview, result.theme.modes[mode], S.edit.kind === "editor" ? S.edit.params : null);
    note.textContent = "";
  } catch (err) {
    if (seq !== S.seq) return;
    if (S.edit.kind === "yaml") $("yaml-error").textContent = err.message;
    else note.textContent = err.message;
  }
}

function openEditor(edit) {
  S.edit = edit;
  S.dirty = false;
  S.yamlModes = null;
  const isYaml = edit.kind === "yaml";
  $("form-editor").hidden = isYaml;
  $("form-yaml").hidden = !isYaml;
  $("yaml-btn").hidden = isYaml;
  $("editor-title").textContent = edit.id ? `${edit.title} bearbeiten` : edit.title;
  if (isYaml) {
    $("f-yaml").value = edit.yaml;
    $("yaml-error").textContent = "";
  } else {
    S.surfaceMode = edit.params.modes[0];
    S.previewMode = edit.params.modes.includes("dark") ? "dark" : edit.params.modes[0];
    fillForm();
  }
  $("gallery-view").hidden = true;
  $("editor-view").hidden = false;
  window.scrollTo(0, 0);
  renderPreview();
}

function closeEditor() {
  if (S.dirty && !confirm("Änderungen verwerfen?")) return;
  S.edit = null;
  $("editor-view").hidden = true;
  $("gallery-view").hidden = false;
  load();
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function editEntry(entry) {
  if (entry.kind === "builtin") {
    const params = clone(entry.params);
    params.name = `${entry.name} eigen`.slice(0, 48);
    openEditor({ id: null, kind: "editor", params, title: `Neu nach „${entry.name}“` });
  } else if (entry.kind === "yaml") {
    openEditor({ id: entry.id, kind: "yaml", yaml: entry.yaml, title: entry.name });
  } else {
    openEditor({ id: entry.id, kind: "editor", params: clone(entry.params), title: entry.name });
  }
}

function newTheme() {
  const base = S.state.themes.find((t) => t.id === "graphit") || S.state.themes[0];
  const params = clone(base.params);
  params.name = "Mein Theme";
  params.modes = ["dark"];
  params.background = "glow";
  params.intensity = 50;
  params.radius = 18;
  openEditor({ id: null, kind: "editor", params, title: "Neues Theme" });
}

async function save() {
  const btn = $("save-btn");
  btn.disabled = true;
  try {
    const body = S.edit.kind === "yaml"
      ? { action: "save_yaml", id: S.edit.id, yaml: $("f-yaml").value }
      : { action: "save", id: S.edit.id, params: { ...S.edit.params, name: S.edit.params.name.trim() } };
    const r = await api("api/theme", body);
    S.edit.id = r.id;
    S.dirty = false;
    const name = S.edit.kind === "yaml" ? (S.yamlName || "Theme") : S.edit.params.name.trim();
    S.edit.title = name;
    $("editor-title").textContent = `${name} bearbeiten`;
    toast(r.written ? "Gespeichert – in Home Assistant unter Profil → Design auswählbar" : "Gespeichert (Themes in HA noch nicht eingerichtet)");
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// Eingaben im Formular
$("f-name").addEventListener("input", (ev) => { S.edit.params.name = ev.target.value; changed(); });
$("f-modes").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  const p = S.edit.params;
  const modes = b.dataset.v === "both" ? ["dark", "light"] : [b.dataset.v];
  for (const mode of modes) {
    // Flächen für einen neu dazugekommenen Modus passend zur Hauptfarbe anlegen
    if (!p.modes.includes(mode) && JSON.stringify(p[mode]) === JSON.stringify(DEFAULT_SURFACES[mode])) {
      p[mode] = surfacesFrom(p.primary, mode);
    }
  }
  p.modes = modes;
  if (modes.length === 1) S.previewMode = S.surfaceMode = modes[0];
  seg("f-modes", b.dataset.v);
  updateModeUi();
  fillColors();
  changed();
});
for (const [id, key] of [["f-background", "background"], ["f-header", "header"], ["f-sidebar", "sidebar"]]) {
  $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    S.edit.params[key] = b.dataset.v;
    seg(id, b.dataset.v);
    if (key === "background") updateBackgroundUi();
    changed();
  });
}
for (const key of RANGES) {
  $(`f-${key}`).addEventListener("input", (ev) => {
    S.edit.params[key] = Number(ev.target.value);
    showRange(key);
    changed();
  });
}
for (const key of CHECKS) {
  $(`f-${key}`).addEventListener("change", (ev) => { S.edit.params[key] = ev.target.checked; changed(); });
}
$("f-font").addEventListener("change", (ev) => { S.edit.params.font = ev.target.value; changed(); });
$("f-image").addEventListener("input", (ev) => { S.edit.params.background_image = ev.target.value.trim(); changed(); });
$("f-upload").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  if (file.size > 12 * 1024 * 1024) return toast("Bild größer als 12 MB", true);
  try {
    toast("Bild wird hochgeladen …");
    const resp = await fetch("api/upload", { method: "POST", headers: { "X-Filename": file.name }, body: file });
    if (!resp.ok) throw new Error(await resp.text());
    const r = await resp.json();
    S.edit.params.background_image = r.url;
    $("f-image").value = r.url;
    changed();
    toast(r.restart_needed ? "Hochgeladen – Home Assistant einmal neu starten, damit /local/ funktioniert" : "Bild hochgeladen");
  } catch (err) {
    toast(err.message, true);
  }
});
$("surface-mode").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b || b.disabled) return;
  S.surfaceMode = b.dataset.mode;
  S.previewMode = b.dataset.mode;
  updateModeUi();
  fillColors();
  scheduleRender();
});
$("preview-mode").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b || b.disabled) return;
  S.previewMode = b.dataset.mode;
  if (S.edit.kind === "editor") S.surfaceMode = b.dataset.mode;
  updateModeUi();
  if (S.edit.kind === "editor") fillColors();
  scheduleRender();
});
$("random-btn").addEventListener("click", () => {
  const p = S.edit.params;
  const h = Math.random() * 360;
  const offset = [35, 60, 150, 180, 210, 300][Math.floor(Math.random() * 6)];
  p.primary = hsl(h, 0.7 + Math.random() * 0.2, 0.55 + Math.random() * 0.08);
  p.accent = hsl(h + offset, 0.65 + Math.random() * 0.25, 0.58 + Math.random() * 0.08);
  for (const mode of p.modes) p[mode] = surfacesFrom(p.primary, mode);
  p.background = ["glow", "gradient", "flat", "glow"][Math.floor(Math.random() * 4)];
  p.intensity = 30 + Math.floor(Math.random() * 50);
  p.radius = [8, 12, 16, 20, 24, 28][Math.floor(Math.random() * 6)];
  p.card_opacity = 70 + Math.floor(Math.random() * 31);
  p.shadow = Math.floor(Math.random() * 70);
  p.font = ["system", "rounded", "serif", "condensed", "roboto"][Math.floor(Math.random() * 5)];
  fillForm();
  changed();
});
$("derive-btn").addEventListener("click", () => {
  const p = S.edit.params;
  const [h, s, l] = hexToHsl(p.primary);
  p.accent = hsl(h + 50, s, l);
  for (const mode of p.modes) p[mode] = surfacesFrom(p.primary, mode);
  fillColors();
  changed();
  toast("Zweitfarbe und Flächen aus der Hauptfarbe erzeugt");
});
$("add-override").addEventListener("click", () => addOverrideRow().querySelector("input").focus());
$("f-yaml").addEventListener("input", () => { S.dirty = true; scheduleRender(); });

// Kopf und Dialoge
$("new-btn").addEventListener("click", newTheme);
$("back-btn").addEventListener("click", closeEditor);
$("save-btn").addEventListener("click", save);
$("yaml-btn").addEventListener("click", () => {
  $("yaml-out").textContent = S.lastYaml;
  $("yaml-dialog").showModal();
});
$("yaml-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(S.lastYaml);
    toast("Kopiert");
  } catch (err) {
    const range = document.createRange();
    range.selectNodeContents($("yaml-out"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    toast("Markiert – mit Strg+C kopieren");
  }
});
$("import-btn").addEventListener("click", () => {
  $("import-text").value = "";
  $("import-dialog").showModal();
});
$("import-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (file) $("import-text").value = await file.text();
});
$("import-go").addEventListener("click", () => {
  const text = $("import-text").value.trim();
  if (!text) return toast("Erst ein Theme einfügen", true);
  $("import-dialog").close();
  openEditor({ id: null, kind: "yaml", yaml: text + "\n", title: "Importiertes Theme" });
  S.dirty = true;
});
window.addEventListener("beforeunload", (ev) => {
  if (S.edit && S.dirty) ev.preventDefault();
});

buildColorFields();
load();
