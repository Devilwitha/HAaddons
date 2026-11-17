"use strict";

const $ = (sel) => document.querySelector(sel);
const canvas = $("#screen");
const ctx = canvas.getContext("2d");
const stage = $("#stage");
const kbd = $("#kbd");
const addr = $("#addr");

const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem("bsbrowser." + key);
      return v === null ? fallback : JSON.parse(v);
    } catch (e) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem("bsbrowser." + key, JSON.stringify(value));
    } catch (e) {
      /* privates Fenster */
    }
  },
};

const isTouch = matchMedia("(pointer: coarse)").matches;
if (isTouch) document.body.classList.add("touch");
const QUALITY = { fast: { q: 50, dpr: 1 }, normal: { q: 70, dpr: 1.5 }, sharp: { q: 85, dpr: 2 } };
const ZOOMS = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
const SENTINEL = " ";

const S = {
  ws: null,
  connected: false,
  retry: 0,
  state: null,
  frame: { w: 0, h: 0, dw: 0, dh: 0 },
  hasFrame: false,
  settings: {
    mobile: store.get("mobile", isTouch && Math.min(screen.width, screen.height) < 820),
    zoom: store.get("zoom", 1),
    quality: store.get("quality", "normal"),
  },
  modal: null,
  start: null,
  startLoaded: 0,
  down: new Set(),
  lastClick: { t: 0, x: 0, y: 0, n: 0, button: "" },
  pendingMove: null,
  wheel: null,
  composing: false,
  toastTimer: null,
  chooser: null,
};

// ------------------------------------------------------------------ Hilfen
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function icon(name, cls) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  if (cls) svg.setAttribute("class", cls);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function favicon(url, cls, fallbackIcon) {
  const fb = icon(fallbackIcon || "globe", cls);
  if (!/^https?:\/\//i.test(url || "")) return fb;
  const img = h("img", { class: cls, alt: "", src: "api/favicon?url=" + encodeURIComponent(url), loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(fb));
  return img;
}

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch (e) {
    return url;
  }
}

function isBlank(url) {
  return !url || url === "about:blank" || url.startsWith("chrome://newtab") || url.startsWith("edge://newtab");
}

function displayUrl(url) {
  return isBlank(url) ? "" : url;
}

function fmtSize(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return (i ? n.toFixed(n < 10 ? 1 : 0) : n) + " " + u[i];
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(S.toastTimer);
  S.toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}

function send(obj) {
  if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(obj));
}

function activeTab() {
  const st = S.state;
  return st ? st.tabs.find((t) => t.id === st.active) : null;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.text()) || "HTTP " + r.status);
  return r.json();
}

// ------------------------------------------------------------------ Verbindung
function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(proto + "//" + location.host + location.pathname.replace(/[^/]*$/, "") + "ws");
  ws.binaryType = "arraybuffer";
  S.ws = ws;
  ws.onopen = () => {
    S.connected = true;
    S.retry = 0;
    sendView(true);
    render();
  };
  ws.onmessage = (e) => {
    if (typeof e.data !== "string") {
      drawFrame(e.data);
      return;
    }
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch (err) {
      return;
    }
    onMessage(msg);
  };
  ws.onclose = () => {
    S.connected = false;
    S.ws = null;
    render();
    const delay = Math.min(5000, 600 * 2 ** S.retry++);
    setTimeout(connect, delay);
  };
}

function onMessage(msg) {
  switch (msg.t) {
    case "state":
      S.state = msg;
      render();
      break;
    case "toast":
      toast(msg.text);
      break;
    case "select":
      showSelect(msg);
      break;
    case "focus":
      onRemoteFocus(msg.field);
      break;
    case "copied":
      onCopied(msg.text);
      break;
    case "files":
      showFiles(msg);
      break;
    default:
      break;
  }
}

async function drawFrame(buf) {
  try {
    const len = new DataView(buf).getUint32(0);
    const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, len)));
    const bmp = await createImageBitmap(new Blob([new Uint8Array(buf, 4 + len)], { type: "image/jpeg" }));
    if (canvas.width !== bmp.width || canvas.height !== bmp.height) {
      canvas.width = bmp.width;
      canvas.height = bmp.height;
    }
    ctx.drawImage(bmp, 0, 0);
    S.frame = { w: bmp.width, h: bmp.height, dw: head.dw || bmp.width, dh: head.dh || bmp.height };
    if (bmp.close) bmp.close();
    if (!S.hasFrame) {
      S.hasFrame = true;
      renderOverlay();
    }
  } catch (e) {
    /* kaputtes Bild überspringen */
  }
  send({ t: "ack" });
}

let viewTimer = null;
let lastView = "";
function sendView(force) {
  clearTimeout(viewTimer);
  viewTimer = setTimeout(() => {
    const r = stage.getBoundingClientRect();
    if (r.width < 50 || r.height < 50) return;
    const q = QUALITY[S.settings.quality] || QUALITY.normal;
    const view = {
      t: "view",
      w: Math.round(r.width),
      h: Math.round(r.height),
      dpr: Math.round(Math.min(window.devicePixelRatio || 1, q.dpr) * 100) / 100,
      zoom: S.settings.zoom,
      mobile: !!S.settings.mobile,
      quality: q.q,
    };
    const key = JSON.stringify(view);
    if (key === lastView && !force) return;
    lastView = key;
    send(view);
  }, force ? 0 : 120);
}
new ResizeObserver(() => sendView(false)).observe(stage);

// ------------------------------------------------------------------ Koordinaten
function toRemote(cx, cy) {
  const f = S.frame;
  if (!f.w) return null;
  const r = canvas.getBoundingClientRect();
  const scale = Math.min(r.width / f.w, r.height / f.h);
  const iw = f.w * scale;
  const ih = f.h * scale;
  const x = ((cx - r.left) / iw) * f.dw;
  const y = ((cy - r.top) / ih) * f.dh;
  return { x: Math.max(0, Math.min(f.dw - 1, x)), y: Math.max(0, Math.min(f.dh - 1, y)), k: f.dw / iw };
}

function mods(e) {
  const altGr = e.getModifierState && e.getModifierState("AltGraph");
  return (e.altKey && !altGr ? 1 : 0) | (e.ctrlKey && !altGr ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
}

function clickCount(p, button) {
  const now = performance.now();
  const lc = S.lastClick;
  if (lc.button === button && now - lc.t < 450 && Math.hypot(p.x - lc.x, p.y - lc.y) < 6) lc.n = Math.min(3, lc.n + 1);
  else lc.n = 1;
  Object.assign(lc, { t: now, x: p.x, y: p.y, button });
  return lc.n;
}

// ------------------------------------------------------------------ Maus
const BUTTONS = ["left", "middle", "right"];
canvas.addEventListener("mousedown", (e) => e.preventDefault());
canvas.addEventListener("contextmenu", (e) => e.preventDefault());

canvas.addEventListener("pointerdown", (e) => {
  if (e.pointerType === "touch") {
    touchStart(e);
    return;
  }
  e.preventDefault();
  closeMenu();
  focusKbd();
  canvas.setPointerCapture(e.pointerId);
  const p = toRemote(e.clientX, e.clientY);
  if (!p) return;
  const button = BUTTONS[e.button] || "left";
  flushMove();
  send({ t: "mouse", type: "down", x: p.x, y: p.y, button, buttons: e.buttons, clicks: clickCount(p, button), mods: mods(e) });
});

canvas.addEventListener("pointermove", (e) => {
  if (e.pointerType === "touch") {
    touchMove(e);
    return;
  }
  const p = toRemote(e.clientX, e.clientY);
  if (!p) return;
  const first = !S.pendingMove;
  S.pendingMove = { t: "mouse", type: "move", x: p.x, y: p.y, buttons: e.buttons, mods: mods(e) };
  if (first) requestAnimationFrame(flushMove);
});

canvas.addEventListener("pointerup", (e) => {
  if (e.pointerType === "touch") {
    touchEnd(e, false);
    return;
  }
  const p = toRemote(e.clientX, e.clientY);
  if (!p) return;
  flushMove();
  const button = BUTTONS[e.button] || "left";
  send({ t: "mouse", type: "up", x: p.x, y: p.y, button, buttons: e.buttons, clicks: S.lastClick.n || 1, mods: mods(e) });
});

canvas.addEventListener("pointercancel", (e) => {
  if (e.pointerType === "touch") touchEnd(e, true);
});

function flushMove() {
  if (S.pendingMove) {
    send(S.pendingMove);
    S.pendingMove = null;
  }
}

function queueWheel(x, y, dx, dy, m) {
  if (!S.wheel) {
    S.wheel = { x, y, dx: 0, dy: 0, mods: m || 0 };
    requestAnimationFrame(() => {
      const w = S.wheel;
      S.wheel = null;
      if (w && (w.dx || w.dy)) send({ t: "mouse", type: "wheel", x: w.x, y: w.y, dx: w.dx, dy: w.dy, mods: w.mods });
    });
  }
  S.wheel.dx += dx;
  S.wheel.dy += dy;
}

canvas.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    const p = toRemote(e.clientX, e.clientY);
    if (!p) return;
    let dx = e.deltaX;
    let dy = e.deltaY;
    if (e.deltaMode === 1) {
      dx *= 40;
      dy *= 40;
    } else if (e.deltaMode === 2) {
      dx *= stage.clientWidth;
      dy *= stage.clientHeight;
    }
    queueWheel(p.x, p.y, dx * p.k, dy * p.k, mods(e));
  },
  { passive: false },
);

// ------------------------------------------------------------------ Touch: Tippen = Klick, Wischen = Scrollen,
// lange drücken und ziehen = mit gedrückter Maustaste ziehen (Regler, Karten)
const T = { id: null, sx: 0, sy: 0, lx: 0, ly: 0, moved: false, drag: false, timer: null, samples: [], kin: null, lastTap: 0, tapX: 0, tapY: 0 };

function stopKinetic() {
  if (T.kin) cancelAnimationFrame(T.kin);
  T.kin = null;
}

function touchStart(e) {
  if (T.id !== null) return;
  e.preventDefault();
  closeMenu();
  stopKinetic();
  T.id = e.pointerId;
  T.sx = T.lx = e.clientX;
  T.sy = T.ly = e.clientY;
  T.moved = false;
  T.drag = false;
  T.samples = [{ x: e.clientX, y: e.clientY, t: performance.now() }];
  try {
    canvas.setPointerCapture(e.pointerId);
  } catch (err) {
    /* egal */
  }
  clearTimeout(T.timer);
  T.timer = setTimeout(() => {
    if (T.id === null || T.moved) return;
    const p = toRemote(T.sx, T.sy);
    if (!p) return;
    T.drag = true;
    if (navigator.vibrate) navigator.vibrate(12);
    send({ t: "mouse", type: "move", x: p.x, y: p.y, buttons: 0 });
    send({ t: "mouse", type: "down", x: p.x, y: p.y, button: "left", buttons: 1, clicks: 1 });
  }, 450);
}

function touchMove(e) {
  if (e.pointerId !== T.id) return;
  const now = performance.now();
  if (!T.moved && Math.hypot(e.clientX - T.sx, e.clientY - T.sy) > 8) {
    T.moved = true;
    if (!T.drag) clearTimeout(T.timer);
  }
  if (T.drag) {
    const p = toRemote(e.clientX, e.clientY);
    if (p) {
      const first = !S.pendingMove;
      S.pendingMove = { t: "mouse", type: "move", x: p.x, y: p.y, buttons: 1 };
      if (first) requestAnimationFrame(flushMove);
    }
  } else if (T.moved) {
    const p = toRemote(T.sx, T.sy);
    if (p) queueWheel(p.x, p.y, -(e.clientX - T.lx) * p.k, -(e.clientY - T.ly) * p.k, 0);
  }
  T.lx = e.clientX;
  T.ly = e.clientY;
  T.samples.push({ x: e.clientX, y: e.clientY, t: now });
  while (T.samples.length > 2 && now - T.samples[0].t > 100) T.samples.shift();
}

function touchEnd(e, cancelled) {
  if (e.pointerId !== T.id) return;
  clearTimeout(T.timer);
  T.id = null;
  const p = toRemote(e.clientX, e.clientY);
  if (!p) return;
  if (T.drag) {
    flushMove();
    send({ t: "mouse", type: "up", x: p.x, y: p.y, button: "left", buttons: 0, clicks: 1 });
    return;
  }
  if (cancelled) return;
  if (!T.moved) {
    const now = performance.now();
    const double = now - T.lastTap < 350 && Math.hypot(e.clientX - T.tapX, e.clientY - T.tapY) < 25;
    T.lastTap = double ? 0 : now;
    T.tapX = e.clientX;
    T.tapY = e.clientY;
    const clicks = double ? 2 : 1;
    send({ t: "mouse", type: "move", x: p.x, y: p.y, buttons: 0 });
    send({ t: "mouse", type: "down", x: p.x, y: p.y, button: "left", buttons: 1, clicks });
    send({ t: "mouse", type: "up", x: p.x, y: p.y, button: "left", buttons: 0, clicks, touch: true });
    return;
  }
  // Schwung: weiter scrollen und langsam auslaufen lassen
  const a = T.samples[0];
  const b = T.samples[T.samples.length - 1];
  const dt = Math.max(1, b.t - a.t);
  let vx = (b.x - a.x) / dt;
  let vy = (b.y - a.y) / dt;
  if (performance.now() - b.t > 80 || Math.hypot(vx, vy) < 0.25) return;
  const start = toRemote(T.sx, T.sy);
  let last = performance.now();
  const step = (now) => {
    const d = now - last;
    last = now;
    const decay = Math.pow(0.996, d);
    vx *= decay;
    vy *= decay;
    queueWheel(start.x, start.y, -vx * d * start.k, -vy * d * start.k, 0);
    T.kin = Math.hypot(vx, vy) > 0.03 ? requestAnimationFrame(step) : null;
  };
  T.kin = requestAnimationFrame(step);
}

// ------------------------------------------------------------------ Tastatur
function resetKbd() {
  if (kbd.value !== SENTINEL) kbd.value = SENTINEL;
  try {
    kbd.setSelectionRange(SENTINEL.length, SENTINEL.length);
  } catch (e) {
    /* egal */
  }
}

function focusKbd() {
  if (document.activeElement !== kbd) kbd.focus({ preventScroll: true });
  resetKbd();
}

function keyEvent(type, e, text) {
  send({
    t: "key",
    type,
    key: e.key,
    code: e.code,
    keyCode: e.keyCode || 0,
    mods: text ? (e.shiftKey ? 8 : 0) : mods(e),
    text: text || "",
    repeat: e.repeat,
    loc: e.location || 0,
  });
}

const NAMED = {
  Backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
  Delete: { key: "Delete", code: "Delete", keyCode: 46 },
  Enter: { key: "Enter", code: "Enter", keyCode: 13, text: "\r" },
};

function pressNamed(name) {
  const k = NAMED[name];
  send({ t: "key", type: k.text ? "keyDown" : "rawKeyDown", key: k.key, code: k.code, keyCode: k.keyCode, text: k.text || "", mods: 0 });
  send({ t: "key", type: "keyUp", key: k.key, code: k.code, keyCode: k.keyCode, mods: 0 });
}

kbd.addEventListener("keydown", (e) => {
  if (e.isComposing || e.keyCode === 229 || e.key === "Unidentified" || e.key === "Process" || e.key === "Dead") return;
  const altGr = e.getModifierState && e.getModifierState("AltGraph");
  const ctrl = (e.ctrlKey || e.metaKey) && !altGr;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (ctrl && !e.altKey) {
    if (k === "v") return; // kommt als paste
    if (k === "c") {
      e.preventDefault();
      send({ t: "copy" });
      return;
    }
    if (k === "x") send({ t: "copy" });
    if (k === "l") {
      e.preventDefault();
      addr.focus();
      addr.select();
      return;
    }
    if (k === "r") {
      e.preventDefault();
      send({ t: "reload" });
      return;
    }
    if (k === "t" || k === "w" || k === "n") return;
  }
  if (e.altKey && !altGr && !e.ctrlKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault();
    send({ t: e.key === "ArrowLeft" ? "back" : "forward" });
    return;
  }
  if (e.key === "F5") {
    e.preventDefault();
    send({ t: "reload" });
    return;
  }
  if (/^F\d+$/.test(e.key)) return;
  e.preventDefault();
  const printable = e.key.length === 1 && !ctrl && !(e.altKey && !altGr);
  const text = printable ? e.key : e.key === "Enter" ? "\r" : "";
  S.down.add(e.code || e.key);
  keyEvent(text ? "keyDown" : "rawKeyDown", e, text);
});

kbd.addEventListener("keyup", (e) => {
  const id = e.code || e.key;
  if (!S.down.has(id)) return;
  S.down.delete(id);
  e.preventDefault();
  keyEvent("keyUp", e, "");
});

kbd.addEventListener("blur", () => {
  // Tasten nicht hängen lassen
  for (const id of S.down) send({ t: "key", type: "keyUp", key: id, code: id, keyCode: 0, mods: 0 });
  S.down.clear();
  $("#b-kbd").classList.remove("kbd-on");
});

kbd.addEventListener("focus", () => {
  resetKbd();
  $("#b-kbd").classList.add("kbd-on");
});

// Bildschirmtastaturen schicken oft keine echten Tasten, sondern Eingabe-Ereignisse
kbd.addEventListener("beforeinput", (e) => {
  const t = e.inputType;
  if (t === "deleteContentBackward" || t === "deleteWordBackward") {
    e.preventDefault();
    pressNamed("Backspace");
  } else if (t === "deleteContentForward") {
    e.preventDefault();
    pressNamed("Delete");
  } else if (t === "insertLineBreak" || t === "insertParagraph") {
    e.preventDefault();
    pressNamed("Enter");
  } else if (t === "insertText" && !S.composing) {
    e.preventDefault();
    if (e.data) send({ t: "text", text: e.data });
  } else if (t === "insertFromPaste") {
    e.preventDefault();
  }
});

kbd.addEventListener("compositionstart", () => {
  S.composing = true;
});

kbd.addEventListener("compositionend", (e) => {
  S.composing = false;
  if (e.data) send({ t: "text", text: e.data });
  setTimeout(resetKbd, 0);
});

kbd.addEventListener("input", () => {
  if (S.composing) return;
  const v = kbd.value;
  if (v !== SENTINEL) {
    const extra = v.startsWith(SENTINEL) ? v.slice(SENTINEL.length) : v;
    if (extra) send({ t: "text", text: extra });
    resetKbd();
  }
});

kbd.addEventListener("paste", (e) => {
  e.preventDefault();
  const text = e.clipboardData && e.clipboardData.getData("text/plain");
  if (text) send({ t: "text", text });
});

function onRemoteFocus(field) {
  if (!isTouch) return;
  if (field) {
    kbd.setAttribute("inputmode", field.mode || ({ number: "decimal", tel: "tel", email: "email", url: "url", search: "search" }[field.type] || "text"));
    kbd.setAttribute("enterkeyhint", field.type === "search" ? "search" : "enter");
    focusKbd();
  } else if (document.activeElement === kbd) {
    kbd.blur();
  }
}

$("#b-kbd").addEventListener("click", () => {
  if (document.activeElement === kbd) kbd.blur();
  else {
    kbd.setAttribute("inputmode", "text");
    focusKbd();
  }
});

async function onCopied(text) {
  if (!text) {
    toast("Nichts markiert");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast("In die Zwischenablage kopiert");
  } catch (e) {
    openModal("copied", () => [
      h("h3", { text: "Markierter Text" }),
      h("p", { class: "msg", text: "Der Browser erlaubt hier kein automatisches Kopieren – bitte von Hand kopieren." }),
      h("textarea", { name: "text", readonly: true }, text),
      h("div", { class: "acts" }, h("button", { class: "btn primary", type: "submit", text: "Schließen" })),
    ], () => closeModal());
    const ta = $("#modal-box textarea");
    ta.focus();
    ta.select();
  }
}

// ------------------------------------------------------------------ Leiste
$("#b-back").addEventListener("click", () => send({ t: "back" }));
$("#b-forward").addEventListener("click", () => send({ t: "forward" }));
$("#b-reload").addEventListener("click", () => {
  const tab = activeTab();
  send({ t: tab && tab.loading ? "stop" : "reload" });
});
$("#b-home").addEventListener("click", goHome);

function goHome() {
  const start = S.start && S.start.start_url;
  send({ t: "nav", url: start || "about:blank" });
}

addr.addEventListener("focus", () => setTimeout(() => addr.select(), 0));
addr.addEventListener("blur", () => {
  const tab = activeTab();
  addr.value = displayUrl(tab && tab.url);
});
addr.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    addr.blur();
    if (!isTouch) focusKbd();
  }
});
$("#addr-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = addr.value.trim();
  if (!v) return;
  send({ t: "nav", url: v });
  addr.blur();
  if (!isTouch) focusKbd();
});

$("#start-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#start-addr");
  const v = input.value.trim();
  if (!v) return;
  send({ t: "nav", url: v });
  input.value = "";
  input.blur();
});

$("#b-star").addEventListener("click", () => bookmarkCurrent());
$("#b-tabs").addEventListener("click", showTabs);

// ------------------------------------------------------------------ Darstellung
function render() {
  const st = S.state;
  const tab = activeTab();
  renderTabs();
  $("#b-back").disabled = !(st && st.back);
  $("#b-forward").disabled = !(st && st.forward);
  $("#reload-ic").setAttribute("href", tab && tab.loading ? "#i-x" : "#i-reload");
  $("#b-reload").title = tab && tab.loading ? "Laden abbrechen" : "Neu laden (F5)";
  $("#progress").classList.toggle("on", !!(tab && tab.loading && !isBlank(tab.url)));
  if (document.activeElement !== addr) addr.value = displayUrl(tab && tab.url);
  const secure = tab && tab.url.startsWith("https://");
  $("#addr-ic").setAttribute("href", secure ? "#i-lock" : isBlank(tab && tab.url) ? "#i-search" : "#i-globe");
  document.querySelector(".addr-ic").classList.toggle("secure", !!secure);
  renderStar();
  renderStart();
  renderOverlay();
  renderModalsFromState();
  renderMenuState();
  if (!$("#sheet").classList.contains("hidden")) refreshSheet();
}

function renderTabs() {
  const st = S.state;
  const strip = $("#tabstrip");
  const tabs = st ? st.tabs : [];
  $("#tab-count").textContent = tabs.length > 99 ? ":)" : String(tabs.length || 1);
  strip.replaceChildren(
    ...tabs.map((t) =>
      h(
        "div",
        {
          class: "tab" + (t.id === st.active ? " active" : ""),
          role: "tab",
          title: t.title || t.url,
          onclick: () => send({ t: "tab_switch", id: t.id }),
          onauxclick: (e) => {
            if (e.button === 1) send({ t: "tab_close", id: t.id });
          },
        },
        t.loading && !isBlank(t.url) ? h("span", { class: "spin" }) : favicon(t.url, "fav"),
        h("span", { class: "tt", text: tabTitle(t) }),
        h(
          "span",
          {
            class: "tx",
            title: "Tab schließen",
            onclick: (e) => {
              e.stopPropagation();
              send({ t: "tab_close", id: t.id });
            },
          },
          icon("x"),
        ),
      ),
    ),
    h("button", { class: "ib sm tab-new", title: "Neuer Tab", onclick: () => send({ t: "tab_new" }) }, icon("plus")),
  );
}

function tabTitle(t) {
  if (isBlank(t.url)) return "Neuer Tab";
  return t.title && t.title !== t.url ? t.title : hostOf(t.url);
}

function isBookmarked(url) {
  return !!(S.start && url && S.start.bookmarks.some((b) => b.url === url));
}

function renderStar() {
  const tab = activeTab();
  const on = !!(tab && isBookmarked(tab.url));
  $("#b-star").classList.toggle("on", on);
  $("#b-star").classList.toggle("hidden", !tab || isBlank(tab.url));
  $("#mi-bookmark").textContent = on ? "Lesezeichen bearbeiten" : "Lesezeichen setzen";
}

function showingStart() {
  const st = S.state;
  const tab = activeTab();
  return !!(S.connected && st && st.status === "running" && tab && isBlank(tab.url));
}

function renderStart() {
  const show = showingStart();
  const el = $("#start");
  const was = !el.classList.contains("hidden");
  el.classList.toggle("hidden", !show);
  if (show && (!was || Date.now() - S.startLoaded > 30000)) loadStart();
}

function renderOverlay() {
  const st = S.state;
  let text = "";
  let detail = "";
  let button = "";
  let spin = true;
  if (!S.connected) text = "Verbindung wird hergestellt …";
  else if (!st || st.status === "starting" || (st.status === "stopped" && !st.error)) text = "Browser startet …";
  else if (st.status === "stopped") {
    text = "Browser startet neu …";
    detail = st.error;
  } else if (st.status === "error") {
    text = "Der Browser läuft nicht";
    detail = st.error;
    button = "Erneut versuchen";
    spin = false;
  } else if (!S.hasFrame && !showingStart()) text = "Seite wird geladen …";
  const ov = $("#overlay");
  ov.classList.toggle("hidden", !text);
  $("#ov-text").textContent = text;
  $("#ov-detail").textContent = detail || "";
  $("#ov-spin").classList.toggle("hidden", !spin);
  $("#ov-btn").classList.toggle("hidden", !button);
  $("#ov-btn").textContent = button;
}
$("#ov-btn").addEventListener("click", () => send({ t: "restart" }));

// ------------------------------------------------------------------ Startseite
async function loadStart() {
  S.startLoaded = Date.now();
  try {
    S.start = await api("api/start");
  } catch (e) {
    S.start = S.start || { bookmarks: [], recent: [], devices: [], devices_error: e.message };
  }
  renderStartData();
  renderStar();
}

function renderStartData() {
  const d = S.start;
  if (!d) return;
  const grid = $("#bm-grid");
  grid.replaceChildren(
    ...d.bookmarks.map((b) =>
      h(
        "div",
        { class: "tile", role: "button", tabindex: "0", title: b.url, onclick: () => send({ t: "nav", url: b.url }) },
        h("button", {
          class: "ib sm tile-e",
          title: "Bearbeiten",
          onclick: (e) => {
            e.stopPropagation();
            editBookmark(b);
          },
        }, icon("edit")),
        tileIcon(b),
        h("div", { class: "tile-t", text: b.title }),
        h("div", { class: "tile-u", text: hostOf(b.url) }),
      ),
    ),
  );
  $("#bm-empty").classList.toggle("hidden", d.bookmarks.length > 0);
  renderDevices();
  const recent = d.recent || [];
  $("#recent-sec").classList.toggle("hidden", recent.length === 0);
  $("#recent-list").replaceChildren(
    ...recent.slice(0, 8).map((r) =>
      h(
        "button",
        { class: "row", type: "button", title: r.url, onclick: () => send({ t: "nav", url: r.url }) },
        favicon(r.url, "ri", "clock"),
        h("span", { class: "rt", text: r.title || hostOf(r.url) }),
        h("span", { class: "ru", text: r.url }),
      ),
    ),
  );
}

function tileIcon(b) {
  const box = h("div", { class: "tile-ic" });
  const letter = (b.title || hostOf(b.url) || "?").trim().charAt(0).toUpperCase();
  const img = h("img", { alt: "", src: "api/favicon?url=" + encodeURIComponent(b.url), loading: "lazy" });
  img.addEventListener("error", () => {
    box.textContent = letter;
  });
  box.append(img);
  return box;
}

function renderDevices() {
  const d = S.start;
  const q = $("#dev-filter").value.trim().toLowerCase();
  const items = (d.devices || []).filter((x) => !q || (x.name + " " + x.info + " " + x.area + " " + x.url).toLowerCase().includes(q));
  const list = $("#dev-list");
  const nodes = [];
  let area = null;
  const grouped = new Set(items.map((x) => x.area)).size > 1;
  for (const x of items) {
    if (grouped && x.area !== area) {
      area = x.area;
      nodes.push(h("div", { class: "dev-area", text: area || "Ohne Bereich" }));
    }
    const ic = h("div", { class: "dev-ic" });
    const img = h("img", { alt: "", src: "api/favicon?url=" + encodeURIComponent(x.url), loading: "lazy" });
    img.addEventListener("error", () => img.replaceWith(icon("router")));
    ic.append(img);
    nodes.push(
      h(
        "button",
        { class: "dev", type: "button", title: x.url, onclick: () => send({ t: "nav", url: x.url }) },
        ic,
        h("div", { class: "dev-b" }, h("div", { class: "dev-t", text: x.name }), h("div", { class: "dev-s", text: [x.info, hostOf(x.url)].filter(Boolean).join(" · ") })),
      ),
    );
  }
  list.replaceChildren(...nodes);
  const empty = $("#dev-empty");
  let msg = "";
  if (d.devices_error) msg = d.devices_error;
  else if (!(d.devices || []).length) msg = "Keine Geräte mit eigener Weboberfläche in Home Assistant gefunden.";
  else if (!items.length) msg = "Kein Gerät passt zum Filter.";
  empty.textContent = msg;
  empty.classList.toggle("hidden", !msg);
}
$("#dev-filter").addEventListener("input", renderDevices);

$("#recent-clear").addEventListener("click", async () => {
  await api("api/recent/clear", { method: "POST" });
  loadStart();
});

$("#bm-add").addEventListener("click", () => editBookmark(null));

async function bookmarkAction(body) {
  try {
    const r = await api("api/bookmarks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (S.start) S.start.bookmarks = r.bookmarks;
    renderStartData();
    renderStar();
    return true;
  } catch (e) {
    toast(e.message);
    return false;
  }
}

function bookmarkCurrent() {
  const tab = activeTab();
  if (!tab || isBlank(tab.url)) {
    editBookmark(null);
    return;
  }
  const existing = S.start && S.start.bookmarks.find((b) => b.url === tab.url);
  if (existing) editBookmark(existing);
  else bookmarkAction({ action: "add", url: tab.url, title: tab.title || hostOf(tab.url) }).then((ok) => ok && toast("Lesezeichen gesetzt"));
}

function editBookmark(b) {
  openModal(
    "bookmark",
    () => [
      h("h3", { text: b ? "Lesezeichen bearbeiten" : "Lesezeichen hinzufügen" }),
      h("label", {}, "Name", h("input", { type: "text", name: "title", value: b ? b.title : "", placeholder: "z. B. Router" })),
      h("label", {}, "Adresse", h("input", { type: "url", name: "url", value: b ? b.url : "", placeholder: "http://192.168.178.1", readonly: !!b })),
      h(
        "div",
        { class: "acts" },
        b && h("button", { class: "btn", type: "button", title: "Nach vorne", onclick: () => bookmarkAction({ action: "move", url: b.url, dir: "up" }) }, icon("up")),
        b && h("button", { class: "btn", type: "button", title: "Nach hinten", onclick: () => bookmarkAction({ action: "move", url: b.url, dir: "down" }) }, icon("down")),
        b && h("button", { class: "btn danger", type: "button", text: "Entfernen", onclick: async () => { await bookmarkAction({ action: "remove", url: b.url }); closeModal(); } }),
        h("span", { class: "spacer" }),
        h("button", { class: "btn", type: "button", text: "Abbrechen", onclick: closeModal }),
        h("button", { class: "btn primary", type: "submit", text: "Speichern" }),
      ),
    ],
    async (form) => {
      const title = form.title.value.trim();
      const ok = b
        ? await bookmarkAction({ action: "rename", url: b.url, title })
        : await bookmarkAction({ action: "add", url: form.url.value.trim(), title });
      if (ok) closeModal();
    },
  );
  $("#modal-box input[name=" + (b ? "title" : "url") + "]").focus();
}

// ------------------------------------------------------------------ Dialoge
function openModal(kind, build, onSubmit, key) {
  const box = $("#modal-box");
  S.modal = { kind, key: key || kind, onSubmit };
  box.replaceChildren(...build().filter(Boolean));
  $("#modal").classList.remove("hidden");
}

function closeModal() {
  S.modal = null;
  $("#modal").classList.add("hidden");
  $("#modal-box").replaceChildren();
}

$("#modal-box").addEventListener("submit", (e) => {
  e.preventDefault();
  if (S.modal && S.modal.onSubmit) S.modal.onSubmit(e.target, e.submitter);
});

$("#modal").addEventListener("pointerdown", (e) => {
  if (e.target.id === "modal" && S.modal && ["select", "copied", "bookmark", "text", "files"].includes(S.modal.kind)) closeModal();
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#menu").classList.contains("hidden")) closeMenu();
  else if (!$("#sheet").classList.contains("hidden")) closeSheet();
  else if (S.modal && S.modal.kind === "dialog") answerDialog(false);
  else if (S.modal && S.modal.kind === "auth") answerAuth(true);
  else if (S.modal) closeModal();
});

function renderModalsFromState() {
  const st = S.state;
  const auth = st && st.auth;
  const dialog = st && st.dialog;
  if (auth) {
    const key = "auth:" + auth.id;
    if (!S.modal || S.modal.key !== key) showAuth(auth, key);
  } else if (dialog) {
    const key = "dialog:" + dialog.tab + ":" + dialog.type + ":" + dialog.message;
    if (!S.modal || S.modal.key !== key) showDialog(dialog, key);
  } else if (S.modal && (S.modal.kind === "auth" || S.modal.kind === "dialog")) {
    closeModal();
  }
}

function showDialog(d, key) {
  const titles = { alert: "Meldung der Seite", confirm: "Bestätigung", prompt: "Eingabe", beforeunload: "Seite verlassen?" };
  openModal(
    "dialog",
    () => [
      h("h3", { text: titles[d.type] || "Meldung" }),
      h("div", { class: "li-s", text: d.title }),
      h("p", { class: "msg", text: d.message || (d.type === "beforeunload" ? "Eingaben auf der Seite gehen eventuell verloren." : "") }),
      d.type === "prompt" && h("input", { type: "text", name: "text", value: d.default || "" }),
      h(
        "div",
        { class: "acts" },
        d.type !== "alert" && h("button", { class: "btn", type: "button", text: "Abbrechen", onclick: () => answerDialog(false) }),
        h("button", { class: "btn primary", type: "submit", text: d.type === "beforeunload" ? "Verlassen" : "OK" }),
      ),
    ],
    (form) => answerDialog(true, form.text ? form.text.value : undefined),
    key,
  );
  const input = $("#modal-box input");
  if (input) input.focus();
  else $("#modal-box .btn.primary").focus();
}

function answerDialog(accept, text) {
  const d = S.state && S.state.dialog;
  if (d) send({ t: "dialog", tab: d.tab, accept, text });
  closeModal();
}

function showAuth(a, key) {
  openModal(
    "auth",
    () => [
      h("h3", { text: a.proxy ? "Anmeldung am Proxy" : "Anmeldung erforderlich" }),
      h("p", { class: "msg", text: hostOf(a.origin) + (a.realm ? " – „" + a.realm + "“" : "") }),
      a.retry && h("p", { class: "warn", text: "Benutzername oder Passwort waren falsch." }),
      h("label", {}, "Benutzername", h("input", { type: "text", name: "user", value: a.user || "", autocomplete: "off", autocapitalize: "off" })),
      h("label", {}, "Passwort", h("input", { type: "password", name: "password", autocomplete: "off" })),
      h("label", { class: "check" }, h("input", { type: "checkbox", name: "remember", checked: !!a.saved }), "Anmeldung im Add-on merken"),
      h(
        "div",
        { class: "acts" },
        h("button", { class: "btn", type: "button", text: "Abbrechen", onclick: () => answerAuth(true) }),
        h("button", { class: "btn primary", type: "submit", text: "Anmelden" }),
      ),
    ],
    (form) => {
      send({ t: "auth", id: a.id, user: form.user.value, password: form.password.value, remember: form.remember.checked });
      closeModal();
    },
    key,
  );
  $("#modal-box input[name=" + (a.user ? "password" : "user") + "]").focus();
}

function answerAuth(cancel) {
  const a = S.state && S.state.auth;
  if (a && cancel) send({ t: "auth", id: a.id, cancel: true });
  closeModal();
}

function showSelect(sel) {
  const nodes = [];
  let group = null;
  sel.options.forEach((o, i) => {
    if (o.group && o.group !== group) {
      group = o.group;
      nodes.push(h("div", { class: "opt-g", text: group }));
    }
    nodes.push(
      h("button", {
        class: "opt" + (i === sel.index ? " on" : ""),
        type: "button",
        disabled: o.disabled,
        text: o.label || "—",
        onclick: () => {
          send({ t: "select", index: i });
          closeModal();
        },
      }),
    );
  });
  openModal("select", () => [h("h3", { text: sel.label || "Auswählen" }), h("div", { class: "opts" }, nodes)], null);
  const on = $("#modal-box .opt.on");
  if (on) on.scrollIntoView({ block: "center" });
}

function showFiles(msg) {
  S.chooser = msg;
  const fileInput = $("#file");
  fileInput.multiple = !!msg.multiple;
  fileInput.value = "";
  openModal(
    "files",
    () => [
      h("h3", { text: msg.multiple ? "Dateien für die Seite" : "Datei für die Seite" }),
      h("p", { class: "msg", text: "Die Seite möchte eine Datei, z. B. ein Firmware-Update oder eine Sicherung. Die Datei wird über Home Assistant an den Browser übergeben." }),
      h("div", { class: "files-pick", id: "files-list" }),
      h(
        "div",
        { class: "acts" },
        h("button", { class: "btn", type: "button", text: "Abbrechen", onclick: closeModal }),
        h("button", { class: "btn primary", type: "button", id: "files-btn", text: msg.multiple ? "Dateien wählen" : "Datei wählen", onclick: () => fileInput.click() }),
      ),
    ],
    null,
  );
}

$("#file").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  const chooser = S.chooser;
  if (!files.length || !chooser) return;
  const list = $("#files-list");
  const btn = $("#files-btn");
  if (btn) btn.disabled = true;
  const ids = [];
  try {
    for (const file of files) {
      const bar = h("i", { style: "width:0%" });
      const row = h("div", { class: "li" }, icon("file", "fav"), h("div", { class: "li-b" }, h("div", { class: "li-t", text: file.name }), h("div", { class: "li-s", text: fmtSize(file.size) }), h("div", { class: "bar-p" }, bar)));
      if (list) list.append(row);
      ids.push(await uploadFile(file, (p) => (bar.style.width = Math.round(p * 100) + "%")));
    }
    send({ t: "files", token: chooser.token, ids });
    S.chooser = null;
    closeModal();
    toast(files.length > 1 ? files.length + " Dateien übergeben" : "Datei übergeben");
  } catch (err) {
    toast("Hochladen fehlgeschlagen: " + err.message);
    if (btn) btn.disabled = false;
  }
});

async function uploadFile(file, onProgress) {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const id = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  const CHUNK = 8 * 1024 * 1024;
  let offset = 0;
  do {
    const end = Math.min(offset + CHUNK, file.size);
    const final = end >= file.size ? 1 : 0;
    const url = `api/upload?id=${id}&name=${encodeURIComponent(file.name)}&offset=${offset}&final=${final}`;
    const r = await fetch(url, { method: "POST", body: file.slice(offset, end) });
    if (!r.ok) throw new Error((await r.text()) || "HTTP " + r.status);
    offset = end;
    onProgress(file.size ? offset / file.size : 1);
  } while (offset < file.size);
  return id;
}

function showTextDialog() {
  openModal(
    "text",
    () => [
      h("h3", { text: "Text senden" }),
      h("p", { class: "msg", text: "Wird an der Stelle eingefügt, an der auf der Seite der Cursor steht – praktisch für Passwörter und lange Texte." }),
      h("textarea", { name: "text", placeholder: "Text hier einfügen" }),
      h("label", { class: "check" }, h("input", { type: "checkbox", name: "enter" }), "Danach Enter drücken"),
      h(
        "div",
        { class: "acts" },
        h("button", { class: "btn", type: "button", text: "Abbrechen", onclick: closeModal }),
        h("button", { class: "btn primary", type: "submit", text: "Senden" }),
      ),
    ],
    (form) => {
      const text = form.text.value;
      if (text) send({ t: "text", text });
      if (form.enter.checked) pressNamed("Enter");
      closeModal();
    },
  );
  $("#modal-box textarea").focus();
}

function confirmDialog(title, text, okText, onOk) {
  openModal(
    "confirm",
    () => [
      h("h3", { text: title }),
      h("p", { class: "msg", text }),
      h(
        "div",
        { class: "acts" },
        h("button", { class: "btn", type: "button", text: "Abbrechen", onclick: closeModal }),
        h("button", { class: "btn danger", type: "submit", text: okText }),
      ),
    ],
    () => {
      closeModal();
      onOk();
    },
  );
}

// ------------------------------------------------------------------ Menü
function closeMenu() {
  $("#menu").classList.add("hidden");
}

$("#b-menu").addEventListener("click", (e) => {
  e.stopPropagation();
  $("#menu").classList.toggle("hidden");
  renderMenuState();
});

document.addEventListener("pointerdown", (e) => {
  if (!$("#menu").classList.contains("hidden") && !e.target.closest("#menu") && !e.target.closest("#b-menu")) closeMenu();
});

function renderMenuState() {
  for (const b of document.querySelectorAll("#seg-view button")) b.classList.toggle("on", (b.dataset.v === "mobile") === !!S.settings.mobile);
  for (const b of document.querySelectorAll("#seg-quality button")) b.classList.toggle("on", b.dataset.v === S.settings.quality);
  $("#zoom-reset").textContent = Math.round(S.settings.zoom * 100) + " %";
  const st = S.state;
  const running = st ? st.downloads.filter((d) => d.state === "progress").length : 0;
  $("#dl-badge").textContent = String(running);
  $("#dl-badge").classList.toggle("hidden", !running);
  const parts = ["BolliSoft Browser" + (S.start && S.start.version ? " " + S.start.version : "")];
  if (st && st.viewers > 1) parts.push(st.viewers + " Fenster offen");
  $("#mfoot").textContent = parts.join(" · ");
}

function setSetting(key, value) {
  S.settings[key] = value;
  store.set(key, value);
  renderMenuState();
  sendView(true);
}

$("#seg-view").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) setSetting("mobile", b.dataset.v === "mobile");
});
$("#seg-quality").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) setSetting("quality", b.dataset.v);
});
function stepZoom(dir) {
  const cur = S.settings.zoom;
  let i = ZOOMS.findIndex((z) => z >= cur - 0.001);
  if (i < 0) i = ZOOMS.length - 1;
  const next = dir > 0 ? ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS[i] > cur + 0.001 ? i : i + 1)] : ZOOMS[Math.max(0, i - 1)];
  setSetting("zoom", next);
}
$("#zoom-in").addEventListener("click", () => stepZoom(1));
$("#zoom-out").addEventListener("click", () => stepZoom(-1));
$("#zoom-reset").addEventListener("click", () => setSetting("zoom", 1));

$("#menu").addEventListener("click", (e) => {
  const item = e.target.closest(".mi");
  if (!item) return;
  closeMenu();
  const tab = activeTab();
  switch (item.dataset.act) {
    case "tab_new":
      send({ t: "tab_new" });
      break;
    case "bookmark":
      bookmarkCurrent();
      break;
    case "text":
      showTextDialog();
      break;
    case "copy":
      send({ t: "copy" });
      break;
    case "external":
      if (tab && !isBlank(tab.url)) window.open(tab.url, "_blank", "noopener");
      else toast("Keine Seite geöffnet");
      break;
    case "downloads":
      showDownloads();
      break;
    case "restart":
      send({ t: "restart" });
      break;
    case "clear":
      confirmDialog(
        "Browserdaten löschen?",
        "Cookies, Anmeldungen bei Seiten, gespeicherte Passwörter, Verlauf und offene Tabs werden gelöscht. Lesezeichen und Downloads bleiben.",
        "Löschen",
        () => send({ t: "clear_data" }),
      );
      break;
    default:
      break;
  }
});

// ------------------------------------------------------------------ Liste: Tabs und Downloads
let sheetKind = null;

function openSheet(kind, title) {
  sheetKind = kind;
  $("#sheet-title").textContent = title;
  $("#sheet").classList.remove("hidden");
  refreshSheet();
}

function closeSheet() {
  sheetKind = null;
  $("#sheet").classList.add("hidden");
}

$("#sheet-close").addEventListener("click", closeSheet);
$("#sheet").addEventListener("pointerdown", (e) => {
  if (e.target.id === "sheet") closeSheet();
});

function showTabs() {
  openSheet("tabs", "Tabs");
}

function showDownloads() {
  openSheet("downloads", "Downloads");
}

let downloadList = null;
async function refreshSheet() {
  const body = $("#sheet-body");
  if (sheetKind === "tabs") {
    const st = S.state;
    const tabs = st ? st.tabs : [];
    body.replaceChildren(
      ...tabs.map((t) =>
        h(
          "div",
          { class: "li" + (t.id === st.active ? " active" : ""), onclick: () => { send({ t: "tab_switch", id: t.id }); closeSheet(); } },
          favicon(t.url, "fav"),
          h("div", { class: "li-b" }, h("div", { class: "li-t", text: tabTitle(t) }), h("div", { class: "li-s", text: displayUrl(t.url) || "Startseite" })),
          h("button", { class: "ib sm", title: "Schließen", onclick: (e) => { e.stopPropagation(); send({ t: "tab_close", id: t.id }); } }, icon("x")),
        ),
      ),
      h("div", { class: "sheet-act" }, h("button", { class: "btn primary", type: "button", onclick: () => { send({ t: "tab_new" }); closeSheet(); } }, icon("plus"), "Neuer Tab")),
    );
  } else if (sheetKind === "downloads") {
    const st = S.state;
    const active = st ? st.downloads.filter((d) => d.state === "progress") : [];
    if (!downloadList || refreshSheet.reload) {
      refreshSheet.reload = false;
      try {
        downloadList = await api("api/downloads");
      } catch (e) {
        downloadList = { downloads: [], folder: "" };
      }
    }
    const rows = active.map((d) =>
      h(
        "div",
        { class: "li" },
        icon("download", "fav"),
        h(
          "div",
          { class: "li-b" },
          h("div", { class: "li-t", text: d.name }),
          h("div", { class: "li-s", text: fmtSize(d.received) + (d.total ? " von " + fmtSize(d.total) : "") }),
          h("div", { class: "bar-p" }, h("i", { style: "width:" + (d.total ? Math.round((d.received / d.total) * 100) : 30) + "%" })),
        ),
      ),
    );
    for (const f of downloadList.downloads) {
      rows.push(
        h(
          "div",
          { class: "li" },
          icon("file", "fav"),
          h("a", { class: "li-b", href: "api/downloads/" + encodeURIComponent(f.name), download: f.name }, h("div", { class: "li-t", text: f.name }), h("div", { class: "li-s", text: fmtSize(f.size) + " · " + new Date(f.time * 1000).toLocaleString("de-DE") })),
          h("a", { class: "ib sm", title: "Herunterladen", href: "api/downloads/" + encodeURIComponent(f.name), download: f.name }, icon("download")),
          h("button", {
            class: "ib sm",
            title: "Löschen",
            onclick: async () => {
              downloadList = await api("api/downloads/" + encodeURIComponent(f.name), { method: "DELETE" });
              refreshSheet();
            },
          }, icon("trash")),
        ),
      );
    }
    if (!rows.length) rows.push(h("p", { class: "empty", text: "Noch keine Downloads." }));
    body.replaceChildren(...rows, h("div", { class: "sheet-foot", text: "Gespeichert in " + (downloadList.folder || "/share/browser/downloads") + " (auch über Samba erreichbar)." }));
  }
}

let doneDownloads = 0;
setInterval(() => {
  const st = S.state;
  if (!st) return;
  const done = st.downloads.filter((d) => d.state === "done").length;
  if (done !== doneDownloads) {
    doneDownloads = done;
    refreshSheet.reload = true;
    if (sheetKind === "downloads") refreshSheet();
  }
}, 1000);

// ------------------------------------------------------------------ Start
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) sendView(true);
});
render();
connect();
