/* Emulator – Belegung für echte Controller (Xbox, PlayStation, Pro Controller u. a., per USB oder Bluetooth).
 *
 * EmulatorJS erkennt Controller selbst (Gamepad API) und speichert die Belegung pro Spiel.
 * Hier kommen Vorlagen dazu (aus system.js, EMU.controller), die für alle Spiele des Systems
 * auf diesem Gerät gelten, plus die Anzeige mit Xbox- oder PlayStation-Beschriftung.
 * Einzelne Tasten ändert man im Steuerungsmenü von EmulatorJS ("Einzelne Tasten ändern").
 */
(function () {
  "use strict";

  const EMU = window.EMU;
  const cfg = EMU && EMU.controller;
  if (!cfg) return;

  const KEY = EMU.id + "-pad-v1";
  const $ = (id) => document.getElementById(id);
  const panel = $("padpanel");
  const btn = $("padbtn");

  // Position im Standard-Layout der Gamepad API -> Name in EmulatorJS
  const EJS_NAMES = ["BUTTON_1", "BUTTON_2", "BUTTON_3", "BUTTON_4", "LEFT_TOP_SHOULDER", "RIGHT_TOP_SHOULDER",
    "LEFT_BOTTOM_SHOULDER", "RIGHT_BOTTOM_SHOULDER", "SELECT", "START", "LEFT_STICK", "RIGHT_STICK",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"];
  const LABELS = {
    xbox: ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu", "LS drücken", "RS drücken", "↑", "↓", "←", "→"],
    ps: ["✕", "○", "□", "△", "L1", "R1", "L2", "R2", "Create", "Options", "L3", "R3", "↑", "↓", "←", "→"],
    // Pro Controller: untere Taste heißt B, rechte A (Standard-Layout der Gamepad API nach Position)
    pro: ["B", "A", "Y", "X", "L", "R", "ZL", "ZR", "−", "+", "LS drücken", "RS drücken", "↑", "↓", "←", "→"],
  };
  const AXES = {
    "LEFT_STICK_X:+1": "Linker Stick →", "LEFT_STICK_X:-1": "Linker Stick ←",
    "LEFT_STICK_Y:+1": "Linker Stick ↓", "LEFT_STICK_Y:-1": "Linker Stick ↑",
    "RIGHT_STICK_X:+1": "Rechter Stick →", "RIGHT_STICK_X:-1": "Rechter Stick ←",
    "RIGHT_STICK_Y:+1": "Rechter Stick ↓", "RIGHT_STICK_Y:-1": "Rechter Stick ↑",
  };

  function load() {
    try {
      return Object.assign({ preset: null, labels: null }, JSON.parse(localStorage.getItem(KEY) || "{}"));
    } catch (e) {
      return { preset: null, labels: null };
    }
  }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch (e) { /* privater Modus */ }
  }

  let settings = load();
  let padId = null;

  function emu() {
    return window.EJS_emulator && window.EJS_emulator.controls ? window.EJS_emulator : null;
  }

  function detectLabels(id) {
    const s = (id || "").toLowerCase();
    if (/xbox|045e|xinput/.test(s)) return "xbox";            // vor "wireless controller" prüfen
    if (/057e|pro controller|joy-con|nintendo/.test(s)) return "pro";
    if (/054c|dualsense|dualshock|playstation|wireless controller|ps[345]/.test(s)) return "ps";
    return "xbox";
  }

  function labelSet() {
    return settings.labels || (padId ? detectLabels(padId) : "xbox");
  }

  // Ohne eigene Wahl: beim Pro Controller die Vorlage nach Beschriftung, sonst die Standard-Vorlage
  function presetKey() {
    return settings.preset || (labelSet() === "pro" && cfg.proPreset) || cfg.defaultPreset;
  }

  function keyText(value) {
    if (value === undefined || value === null || value === "") return "–";
    if (AXES[value]) return AXES[value];
    const idx = typeof value === "number" ? value : EJS_NAMES.indexOf(value);
    if (idx >= 0 && idx < LABELS[labelSet()].length) return LABELS[labelSet()][idx];
    return String(value);
  }

  // ------------------------------------------------------------------ Vorlage anwenden
  function matches(controls, map) {
    return Object.keys(map).every((id) => controls[id] && controls[id].value2 === map[id]);
  }

  function applyPreset(force) {
    const e = emu();
    const preset = cfg.presets[presetKey()];
    if (!e || !preset) return false;
    const current = e.controls[0] || (e.controls[0] = {});
    if (!force) {
      // Nur anwenden, wenn das Spiel noch die Standard- oder eine unserer Belegungen hat –
      // eigene Änderungen an einem einzelnen Spiel bleiben so erhalten.
      const defaults = {};
      Object.keys(preset.map).forEach((id) => {
        const d = e.defaultControllers && e.defaultControllers[0] && e.defaultControllers[0][id];
        defaults[id] = d ? d.value2 : undefined;
      });
      const known = matches(current, defaults) || Object.values(cfg.presets).some((p) => matches(current, p.map));
      if (!known) return false;
    }
    Object.entries(preset.map).forEach(([id, value]) => {
      if (!current[id]) current[id] = {};
      current[id].value2 = value;
    });
    try { e.saveSettings(); } catch (err) { /* noch nicht gestartet */ }
    return true;
  }

  // War der Controller schon aktiv, bevor EmulatorJS sein "connected"-Ereignis abonniert hat
  // (z. B. Wechsel aus der Bibliothek mit eingeschaltetem Controller), ordnet EmulatorJS ihn
  // keinem Spieler zu und ignoriert alle Tasten. Dann den ersten Controller Spieler 1 geben.
  function ensureAssigned() {
    const e = emu();
    if (!e || !e.gamepad || !Array.isArray(e.gamepadSelection)) return;
    const pads = (e.gamepad.gamepads || []).filter(Boolean);
    if (!pads.length || e.gamepadSelection.some((s) => s)) return;
    e.gamepadSelection[0] = pads[0].id + "_" + pads[0].index;
    padId = pads[0].id;
    try { e.updateGamepadLabels(); } catch (err) { /* Menü noch nicht aufgebaut */ }
  }

  // ------------------------------------------------------------------ Anzeige
  function render() {
    $("paddetected").textContent = padId
      ? "Verbunden: " + padId
      : "Kein Controller erkannt. Controller per USB oder Bluetooth verbinden (auch am Handy) und eine Taste drücken.";
    const ls = labelSet();
    $("padlabels").value = ls;
    const sel = $("padpreset");
    sel.innerHTML = "";
    Object.entries(cfg.presets).forEach(([key, p]) => {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = p.name[ls] || p.name.xbox;
      sel.appendChild(opt);
    });
    const custom = document.createElement("option");
    custom.value = "custom";
    custom.textContent = "Eigene Belegung (im Steuerungsmenü)";
    sel.appendChild(custom);
    sel.value = cfg.presets[presetKey()] ? presetKey() : "custom";

    const e = emu();
    const controls = (e && e.controls[0]) || {};
    const tbody = $("padmap").querySelector("tbody");
    tbody.innerHTML = "";
    cfg.rows.forEach(([ids, name]) => {
      const list = Array.isArray(ids) ? ids : [ids];
      const tr = document.createElement("tr");
      const a = document.createElement("td");
      a.textContent = name;
      const b = document.createElement("td");
      const values = list.map((id) => (controls[id] ? controls[id].value2 : undefined));
      // Stick-Zeilen: "Linker Stick" statt vier Einzelrichtungen
      const stick = values.every((v) => typeof v === "string" && v.startsWith("LEFT_STICK_")) ? "Linker Stick"
        : values.every((v) => typeof v === "string" && v.startsWith("RIGHT_STICK_")) ? "Rechter Stick" : null;
      b.innerHTML = '<span class="key"></span>';
      b.firstChild.textContent = stick || values.map(keyText).join(" / ");
      tr.append(a, b);
      tbody.appendChild(tr);
    });
  }

  function open(show) {
    panel.hidden = !show;
    btn.setAttribute("aria-expanded", show ? "true" : "false");
    if (show) {
      const other = $("touchpanel");
      if (other && !other.hidden) $("touchclose").click();
      render();
    }
  }

  btn.addEventListener("click", () => open(panel.hidden));
  $("padclose").addEventListener("click", () => open(false));

  $("padpreset").addEventListener("change", (ev) => {
    settings.preset = ev.target.value;
    save();
    if (settings.preset !== "custom") applyPreset(true);
    render();
  });

  $("padlabels").addEventListener("change", (ev) => {
    settings.labels = ev.target.value;
    save();
    if (!settings.preset) applyPreset(false);
    render();
  });

  $("padedit").addEventListener("click", () => {
    const e = emu();
    if (!e || !e.controlMenu) return;
    settings.preset = "custom";
    save();
    open(false);
    e.controlMenu.style.display = "";
  });

  // Touch-Feld schließt dieses Feld
  const touchBtn = $("touchbtn");
  if (touchBtn) touchBtn.addEventListener("click", () => { if (!panel.hidden) open(false); });

  window.addEventListener("gamepadconnected", (ev) => {
    padId = ev.gamepad.id;
    setTimeout(ensureAssigned, 100);
    if (!settings.preset) setTimeout(() => applyPreset(false), 150);   // passende Vorlage zum Controller
    if (!panel.hidden) render();
  });
  window.addEventListener("gamepaddisconnected", () => {
    const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
    padId = pads.length ? pads[0].id : null;
    if (!panel.hidden) render();
  });

  const prevStart = window.EJS_onGameStart;
  window.EJS_onGameStart = function () {
    if (typeof prevStart === "function") prevStart.apply(this, arguments);
    setTimeout(() => {
      applyPreset(false);
      ensureAssigned();
      if (!panel.hidden) render();
    }, 500);
    // Controller, die erst später erkannt werden, ohne eigenes Ereignis nachziehen
    setInterval(ensureAssigned, 2000);
  };
})();
