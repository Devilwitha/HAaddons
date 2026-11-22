/* Game Remote – Oberfläche.
 *
 * - WebSocket "ws" (über den Ingress-Server an die Bridge): Eingaben senden, Status empfangen
 * - Video: WebRTC über "video/offer", automatischer Wechsel auf MJPEG ("video/stream.mjpg"),
 *   wenn WebRTC nicht zustande kommt (z. B. unterwegs über Nabu Casa)
 * - Eingaben aus Touch-Gamepad und echten Controllern (HTML5 Gamepad API) werden zusammengeführt
 *
 * - Eingaben gehen bei WebRTC über den Datenkanal "input" direkt an den Pi (mit "seq"), sonst über den WebSocket
 *
 * Nachrichten an die Bridge:
 *   {"type":"input","buttons":["A","DPAD_UP",...],"ls":[x,y],"rs":[x,y]}   x/y: -100..100, y oben positiv
 *   {"type":"heartbeat","t":<ms>}                                          Antwort: {"type":"pong","t":<ms>}
 *   {"type":"system","action":"pair"}
 *   {"type":"system","action":"capture_restart"}            Capture-Card per USB-Reset neu starten
 *   {"type":"system","action":"video_mode","mode":"1080p30"} Bildqualität umschalten
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DEADZONE = 0.12;
  const HEARTBEAT_MS = 500;
  const WEBRTC_TIMEOUT_MS = 8000;
  const INPUT_REPEAT_MS = 50;
  const PAD_POLL_MS = 4;

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
  let lastStatus = null;
  let wsPing = null;

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
      if (lastInput && !channelOpen()) send(lastInput);
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

  // Eingaben gehen bevorzugt über den WebRTC-Datenkanal direkt an den Pi (ohne Umweg über Home Assistant,
  // ungeordnet und ohne Wiederholung = kein Warten auf verlorene Pakete). Damit ein verlorenes Paket keine Taste
  // hängen lässt, wird der aktuelle Zustand alle INPUT_REPEAT_MS erneut geschickt; "seq" verwirft veraltete Pakete.
  let inputChannel = null;
  let inputSeq = 0;
  let lastInput = null;

  function channelOpen() {
    return inputChannel && inputChannel.readyState === "open";
  }

  function sendInput(msg) {
    lastInput = msg;
    if (channelOpen()) {
      inputSeq += 1;
      try {
        inputChannel.send(JSON.stringify(Object.assign({ seq: inputSeq }, msg)));
        return;
      } catch (err) { /* Kanal gerade zu – über den WebSocket */ }
    }
    send(msg);
  }

  setInterval(() => {
    if (lastInput && channelOpen()) sendInput(lastInput);
  }, INPUT_REPEAT_MS);

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
      lastStatus = msg;
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
      wsPing = ms;
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
  const audio = $("audio");
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
    if (inputChannel) {
      inputChannel = null;
      if (lastInput) send(lastInput);
    }
    video.srcObject = null;
    audio.srcObject = null;
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
    conn.addTransceiver("audio", { direction: "recvonly" });
    const channel = conn.createDataChannel("input", { ordered: false, maxRetransmits: 0 });
    channel.onopen = () => {
      if (conn !== pc) return;
      inputChannel = channel;
      // Was bisher über den WebSocket kam, dort loslassen – ab jetzt zählt der Datenkanal
      send({ type: "input", buttons: [], ls: [0, 0], rs: [0, 0] });
      if (lastInput) sendInput(lastInput);
    };
    channel.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (err) { return; }
      if (msg.type === "pong" && typeof msg.t === "number") dcPing = Math.max(0, Math.round(performance.now() - msg.t));
    };
    channel.onclose = () => {
      if (inputChannel !== channel) return;
      inputChannel = null;
      if (lastInput) send(lastInput);        // weiter über den WebSocket
    };
    conn.ontrack = (ev) => {
      // Möglichst kleiner Puffer im Browser: Bilder sofort anzeigen statt für gleichmäßige Wiedergabe zu sammeln
      try {
        if ("jitterBufferTarget" in ev.receiver) ev.receiver.jitterBufferTarget = 0;
        if ("playoutDelayHint" in ev.receiver) ev.receiver.playoutDelayHint = 0;
      } catch (err) { /* ältere Browser */ }
      if (ev.track.kind === "audio") {
        audio.srcObject = new MediaStream([ev.track]);
        applySound();
        return;
      }
      video.srcObject = new MediaStream([ev.track]);
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

  // Sofort senden (nicht erst zum nächsten Bild); mehrere Änderungen im selben Ereignis werden zusammengefasst
  function queueSend() {
    if (sendQueued) return;
    sendQueued = true;
    queueMicrotask(() => {
      sendQueued = false;
      const msg = merged();
      const key = JSON.stringify(msg);
      if (key !== lastSent) {
        lastSent = key;
        sendInput(msg);
        reactionInput(msg);
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
    pro: { position: "Nach Beschriftung (A = A, B = B)", label: "Vertauscht (B-Taste = A)" },
  };
  const LABELS = {
    xbox: ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu", "LS", "RS", "↑", "↓", "←", "→", "Xbox", "Share"],
    ps: ["✕", "○", "□", "△", "L1", "R1", "L2", "R2", "Create", "Options", "L3", "R3", "↑", "↓", "←", "→", "PS", "Touchpad"],
    pro: ["B", "A", "Y", "X", "L", "R", "ZL", "ZR", "−", "+", "LS", "RS", "↑", "↓", "←", "→", "HOME", "Aufnahme"],
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
    if (/057e|pro controller|joy-con|nintendo/.test(s)) return "pro";
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
    if (open) openStats(false);
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
    // Öfter als einmal pro Bild abfragen: ein Tastendruck ist so spätestens nach 4 ms unterwegs
    setTimeout(readPads, PAD_POLL_MS);
  }

  function startPolling() {
    if (polling) return;
    polling = true;
    setTimeout(readPads, 0);
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
    // Antwort (läuft schon / gestartet / Fehler) kommt von der Bridge; kurz sperren gegen Doppelklicks
    send({ type: "system", action: "pair" });
    toast("Kopplung wird vorbereitet …");
    $("pair-btn").disabled = true;
    setTimeout(() => { $("pair-btn").disabled = false; }, 5000);
  });

  // ------------------------------------------------------------------ Bildqualität und Capture-Card
  $("video-mode").addEventListener("change", (ev) => {
    send({ type: "system", action: "video_mode", mode: ev.target.value });
    toast("Bildqualität wird umgestellt …");
  });

  $("cap-restart").addEventListener("click", () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      toast("Keine Verbindung zur Bridge.", true);
      return;
    }
    send({ type: "system", action: "capture_restart" });
    toast("Capture-Card wird neu gestartet …");
    $("cap-restart").disabled = true;
    setTimeout(() => { $("cap-restart").disabled = false; }, 8000);
  });

  function renderVideoControls(stream, cap) {
    const modes = Array.isArray(stream.modes) ? stream.modes : [];
    const select = $("video-mode");
    const key = modes.map((m) => m.id).join(",");
    if (select.dataset.key !== key) {
      select.dataset.key = key;
      select.replaceChildren(...modes.map((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.label;
        return opt;
      }));
    }
    if (document.activeElement !== select && stream.mode) select.value = stream.mode;
    $("video-mode-field").hidden = modes.length === 0;
    $("cap-restart").hidden = !cap.can_restart;
    $("stats-actions").hidden = modes.length === 0 && !cap.can_restart;
  }

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

  // ------------------------------------------------------------------ Messwerte
  let dcPing = null;
  let statsTimer = null;
  let prevRtc = null;

  function setVal(id, text, level) {
    const cell = $(id);
    cell.textContent = text;
    cell.className = level || "";
  }

  // Grenzwerte: bis "good" grün, bis "warn" gelb, darüber rot
  function grade(value, good, warn) {
    if (value === null || value === undefined || Number.isNaN(value)) return "";
    return value <= good ? "good" : value <= warn ? "warn" : "bad";
  }

  function ms(value) {
    return value === null || value === undefined || Number.isNaN(value) ? "–" : Math.round(value) + " ms";
  }

  async function updateStats() {
    if (channelOpen()) {
      try { inputChannel.send(JSON.stringify({ type: "heartbeat", t: performance.now() })); } catch (err) { /* zu */ }
    } else {
      dcPing = null;
    }
    const st = lastStatus || {};
    const cap = st.capture || {};
    const stream = st.stream || {};
    const rtc = stream.webrtc || null;
    const wanted = cap.fps || 0;
    renderVideoControls(stream, cap);
    // Ampel nach der gewählten Bildqualität (60 oder 30 fps), ältere Bridges ohne Angabe: 60
    const capTarget = cap.target_fps || 60;
    const sendTarget = stream.target_fps || 60;
    setVal("sv-capture", cap.state === "ok" ? (cap.width + "×" + cap.height + " · " + cap.fps + " fps") : (cap.detail || cap.state || "–"),
      cap.state === "ok" ? grade(-cap.fps, -(capTarget - 5), -(capTarget / 2 - 2)) : "bad");
    if (mode === "webrtc" && rtc) {
      setVal("sv-sent", rtc.fps + " fps (WebRTC)", grade(-rtc.fps, -Math.min(sendTarget - 5, wanted - 5), -(sendTarget / 2 - 5)));
      setVal("sv-age", ms(rtc.age), grade(rtc.age, 20, 40));
      setVal("sv-convert", ms(rtc.convert), grade(rtc.convert, 5, 10));
      setVal("sv-encode", ms(rtc.encode), grade(rtc.encode, 16, 33));
    } else {
      setVal("sv-sent", stream.mjpeg_fps !== undefined ? stream.mjpeg_fps + " fps (MJPEG)" : "–");
      ["sv-age", "sv-convert", "sv-encode"].forEach((id) => setVal(id, "–"));
    }
    setVal("sv-ping-dc", channelOpen() ? ms(dcPing) : "kein Datenkanal", channelOpen() ? grade(dcPing, 5, 20) : "warn");
    setVal("sv-ping-ws", ms(wsPing), grade(wsPing, 15, 50));
    setVal("sv-input", channelOpen() ? "Datenkanal (direkt)" : "WebSocket (über Home Assistant)", channelOpen() ? "good" : "warn");

    const el = mode === "webrtc" ? video : mjpeg;
    const w = el.videoWidth || el.naturalWidth || 0;
    const h = el.videoHeight || el.naturalHeight || 0;
    setVal("sv-video", (mode === "webrtc" ? "WebRTC" : "MJPEG") + (w ? " · " + w + "×" + h : ""), mode === "webrtc" ? "good" : "warn");
    const sound = soundInfo();
    setVal("sv-audio", sound[0], sound[1]);

    if (mode === "webrtc" && pc) {
      try {
        const report = await pc.getStats();
        let inbound = null;
        report.forEach((r) => { if (r.type === "inbound-rtp" && r.kind === "video") inbound = r; });
        if (inbound) {
          const cur = {
            t: inbound.timestamp, frames: inbound.framesDecoded || 0, decode: inbound.totalDecodeTime || 0,
            jitter: inbound.jitterBufferDelay || 0, emitted: inbound.jitterBufferEmittedCount || 0,
            dropped: inbound.framesDropped || 0, bytes: inbound.bytesReceived || 0,
          };
          if (prevRtc) {
            const frames = cur.frames - prevRtc.frames;
            const seconds = (cur.t - prevRtc.t) / 1000;
            const fps = seconds > 0 ? frames / seconds : 0;
            const emitted = cur.emitted - prevRtc.emitted;
            setVal("sv-fps", Math.round(inbound.framesPerSecond || fps) + " fps",
              grade(-(inbound.framesPerSecond || fps), -(sendTarget * 5 / 6), -(sendTarget / 2 - 5)));
            setVal("sv-decode", frames > 0 ? ms(((cur.decode - prevRtc.decode) / frames) * 1000) : "–",
              frames > 0 ? grade(((cur.decode - prevRtc.decode) / frames) * 1000, 8, 16) : "");
            const jitter = emitted > 0 ? ((cur.jitter - prevRtc.jitter) / emitted) * 1000 : null;
            setVal("sv-jitter", ms(jitter), grade(jitter, 20, 50));
            setVal("sv-dropped", String(cur.dropped), cur.dropped - prevRtc.dropped > 0 ? "warn" : "good");
            const mbit = seconds > 0 ? ((cur.bytes - prevRtc.bytes) * 8) / seconds / 1e6 : 0;
            setVal("sv-rate", mbit.toFixed(1).replace(".", ",") + " Mbit/s", grade(-mbit, -2.5, -1));
          }
          prevRtc = cur;
        }
      } catch (err) { /* Verbindung gerade weg */ }
    } else {
      prevRtc = null;
      ["sv-fps", "sv-jitter", "sv-decode", "sv-dropped", "sv-rate"].forEach((id) => setVal(id, "–"));
    }
  }

  function openStats(open) {
    if (open && !$("pad-panel").hidden) openPanel(false);
    $("stats-panel").hidden = !open;
    $("stats-btn").setAttribute("aria-expanded", open ? "true" : "false");
    clearInterval(statsTimer);
    prevRtc = null;
    if (open) {
      updateStats();
      statsTimer = setInterval(updateStats, 1000);
    } else {
      stopReaction();
    }
  }

  $("stats-btn").addEventListener("click", () => openStats($("stats-panel").hidden));
  $("stats-close").addEventListener("click", () => openStats(false));

  // ------------------------------------------------------------------ Reaktionstest
  // Beim Drücken einer Taste wird das aktuelle Bild verkleinert gemerkt; danach wird jedes neue Bild
  // damit verglichen. Ändert sich ein spürbarer Teil des Bildes, ist die Zeit seit dem Tastendruck das Ergebnis.
  const RT_W = 64;
  const RT_H = 36;
  const RT_TIMEOUT_MS = 1500;
  const rtCanvas = document.createElement("canvas");
  rtCanvas.width = RT_W;
  rtCanvas.height = RT_H;
  const rtCtx = rtCanvas.getContext("2d", { willReadFrequently: true });
  let rtActive = false;
  let rtPressed = false;
  let rtMeasure = null;            // { t0, ref }
  let rtResults = [];

  function grab() {
    const el = mode === "webrtc" ? video : mjpeg;
    if (!(el.videoWidth || el.naturalWidth)) return null;
    try {
      rtCtx.drawImage(el, 0, 0, RT_W, RT_H);
      const data = rtCtx.getImageData(0, 0, RT_W, RT_H).data;
      const gray = new Uint8Array(RT_W * RT_H);
      for (let i = 0, j = 0; i < data.length; i += 4, j += 1) gray[j] = (data[i] * 3 + data[i + 1] * 6 + data[i + 2]) / 10;
      return gray;
    } catch (err) {
      return null;
    }
  }

  function changed(a, b) {
    let count = 0;
    for (let i = 0; i < a.length; i += 1) if (Math.abs(a[i] - b[i]) > 24) count += 1;
    return count > a.length * 0.01;
  }

  function median(list) {
    const sorted = list.slice().sort((x, y) => x - y);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function showReaction(text, level) {
    const el = $("rt-result");
    el.textContent = text;
    el.className = "rt-result " + (level || "");
  }

  function watchFrame() {
    if (!rtMeasure) return;
    const now = performance.now();
    const elapsed = now - rtMeasure.t0;
    const cur = grab();
    if (cur && changed(rtMeasure.ref, cur)) {
      rtMeasure = null;
      rtResults = rtResults.concat([elapsed]).slice(-7);
      const med = median(rtResults);
      showReaction("Letzte " + Math.round(elapsed) + " ms · Mittel " + Math.round(med) + " ms (" + rtResults.length + "×)",
        grade(med, 100, 160));
      return;
    }
    if (elapsed > RT_TIMEOUT_MS) {
      rtMeasure = null;
      showReaction("Keine Bildänderung erkannt – eine Taste nehmen, die sofort etwas im Bild ändert", "warn");
      return;
    }
    if (mode === "webrtc" && video.requestVideoFrameCallback) video.requestVideoFrameCallback(watchFrame);
    else requestAnimationFrame(watchFrame);
  }

  // Wird bei jeder gesendeten Eingabe aufgerufen: misst beim Übergang "nichts gedrückt" -> "Taste gedrückt"
  function reactionInput(msg) {
    const pressed = msg.buttons.length > 0;
    const rising = pressed && !rtPressed;
    rtPressed = pressed;
    if (!rtActive || !rising || rtMeasure) return;
    const ref = grab();
    if (!ref) {
      showReaction("Kein Videobild", "bad");
      return;
    }
    rtMeasure = { t0: performance.now(), ref };
    showReaction("Messe …");
    if (mode === "webrtc" && video.requestVideoFrameCallback) video.requestVideoFrameCallback(watchFrame);
    else requestAnimationFrame(watchFrame);
  }

  function stopReaction() {
    rtActive = false;
    rtMeasure = null;
    $("rt-btn").textContent = "Test starten";
    $("rt-hint").hidden = true;
  }

  $("rt-btn").addEventListener("click", () => {
    if (rtActive) {
      stopReaction();
      return;
    }
    rtActive = true;
    rtResults = [];
    $("rt-btn").textContent = "Test beenden";
    $("rt-hint").hidden = false;
    $("rt-hint").textContent = "Jetzt eine Taste drücken – jeder Druck ist eine Messung. Am Touch-Gamepad eine Taste links nehmen (L, Steuerkreuz), rechts liegt dieses Feld darüber.";
    showReaction("Warte auf Tastendruck …");
  });

  // ------------------------------------------------------------------ Ton
  // Der Ton kommt als eigene WebRTC-Spur (nur bei WebRTC, nicht bei MJPEG). Browser spielen Ton erst nach
  // einer Berührung oder einem Klick ab – darum startet er spätestens beim ersten Tippen auf die Seite.
  let soundWanted = true;
  try { soundWanted = localStorage.getItem("sw2-sound") !== "off"; } catch (err) { /* ohne Speicher */ }

  function soundInfo() {
    const a = (lastStatus && lastStatus.stream && lastStatus.stream.audio) || null;
    if (mode !== "webrtc") return ["Ton gibt es nur über WebRTC", "warn"];
    if (!audio.srcObject) return [a && a.state === "disabled" ? "am Pi abgeschaltet" : "keine Tonspur", "warn"];
    if (a && a.state === "unavailable") return [a.detail || "keine Ton-Quelle", "bad"];
    if (!soundWanted) return ["stumm", ""];
    if (audio.paused) return ["zum Abspielen einmal tippen", "warn"];
    return ["läuft" + (a && a.device ? " (" + a.device + ")" : ""), "good"];
  }

  function updateSoundBtn() {
    const btn = $("sound-btn");
    btn.innerHTML = (soundWanted ? "&#128266;" : "&#128263;") + " Ton";
    btn.setAttribute("aria-pressed", soundWanted ? "true" : "false");
    btn.title = "Ton " + (soundWanted ? "ausschalten" : "einschalten") + " – " + soundInfo()[0];
  }

  function applySound() {
    audio.muted = !soundWanted;
    if (soundWanted && audio.srcObject && audio.paused) audio.play().then(updateSoundBtn, updateSoundBtn);
    updateSoundBtn();
  }

  $("sound-btn").addEventListener("click", () => {
    soundWanted = !soundWanted;
    try { localStorage.setItem("sw2-sound", soundWanted ? "on" : "off"); } catch (err) { /* ohne Speicher */ }
    applySound();
  });
  ["pointerdown", "keydown"].forEach((type) => document.addEventListener(type, () => {
    if (soundWanted && audio.srcObject && audio.paused) applySound();
  }, true));
  setInterval(updateSoundBtn, 2000);

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
