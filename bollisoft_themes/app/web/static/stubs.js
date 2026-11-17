"use strict";
// Ersatz für ha-card, ha-icon und ha-state-icon, damit die Karten in der Vorschau des Add-ons laufen.
// In Home Assistant selbst kommen die echten Elemente zum Einsatz.
(() => {
  const PATHS = {
    "mdi:thermometer": "M10 13V5a2 2 0 0 1 4 0v8a4 4 0 1 1-4 0zM12 9v7",
    "mdi:water-percent": "M12 3s6 7 6 11a6 6 0 0 1-12 0c0-4 6-11 6-11zM9.5 17l5-5M10 12.5h.01M14 16.5h.01",
    "mdi:air-filter": "M3 7h11a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 17h8",
    "mdi:desktop-classic": "M6 3h12v10H6zM4 16h16v4H4zM8 18h3",
    "mdi:sofa": "M5 11V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3M3 11h4v3h10v-3h4v6H3zM5 17v2M19 17v2",
    "mdi:bed": "M3 19V6M3 14h18v5M21 14a3 3 0 0 0-3-3h-8v3M6.5 11.5h.01",
    "mdi:fridge": "M7 3h10v18H7zM7 10h10M10 6v2M10 13v3",
    "mdi:account-group": "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 19c0-3 3-5 6-5s6 2 6 5M16 11a2.5 2.5 0 1 0 0-5M17 14c2 .5 4 2 4 5",
    "mdi:window-closed-variant": "M5 3h14v18H5zM12 3v18M5 12h14",
    "mdi:lightbulb": "M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z",
    "mdi:webcam": "M12 3a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM12 7a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM8 21h8M12 15v6",
    "mdi:television": "M3 6h18v11H3zM8 21h8",
    "mdi:battery": "M8 5h8v16H8zM10 3h4",
    "mdi:battery-charging": "M8 5h8v16H8zM10 3h4M13 8l-3 5h4l-3 5",
    "mdi:wifi": "M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01",
    "mdi:bluetooth": "M7 7l10 10-5 4V3l5 4L7 17",
    "mdi:toggle-switch": "M3 8h18v8H3zM16 12h.01",
    "mdi:help": "M9 9a3 3 0 1 1 4 2.8c-.6.3-1 .9-1 1.6V15M12 19h.01",
    "mdi:chart-bell-curve": "M3 20h18M4 18c4 0 4-12 8-12s4 12 8 12",
    "mdi:account": "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c1-4 4-6 8-6s7 2 8 6",
  };
  const DOMAIN_ICONS = { light: "mdi:lightbulb", switch: "mdi:toggle-switch", person: "mdi:account" };
  const CLASS_ICONS = { window: "mdi:window-closed-variant", occupancy: "mdi:account-group", presence: "mdi:account-group" };

  function svg(icon) {
    const d = PATHS[icon] || PATHS["mdi:help"];
    return `<svg viewBox="0 0 24 24" style="width:var(--mdc-icon-size,24px);height:var(--mdc-icon-size,24px);display:block;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round"><path d="${d}"/></svg>`;
  }

  class StubIcon extends HTMLElement {
    static get observedAttributes() {
      return ["icon"];
    }

    set icon(value) {
      this.setAttribute("icon", value);
    }

    connectedCallback() {
      this.style.display = "inline-flex";
      this.innerHTML = svg(this.getAttribute("icon"));
    }

    attributeChangedCallback() {
      if (this.isConnected) this.innerHTML = svg(this.getAttribute("icon"));
    }
  }

  class StubStateIcon extends HTMLElement {
    set stateObj(obj) {
      this._obj = obj;
      this._draw();
    }

    connectedCallback() {
      this.style.display = "inline-flex";
      this._draw();
    }

    _draw() {
      const obj = this._obj;
      if (!obj) return;
      const id = obj.entity_id;
      const icon = obj.attributes.icon || CLASS_ICONS[obj.attributes.device_class]
        || (id.includes("wifi") ? "mdi:wifi" : id.includes("bluetooth") ? "mdi:bluetooth" : DOMAIN_ICONS[id.split(".")[0]]);
      this.innerHTML = svg(icon);
    }
  }

  // wie das echte ha-card: Aussehen aus dem Theme, die Karte selbst darf es überschreiben
  class StubCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" }).innerHTML = `<style>:host{display:block;
        background:var(--ha-card-background,var(--card-background-color,#fff));
        border-radius:var(--ha-card-border-radius,12px);
        border:var(--ha-card-border-width,1px) solid var(--ha-card-border-color,var(--divider-color,#e0e0e0));
        box-shadow:var(--ha-card-box-shadow,none);color:var(--primary-text-color)}</style><slot></slot>`;
    }
  }

  if (!customElements.get("ha-card")) customElements.define("ha-card", StubCard);
  if (!customElements.get("ha-icon")) customElements.define("ha-icon", StubIcon);
  if (!customElements.get("ha-state-icon")) customElements.define("ha-state-icon", StubStateIcon);
})();
