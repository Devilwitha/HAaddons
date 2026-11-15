/* Game Remote – Oberfläche.
 *
 * - WebSocket "ws" (über den Ingress-Server an die Bridge): Eingaben senden, Status empfangen
 * - Video: WebRTC über "video/offer", automatischer Wechsel auf MJPEG ("video/stream.mjpg"),
 *   wenn WebRTC nicht zustande kommt (z. B. unterwegs über Nabu Casa)
 * - Eingaben aus Touch-Gamepad und echten Controllern (HTML5 Gamepad API) werden zusammengeführt
 *
 * Nachrichten an die Bridge:
 *   {"type":"input","buttons":["A","DPAD_UP",...],"ls":[x,y],"rs":[x,y]}   x/y: -100..100, y oben positiv
 *   {"type":"heartbeat","t":<ms>}                                          Antwort: {"type":"pong","t":<ms>}
 *   {"type":"system","action":"pair"}
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DEADZONE = 0.12;
  const HEARTBEAT_MS = 500;
  const WEBRTC_TIMEOUT_MS = 8000;

  const config = { stream_mode: "webrtc", default_touch_overlay: true, haptic_feedback: true };

  // ------------------------------------------------------------------ Hilfen
  function store(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (err) { /* privater Modus */ }
  }

  function load(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (err) {
      return fallback;
    }
  }

  let toastTimer = null;
  function toast(text, isError) {
    const el = $("toast");
    el.textContent = text;
    el.classList.toggle("error", !!isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 3500);
  }

  function setPill(id, state, title) {
    const el = $(id);
    el.dataset.state = state;
    if (title) el.title = title;
  }

  // ------------------------------------------------------------------ WebSocket zur Bridge
  let ws = null;
  let wsRetry = 1000;
  let lastPong = 0;

  function wsUrl() {
    const url = new URL("ws", location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  }

  function connect() {
    setPill("st-bridge", "busy", "Verbinde …");
    ws = new WebSocket(wsUrl());
    ws.onopen = () => {
      wsRetry = 1000;
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (err) { return; }
      handleMessage(msg);
    };
    ws.onclose = () => {
      setPill("st-bridge", "err", "Keine Verbindung zur Bridge – neuer Versuch …");
      setPill("st-switch", "off");
      setPill("st-capture", capturePlaying ? "ok" : "off");
      setPill("st-ping", "off");
      $("ping-value").textContent = "–";
      setTimeout(connect, wsRetry);
      wsRetry = Math.min(wsRetry * 2, 8000);
    };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  const SWITCH_TEXT = {
    connected: ["ok", "Mit der Konsole verbunden"],
    connecting: ["busy", "Warte auf die Konsole – dort „Controller → Griffweise/Reihenfolge ändern“ öffnen"],
    reconnecting: ["busy", "Verbinde erneut mit der zuletzt gekoppelten Konsole …"],
    initializing: ["busy", "Controller wird gestartet …"],
    disconnected: ["off", "Nicht gekoppelt – auf „Konsole koppeln“ tippen"],
    crashed: ["err", "Controller-Emulation abgestürzt"],
    unavailable: ["err", "Controller-Emulation nicht verfügbar"],
  };

  function handleMessage(msg) {
    if (msg.type === "proxy") {
      if (msg.status === "connected") {
        setPill("st-bridge", "ok", "Mit der Bridge verbunden");
      } else if (msg.status === "unreachable") {
        setPill("st-bridge", "err", "Bridge nicht erreichbar: " + (msg.error || ""));
        $("novideo-text").textContent = "Raspberry Pi 5 Bridge nicht erreichbar – IP und Ports in der Add-on-Konfiguration prüfen.";
      }
    } else if (msg.type === "status") {
      const sw = msg.switch || {};
      const t = SWITCH_TEXT[sw.state] || ["off", sw.state || "unbekannt"];
      setPill("st-switch", t[0], t[1] + (sw.detail ? " (" + sw.detail + ")" : ""));
      const cap = msg.capture || {};
      if (cap.state === "ok") {
        setPill("st-capture", "ok", "Videosignal " + (cap.width || "") + "×" + (cap.height || "") + " @ " + (cap.fps || "?") + " fps");
      } else if (cap.state === "no_signal") {
        setPill("st-capture", "busy", "Capture-Card gefunden, aber kein Bild – Konsole an? " + (cap.detail || ""));
      } else {
        setPill("st-capture", "err", "Capture-Card nicht verfügbar: " + (cap.detail || ""));
      }
      if (!capturePlaying) {
        $("novideo-text").textContent = cap.state === "ok" ? "Video wird gestartet …" : "Kein Videosignal von der Capture-Card.";
      }
    } else if (msg.type === "pong") {
      lastPong = performance.now();
      const ms = Math.max(0, Math.round(lastPong - msg.t));
      $("ping-value").textContent = ms + " ms";
      setPill("st-ping", ms < 60 ? "ok" : ms < 150 ? "busy" : "err", "Antwortzeit Browser ↔ Bridge");
    } else if (msg.type === "info") {
      toast(msg.message);
    } else if (msg.type === "error") {
      toast(msg.message, true);
    }
  }

  setInterval(() => send({ type: "heartbeat", t: performance.now() }), HEARTBEAT_MS);

  // ------------------------------------------------------------------ Video
  const video = $("video");
  const mjpeg = $("mjpeg");
  let pc = null;
  let mode = "webrtc";
  let capturePlaying = false;
  let webrtcTimer = null;
  let mjpegRetry = null;

  function showVideo(which) {
    capturePlaying = !!which;
    video.hidden = which !== "webrtc";
    mjpeg.hidden = which !== "mjpeg";
    $("novideo").hidden = !!which;
    $("st-mode").textContent = which === "webrtc" ? "· WebRTC" : which === "mjpeg" ? "· MJPEG" : "";
  }

  function stopVideo() {
    clearTimeout(webrtcTimer);
    clearTimeout(mjpegRetry);
    if (pc) {
      pc.close();
      pc = null;
    }
    video.srcObject = null;
    mjpeg.onload = null;
    mjpeg.onerror = null;
    mjpeg.removeAttribute("src");
    showVideo(null);
  }

  function startVideo(wanted) {
    stopVideo();
    mode = wanted;
    if (wanted === "webrtc" && window.RTCPeerConnection) startWebRTC();
    else startMjpeg();
  }

  function fallbackToMjpeg(reason) {
    if (mode !== "webrtc") return;
    toast("WebRTC nicht möglich (" + reason + ") – Video läuft jetzt über MJPEG.");
    startVideo("mjpeg");
  }

  function waitIce(conn, timeout) {
    if (conn.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => {
        conn.removeEventListener("icegatheringstatechange", check);
        resolve();
      };
      const check = () => { if (conn.iceGatheringState === "complete") done(); };
      conn.addEventListener("icegatheringstatechange", check);
      setTimeout(done, timeout);
    });
  }

  async function startWebRTC() {
    const conn = new RTCPeerConnection({ iceServers: [] });
    pc = conn;
    conn.addTransceiver("video", { direction: "recvonly" });
    conn.ontrack = (ev) => {
      video.srcObject = ev.streams[0] || new MediaStream([ev.track]);
      video.play().catch(() => {});
    };
    conn.onconnectionstatechange = () => {
      if (conn !== pc) return;
      if (conn.connectionState === "connected") {
        clearTimeout(webrtcTimer);
        showVideo("webrtc");
      } else if (conn.connectionState === "failed") {
        fallbackToMjpeg("Verbindung fehlgeschlagen");
      } else if (conn.connectionState === "disconnected" && capturePlaying) {
        webrtcTimer = setTimeout(() => {
          if (conn === pc && conn.connectionState !== "connected") startVideo("webrtc");
        }, 3000);
      }
    };
    webrtcTimer = setTimeout(() => {
      if (conn === pc && conn.connectionState !== "connected") fallbackToMjpeg("Pi nicht direkt erreichbar");
    }, WEBRTC_TIMEOUT_MS);
    try {
      const offer = await conn.createOffer();
      await conn.setLocalDescription(offer);
      await waitIce(conn, 2000);
      const res = await fetch("video/offer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sdp: conn.localDescription.sdp, type: conn.localDescription.type }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const answer = await res.json();
      if (conn !== pc) return;
      await conn.setRemoteDescription(answer);
    } catch (err) {
      if (conn === pc) fallbackToMjpeg(err.message);
    }
  }

  function startMjpeg() {
    mjpeg.onload = () => showVideo("mjpeg");
    mjpeg.onerror = () => {
      showVideo(null);
      mjpegRetry = setTimeout(() => {
        mjpeg.src = "video/stream.mjpg?t=" + Date.now();
      }, 2000);
    };
    mjpeg.src = "video/stream.mjpg?t=" + Date.now();
  }

  // ------------------------------------------------------------------ Eingaben zusammenführen
  let touchState = window.VirtualGamepad.emptyState();
  let padState = window.VirtualGamepad.emptyState();
  let lastSent = "";
  let sendQueued = false;

  function bigger(a, b) {
    return Math.hypot(a[0], a[1]) >= Math.hypot(b[0], b[1]) ? a : b;
  }

  function merged() {
    const buttons = [];
    window.VirtualGamepad.BUTTONS.concat(["LS", "RS"]).forEach((name) => {
      if (touchState.buttons[name] || padState.buttons[name]) buttons.push(name);
    });
    return { type: "input", buttons, ls: bigger(touchState.ls, padState.ls), rs: bigger(touchState.rs, padState.rs) };
  }

  function queueSend() {
    if (sendQueued) return;
    sendQueued = true;
    requestAnimationFrame(() => {
      sendQueued = false;
      const msg = merged();
      const key = JSON.stringify(msg);
      if (key !== lastSent) {
        lastSent = key;
        send(msg);
      }
    });
  }

  // ------------------------------------------------------------------ Touch-Gamepad
  const vpad = new window.VirtualGamepad($("gamepad"), {
    haptics: true,
    onChange: (state) => {
      touchState = state;
      queueSend();
    },
  });

  function setTouch(visible, remember) {
    $("touch-toggle").checked = visible;
    vpad.show(visible);
    if (remember) store("switch2-touch", visible);
  }

  $("touch-toggle").addEventListener("change", (e) => setTouch(e.target.checked, true));

  // ------------------------------------------------------------------ Echte Controller (Gamepad API)
  // Ziele in der Tabelle; Wert = Index der Taste im Standard-Layout der Gamepad API
  const TARGETS = ["A", "B", "X", "Y", "L", "R", "ZL", "ZR", "MINUS", "PLUS", "LS", "RS",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "HOME", "CAPTURE"];
  const TARGET_NAMES = {
    LS: "Linker Stick drücken", RS: "Rechter Stick drücken", MINUS: "−", PLUS: "+",
    DPAD_UP: "Steuerkreuz ↑", DPAD_DOWN: "Steuerkreuz ↓", DPAD_LEFT: "Steuerkreuz ←", DPAD_RIGHT: "Steuerkreuz →",
    CAPTURE: "Aufnahme",
  };
  const BASE_MAP = { L: 4, R: 5, ZL: 6, ZR: 7, MINUS: 8, PLUS: 9, LS: 10, RS: 11,
    DPAD_UP: 12, DPAD_DOWN: 13, DPAD_LEFT: 14, DPAD_RIGHT: 15, HOME: 16, CAPTURE: 17 };
  const PRESETS = {
    // Rechte Taste = A wie an der Konsole (Xbox B / PlayStation ○ drückt A)
    position: Object.assign({ A: 1, B: 0, X: 3, Y: 2 }, BASE_MAP),
    // Beschriftung (Xbox A = A) bzw. untere Taste ✕ = A bei PlayStation
    label: Object.assign({ A: 0, B: 1, X: 2, Y: 3 }, BASE_MAP),
  };
  const PRESET_TEXT = {
    xbox: { position: "Wie Konsolen-Anordnung (Xbox B = A)", label: "Nach Beschriftung (Xbox A = A)" },
    ps: { position: "Wie Konsolen-Anordnung (○ = A, ✕ = B)", label: "Bestätigen unten (✕ = A, ○ = B)" },
  };
  const LABELS = {
    xbox: ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu", "LS", "RS", "↑", "↓", "←", "→", "Xbox", "Share"],
    ps: ["✕", "○", "□", "△", "L1", "R1", "L2", "R2", "Create", "Options", "L3", "R3", "↑", "↓", "←", "→", "PS", "Touchpad"],
  };

  let padCfg = load("switch2-pad-v1", null) || { preset: "position", labels: null, map: Object.assign({}, PRESETS.position) };
  let listening = null;            // Ziel, das gerade auf einen Tastendruck wartet
  let listenTimer = null;
  let prevPressed = [];
  let activePad = null;
  let polling = false;

  function detectLabels(id) {
    const s = (id || "").toLowerCase();
    // Xbox zuerst prüfen: "Xbox Wireless Controller" enthält auch "wireless controller"
    if (/xbox|045e|xinput/.test(s)) return "xbox";
    if (/054c|dualsense|dualshock|playstation|wireless controller|ps[345]/.test(s)) return "ps";
    return "xbox";
  }

  function labelSet() {
    return padCfg.labels || (activePad ? detectLabels(activePad.id) : "xbox");
  }

  function keyName(index) {
    const names = LABELS[labelSet()];
    return index === undefined || index === null ? "–" : names[index] || "Taste " + index;
  }

  function savePad() {
    store("switch2-pad-v1", padCfg);
  }

  function renderPresetOptions() {
    const sel = $("pad-preset");
    const texts = PRESET_TEXT[labelSet()];
    sel.innerHTML = "";
    [["position", texts.position], ["label", texts.label], ["custom", "Eigene Belegung"]].forEach(([value, text]) => {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = text;
      sel.appendChild(opt);
    });
    sel.value = padCfg.preset;
    $("pad-labels").value = labelSet();
  }

  function renderMap() {
    const tbody = $("pad-map").querySelector("tbody");
    tbody.innerHTML = "";
    TARGETS.forEach((target) => {
      const tr = document.createElement("tr");
      if (listening === target) tr.className = "active";
      const name = document.createElement("td");
      name.textContent = TARGET_NAMES[target] || target;
      const key = document.createElement("td");
      key.innerHTML = '<span class="key"></span>';
      key.firstChild.textContent = keyName(padCfg.map[target]);
      const act = document.createElement("td");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = listening === target ? "Drücken …" : "Ändern";
      btn.addEventListener("click", () => startListen(target));
      act.appendChild(btn);
      tr.append(name, key, act);
      tbody.appendChild(tr);
    });
  }

  function renderPanel() {
    if (activePad) {
      $("pad-detected").textContent = "Verbunden: " + activePad.id + (activePad.mapping === "standard" ? "" : " (kein Standard-Layout – Tasten bitte prüfen)");
    } else {
      $("pad-detected").textContent = "Kein Controller erkannt. Verbinde einen Controller per USB oder Bluetooth und drücke eine Taste.";
    }
    renderPresetOptions();
    renderMap();
  }

  function startListen(target) {
    if (!activePad) {
      toast("Erst einen Controller verbinden und eine Taste drücken.", true);
      return;
    }
    listening = target;
    $("pad-listen").hidden = false;
    $("pad-listen").textContent = "Drücke jetzt die Controller-Taste für „" + (TARGET_NAMES[target] || target) + "“ …";
    clearTimeout(listenTimer);
    listenTimer = setTimeout(stopListen, 8000);
    renderMap();
  }

  function stopListen() {
    listening = null;
    $("pad-listen").hidden = true;
    clearTimeout(listenTimer);
    renderMap();
  }

  $("pad-preset").addEventListener("change", (e) => {
    const value = e.target.value;
    padCfg.preset = value;
    if (PRESETS[value]) padCfg.map = Object.assign({}, PRESETS[value]);
    savePad();
    renderPanel();
  });

  $("pad-labels").addEventListener("change", (e) => {
    padCfg.labels = e.target.value;
    savePad();
    renderPanel();
  });

  $("pad-reset").addEventListener("click", () => {
    const preset = PRESETS[padCfg.preset] ? padCfg.preset : "position";
    padCfg = { preset, labels: null, map: Object.assign({}, PRESETS[preset]) };
    savePad();
    stopListen();
    renderPanel();
    toast("Belegung zurückgesetzt");
  });

  function openPanel(open) {
    $("pad-panel").hidden = !open;
    $("pad-btn").setAttribute("aria-expanded", open ? "true" : "false");
    if (open) renderPanel();
    else stopListen();
  }

  $("pad-btn").addEventListener("click", () => openPanel($("pad-panel").hidden));
  $("pad-close").addEventListener("click", () => openPanel(false));

  function axisPair(x, y) {
    let mag = Math.hypot(x, y);
    if (mag > 1) {
      x /= mag;
      y /= mag;
      mag = 1;
    }
    if (mag < DEADZONE) return [0, 0];
    const scale = (mag - DEADZONE) / (1 - DEADZONE) / mag;
    return [Math.round(x * scale * 1000) / 10, Math.round(-y * scale * 1000) / 10];
  }

  function readPads() {
    const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
    if (!pads.length) {
      if (activePad) {
        activePad = null;
        padState = window.VirtualGamepad.emptyState();
        queueSend();
        if (!$("pad-panel").hidden) renderPanel();
      }
      polling = false;
      return;
    }
    const gp = pads.find((p) => p.connected) || pads[0];
    if (!activePad || activePad.id !== gp.id) {
      activePad = { id: gp.id, mapping: gp.mapping };
      if (!$("pad-panel").hidden) renderPanel();
    }
    const pressed = gp.buttons.map((b) => (typeof b === "object" ? b.pressed || b.value > 0.5 : b > 0.5));

    if (listening) {
      const idx = pressed.findIndex((p, i) => p && !prevPressed[i]);
      if (idx >= 0) {
        // War die Taste schon belegt, bekommt die andere Funktion die bisherige Taste (Tausch)
        const other = TARGETS.find((t) => t !== listening && padCfg.map[t] === idx);
        if (other) padCfg.map[other] = padCfg.map[listening];
        padCfg.map[listening] = idx;
        padCfg.preset = "custom";
        savePad();
        const done = listening;
        stopListen();
        renderPanel();
        toast((TARGET_NAMES[done] || done) + " = " + keyName(idx));
      }
    }
    prevPressed = pressed;

    const state = window.VirtualGamepad.emptyState();
    state.buttons.LS = false;
    state.buttons.RS = false;
    TARGETS.forEach((target) => {
      const idx = padCfg.map[target];
      state.buttons[target] = idx !== undefined && idx !== null && !!pressed[idx];
    });
    const ax = gp.axes || [];
    state.ls = axisPair(ax[0] || 0, ax[1] || 0);
    state.rs = axisPair(ax[2] || 0, ax[3] || 0);
    if (listening) state.buttons = Object.fromEntries(Object.keys(state.buttons).map((k) => [k, false]));
    padState = state;
    queueSend();
    requestAnimationFrame(readPads);
  }

  function startPolling() {
    if (polling) return;
    polling = true;
    requestAnimationFrame(readPads);
  }

  window.addEventListener("gamepadconnected", (e) => {
    toast("Controller verbunden: " + e.gamepad.id);
    startPolling();
  });
  window.addEventListener("gamepaddisconnected", () => toast("Controller getrennt"));

  // ------------------------------------------------------------------ Leiste, Koppeln, Vollbild
  $("pair-btn").addEventListener("click", () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      toast("Keine Verbindung zur Bridge.", true);
      return;
    }
    send({ type: "system", action: "pair" });
    toast("Kopplung gestartet – auf der Konsole „Controller → Griffweise/Reihenfolge ändern“ öffnen.");
  });

  $("fs-btn").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
        return;
      }
      await document.documentElement.requestFullscreen({ navigationUI: "hide" });
      if (screen.orientation && screen.orientation.lock) screen.orientation.lock("landscape").catch(() => {});
    } catch (err) {
      toast("Vollbild ist im eingebetteten Fenster nicht erlaubt – die Seite in einem eigenen Browser-Tab öffnen.", true);
    }
  });

  function setBar(collapsed) {
    $("topbar").classList.toggle("collapsed", collapsed);
    $("bar-toggle").setAttribute("aria-expanded", collapsed ? "false" : "true");
    store("switch2-bar-collapsed", collapsed);
  }

  $("bar-toggle").addEventListener("click", () => setBar(!$("topbar").classList.contains("collapsed")));

  // Tab im Hintergrund: alles loslassen (die Bridge lässt nach 2 s ohnehin los)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      vpad.releaseAll();
      padState = window.VirtualGamepad.emptyState();
      queueSend();
    } else {
      requestWakeLock();
    }
  });

  let wakeLock = null;
  async function requestWakeLock() {
    try {
      if ("wakeLock" in navigator && !wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      }
    } catch (err) { /* nicht erlaubt */ }
  }

  // ------------------------------------------------------------------ Start
  async function init() {
    try {
      const res = await fetch("api/config", { cache: "no-store" });
      if (res.ok) Object.assign(config, await res.json());
    } catch (err) { /* Standardwerte */ }
    vpad.setHaptics(config.haptic_feedback);
    setTouch(load("switch2-touch", config.default_touch_overlay), false);
    setBar(load("switch2-bar-collapsed", false));
    connect();
    startVideo(config.stream_mode);
    requestWakeLock();
    if (navigator.getGamepads && Array.from(navigator.getGamepads()).some(Boolean)) startPolling();
  }

  init();
})();
