/* GBA Emulator – Touch-Steuerung einstellen (anzeigen, Größe, Position, Deckkraft).
 *
 * Die Werte gelten pro Gerät (localStorage), weil Handy und Tablet ganz andere Maße brauchen.
 * Größe und Abstände wirken über CSS-Variablen auf das virtuelle Gamepad von EmulatorJS
 * (siehe app.css, Abschnitt „Touch-Gamepad“).
 */
(function () {
  "use strict";

  const KEY = "gba-touch-v1";
  const DEFAULTS = { mode: "auto", scale: 100, bottom: 50, side: 10, opacity: 100 };
  const $ = (id) => document.getElementById(id);
  const panel = $("touchpanel");
  const btn = $("touchbtn");

  function load() {
    try {
      const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
      return Object.assign({}, DEFAULTS, raw);
    } catch (e) {
      return Object.assign({}, DEFAULTS);
    }
  }

  function store(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* privater Modus */ }
  }

  let settings = load();

  function emu() {
    return window.EJS_emulator && window.EJS_emulator.started ? window.EJS_emulator : null;
  }

  // "Automatisch": sichtbar, wenn mit dem Finger gestartet wurde oder ein Handy erkannt ist.
  // (EmulatorJS selbst blendet es auf Tablets/iPads/Touch-Laptops wieder aus, weil es dort
  // nur nach dem Browser-Kennzeichen geht.)
  function autoVisible(e) {
    return !!(e.touch || e.isMobile);
  }

  function applyMode() {
    const e = emu();
    if (!e || typeof e.toggleVirtualGamepad !== "function") return;
    if (settings.mode === "on") e.toggleVirtualGamepad(true);
    else if (settings.mode === "off") e.toggleVirtualGamepad(false);
    else e.toggleVirtualGamepad(autoVisible(e));
  }

  // Steuerkreuz-Block (125 px) und A/B-Block (130 px) sollen nebeneinander passen, sonst
  // verdeckt der eine den anderen. Größte Skalierung für die aktuelle Breite:
  // In der Höhe: Block (125 px) plus L/R darüber (90 px) über dem festen Abstand von 50 px.
  const PAD_WIDTH = 125 + 130;
  const PAD_HEIGHT = 125 + 90;
  const PAD_GAP = 12;
  let effectiveScale = 100;

  function maxScale() {
    const stage = $("stage");
    const w = stage.clientWidth || window.innerWidth;
    const h = stage.clientHeight || window.innerHeight;
    const byWidth = (100 * (w - 2 * settings.side - PAD_GAP)) / PAD_WIDTH;
    const byHeight = (100 * (h - settings.bottom - 50 - 8)) / PAD_HEIGHT;
    return Math.max(50, Math.floor(Math.min(byWidth, byHeight)));
  }

  // Variablen am Wurzelelement – EmulatorJS überschreibt beim Start das style-Attribut von #game
  function applyLayout() {
    const root = document.documentElement.style;
    effectiveScale = Math.min(settings.scale, maxScale());
    root.setProperty("--pad-scale", String(effectiveScale / 100));
    root.setProperty("--pad-bottom", settings.bottom + "px");
    root.setProperty("--pad-side", settings.side + "px");
    root.setProperty("--pad-opacity", String(settings.opacity / 100));
  }

  function hint() {
    const e = window.EJS_emulator;
    const touchDevice = !!(e && (e.touch || e.hasTouchScreen)) || navigator.maxTouchPoints > 0;
    const texts = {
      auto: "Erscheint, wenn du das Spiel mit dem Finger startest.",
      on: touchDevice ? "Wird immer angezeigt." : "Wird immer angezeigt. Bedienen lässt es sich nur per Touch, die Maus geht hier nicht.",
      off: "Ausgeblendet, z. B. für einen Controller oder die Tastatur.",
    };
    $("tmodehint").textContent = texts[settings.mode] || "";
  }

  function fillForm() {
    document.querySelectorAll('input[name="tmode"]').forEach((r) => { r.checked = r.value === settings.mode; });
    $("t-scale").value = settings.scale;
    $("t-bottom").value = settings.bottom;
    $("t-side").value = settings.side;
    $("t-opacity").value = settings.opacity;
    $("o-scale").textContent = effectiveScale < settings.scale
      ? effectiveScale + " % (mehr passt hier nicht)"
      : settings.scale + " %";
    $("o-bottom").textContent = settings.bottom + " px";
    $("o-side").textContent = settings.side + " px";
    $("o-opacity").textContent = settings.opacity + " %";
    hint();
  }

  function update(change) {
    settings = Object.assign(settings, change);
    store(settings);
    applyLayout();
    fillForm();
  }

  // ------------------------------------------------------------------ Bedienung
  document.querySelectorAll('input[name="tmode"]').forEach((r) => {
    r.addEventListener("change", () => {
      update({ mode: r.value });
      applyMode();
    });
  });
  [["t-scale", "scale"], ["t-bottom", "bottom"], ["t-side", "side"], ["t-opacity", "opacity"]].forEach(([id, key]) => {
    $(id).addEventListener("input", (ev) => {
      update({ [key]: parseInt(ev.target.value, 10) });
      // Beim Verschieben das Gamepad zeigen, damit man das Ergebnis sieht
      const e = emu();
      if (e && settings.mode !== "off") e.toggleVirtualGamepad(true);
    });
  });
  $("treset").addEventListener("click", () => {
    settings = Object.assign({}, DEFAULTS);
    store(settings);
    applyLayout();
    fillForm();
    applyMode();
  });

  function openPanel(open) {
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      fillForm();
    } else {
      applyMode();          // Vorschau beenden: wieder nach Einstellung anzeigen
    }
  }

  btn.addEventListener("click", () => openPanel(panel.hidden));
  $("touchclose").addEventListener("click", () => openPanel(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) openPanel(false); });

  // ------------------------------------------------------------------ Start
  applyLayout();
  window.addEventListener("resize", () => {
    applyLayout();
    if (!panel.hidden) fillForm();
  });
  const prevStart = window.EJS_onGameStart;
  window.EJS_onGameStart = function () {
    if (typeof prevStart === "function") prevStart.apply(this, arguments);
    // EmulatorJS blendet das Gamepad beim Start selbst ein/aus – danach gilt unsere Einstellung
    setTimeout(applyMode, 300);
    setTimeout(applyMode, 1500);
  };
})();
