/* Vorrat – Oberfläche: Orte mit Vorräten, Kamerabilder, Verlauf. Alle Adressen relativ (Ingress). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let state = null;
  let timer = null;
  let editing = null;            // { location, name }

  // ------------------------------------------------------------------ Hilfen
  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach((child) => { if (child) node.appendChild(child); });
    return node;
  }

  let toastTimer = null;
  function toast(text, isError) {
    const t = $("toast");
    t.textContent = text;
    t.classList.toggle("error", !!isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 2500);
  }

  function ago(ts) {
    if (!ts) return "noch nie";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "gerade eben";
    if (s < 3600) return "vor " + Math.round(s / 60) + " min";
    if (s < 86400) return "vor " + Math.round(s / 3600) + " h";
    return new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? { cache: "no-store" } : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.text()) || "HTTP " + res.status);
    return res.json();
  }

  async function itemAction(location, name, action, value) {
    try {
      await api("api/item", { location, name, action, value });
      await refresh();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function scan(location) {
    try {
      await api("api/scan", location ? { location } : {});
      toast(location ? location + " wird gezählt …" : "Alle Orte werden gezählt …");
      await refresh();
    } catch (err) {
      toast(err.message, true);
    }
  }

  // ------------------------------------------------------------------ Darstellung
  function renderItem(loc, item) {
    const minus = el("button", { class: "step", type: "button", "aria-label": "Eins weniger", text: "−",
      onclick: () => itemAction(loc.name, item.name, "count", Math.max(0, item.count - 1)), disabled: item.ignored || item.count === 0 });
    const plus = el("button", { class: "step", type: "button", "aria-label": "Eins mehr", text: "+",
      onclick: () => itemAction(loc.name, item.name, "count", item.count + 1), disabled: item.ignored });
    const badges = [];
    if (item.low) badges.push(el("span", { class: "badge warn", text: "fehlt (mind. " + item.min + ")" }));
    if (item.ignored) badges.push(el("span", { class: "badge", text: "ignoriert" }));
    return el("li", { class: "item" + (item.count === 0 ? " zero" : "") + (item.ignored ? " ignored" : "") }, [
      el("button", { class: "name", type: "button", title: "Bearbeiten", onclick: () => openEdit(loc.name, item) },
        [el("span", { text: item.name })].concat(badges)),
      el("div", { class: "count" }, [minus, el("b", { text: String(item.count) }), plus]),
    ]);
  }

  function renderLocation(loc) {
    const total = loc.items.filter((i) => !i.ignored).reduce((sum, i) => sum + i.count, 0);
    const busy = loc.busy || loc.pending;
    const scanBtn = el("button", { class: "btn", type: "button", disabled: busy,
      text: loc.busy ? "Zählt " + (state.step ? "(" + state.step + ")" : "…") : loc.pending ? "Wartet …" : "Jetzt zählen",
      onclick: () => scan(loc.name) });
    const head = el("div", { class: "card-head" }, [
      el("h2", { text: loc.name }),
      el("span", { class: "muted", text: total + " Stück · gezählt " + ago(loc.last_scan) }),
      el("span", { class: "grow" }),
      scanBtn,
    ]);
    const cams = el("div", { class: "cams" }, loc.cameras.map((cam) => el("figure", { class: "cam" }, [
      cam.time ? el("a", { href: "api/snapshot/" + cam.slug + "?t=" + Math.round(cam.time), target: "_blank", rel: "noopener" },
        [el("img", { src: "api/snapshot/" + cam.slug + "?t=" + Math.round(cam.time), alt: cam.name, loading: "lazy" })])
        : el("div", { class: "noimg", text: "noch kein Foto" }),
      el("figcaption", { class: cam.error ? "err" : "", text: cam.name + (cam.error ? " – " + cam.error : " · " + ago(cam.time)) }),
    ])));
    const list = el("ul", { class: "items" }, loc.items.map((item) => renderItem(loc, item)));
    if (!loc.items.length) list.appendChild(el("li", { class: "muted pad", text: loc.last_scan ? "Nichts erkannt." : "Noch nicht gezählt." }));
    const addInput = el("input", { placeholder: "Vorrat von Hand hinzufügen", maxlength: "60", "aria-label": "Neuer Vorrat" });
    const add = el("form", { class: "add", onsubmit: (ev) => {
      ev.preventDefault();
      const name = addInput.value.trim();
      if (name) itemAction(loc.name, name, "add", 1);
      addInput.value = "";
    } }, [addInput, el("button", { class: "btn", type: "submit", text: "+" })]);
    return el("section", { class: "card" }, [
      head,
      loc.error ? el("p", { class: "error", text: "Letzter Versuch fehlgeschlagen: " + loc.error }) : null,
      cams, list, add,
    ]);
  }

  function renderHistory() {
    const list = $("history");
    list.textContent = "";
    if (!state.history.length) {
      list.appendChild(el("li", { class: "muted", text: "Noch keine Änderungen." }));
      return;
    }
    state.history.slice(0, 60).forEach((h) => {
      const diff = h.new - h.old;
      list.appendChild(el("li", {}, [
        el("span", { class: "when muted", text: ago(h.time) }),
        el("span", { class: "what", text: h.location + ": " + h.item }),
        el("span", { class: "diff " + (diff > 0 ? "up" : "down"), text: (diff > 0 ? "+" : "") + diff + "  (" + h.old + " → " + h.new + ")" }),
        el("span", { class: "src muted", text: h.source === "hand" ? "von Hand" : "Kamera" }),
      ]));
    });
  }

  function render() {
    const status = $("status");
    status.textContent = state.status;
    status.dataset.state = state.status.startsWith("Fehler") ? "err" : state.status === "Bereit" ? "ok" : "busy";
    $("empty").hidden = state.locations.length > 0;
    $("scan-all").hidden = state.locations.length === 0;
    const box = $("locations");
    box.textContent = "";
    state.locations.forEach((loc) => box.appendChild(renderLocation(loc)));
    renderHistory();
    const parts = ["Version " + state.version];
    parts.push(state.interval_minutes ? "zählt alle " + state.interval_minutes + " min" + (state.active_hours ? " (" + state.active_hours + ")" : "") : "zählt nur auf Anforderung");
    if (state.last_server) parts.push("zuletzt: " + state.last_server);
    else if (state.servers.length) parts.push("KI: " + state.servers.join(", "));
    else parts.push("kein Ollama-Server eingetragen");
    if (state.ha !== "off") parts.push("Home Assistant: " + (state.ha === "mqtt" ? "MQTT" : "REST-API"));
    if (state.todo) parts.push("Einkaufsliste: " + state.todo);
    $("foot").textContent = parts.join(" · ");
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      state = await api("api/state");
      if (!$("edit").open) render();
    } catch (err) {
      $("status").textContent = "Keine Verbindung";
      $("status").dataset.state = "err";
    }
    const busy = state && state.locations.some((l) => l.busy || l.pending);
    timer = setTimeout(refresh, busy ? 2000 : 15000);
  }

  // ------------------------------------------------------------------ Bearbeiten
  function openEdit(location, item) {
    editing = { location, name: item.name };
    $("edit-title").textContent = location;
    $("edit-name").value = item.name;
    $("edit-count").value = item.count;
    $("edit-min").value = item.min;
    $("edit-ignore").checked = item.ignored;
    $("edit").showModal();
  }

  $("edit-form").addEventListener("submit", async (ev) => {
    if (ev.submitter && ev.submitter.value !== "save") return;
    ev.preventDefault();
    const { location } = editing;
    let { name } = editing;
    const item = state.locations.find((l) => l.name === location).items.find((i) => i.name === name) || {};
    try {
      const newName = $("edit-name").value.trim();
      if (newName && newName !== name) name = (await api("api/item", { location, name, action: "rename", value: newName })).name;
      const count = parseInt($("edit-count").value, 10);
      if (!Number.isNaN(count) && count !== item.count) await api("api/item", { location, name, action: "count", value: count });
      const min = parseInt($("edit-min").value, 10);
      if (!Number.isNaN(min) && min !== item.min) await api("api/item", { location, name, action: "min", value: min });
      if ($("edit-ignore").checked !== !!item.ignored) await api("api/item", { location, name, action: "ignore", value: $("edit-ignore").checked });
      $("edit").close();
    } catch (err) {
      toast(err.message, true);
    }
    refresh();
  });

  $("edit-delete").addEventListener("click", async () => {
    if (!confirm("„" + editing.name + "“ aus der Liste löschen? Erkennt die Kamera den Vorrat wieder, taucht er erneut auf.")) return;
    $("edit").close();
    await itemAction(editing.location, editing.name, "delete");
  });

  $("edit").addEventListener("close", () => refresh());
  $("scan-all").addEventListener("click", () => scan(null));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  refresh();
})();
