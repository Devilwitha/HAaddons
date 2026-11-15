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
  const game = $("game");
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
    const texts = {
      auto: "Erscheint, wenn du das Spiel mit dem Finger startest.",
      on: "Wird immer angezeigt – bedienbar mit Finger, Stift oder Maus.",
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

  // ------------------------------------------------------------------ Maus & Stift
  // Das Gamepad von EmulatorJS hört nur auf Touch-Ereignisse. Maus- und Stifteingaben darauf
  // werden deshalb als Touch-Ereignisse nachgereicht (EmulatorJS liest nur targetTouches/
  // changedTouches mit clientX/clientY). Echte Finger-Eingaben laufen unverändert direkt.
  function fakeTouch(type, target, x, y) {
    const ev = new Event(type, { bubbles: true, cancelable: true });
    const point = { identifier: 9001, target, clientX: x, clientY: y, pageX: x, pageY: y, screenX: x, screenY: y };
    const active = type === "touchend" || type === "touchcancel" ? [] : [point];
    Object.defineProperty(ev, "touches", { value: active });
    Object.defineProperty(ev, "targetTouches", { value: active });
    Object.defineProperty(ev, "changedTouches", { value: [point] });
    target.dispatchEvent(ev);
  }

  let pressed = null;   // { el, dpad, id }

  game.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch" || e.button !== 0) return;
    if (!e.target.closest || !e.target.closest(".ejs_virtualGamepad_parent")) return;
    const dpad = e.target.closest(".ejs_dpad_main");
    const el = dpad || e.target.closest(".ejs_virtualGamepad_button");
    if (!el) return;
    e.preventDefault();           // kein Menü-Einblenden/Markieren durch den Klick
    e.stopPropagation();
    pressed = { el, dpad: !!dpad, id: e.pointerId };
    fakeTouch("touchstart", el, e.clientX, e.clientY);
  }, true);

  window.addEventListener("pointermove", (e) => {
    if (pressed && pressed.dpad && e.pointerId === pressed.id) {
      fakeTouch("touchmove", pressed.el, e.clientX, e.clientY);
    }
  });

  function release(e) {
    if (!pressed || e.pointerId !== pressed.id) return;
    fakeTouch(e.type === "pointercancel" ? "touchcancel" : "touchend", pressed.el, e.clientX, e.clientY);
    pressed = null;
  }
  window.addEventListener("pointerup", release);
  window.addEventListener("pointercancel", release);
  window.addEventListener("blur", () => {
    if (pressed) {
      fakeTouch("touchcancel", pressed.el, 0, 0);
      pressed = null;
    }
  });

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
