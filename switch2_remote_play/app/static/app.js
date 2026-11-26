/* Game Remote – Oberfläche.
 *
 * - WebSocket "ws" (über den Ingress-Server an die Bridge): Eingaben senden, Status empfangen
 * - Video: WebRTC über "video/offer" (der Pi kodiert) oder Einzelbilder über den WebSocket "video/frames"
 *   (JPEG, der Browser dekodiert). Wählbar unter Messwerte → Übertragung; kommt WebRTC nicht zustande
 *   (z. B. unterwegs über Nabu Casa), wird automatisch auf Einzelbilder gewechselt
 * - Eingaben aus Touch-Gamepad und echten Controllern (HTML5 Gamepad API) werden zusammengeführt
 *
 * - Eingaben gehen bei WebRTC über den Datenkanal "input" direkt an den Pi (mit "seq"), sonst über den WebSocket
 *
 * Nachrichten an die Bridge:
 *   {"type":"input","buttons":["A","DPAD_UP",...],"ls":[x,y],"rs":[x,y]}   x/y: -100..100, y oben positiv
 *   {"type":"heartbeat","t":<ms>}                                          Antwort: {"type":"pong","t":<ms>}
 *   {"type":"system","action":"pair"}
 *   {"type":"system","action":"control","active":true}       will steuern (Touch an oder Controller da) – will
 *                                                             niemand steuern, trennt die Bridge das Bluetooth
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
      updateControl(true);
      if (lastInput && !channelOpen()) send(lastInput);
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (err) { return; }
      handleMessage(msg);
    };
    ws.onclose = () => {
      recEvent("bridge", "WebSocket zur Bridge getrennt");
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
    if (rec) rec.acc[channelOpen() ? "inDc" : "inWs"] += 1;
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
    reconnecting: ["busy", "Verbinde mit der gekoppelten Konsole … – Konsole einschalten bzw. aufwecken (andere Konsole: „Konsole koppeln“)"],
    initializing: ["busy", "Controller wird gestartet …"],
    paused: ["off", "Bluetooth aus – niemand steuert (Touch-Steuerung einschalten oder am Controller eine Taste drücken)"],
    disconnected: ["off", "Nicht gekoppelt – auf „Konsole koppeln“ tippen"],
    crashed: ["err", "Controller-Emulation abgestürzt"],
    unavailable: ["err", "Controller-Emulation nicht verfügbar"],
  };

  function handleMessage(msg) {
    if (msg.type === "proxy") {
      if (msg.status === "connected") {
        setPill("st-bridge", "ok", "Mit der Bridge verbunden");
      } else if (msg.status === "unreachable") {
        recEvent("bridge", "Bridge nicht erreichbar: " + (msg.error || ""));
        setPill("st-bridge", "err", "Bridge nicht erreichbar: " + (msg.error || ""));
        $("novideo-text").textContent = "Raspberry Pi 5 Bridge nicht erreichbar – IP und Ports in der Add-on-Konfiguration prüfen.";
      }
    } else if (msg.type === "status") {
      lastStatus = msg;
      const sw = msg.switch || {};
      const t = SWITCH_TEXT[sw.state] || ["off", sw.state || "unbekannt"];
      setPill("st-switch", t[0], t[1] + (sw.detail ? " (" + sw.detail + ")" : ""));
      const cap = msg.capture || {};
      if (cap.state === "ok" && cap.blank) {
        setPill("st-capture", "busy", "Bild seit " + cap.blank + " s schwarz – Konsole im Standby? Sonst HDMI-Kabel prüfen");
      } else if (cap.state === "ok") {
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
      if (rec) rec.acc.ws.push(ms);
      $("ping-value").textContent = ms + " ms";
      setPill("st-ping", ms < 60 ? "ok" : ms < 150 ? "busy" : "err", "Antwortzeit Browser ↔ Bridge");
    } else if (msg.type === "record") {
      recBridgeMessage(msg);
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
  const frames = $("frames");
  const framesCtx = frames.getContext("2d", { alpha: false, desynchronized: true });
  let pc = null;
  let mode = "webrtc";
  let capturePlaying = false;
  let webrtcTimer = null;
  let framesWs = null;
  let framesRetry = null;
  // Messwerte der Einzelbilder (im Browser gezählt)
  const fstat = { frames: 0, bytes: 0, decode: 0, skipped: 0, info: null, last: -2 };

  function showVideo(which) {
    capturePlaying = !!which;
    video.hidden = which !== "webrtc";
    frames.hidden = which !== "jpeg";
    $("novideo").hidden = !!which;
    $("st-mode").textContent = which === "webrtc" ? "· WebRTC" : which === "jpeg" ? "· Einzelbilder" : "";
  }

  function stopVideo() {
    clearTimeout(webrtcTimer);
    clearTimeout(framesRetry);
    if (framesWs) {
      const ws = framesWs;
      framesWs = null;
      try { ws.close(); } catch (err) { /* schon zu */ }
    }
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
    showVideo(null);
  }

  function startVideo(wanted) {
    stopVideo();
    mode = wanted === "webrtc" && window.RTCPeerConnection ? "webrtc" : "jpeg";
    if (mode === "webrtc") startWebRTC();
    else startFrames();
  }

  function fallbackToJpeg(reason) {
    if (mode !== "webrtc") return;
    toast("WebRTC nicht möglich (" + reason + ") – Video läuft jetzt als Einzelbilder über Home Assistant.");
    startVideo("jpeg");
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
      if (msg.type === "pong" && typeof msg.t === "number") {
        dcPing = Math.max(0, Math.round(performance.now() - msg.t));
        if (rec) rec.acc.dc.push(dcPing);
      }
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
      recEvent("webrtc", "WebRTC-Verbindung: " + conn.connectionState);
      if (conn.connectionState === "connected") {
        clearTimeout(webrtcTimer);
        showVideo("webrtc");
      } else if (conn.connectionState === "failed") {
        fallbackToJpeg("Verbindung fehlgeschlagen");
      } else if (conn.connectionState === "disconnected" && capturePlaying) {
        webrtcTimer = setTimeout(() => {
          if (conn === pc && conn.connectionState !== "connected") startVideo("webrtc");
        }, 3000);
      }
    };
    webrtcTimer = setTimeout(() => {
      if (conn === pc && conn.connectionState !== "connected") fallbackToJpeg("Pi nicht direkt erreichbar");
    }, WEBRTC_TIMEOUT_MS);
    try {
      const offer = await conn.createOffer();
      await conn.setLocalDescription(offer);
      await waitIce(conn, 2000);
      const res = await fetch("video/offer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sdp: conn.localDescription.sdp, type: conn.localDescription.type, codec: preferredCodec() }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const answer = await res.json();
      if (conn !== pc) return;
      await conn.setRemoteDescription(answer);
    } catch (err) {
      if (conn === pc) fallbackToJpeg(err.message);
    }
  }

  // Einzelbilder: JPEG über WebSocket (läuft durch Ingress), dekodiert wird hier im Browser
  // (createImageBitmap, meist in Hardware). Jedes gezeichnete Bild wird bestätigt; die Bridge schickt
  // höchstens zwei unbestätigte Bilder – bei langsamer Leitung sinkt die Bildrate, das Bild bleibt aktuell.
  function decodeJpeg(blob) {
    if (window.createImageBitmap) return createImageBitmap(blob);
    return new Promise((resolve, reject) => {
      const img = new Image();
      const url = URL.createObjectURL(blob);
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("JPEG")); };
      img.src = url;
    });
  }

  function startFrames() {
    const url = new URL("video/frames", location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(url.href);
    ws.binaryType = "arraybuffer";
    framesWs = ws;
    fstat.last = -2;
    const ack = (id) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ack", id }));
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string" || ev.data.byteLength < 4) return;
      const view = new DataView(ev.data);
      const len = view.getUint32(0);
      let head = {};
      try { head = JSON.parse(new TextDecoder().decode(new Uint8Array(ev.data, 4, len))); } catch (err) { /* ohne Kopf */ }
      const blob = new Blob([new Uint8Array(ev.data, 4 + len)], { type: "image/jpeg" });
      const t0 = performance.now();
      decodeJpeg(blob).then((img) => {
        if (ws !== framesWs) return;
        const id = typeof head.id === "number" ? head.id : -1;
        // Zwei Bilder dekodieren gleichzeitig – ein älteres, das später fertig wird, nicht mehr zeichnen
        if (id === -1 || id > fstat.last) {
          if (frames.width !== img.width || frames.height !== img.height) {
            frames.width = img.width;
            frames.height = img.height;
          }
          framesCtx.drawImage(img, 0, 0);
          fstat.last = id;
          fstat.frames += 1;
          recShown(performance.now());
          fstat.bytes += ev.data.byteLength;
          fstat.decode += performance.now() - t0;
          fstat.info = head;
          if (!capturePlaying) showVideo("jpeg");
        } else {
          fstat.skipped += 1;
        }
        if (img.close) img.close();
        ack(head.id);
      }).catch(() => ack(head.id));
    };
    ws.onclose = () => {
      if (ws !== framesWs) return;
      framesWs = null;
      showVideo(null);
      framesRetry = setTimeout(() => { if (mode === "jpeg" && !framesWs) startFrames(); }, 2000);
    };
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
        const before = lastSent ? JSON.parse(lastSent).buttons : [];
        lastSent = key;
        if (rec && msg.buttons.some((b) => before.indexOf(b) < 0)) rec.presses += 1;
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
    updateControl(false);
    if (remember && !visible && !wantsControl() && lastStatus && (lastStatus.switch || {}).state === "connected") {
      toast("Touch-Steuerung aus – Bluetooth zur Konsole wird in 10 s getrennt (sofern niemand sonst steuert)");
    }
  }

  // Bluetooth zur Konsole nur, solange jemand steuern will: Touch-Steuerung an oder ein Controller am Gerät.
  // Die Bridge trennt den Controller, wenn in keinem offenen Fenster gesteuert wird.
  let controlSent = null;

  function wantsControl() {
    const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).some(Boolean) : false;
    return $("touch-toggle").checked || pads;
  }

  function updateControl(force) {
    const active = wantsControl();
    if (!force && active === controlSent) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    controlSent = active;
    send({ type: "system", action: "control", active });
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
      updateControl(false);
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
    updateControl(false);
  });
  window.addEventListener("gamepaddisconnected", () => {
    toast("Controller getrennt");
    setTimeout(() => updateControl(false), 0);
  });

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

  // Home-Taste über Home Assistant (Option home_button, z. B. ein Servo an der Konsole)
  $("home-btn").addEventListener("click", async () => {
    const btn = $("home-btn");
    btn.disabled = true;
    setTimeout(() => { btn.disabled = false; }, 3000);
    try {
      const res = await fetch("api/home_button", { method: "POST", cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (res.ok) toast("Home-Taste gedrückt");
      else toast(data.error || "Home-Taste fehlgeschlagen (" + res.status + ")", true);
    } catch (err) {
      toast("Home-Taste fehlgeschlagen: keine Verbindung zu Home Assistant", true);
    }
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

  function preferredTransport() {
    let saved = null;
    try { saved = localStorage.getItem("sw2-transport"); } catch (err) { /* ohne Speicher */ }
    if (saved === "webrtc" || saved === "jpeg") return saved;
    return config.stream_mode === "webrtc" ? "webrtc" : "jpeg";
  }

  // Video-Codec für WebRTC: H.264 kodiert der Pi etwa doppelt so schnell wie VP8. Ohne x264 auf dem Pi oder ohne
  // H.264 im Browser nimmt die Bridge von selbst VP8.
  function preferredCodec() {
    let saved = null;
    try { saved = localStorage.getItem("sw2-codec"); } catch (err) { /* ohne Speicher */ }
    return saved === "vp8" ? "vp8" : "h264";
  }

  $("codec").addEventListener("change", (ev) => {
    const value = ev.target.value === "vp8" ? "vp8" : "h264";
    try { localStorage.setItem("sw2-codec", value); } catch (err) { /* ohne Speicher */ }
    prevRtc = null;
    if (mode === "webrtc") startVideo("webrtc");
    toast("Video-Codec: " + (value === "vp8" ? "VP8" : "H.264") + " – Video wird neu verbunden");
  });

  $("transport").addEventListener("change", (ev) => {
    const value = ev.target.value === "webrtc" ? "webrtc" : "jpeg";
    try { localStorage.setItem("sw2-transport", value); } catch (err) { /* ohne Speicher */ }
    prevRtc = null;
    startVideo(value);
    toast(value === "webrtc" ? "Übertragung: Video (der Pi kodiert)" : "Übertragung: Einzelbilder (dein Gerät dekodiert)");
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
    const codecs = Array.isArray(stream.codecs) ? stream.codecs : null;
    $("codec-field").hidden = mode !== "webrtc" || !codecs;
    if (codecs) {
      $("codec").querySelector('option[value="h264"]').disabled = !codecs.includes("h264");
      if (document.activeElement !== $("codec")) $("codec").value = codecs.includes("h264") ? preferredCodec() : "vp8";
    }
    $("cap-restart").hidden = !cap.can_restart;
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
  let prevJpeg = null;

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
    const sys = st.system || null;
    if (sys) {
      const parts = [];
      if (typeof sys.temp === "number") parts.push(Math.round(sys.temp) + " °C");
      if (typeof sys.cpu === "number") parts.push("CPU " + sys.cpu + " %");
      const now = Array.isArray(sys.throttled) ? sys.throttled : [];
      if (now.length) parts.push(now.join(", "));
      else if (sys.throttled_before) parts.push("war gedrosselt");
      // Hänger: längste Verzögerung der Bridge in den letzten Sekunden (über 17 ms fällt bei 60 fps ein Bild aus)
      const lag = typeof sys.lag === "number" ? sys.lag : 0;
      if (lag >= 8) parts.push("Hänger " + lag + " ms");
      const hot = (sys.temp || 0) >= 80 || (sys.cpu || 0) >= 90 || lag > 33;
      const warm = (sys.temp || 0) >= 70 || (sys.cpu || 0) >= 75 || sys.throttled_before || lag > 17;
      setVal("sv-system", parts.join(" · ") || "–", now.length || hot ? "bad" : warm ? "warn" : "good");
      $("sv-system").title = sys.governor ? "CPU-Regler: " + sys.governor : "";
    } else {
      setVal("sv-system", "–");
    }
    // Ampel nach der gewählten Bildqualität (60 oder 30 fps), ältere Bridges ohne Angabe: 60
    const capTarget = cap.target_fps || 60;
    // Einzelbilder haben eine eigene Bildrate (1080p · 60 fps: Einzelbilder 60, Video 30)
    const sendTarget = (mode === "jpeg" ? stream.jpeg_fps : 0) || stream.target_fps || 60;
    // z. B. „1920×1080 · 60 fps · NV12 → 1280×720“: die Karte liefert das HDMI-Signal, der Pi verkleinert
    const capText = cap.width + "×" + cap.height + " · " + cap.fps + " fps" + (cap.format ? " · " + cap.format : "")
      + (Array.isArray(cap.scaled) ? " → " + cap.scaled[0] + "×" + cap.scaled[1] : "");
    // 4K-Signal: Die Cam Link schafft 3840×2160 über USB nur mit etwa 20 fps – Konsole auf 1080p stellen
    const uhd = cap.state === "ok" && (cap.height || 0) > 1080;
    if (cap.state === "ok" && cap.blank) {
      setVal("sv-capture", "Bild schwarz seit " + cap.blank + " s (Konsole im Standby?) · " + capText, "warn");
    } else {
      setVal("sv-capture", cap.state === "ok" ? capText + (uhd ? " · Konsole auf 1080p stellen" : "") : (cap.detail || cap.state || "–"),
        cap.state === "ok" ? (uhd ? "bad" : grade(-cap.fps, -(capTarget - 5), -(capTarget / 2 - 2))) : "bad");
    }
    if (mode === "webrtc" && rtc) {
      setVal("sv-sent", rtc.fps + " fps (WebRTC)", grade(-rtc.fps, -Math.min(sendTarget - 5, wanted - 5), -(sendTarget / 2 - 5)));
      setVal("sv-age", ms(rtc.age), grade(rtc.age, 20, 40));
      setVal("sv-convert", ms(rtc.convert), grade(rtc.convert, 5, 10));
      // Zeit pro Bild bei der Ziel-Bildrate (60 fps: 16,7 ms). Die Spitze zeigt einzelne langsame Bilder,
      // die der Mittelwert verdeckt – liegt sie über der Zeit pro Bild, sinkt die Bildrate
      const budget = 1000 / sendTarget;
      const peak = rtc.encode_peak;
      const showPeak = peak != null && peak > (rtc.encode || 0) * 1.3 && peak > 3;
      // Mittelwert über der Zeit pro Bild = Bildrate sinkt; einzelne Spitzen bis zur doppelten Zeit
      // überbrückt das nächste, schon wartende Bild (nur gelb), darüber fällt sichtbar ein Bild aus
      const grades = [grade(rtc.encode, budget * 0.8, budget), peak != null ? grade(peak, budget, budget * 2) : ""];
      setVal("sv-encode", ms(rtc.encode) + (showPeak ? " · Spitze " + ms(peak) : ""),
        grades.includes("bad") ? "bad" : grades.includes("warn") ? "warn" : grades[0]);
    } else if (mode === "jpeg" && stream.jpeg) {
      const jp = stream.jpeg;
      const through = cap.passthrough || (fstat.info && fstat.info.pt);
      setVal("sv-sent", jp.fps + " fps (Einzelbilder" + (through ? ", JPEG der Karte durchgereicht" : "") + ")",
        grade(-jp.fps, -Math.min(sendTarget - 5, wanted - 5), -(sendTarget / 2 - 5)));
      setVal("sv-age", ms(jp.age), grade(jp.age, 20, 40));
      setVal("sv-convert", "–");
      setVal("sv-encode", through ? "0 ms (durchgereicht)" : ms(jp.encode) + " (JPEG)", through ? "good" : grade(jp.encode, 1000 / sendTarget, 2000 / sendTarget));   // zwei JPEGs gleichzeitig
    } else {
      setVal("sv-sent", "–");
      ["sv-age", "sv-convert", "sv-encode"].forEach((id) => setVal(id, "–"));
    }
    setVal("sv-ping-dc", channelOpen() ? ms(dcPing) : "kein Datenkanal", channelOpen() ? grade(dcPing, 5, 20) : "warn");
    // Laufen die Eingaben über den Datenkanal, gehen über Home Assistant nur noch Status und Messwerte
    setVal("sv-ping-ws", ms(wsPing) + (channelOpen() && wsPing != null ? " · nur Status" : ""),
      channelOpen() ? "" : grade(wsPing, 15, 50));
    setVal("sv-input", channelOpen() ? "Datenkanal (direkt)" : "WebSocket (über Home Assistant)", channelOpen() ? "good" : "warn");

    const w = mode === "webrtc" ? video.videoWidth : (capturePlaying ? frames.width : 0);
    const h = mode === "webrtc" ? video.videoHeight : (capturePlaying ? frames.height : 0);
    setVal("sv-video", (mode === "webrtc" ? "WebRTC" : "Einzelbilder (JPEG)") + (w ? " · " + w + "×" + h : ""),
      capturePlaying ? "good" : "warn");
    const sound = soundInfo();
    setVal("sv-audio", sound[0], sound[1]);

    if (mode === "webrtc" && pc) {
      try {
        const report = await pc.getStats();
        let inbound = null;
        report.forEach((r) => { if (r.type === "inbound-rtp" && r.kind === "video") inbound = r; });
        const codec = inbound && inbound.codecId && report.get(inbound.codecId);
        if (codec && codec.mimeType) {
          const name = codec.mimeType.replace(/^video\//i, "").replace("H264", "H.264");
          setVal("sv-video", "WebRTC · " + name + (w ? " · " + w + "×" + h : ""), "good");
        }
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
    } else if (mode === "jpeg") {
      prevRtc = null;
      const now = performance.now();
      const cur = { t: now, frames: fstat.frames, bytes: fstat.bytes, decode: fstat.decode, skipped: fstat.skipped };
      if (prevJpeg && now > prevJpeg.t) {
        const seconds = (now - prevJpeg.t) / 1000;
        const count = cur.frames - prevJpeg.frames;
        const fps = count / seconds;
        setVal("sv-fps", Math.round(fps) + " fps", grade(-fps, -(sendTarget * 5 / 6), -(sendTarget / 2 - 5)));
        const dec = count > 0 ? (cur.decode - prevJpeg.decode) / count : null;
        setVal("sv-decode", ms(dec), grade(dec, 8, 16));
        setVal("sv-jitter", "keiner", "good");
        setVal("sv-dropped", String(cur.skipped), cur.skipped - prevJpeg.skipped > 0 ? "warn" : "good");
        const mbit = ((cur.bytes - prevJpeg.bytes) * 8) / seconds / 1e6;
        setVal("sv-rate", mbit.toFixed(1).replace(".", ",") + " Mbit/s", "");
      }
      prevJpeg = cur;
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
    prevJpeg = null;
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
    const el = mode === "webrtc" ? video : frames;
    if (mode === "webrtc" ? !video.videoWidth : !capturePlaying) return null;
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

  // ------------------------------------------------------------------ Aufzeichnung
  // Zeichnet die Messwerte von Browser und Pi in Fenstern zu 250 ms auf, dazu Ereignisse (Lücken, Hänger,
  // Schlüsselbilder, verlorene Pakete, Markierungen). Beim Beenden schickt die Bridge ihre Daten, der Browser wertet
  // beides aus und speichert eine Textdatei (Download und auf dem HA-Server unter /share/game_remote/aufzeichnungen).
  const REC_WINDOW_MS = 250;
  const REC_MAX_MS = 15 * 60 * 1000;
  const REC_MAX_EVENTS = 5000;
  let rec = null;
  let recBridge = null;            // wartet auf die Teile der Bridge: { parts, got, resolve }
  let recReport = null;            // letzte Auswertung: { name, text }

  function recAcc() {
    return { shown: 0, gap: 0, inDc: 0, inWs: 0, dc: [], ws: [], long: 0 };
  }

  function recEvent(kind, text) {
    if (!rec || rec.events.length >= REC_MAX_EVENTS) return;
    rec.events.push([(performance.now() - rec.t0) / 1000, kind, text]);
  }

  // Ein Bild wurde wirklich angezeigt (WebRTC: requestVideoFrameCallback, Einzelbilder: gezeichnet)
  function recShown(now) {
    if (!rec) return;
    if (rec.lastShown !== null) {
      const gap = now - rec.lastShown;
      if (gap > rec.acc.gap) rec.acc.gap = gap;
      if (gap > 100) recEvent("anzeige", Math.round(gap) + " ms kein neues Bild angezeigt");
    }
    rec.lastShown = now;
    rec.acc.shown += 1;
  }

  function recWatchVideo(r) {
    if (!video.requestVideoFrameCallback) return;
    const step = (now) => {
      if (rec !== r) return;
      if (mode === "webrtc") recShown(now);
      video.requestVideoFrameCallback(step);
    };
    video.requestVideoFrameCallback(step);
  }

  // Blockaden der Browser-Seite: ein Takt alle 50 ms, der später kommt, zeigt, wie lange die Seite nichts tun konnte
  // (auch Zeichnen und Eingaben warten dann). Im Hintergrund drosselt der Browser Takte – dort nicht zählen.
  function recWatchPage(r) {
    let last = performance.now();
    r.beat = setInterval(() => {
      const now = performance.now();
      const late = now - last - 50;
      last = now;
      if (rec !== r || document.hidden || late < 0) return;
      if (late > r.acc.long) r.acc.long = late;
      if (late >= 100) recEvent("browser", "Seite " + Math.round(late) + " ms blockiert (Zeichnen und Eingaben warten)");
    }, 50);
  }

  document.addEventListener("visibilitychange", () => {
    recEvent("browser", document.hidden ? "Seite im Hintergrund (Browser drosselt)" : "Seite wieder sichtbar");
  });

  function clock(ms) {
    const s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  async function recTick(r) {
    if (rec !== r || r.busy) return;
    r.busy = true;
    const now = performance.now();
    const acc = r.acc;
    r.acc = recAcc();
    const row = {
      i: Math.max(0, Math.round((now - r.t0) / REC_WINDOW_MS) - 1),
      shown: acc.shown, gap: Math.round(acc.gap), inDc: acc.inDc, inWs: acc.inWs, long: Math.round(acc.long),
      dc: acc.dc.length ? Math.max(...acc.dc) : null, ws: acc.ws.length ? Math.max(...acc.ws) : null, mode,
    };
    if (channelOpen()) {
      try { inputChannel.send(JSON.stringify({ type: "heartbeat", t: performance.now() })); } catch (err) { /* zu */ }
    }
    if (mode === "webrtc" && pc) {
      try {
        const report = await pc.getStats();
        const byId = {};
        let v = null;
        let a = null;
        let pairId = null;
        report.forEach((s) => {
          byId[s.id] = s;
          if (s.type === "inbound-rtp" && s.kind === "video") v = s;
          if (s.type === "inbound-rtp" && s.kind === "audio") a = s;
          if (s.type === "transport" && s.selectedCandidatePairId) pairId = s.selectedCandidatePairId;
        });
        let cp = pairId ? byId[pairId] : null;
        if (!cp) report.forEach((s) => { if (s.type === "candidate-pair" && s.nominated && s.state === "succeeded") cp = s; });
        const cur = v ? {
          recv: v.framesReceived || 0, dec: v.framesDecoded || 0, drop: v.framesDropped || 0, kf: v.keyFramesDecoded || 0,
          pkts: v.packetsReceived || 0, lost: v.packetsLost || 0, nack: v.nackCount || 0, pli: v.pliCount || 0,
          jb: v.jitterBufferDelay || 0, jbn: v.jitterBufferEmittedCount || 0, dect: v.totalDecodeTime || 0,
          frz: v.freezeCount || 0, frzt: v.totalFreezesDuration || 0, bytes: v.bytesReceived || 0,
          alost: a ? a.packetsLost || 0 : 0, conceal: a ? a.concealedSamples || 0 : 0,
        } : null;
        // Neue WebRTC-Verbindung (z. B. nach Codec-Wechsel): Zähler beginnen bei 0
        if (cur && r.prev && (r.prevPc !== pc || cur.recv < r.prev.recv || cur.pkts < r.prev.pkts)) {
          r.prev = Object.fromEntries(Object.keys(cur).map((k) => [k, 0]));
        }
        if (cur && r.prev) {
          const p = r.prev;
          row.recv = cur.recv - p.recv;
          row.dec = cur.dec - p.dec;
          row.drop = cur.drop - p.drop;
          row.kf = cur.kf - p.kf;
          row.pkts = cur.pkts - p.pkts;
          row.lost = Math.max(0, cur.lost - p.lost);
          row.nack = cur.nack - p.nack;
          row.pli = cur.pli - p.pli;
          const jbn = cur.jbn - p.jbn;
          row.jb = jbn > 0 ? Math.round(((cur.jb - p.jb) / jbn) * 1000) : null;
          row.decms = row.dec > 0 ? ((cur.dect - p.dect) / row.dec) * 1000 : null;
          row.frz = cur.frz - p.frz;
          row.frzt = Math.round((cur.frzt - p.frzt) * 1000);
          row.kbit = Math.round(((cur.bytes - p.bytes) * 8) / REC_WINDOW_MS);
          row.alost = cur.alost - p.alost;
          row.conceal = cur.conceal - p.conceal;
          if (row.frz > 0) recEvent("anzeige", "Browser meldet eingefrorenes Video (" + row.frzt + " ms)");
          if (row.lost > 0) recEvent("netz", row.lost + " Videopakete verloren");
          if (row.pli > 0) recEvent("netz", "Browser fordert ein Schlüsselbild an (PLI)");
        }
        if (cur) {
          r.prev = cur;
          r.prevPc = pc;
        }
        if (cp) {
          if (cp.currentRoundTripTime != null) row.rtt = Math.round(cp.currentRoundTripTime * 1000);
          const l = byId[cp.localCandidateId];
          const rm = byId[cp.remoteCandidateId];
          if (!r.path && l && rm) {
            r.path = (l.candidateType || "?") + "/" + (l.protocol || "?") + (l.networkType ? " (" + l.networkType + ")" : "")
              + " → " + (rm.address || rm.ip || "?") + ":" + (rm.port || "?") + " " + (rm.candidateType || "");
          }
        }
        if (v && v.frameWidth) r.size = v.frameWidth + "×" + v.frameHeight;
        if (v && v.codecId && byId[v.codecId]) r.codec = byId[v.codecId].mimeType;
      } catch (err) { /* Verbindung gerade weg */ }
    } else if (mode === "jpeg") {
      const cur = { recv: fstat.frames, bytes: fstat.bytes, decode: fstat.decode, skipped: fstat.skipped };
      if (r.prevJ) {
        row.recv = cur.recv - r.prevJ.recv;
        row.kbit = Math.round(((cur.bytes - r.prevJ.bytes) * 8) / REC_WINDOW_MS);
        row.decms = row.recv > 0 ? (cur.decode - r.prevJ.decode) / row.recv : null;
        row.drop = cur.skipped - r.prevJ.skipped;
      }
      r.prevJ = cur;
      r.size = frames.width + "×" + frames.height;
    }
    r.rows.push(row);
    $("rec-time").textContent = clock(now - r.t0);
    r.busy = false;
    if (now - r.t0 > REC_MAX_MS) stopRecording("Höchstdauer von 15 Minuten erreicht");
  }

  function startRecording() {
    if (rec) return;
    const st = lastStatus || {};
    const r = {
      t0: performance.now(), started: new Date(), rows: [], events: [], acc: recAcc(), prev: null, prevJ: null,
      lastShown: null, busy: false, marks: 0, presses: 0, path: null, size: null, bridgeOk: false,
      info: {
        version: config.version || "?", ua: navigator.userAgent, host: location.hostname,
        screen: screen.width + "×" + screen.height + " @" + (window.devicePixelRatio || 1),
        view: innerWidth + "×" + innerHeight, mode, touch: $("touch-toggle").checked,
        net: navigator.connection ? [navigator.connection.type, navigator.connection.effectiveType].filter(Boolean).join("/") : "",
        pads: navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean).map((p) => p.id) : [],
        console: (st.switch || {}).state || "?",
      },
    };
    rec = r;
    r.timer = setInterval(() => recTick(r), REC_WINDOW_MS);
    recWatchVideo(r);
    recWatchPage(r);
    recEvent("start", "Aufzeichnung gestartet (" + (mode === "webrtc" ? "Video/WebRTC" : "Einzelbilder") + ")");
    if (ws && ws.readyState === WebSocket.OPEN) send({ type: "system", action: "record", state: "start" });
    else recEvent("bridge", "keine Verbindung zur Bridge beim Start – nur Browser-Werte");
    updateRecUi();
  }

  function recBridgeMessage(msg) {
    if (msg.state === "started") {
      if (rec) rec.bridgeOk = true;
      return;
    }
    if (!recBridge || typeof msg.parts !== "number") return;
    recBridge.parts[msg.part] = msg.data || "";
    recBridge.got += 1;
    if (recBridge.got >= msg.parts) {
      const done = recBridge;
      recBridge = null;
      try { done.resolve(JSON.parse(done.parts.join(""))); } catch (err) { done.resolve(null); }
    }
  }

  function stamp(date) {
    const p = (n) => String(n).padStart(2, "0");
    return date.getFullYear() + p(date.getMonth() + 1) + p(date.getDate()) + "-" + p(date.getHours()) + p(date.getMinutes()) + p(date.getSeconds());
  }

  function downloadText(name, text) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 2000);
  }

  async function stopRecording(reason) {
    const r = rec;
    if (!r) return;
    rec = null;
    clearInterval(r.timer);
    clearInterval(r.beat);
    r.ended = performance.now();
    if (reason) r.events.push([(r.ended - r.t0) / 1000, "ende", reason]);
    updateRecUi("Wird ausgewertet …");
    let bridge = null;
    let note = "";
    if (ws && ws.readyState === WebSocket.OPEN) {
      bridge = await new Promise((resolve) => {
        recBridge = { parts: [], got: 0, resolve };
        send({ type: "system", action: "record", state: "stop" });
        setTimeout(() => resolve(null), 15000);
      });
      recBridge = null;
      if (!bridge) {
        note = r.bridgeOk ? "Die Bridge hat ihre Daten nicht rechtzeitig geschickt."
          : "Die Bridge kennt die Aufzeichnung nicht (älter als 1.0.18 – install.sh am Pi ausführen). Nur Browser-Werte.";
      }
    } else {
      note = "Keine Verbindung zur Bridge – nur Browser-Werte.";
    }
    let text;
    try {
      text = buildReport(r, bridge, note);
    } catch (err) {
      text = "Auswertung fehlgeschlagen: " + err + "\n\nRohdaten:\n" + JSON.stringify({ browser: r.rows, events: r.events, bridge }, null, 1);
    }
    const name = "game-remote-aufzeichnung-" + stamp(r.started) + ".txt";
    recReport = { name, text, saved: "" };
    downloadText(name, text);
    try {
      const res = await fetch("api/recording?name=" + encodeURIComponent(name), {
        method: "POST", body: text, headers: { "Content-Type": "text/plain; charset=utf-8" },
      });
      if (res.ok) recReport.saved = (await res.json()).path || "";
    } catch (err) { /* nur Download */ }
    updateRecUi();
  }

  function markStutter() {
    if (!rec) return;
    rec.marks += 1;
    recEvent("markierung", "★ Markierung " + rec.marks + " – Ruckler bemerkt");
    toast("Markierung " + rec.marks + " gesetzt");
  }

  function updateRecUi(busyText) {
    const running = !!rec;
    $("rec-btn").textContent = running ? "Aufzeichnung beenden" : "Aufzeichnung starten";
    $("rec-btn").disabled = !!busyText;
    $("rec-mark").hidden = !running;
    $("rec-copy").hidden = running || !recReport || !!busyText;
    $("rec-save").hidden = running || !recReport || !!busyText;
    const result = $("rec-result");
    const hint = $("rec-hint");
    if (busyText) {
      result.textContent = busyText;
      hint.hidden = true;
    } else if (running) {
      result.textContent = "läuft …";
      hint.hidden = false;
      hint.textContent = "Spiel weiter, bis es ruckelt, und tippe dann oben auf „Ruckler!“. Das Fenster hier darf zu sein.";
    } else if (recReport) {
      const first = recReport.text.split("\n").find((line) => line.startsWith("Ergebnis:")) || "";
      result.textContent = "fertig";
      hint.hidden = false;
      hint.textContent = (first ? first + " " : "") + "Gespeichert als " + recReport.name
        + (recReport.saved ? " (auch auf dem HA-Server: " + recReport.saved + ")" : "") + ".";
    } else {
      result.textContent = "–";
      hint.hidden = true;
    }
  }

  // ------------------------------------------------------------------ Auswertung
  function buildReport(r, bridge, note) {
    const W = REC_WINDOW_MS / 1000;
    const info = (bridge && bridge.info) || {};
    const jpegRec = r.info.mode === "jpeg";
    const st = (lastStatus && lastStatus.stream) || {};
    const target = (jpegRec ? info.jpeg_fps || st.jpeg_fps : 0) || info.webrtc_fps || st.target_fps || 60;
    const cardFps = (Array.isArray(info.actual) && info.actual[2]) || 60;
    const budget = 1000 / target;
    const pi = new Map();
    if (bridge) bridge.windows.forEach((w) => pi.set(w[0], w[1]));
    const br = new Map();
    r.rows.forEach((row) => br.set(row.i, row));
    const lastPi = bridge && bridge.windows.length ? bridge.windows[bridge.windows.length - 1][0] : -1;
    const lastBr = r.rows.length ? r.rows[r.rows.length - 1].i : -1;
    const count = Math.max(lastPi, lastBr) + 1;
    const fps = (n) => (n == null ? null : Math.round(n / W));
    const num = (v) => (typeof v === "number" && !Number.isNaN(v) ? v : null);
    const avg = (list) => { const l = list.filter((x) => x != null); return l.length ? l.reduce((a, b) => a + b, 0) / l.length : null; };
    const max = (list) => { const l = list.filter((x) => x != null); return l.length ? Math.max(...l) : null; };
    const min = (list) => { const l = list.filter((x) => x != null); return l.length ? Math.min(...l) : null; };
    const sum = (list) => list.filter((x) => x != null).reduce((a, b) => a + b, 0);
    const f = (v, digits) => (v == null ? "·" : digits ? v.toFixed(digits).replace(".", ",") : String(Math.round(v)));
    const ms = (v) => (v == null ? "–" : Math.round(v) + " ms");
    const sec = (t) => t.toFixed(2).replace(".", ",") + " s";
    // Fenster ohne Rand (erstes und letztes sind unvollständig)
    const inner = [];
    for (let i = 1; i < count - 1; i += 1) inner.push(i);
    const piVals = (key) => inner.map((i) => (pi.has(i) ? num(pi.get(i)[key]) : null));
    const piCounts = (key) => inner.filter((i) => pi.has(i)).map((i) => pi.get(i)[key] || 0);
    const brVals = (key) => inner.map((i) => (br.has(i) ? num(br.get(i)[key]) : null));
    const piMean = (key) => {
      let s = 0;
      let n = 0;
      pi.forEach((v) => { s += v[key + "_s"] || 0; n += v[key + "_n"] || 0; });
      return n ? s / n : null;
    };
    const sentKey = r.info.mode === "jpeg" ? "jpeg" : "sent";
    // Sollwerte je Fenster: Die Bildqualität (60/30 fps) und das HDMI-Signal können während der Aufzeichnung wechseln.
    // Die Bridge meldet sie jede Sekunde (tfps, cfps); dazwischen gilt der letzte Wert.
    const tAt = [];
    const cAt = [];
    let tCur = target;
    let cCur = cardFps;
    // Gesendet zählt die Bilder aller Zuschauer: pro Zuschauer umrechnen (mehrere offene Fenster)
    const viewKey = r.info.mode === "jpeg" ? "jviews" : "views";
    let vCur = Math.max(1, (r.info.mode === "jpeg" ? info.jpeg_clients : info.webrtc_clients) || 1);
    for (let i = 0; i < count; i += 1) {
      const v = pi.get(i);
      if (v && (jpegRec ? v.jfps || v.tfps : v.tfps)) tCur = jpegRec ? v.jfps || v.tfps : v.tfps;
      if (v && v.cfps) cCur = v.cfps;
      if (v && v[viewKey]) vCur = v[viewKey];
      if (v) v.sentpv = (v[sentKey] || 0) / Math.max(1, vCur);
      tAt.push(tCur);
      cAt.push(cCur);
    }
    const targets = Array.from(new Set(tAt)).sort((a, b) => b - a);
    // Grenze für „zu wenig angezeigt“ je Fenster: 15 % unter Soll, aber mindestens 1,5 Bilder – bei 30 fps fallen in
    // ein 250-ms-Fenster 7 oder 8 Bilder, ein Bild Versatz an der Fenstergrenze ist noch kein Einbruch
    const minShown = (t) => t * W - Math.max(1.5, t * W * 0.15);
    const mhzMax = max(Array.from(pi.values()).map((v) => num(v.p_mhz)));
    // Prozesse über die ganze Aufzeichnung: Ø und Spitze in % eines Kerns (Proben alle 0,5 s)
    const procSummary = (key) => {
      const seen = {};
      let samples = 0;
      pi.forEach((v) => {
        if (!Array.isArray(v[key])) return;
        samples += 1;
        v[key].forEach((p) => {
          const entry = seen[p[0]] || (seen[p[0]] = { sum: 0, peak: 0 });
          entry.sum += p[2];
          entry.peak = Math.max(entry.peak, p[2]);
        });
      });
      return Object.keys(seen).map((name) => ({ name, avg: samples ? seen[name].sum / samples : 0, peak: seen[name].peak }))
        .sort((a, b) => b.peak - a.peak).slice(0, 6)
        .map((p) => "„" + p.name + "“ Ø " + Math.round(p.avg) + " % / Spitze " + p.peak + " %").join(", ");
    };

    // Ereignisse beider Seiten nach Fenster
    const events = new Map();
    const addEvent = (t, who, kind, text) => {
      const i = Math.max(0, Math.floor(t / W));
      if (!events.has(i)) events.set(i, []);
      events.get(i).push({ t, who, kind, text });
    };
    r.events.forEach((e) => addEvent(e[0], "Browser", e[1], e[2]));
    if (bridge) bridge.events.forEach((e) => addEvent(e[0], "Pi", e[1], e[2]));
    const allEvents = [];
    events.forEach((list) => list.forEach((e) => allEvents.push(e)));
    allEvents.sort((a, b) => a.t - b.t);
    const countKind = (who, kind) => allEvents.filter((e) => e.who === who && e.kind === kind).length;

    // Ursachen für einen Zeitraum (Pi-Fenster etwas weiter, die Uhren laufen nicht exakt gleich)
    function causes(from, to) {
      const target = tAt[from] || tCur;
      const budget = 1000 / target;
      const cardFps = cAt[from] || cCur;
      const list = [];
      const cats = [];
      const piRange = [];
      for (let i = from - 2; i <= to + 1; i += 1) if (pi.has(i)) piRange.push(pi.get(i));
      const brRange = [];
      for (let i = from; i <= to; i += 1) if (br.has(i)) brRange.push(br.get(i));
      const capMin = min(piRange.map((v) => fps(v.cap || 0)));
      const gapMax = max(piRange.map((v) => num(v.cap_gap)));
      const lag = max(piRange.map((v) => num(v.lag)));
      const nx = max(piRange.map((v) => num(v.nxbt)));
      const enc = max(piRange.map((v) => num(v.enc)));
      // Einzelbilder: der Pi kodiert zwei JPEGs gleichzeitig – erst über der doppelten Zeit pro Bild sinkt die Bildrate
      const jenc = jpegRec ? max(piRange.map((v) => num(v.jpeg_enc))) : null;
      const es = max(piRange.map((v) => num(v.encsend)));
      const kf = sum(piRange.map((v) => v.kf || 0));
      const sent = min(piRange.map((v) => fps(v.sentpv || 0)));
      const lost = sum(brRange.map((v) => v.lost || 0));
      const nack = sum(brRange.map((v) => v.nack || 0));
      const rtt = max(brRange.map((v) => num(v.rtt)));
      // Mittel über den Zeitraum: nach einer Blockade kommen die gestauten Bilder gebündelt im nächsten Fenster an
      const recv = avg(brRange.map((v) => fps(num(v.recv))));
      const shown = avg(brRange.map((v) => fps(v.shown)));
      const sentAvg = avg(piRange.map((v) => fps(v.sentpv || 0)));
      const long = max(brRange.map((v) => num(v.long)));
      const frz = sum(brRange.map((v) => v.frz || 0));
      // Umschalten der Bildqualität oder neues HDMI-Signal: kurze Lücke ist dann normal
      const near = [];
      for (let i = from - 4; i <= to; i += 1) (events.get(i) || []).forEach((e) => near.push(e));
      if (near.some((e) => e.who === "Pi" && e.kind === "mode")) { list.push("Bildqualität umgeschaltet"); cats.push("Umschalten"); }
      if (near.some((e) => e.who === "Pi" && e.kind === "capture" && /geöffnet/.test(e.text))) {
        list.push("Capture-Card neu geöffnet (anderes HDMI-Signal oder Bildqualität)");
        cats.push("Umschalten");
      }
      if (bridge && piRange.length) {
        // Systemmessung der Bridge (alle 0,5 s): wer hat dem Pi in dieser Zeit die Kerne weggenommen?
        const foreign = {};
        piRange.forEach((v) => (v.p_top || []).forEach((p) => {
          if (!foreign[p[0]] || foreign[p[0]][1] < p[2]) foreign[p[0]] = [p[1], p[2]];
        }));
        const worst = Object.keys(foreign).sort((a, b) => foreign[b][1] - foreign[a][1])[0];
        const selfMax = max(piRange.map((v) => num(v.p_self)));
        const busyMax = max(piRange.map((v) => num(v.p_busy)));
        const mhzMin = min(piRange.map((v) => num(v.p_mhz)));
        const irqMax = max(piRange.map((v) => num(v.p_irq)));
        const ioMax = max(piRange.map((v) => num(v.p_io)));
        const memStall = sum(piRange.map((v) => v.p_psi_mem || 0));
        if (worst && foreign[worst][1] >= 40) {
          list.push("anderes Programm auf dem Pi: „" + worst + "“ (PID " + foreign[worst][0] + ") mit " + foreign[worst][1] + " % eines Kerns");
          cats.push("anderes Programm: " + worst);
        }
        if (selfMax != null && selfMax >= 300) { list.push("Bridge rechnete am Anschlag (" + selfMax + " % = fast alle 4 Kerne)"); cats.push("Bridge am Anschlag"); }
        if (mhzMin != null && mhzMax && mhzMin < mhzMax * 0.8) { list.push("Takt gesenkt auf " + mhzMin + " MHz"); cats.push("Takt gesenkt"); }
        if (irqMax != null && irqMax >= 25) { list.push("Kernel/Interrupts " + irqMax + " %"); cats.push("Kernel/Interrupts"); }
        if (ioMax != null && ioMax >= 20) { list.push("Warten auf Speicherkarte/USB (" + ioMax + " %)"); cats.push("I/O"); }
        if (memStall >= 100) { list.push("Speicherdruck: " + memStall + " ms Wartezeit"); cats.push("Speicher"); }
        if (busyMax != null && busyMax >= 95 && !(worst && foreign[worst][1] >= 40) && !(selfMax >= 300)) { list.push("alle Kerne ausgelastet (" + busyMax + " %)"); cats.push("Pi ausgelastet"); }
        if (capMin != null && capMin < cardFps * 0.85) { list.push("Capture-Card lieferte nur " + capMin + " fps (Lücke " + ms(gapMax) + ")"); cats.push("Capture-Card"); }
        if (lag != null && lag > 17) { list.push("Bridge hing " + ms(lag)); cats.push("Bridge-Hänger"); }
        if (nx != null && nx > 15) { list.push("nxbt-Aufruf blockierte " + ms(nx)); cats.push("nxbt"); }
        if (enc != null && enc > budget) { list.push("Kodieren dauerte " + ms(enc) + " (Zeit pro Bild " + ms(budget) + ")"); cats.push("Kodieren"); }
        if (jenc != null && jenc > budget * 2) { list.push("JPEG kodieren dauerte " + ms(jenc) + " (zwei gleichzeitig, Grenze " + ms(budget * 2) + ")"); cats.push("Kodieren"); }
        if (kf > 0) { list.push(kf + "× Schlüsselbild"); cats.push("Schlüsselbild"); }
        if (es != null && es > budget * 2 && !(enc != null && enc > budget)) { list.push("Kodieren + Senden " + ms(es) + " (Warten in aiortc/Bridge)"); cats.push("Senden"); }
        if (!list.length && sent != null && sent < target * 0.85) { list.push("Pi sendete nur " + sent + " fps"); cats.push("Pi sendet zu wenig"); }
      } else if (!bridge) {
        list.push("keine Pi-Daten");
      }
      if (lost > 0 || nack > 0) { list.push("Netz: " + lost + " Pakete verloren, " + nack + " nachgefordert"); cats.push("Netz/WLAN"); }
      if (rtt != null && rtt > 30) { list.push("Netz langsam: " + ms(rtt) + " Antwortzeit"); cats.push("Netz/WLAN"); }
      if (recv != null && sentAvg != null && sentAvg >= target * 0.85 && recv < sentAvg * 0.85 && lost === 0) {
        list.push("Netz: weniger angekommen als gesendet (" + Math.round(recv) + " von " + Math.round(sentAvg) + " fps, verspätet)");
        cats.push("Netz/WLAN");
      }
      if (recv != null && shown != null && recv >= target * 0.85 && shown < recv * 0.85) { list.push("Browser zeigte weniger an, als ankam (" + Math.round(shown) + " von " + Math.round(recv) + " fps)"); cats.push("Browser/Anzeige"); }
      if (long != null && long >= 50) { list.push("Browser-Seite " + ms(long) + " beschäftigt"); cats.push("Browser/Anzeige"); }
      if (frz > 0) list.push("Browser meldet Einfrieren");
      if (!list.length) { list.push("keine Ursache in den Messwerten erkannt"); cats.push("unklar"); }
      return { list, cats };
    }

    // Schlechte Fenster: weniger angezeigt als erwartet oder lange Lücke in der Anzeige
    const bad = [];
    inner.forEach((i) => {
      const row = br.get(i);
      if (!row || i < 3) return;
      const t = tAt[i] || target;
      if (row.shown < minShown(t) || row.gap > Math.max(50, (1000 / t) * 2.5)) bad.push(i);
    });
    const moments = [];
    bad.forEach((i) => {
      const last = moments[moments.length - 1];
      if (last && i - last.to <= 2) last.to = i;
      else moments.push({ from: i, to: i });
    });
    const catCount = {};
    moments.forEach((m) => {
      m.info = causes(m.from, m.to);
      const rows = [];
      for (let i = m.from; i <= m.to; i += 1) if (br.has(i)) rows.push(br.get(i));
      m.shown = min(rows.map((v) => fps(v.shown)));
      m.gap = max(rows.map((v) => v.gap));
      Array.from(new Set(m.info.cats)).forEach((c) => { catCount[c] = (catCount[c] || 0) + 1; });
    });
    const topCats = Object.keys(catCount).sort((a, b) => catCount[b] - catCount[a]);

    const L = [];
    const dur = (r.ended - r.t0) / 1000;
    L.push("Game Remote – Aufzeichnung");
    L.push("==========================");
    L.push("Start: " + r.started.toLocaleString("de-DE") + "   Dauer: " + sec(dur) + "   Fenster: 250 ms (" + Math.round(target * W) + " Bilder = " + target + " fps)");
    if (note) L.push("Hinweis: " + note);
    L.push("Ergebnis: " + (moments.length
      ? moments.length + " Einbrüche, häufigste Ursache: " + topCats.slice(0, 3).map((c) => c + " (" + catCount[c] + "×)").join(", ")
      : "keine Einbrüche (angezeigt durchgehend über 85 % der eingestellten Bildrate)"));
    L.push("");
    L.push("UMGEBUNG");
    L.push("  Add-on " + r.info.version + ", Bridge-Stand " + (info.bridge || "?") + ", Konsole: " + (info.console || r.info.console));
    L.push("  Browser: " + r.info.ua);
    L.push("  Bildschirm " + r.info.screen + ", Fenster " + r.info.view + ", Seite über " + r.info.host
      + (/nabu\.casa$/.test(r.info.host) ? " (Nabu Casa – Status/Messwerte über das Internet)" : "")
      + (r.info.net ? ", Netz " + r.info.net : ""));
    L.push("  Übertragung " + (r.info.mode === "webrtc" ? "Video (WebRTC" + (r.codec ? ", " + r.codec : "") + ")" : "Einzelbilder (JPEG)") + ", Bild " + (r.size || "?")
      + (r.path ? ", Weg " + r.path : "") + ", Controller: " + (r.info.pads.length ? r.info.pads.join("; ") : "keiner") + (r.info.touch ? ", Touch an" : ""));
    if (bridge) {
      L.push("  Pi: Bildqualität " + (info.mode || "?") + " (" + target + " fps), Karte " + (info.device || "?") + " " + (info.format || "")
        + " " + (Array.isArray(info.actual) ? info.actual[0] + "×" + info.actual[1] + "@" + info.actual[2] : "")
        + (info.scaled ? " → " + info.scaled.join("×") : "") + ", " + (info.buffers || "?") + " Puffer"
        + (info.blank ? ", Bild schwarz seit " + info.blank + " s (kein HDMI-Signal? Konsole im Standby?)" : "")
        + (Array.isArray(info.actual) && info.actual[1] > 1080 ? ", 4K-Signal (Konsole auf 1080p stellen)" : ""));
      L.push("  Encoder: " + (info.codec || "?") + " (VP8 cpu-used " + info.speed + (info.h264 ? ", x264 " + info.h264 : ", ohne x264") + "), " + info.threads + " Threads, " + Math.round((info.bitrate || 0) / 1e6) + " Mbit/s"
        + (info.fixed_bitrate ? " fest" : "") + "; nice " + (info.nice != null ? info.nice : "?")
        + (info.encode_nice != null ? " (Kodieren " + info.encode_nice + ")" : "") + ", Regler " + (info.governor || "?")
        + ", " + (info.temp != null ? info.temp + " °C" : "") + "; aiortc " + (info.aiortc || "?") + ", av " + (info.av || "?") + ", OpenCV " + (info.opencv || "?"));
      L.push("  Zuschauer: " + info.webrtc_clients + " WebRTC, " + info.jpeg_clients + " Einzelbilder; Ton " + (info.audio || "?"));
      L.push("  Uhren: Pi-Zeit beginnt beim Eintreffen des Starts (über Home Assistant), Abweichung meist unter 0,1 s.");
    }
    L.push("");
    L.push("ZUSAMMENFASSUNG");
    if (bridge) {
      L.push("  Pi   Capture-Card   Ø " + f(avg(piCounts("cap").map(fps))) + " fps, schlechtestes Fenster " + f(min(piCounts("cap").map(fps))) + " fps, längste Lücke " + ms(max(piVals("cap_gap"))) + ", Lücken: " + countKind("Pi", "capture"));
      L.push("       Gesendet       Ø " + f(avg(piCounts("sentpv").map(fps))) + " fps, schlechtestes Fenster " + f(min(piCounts("sentpv").map(fps))) + " fps (je Zuschauer)");
      if (r.info.mode === "webrtc") {
        L.push("       Kodieren       Ø " + ms(piMean("enc")) + ", max " + ms(max(piVals("enc"))) + " (Zeit pro Bild " + targets.map((t) => ms(1000 / t)).join(" bzw. ") + "), Schlüsselbilder " + sum(piCounts("kf")) + ", Encoder neu angelegt " + countKind("Pi", "encoder"));
        L.push("       Kodieren+Senden Ø " + ms(piMean("encsend")) + ", max " + ms(max(piVals("encsend"))) + "; Umrechnung Ø " + ms(piMean("conv")) + ", Bildalter Ø " + ms(piMean("age")));
      } else {
        L.push("       JPEG           Ø " + ms(piMean("jpeg_enc")) + ", max " + ms(max(piVals("jpeg_enc")))
          + " (zwei gleichzeitig: reicht bis " + targets.map((t) => ms(2000 / t)).join(" bzw. ") + " pro Bild)");
      }
      L.push("       Hänger         max " + ms(max(piVals("lag"))) + ", über 15 ms: " + countKind("Pi", "lag") + "×");
      L.push("       nxbt           Ø " + ms(piMean("nxbt")) + ", max " + ms(max(piVals("nxbt"))) + ", über 15 ms: " + countKind("Pi", "nxbt") + "×");
      L.push("       Eingaben       " + sum(piCounts("in_dc")) + " über Datenkanal, " + sum(piCounts("in_ws")) + " über WebSocket, längste Lücke im Datenkanal " + ms(max(piVals("in_gap"))));
      L.push("       Tastendrücke   " + (r.presses || 0) + " im Browser, " + sum(piCounts("in_press")) + " am Pi angekommen, "
        + sum(piCounts("nx_press")) + " an die Konsole weitergegeben" + (sum(piCounts("in_press")) && !sum(piCounts("nx_press")) ? " (Konsole nicht verbunden?)" : ""));
      L.push("       System         CPU max " + f(max(piVals("cpu"))) + " %, Temperatur max " + f(max(piVals("temp")), 1) + " °C");
      if (Array.from(pi.values()).some((v) => v.p_busy != null)) {
        L.push("       Alle Kerne     Ø " + f(avg(piVals("p_busy"))) + " %, max " + f(max(piVals("p_busy"))) + " %; Kernel/Interrupts max " + f(max(piVals("p_irq"))) + " %, I/O max " + f(max(piVals("p_io"))) + " %");
        L.push("       Bridge         Ø " + f(avg(piVals("p_self"))) + " %, max " + f(max(piVals("p_self"))) + " % (100 % = ein Kern, 400 % = alle)" + (procSummary("p_kids") ? "; davon " + procSummary("p_kids") : ""));
        L.push("       Takt           " + f(min(piVals("p_mhz"))) + "–" + f(mhzMax) + " MHz; Wartezeit (PSI) CPU " + ms(sum(piVals("p_psi_cpu"))) + ", Speicher " + ms(sum(piVals("p_psi_mem"))) + ", I/O " + ms(sum(piVals("p_psi_io"))));
        L.push("       Andere Programme " + (procSummary("p_top") || "keins über 5 %"));
      }
    }
    const pkts = sum(brVals("pkts"));
    const lost = sum(brVals("lost"));
    L.push("  Browser Empfangen   Ø " + f(avg(brVals("recv").map(fps))) + " fps, schlechtestes Fenster " + f(min(brVals("recv").map(fps))) + " fps");
    L.push("       Angezeigt      Ø " + f(avg(brVals("shown").map(fps))) + " fps, schlechtestes Fenster " + f(min(brVals("shown").map(fps))) + " fps, längste Lücke " + ms(max(brVals("gap"))));
    L.push("       Netz           " + lost + " von " + pkts + " Paketen verloren (" + f(pkts ? (lost / (pkts + lost)) * 100 : 0, 2) + " %), nachgefordert " + sum(brVals("nack")) + ", Schlüsselbild angefordert " + sum(brVals("pli")) + "×");
    L.push("       Antwortzeit    direkt Ø " + ms(avg(brVals("dc"))) + " / max " + ms(max(brVals("dc"))) + ", WebRTC-Weg Ø " + ms(avg(brVals("rtt"))) + ", über Home Assistant Ø " + ms(avg(brVals("ws"))));
    L.push("       Puffer Ø " + ms(avg(brVals("jb"))) + ", Dekodieren Ø " + ms(avg(brVals("decms"))) + ", verworfen " + sum(brVals("drop")) + ", eingefroren " + sum(brVals("frz")) + "× (" + ms(sum(brVals("frzt"))) + "), Seite beschäftigt max " + ms(max(brVals("long"))));
    L.push("");

    const marks = allEvents.filter((e) => e.kind === "markierung");
    if (marks.length) {
      L.push("MARKIERUNGEN (Ruckler bemerkt – ausgewertet werden die 3 s davor)");
      marks.forEach((m) => {
        const to = Math.floor(m.t / W);
        const from = Math.max(0, to - 12);
        const near = moments.filter((mo) => mo.to >= from && mo.from <= to + 1);
        L.push("  " + sec(m.t) + "  " + m.text.replace("★ ", ""));
        if (near.length) near.forEach((mo) => L.push("     Einbruch bei " + sec(mo.from * W) + ": angezeigt " + mo.shown + " fps, Lücke " + ms(mo.gap) + " – " + mo.info.list.join("; ")));
        else L.push("     kein Einbruch gemessen – " + causes(from, to).list.join("; "));
      });
      L.push("");
    }

    L.push("EINBRÜCHE (angezeigt unter " + targets.map((t) => Math.round(minShown(t) / W) + " fps bei " + t + " fps").join(" bzw. ")
      + " oder Lücke über " + targets.map((t) => Math.round(Math.max(50, (1000 / t) * 2.5)) + " ms").join(" bzw. ") + ")");
    if (!moments.length) L.push("  keine");
    moments.slice(0, 200).forEach((m) => {
      L.push("  " + sec(m.from * W) + (m.to > m.from ? " – " + sec((m.to + 1) * W) : "") + ": angezeigt " + m.shown + " fps, Lücke " + ms(m.gap) + " – " + m.info.list.join("; "));
    });
    if (moments.length > 200) L.push("  … " + (moments.length - 200) + " weitere");
    L.push("");

    L.push("VERLAUF (je 250 ms, fps hochgerechnet; ! = Einbruch)");
    const head = ["Zeit", "Karte", "Gesen", "Kod", "K+S", "Häng", "nxbt", "CPU", "Brid", "MHz", "|", "Empf", "Anz", "Lücke", "Verl", "NACK", "PLI", "Puff", "RTT", "Dek", "Seite"];
    const widths = [8, 6, 6, 5, 5, 5, 5, 5, 5, 5, 2, 5, 5, 6, 5, 5, 4, 5, 5, 5, 6];
    // Systemwerte kommen alle 0,5 s: im Fenster dazwischen den letzten Wert zeigen
    let sys = {};
    const line = (cells) => cells.map((c, k) => String(c).padStart(widths[k])).join("");
    L.push("   " + line(head) + "  Ereignisse");
    const badSet = new Set(bad);
    for (let i = 0; i < count; i += 1) {
      const p = pi.get(i);
      const b = br.get(i);
      if (p && p.p_busy != null) sys = p;
      if (!p && !b && !events.has(i)) continue;
      const cells = [
        (i * W).toFixed(2).replace(".", ","),
        p ? fps(p.cap || 0) : "·", p ? fps(p.sentpv || 0) : "·", p ? f(num(p.enc)) : "·", p ? f(num(p.encsend)) : "·",
        p ? f(num(p.lag)) : "·", p ? f(num(p.nxbt)) : "·",
        f(num(sys.p_busy)), f(num(sys.p_self)), f(num(sys.p_mhz)), "|",
        b && b.recv != null ? fps(b.recv) : "·", b ? fps(b.shown) : "·", b ? f(b.gap) : "·", b ? f(num(b.lost)) : "·",
        b ? f(num(b.nack)) : "·", b ? f(num(b.pli)) : "·", b ? f(num(b.jb)) : "·", b ? f(num(b.rtt)) : "·",
        b ? f(num(b.decms)) : "·", b ? f(num(b.long)) : "·",
      ];
      let ev = (events.get(i) || []).map((e) => e.who + ": " + e.text).join("; ");
      const busy = p && Array.isArray(p.p_top) ? p.p_top.filter((x) => x[2] >= 25) : [];
      if (busy.length) ev = "Pi rechnet auch: " + busy.map((x) => x[0] + " " + x[2] + " %").join(", ") + (ev ? "; " + ev : "");
      L.push((badSet.has(i) ? " ! " : "   ") + line(cells) + (ev ? "  " + ev : ""));
    }
    L.push("");
    L.push("Spalten: Karte = Bilder der Capture-Card, Gesen = vom Pi gesendet (je Zuschauer), Kod = längstes Kodieren eines Bildes (ms),");
    L.push("K+S = längstes Kodieren + Senden in aiortc (ms), Häng = längster Hänger der Bridge (ms), nxbt = längster Aufruf an nxbt (ms),");
    L.push("CPU = Auslastung aller Kerne (%), Brid = Rechenzeit der Bridge mit nxbt und Ton (100 = ein Kern, 400 = alle), MHz = Takt,");
    L.push("Empf = im Browser angekommen, Anz = wirklich angezeigt, Lücke = längste Zeit ohne neues Bild (ms), Verl/NACK/PLI = verlorene,");
    L.push("nachgeforderte Pakete und angeforderte Schlüsselbilder, Puff = Puffer (ms), RTT = Antwortzeit direkt (ms), Dek = Dekodieren (ms),");
    L.push("Seite = längste Blockade der Browser-Seite (ms).");
    if (bridge && bridge.logs.length) {
      L.push("");
      L.push("PROTOKOLL DER BRIDGE");
      bridge.logs.forEach((entry) => L.push("  " + sec(entry[0]) + "  " + entry[1]));
    }
    if (bridge && bridge.lost) L.push("(" + bridge.lost + " Ereignisse/Zeilen der Bridge nicht mehr aufgenommen – Speichergrenze)");
    return L.join("\n") + "\n";
  }

  $("rec-btn").addEventListener("click", () => {
    if (rec) stopRecording();
    else startRecording();
  });
  $("rec-mark").addEventListener("click", markStutter);
  $("rec-save").addEventListener("click", () => { if (recReport) downloadText(recReport.name, recReport.text); });
  $("rec-copy").addEventListener("click", async () => {
    if (!recReport) return;
    try {
      await navigator.clipboard.writeText(recReport.text);
      toast("Aufzeichnung in die Zwischenablage kopiert");
    } catch (err) {
      const area = document.createElement("textarea");
      area.value = recReport.text;
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      toast(ok ? "Aufzeichnung in die Zwischenablage kopiert" : "Kopieren nicht möglich – „Speichern“ nehmen", !ok);
    }
  });

  // ------------------------------------------------------------------ Ton
  // Der Ton kommt als eigene WebRTC-Spur (nur bei WebRTC, nicht bei Einzelbildern). Browser spielen Ton erst nach
  // einer Berührung oder einem Klick ab – darum startet er spätestens beim ersten Tippen auf die Seite.
  let soundWanted = true;
  try { soundWanted = localStorage.getItem("sw2-sound") !== "off"; } catch (err) { /* ohne Speicher */ }

  function soundInfo() {
    const a = (lastStatus && lastStatus.stream && lastStatus.stream.audio) || null;
    if (mode !== "webrtc") return ["Ton gibt es nur bei Übertragung „Video“", "warn"];
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
    $("home-btn").hidden = !config.home_button;
    setTouch(load("switch2-touch", config.default_touch_overlay), false);
    setBar(load("switch2-bar-collapsed", false));
    connect();
    $("transport").value = preferredTransport();
    startVideo(preferredTransport());
    requestWakeLock();
    if (navigator.getGamepads && Array.from(navigator.getGamepads()).some(Boolean)) startPolling();
  }

  init();
})();
