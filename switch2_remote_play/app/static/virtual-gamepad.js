/* Game Remote – virtuelles Multi-Touch-Gamepad.
 *
 * Pointer Events mit setPointerCapture: Jeder Finger bleibt an dem Bedienelement, auf dem er
 * aufgesetzt hat. So lassen sich linker Stick und rechte Tasten gleichzeitig bedienen.
 *
 * Zustand (über onChange gemeldet):
 *   buttons: { A, B, X, Y, L, ZL, R, ZR, MINUS, PLUS, HOME, CAPTURE,
 *              DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT }  -> true/false
 *   ls, rs:  [x, y] von -100.0 bis +100.0 (x: rechts positiv, y: oben positiv)
 */
(function () {
  "use strict";

  const BUTTONS = ["A", "B", "X", "Y", "L", "ZL", "R", "ZR", "MINUS", "PLUS", "HOME", "CAPTURE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"];
  const DEADZONE = 0.12;

  const LAYOUT = `
    <div class="vg-shoulder vg-left">
      <div class="vg-btn vg-pill" data-btn="ZL">ZL</div>
      <div class="vg-btn vg-pill" data-btn="L">L</div>
    </div>
    <div class="vg-shoulder vg-right">
      <div class="vg-btn vg-pill" data-btn="ZR">ZR</div>
      <div class="vg-btn vg-pill" data-btn="R">R</div>
    </div>
    <div class="vg-stick vg-stick-left" data-stick="ls">
      <div class="vg-base"><div class="vg-knob"></div></div>
    </div>
    <div class="vg-dpad" data-dpad>
      <div class="vg-dpad-arm vg-up" data-dir="DPAD_UP"></div>
      <div class="vg-dpad-arm vg-down" data-dir="DPAD_DOWN"></div>
      <div class="vg-dpad-arm vg-leftarm" data-dir="DPAD_LEFT"></div>
      <div class="vg-dpad-arm vg-rightarm" data-dir="DPAD_RIGHT"></div>
      <div class="vg-dpad-center"></div>
    </div>
    <div class="vg-abxy">
      <div class="vg-btn vg-round vg-x" data-btn="X">X</div>
      <div class="vg-btn vg-round vg-y" data-btn="Y">Y</div>
      <div class="vg-btn vg-round vg-a" data-btn="A">A</div>
      <div class="vg-btn vg-round vg-b" data-btn="B">B</div>
    </div>
    <div class="vg-stick vg-stick-right" data-stick="rs">
      <div class="vg-base"><div class="vg-knob"></div></div>
    </div>
    <div class="vg-system">
      <div class="vg-btn vg-small" data-btn="MINUS" title="Minus">&minus;</div>
      <div class="vg-btn vg-small" data-btn="CAPTURE" title="Aufnahme">&#9673;</div>
      <div class="vg-btn vg-small" data-btn="HOME" title="HOME">&#8962;</div>
      <div class="vg-btn vg-small" data-btn="PLUS" title="Plus">+</div>
    </div>`;

  function emptyState() {
    const buttons = {};
    BUTTONS.forEach((b) => { buttons[b] = false; });
    return { buttons, ls: [0, 0], rs: [0, 0] };
  }

  // Radiale Deadzone, danach auf 0..1 umskaliert, Ergebnis -100..100
  function normalize(dx, dy, radius) {
    let x = dx / radius;
    let y = -dy / radius;                       // Bildschirm-y nach unten -> Stick-y nach oben
    let mag = Math.hypot(x, y);
    if (mag > 1) {
      x /= mag;
      y /= mag;
      mag = 1;
    }
    if (mag < DEADZONE) return [0, 0];
    const scale = (mag - DEADZONE) / (1 - DEADZONE) / mag;
    return [Math.round(x * scale * 1000) / 10, Math.round(y * scale * 1000) / 10];
  }

  class VirtualGamepad {
    /**
     * @param {HTMLElement} container  Element, in das das Gamepad gezeichnet wird
     * @param {object} opts  { onChange(state), haptics: bool, stickMode: "fixed" | "dynamic" }
     */
    constructor(container, opts) {
      this.el = container;
      this.opts = Object.assign({ onChange: () => {}, haptics: true, stickMode: "fixed" }, opts || {});
      this.state = emptyState();
      this.pointers = new Map();               // pointerId -> { kind, el, ... }
      this.el.classList.add("vg");
      this.el.innerHTML = LAYOUT;
      this.el.addEventListener("pointerdown", (e) => this._down(e));
      this.el.addEventListener("pointermove", (e) => this._move(e));
      this.el.addEventListener("pointerup", (e) => this._up(e));
      this.el.addEventListener("pointercancel", (e) => this._up(e));
      this.el.addEventListener("lostpointercapture", (e) => this._up(e));
      this.el.addEventListener("contextmenu", (e) => e.preventDefault());
    }

    setHaptics(on) {
      this.opts.haptics = !!on;
    }

    setStickMode(mode) {
      this.opts.stickMode = mode === "dynamic" ? "dynamic" : "fixed";
    }

    show(visible) {
      this.el.hidden = !visible;
      if (!visible) this.releaseAll();
    }

    releaseAll() {
      this.pointers.forEach((p) => this._release(p));
      this.pointers.clear();
      this.state = emptyState();
      this.el.querySelectorAll(".vg-pressed").forEach((n) => n.classList.remove("vg-pressed"));
      this.el.querySelectorAll(".vg-knob").forEach((k) => { k.style.transform = ""; });
      this.el.querySelectorAll(".vg-base").forEach((b) => { b.style.transform = ""; });
      this._emit();
    }

    _vibrate() {
      if (this.opts.haptics && navigator.vibrate) {
        try { navigator.vibrate(15); } catch (err) { /* nicht unterstützt */ }
      }
    }

    _emit() {
      this.opts.onChange(this.state);
    }

    _down(e) {
      const target = e.target.closest("[data-btn], [data-stick], [data-dpad]");
      if (!target || !this.el.contains(target)) return;
      e.preventDefault();
      try { target.setPointerCapture(e.pointerId); } catch (err) { /* Pointer schon weg */ }

      if (target.dataset.btn) {
        const name = target.dataset.btn;
        this.pointers.set(e.pointerId, { kind: "btn", el: target, name });
        this.state.buttons[name] = true;
        target.classList.add("vg-pressed");
        this._vibrate();
        this._emit();
      } else if (target.dataset.stick) {
        const base = target.querySelector(".vg-base");
        const knob = target.querySelector(".vg-knob");
        const rect = base.getBoundingClientRect();
        let cx = rect.left + rect.width / 2;
        let cy = rect.top + rect.height / 2;
        let offset = [0, 0];
        if (this.opts.stickMode === "dynamic") {
          // Basis springt unter den Finger
          offset = [e.clientX - cx, e.clientY - cy];
          base.style.transform = `translate(${offset[0]}px, ${offset[1]}px)`;
          cx = e.clientX;
          cy = e.clientY;
        }
        const p = { kind: "stick", el: target, name: target.dataset.stick, base, knob, cx, cy, radius: rect.width / 2 };
        this.pointers.set(e.pointerId, p);
        target.classList.add("vg-pressed");
        this._stick(p, e.clientX, e.clientY);
      } else if (target.dataset.dpad !== undefined) {
        const rect = target.getBoundingClientRect();
        const p = { kind: "dpad", el: target, cx: rect.left + rect.width / 2, cy: rect.top + rect.height / 2, size: rect.width, dirs: [] };
        this.pointers.set(e.pointerId, p);
        this._dpad(p, e.clientX, e.clientY, true);
      }
    }

    _move(e) {
      const p = this.pointers.get(e.pointerId);
      if (!p) return;
      e.preventDefault();
      if (p.kind === "stick") this._stick(p, e.clientX, e.clientY);
      else if (p.kind === "dpad") this._dpad(p, e.clientX, e.clientY, false);
    }

    _up(e) {
      const p = this.pointers.get(e.pointerId);
      if (!p) return;
      this.pointers.delete(e.pointerId);
      this._release(p);
      this._emit();
    }

    _release(p) {
      if (p.kind === "btn") {
        // Gleiche Taste kann von einem zweiten Finger noch gehalten werden
        const still = [...this.pointers.values()].some((o) => o.kind === "btn" && o.name === p.name);
        if (!still) {
          this.state.buttons[p.name] = false;
          p.el.classList.remove("vg-pressed");
        }
      } else if (p.kind === "stick") {
        this.state[p.name] = [0, 0];
        p.knob.style.transform = "";
        p.base.style.transform = "";
        p.el.classList.remove("vg-pressed");
      } else if (p.kind === "dpad") {
        this._setDpad(p, []);
      }
    }

    _stick(p, x, y) {
      let dx = x - p.cx;
      let dy = y - p.cy;
      const dist = Math.hypot(dx, dy);
      if (dist > p.radius) {
        dx = (dx / dist) * p.radius;
        dy = (dy / dist) * p.radius;
      }
      p.knob.style.transform = `translate(${dx}px, ${dy}px)`;
      const value = normalize(x - p.cx, y - p.cy, p.radius);
      const old = this.state[p.name];
      if (old[0] !== value[0] || old[1] !== value[1]) {
        this.state[p.name] = value;
        this._emit();
      }
    }

    _dpad(p, x, y, first) {
      const dx = x - p.cx;
      const dy = y - p.cy;
      const dirs = [];
      if (Math.hypot(dx, dy) > p.size * 0.12) {
        // 8 Richtungen: je 45° Sektor
        const angle = (Math.atan2(-dy, dx) * 180) / Math.PI;   // 0° = rechts, 90° = oben
        const a = (angle + 360) % 360;
        if (a >= 22.5 && a < 157.5) dirs.push("DPAD_UP");
        if (a >= 202.5 && a < 337.5) dirs.push("DPAD_DOWN");
        if (a >= 112.5 && a < 247.5) dirs.push("DPAD_LEFT");
        if (a >= 292.5 || a < 67.5) dirs.push("DPAD_RIGHT");
      }
      if (first && dirs.length) this._vibrate();
      this._setDpad(p, dirs);
    }

    _setDpad(p, dirs) {
      const same = dirs.length === p.dirs.length && dirs.every((d) => p.dirs.includes(d));
      if (same) return;
      if (dirs.some((d) => !p.dirs.includes(d)) && p.dirs.length) this._vibrate();
      p.dirs = dirs;
      // Mehrere Finger auf dem Steuerkreuz: Richtungen aller Finger zusammenfassen
      const active = new Set();
      this.pointers.forEach((o) => { if (o.kind === "dpad") o.dirs.forEach((d) => active.add(d)); });
      ["DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"].forEach((d) => {
        this.state.buttons[d] = active.has(d);
        const arm = p.el.querySelector(`[data-dir="${d}"]`);
        if (arm) arm.classList.toggle("vg-pressed", active.has(d));
      });
      this._emit();
    }
  }

  VirtualGamepad.BUTTONS = BUTTONS;
  VirtualGamepad.DEADZONE = DEADZONE;
  VirtualGamepad.normalize = normalize;
  VirtualGamepad.emptyState = emptyState;
  window.VirtualGamepad = VirtualGamepad;
})();
