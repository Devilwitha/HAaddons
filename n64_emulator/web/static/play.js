/* Emulator – Spielseite.
 *
 * Konfiguriert EmulatorJS (System aus system.js) und gleicht die Spielstände mit dem Server ab:
 *  - Spielstand des Spiels (SRAM): beim Start vom Server laden, danach alle 30 s und beim
 *    Verlassen der Seite hochladen, wenn er sich geändert hat.
 *  - Speicherstände (Slot 1–9 im Menü des Emulators): landen auf dem Server statt als Download.
 */
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const rom = params.get("rom");
  if (!rom) {
    location.replace("./");
    return;
  }
  const enc = encodeURIComponent(rom);
  const stem = rom.replace(/\.[^.]+$/, "");
  const title = stem.replace(/_/g, " ").replace(/^\d{3,4}\s*-\s*/, "");

  document.getElementById("title").textContent = title;
  document.title = title + " – " + window.EMU.short;

  // In der HA-Seitenleiste läuft die Seite in einem iframe – dort sperren manche Browser
  // Vollbild und Controller. Darum ein Knopf, der dieselbe Seite als eigenen Tab öffnet.
  if (window.top !== window.self) {
    const nt = document.getElementById("newtab");
    nt.href = location.href;
    nt.hidden = false;
  }

  // ------------------------------------------------------------------ Hinweise
  const toastEl = document.getElementById("toast");
  const syncEl = document.getElementById("sync");
  let toastTimer = null;

  function toast(text, isError) {
    toastEl.textContent = text;
    toastEl.classList.toggle("error", !!isError);
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, isError ? 6000 : 2500);
  }

  function setSync(state, text) {
    syncEl.dataset.state = state;          // ok | busy | error | idle
    syncEl.title = text;
  }
  setSync("idle", "Spiel noch nicht gestartet");

  function emuMessage(text) {
    try { window.EJS_emulator.displayMessage(text, 2500); } catch (e) { toast(text); }
  }

  // ------------------------------------------------------------------ Spielstand (SRAM)
  let ready = false;            // erst nach dem Laden vom Server hochladen
  let lastSent = null;          // zuletzt gesicherter Stand
  let inflight = null;          // laufender Upload (Promise)
  let queued = null;            // neuerer Stand, der nach dem laufenden Upload folgt

  function sameBytes(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  function gm() {
    return window.EJS_emulator && window.EJS_emulator.gameManager;
  }

  function writeSaveFile(bytes) {
    const g = gm();
    const path = g.getSaveFilePath();
    let dir = "";
    const parts = path.split("/");
    for (let i = 0; i < parts.length - 1; i++) {
      if (!parts[i]) continue;
      dir += "/" + parts[i];
      if (!g.FS.analyzePath(dir).exists) g.FS.mkdir(dir);
    }
    if (g.FS.analyzePath(path).exists) g.FS.unlink(path);
    g.FS.writeFile(path, bytes);
    g.loadSaveFiles();
  }

  function upload(bytes, keepalive) {
    const copy = new Uint8Array(bytes);
    setSync("busy", "Spielstand wird gesichert …");
    return fetch("api/save/" + enc, {
      method: "PUT",
      body: copy,
      headers: { "Content-Type": "application/octet-stream" },
      keepalive: !!keepalive && copy.length < 60000,
    }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      lastSent = copy;
      setSync("ok", "Spielstand gesichert " + new Date().toLocaleTimeString("de-DE"));
    }).catch((e) => {
      setSync("error", "Sichern fehlgeschlagen – wird erneut versucht");
      toast("Spielstand konnte nicht auf dem Server gesichert werden (" + e.message + ")", true);
    });
  }

  function onSaveFiles(data, keepalive) {
    if (!ready || !data || !data.length || sameBytes(data, lastSent)) return inflight;
    if (inflight) {
      queued = new Uint8Array(data);
      return inflight;
    }
    inflight = upload(data, keepalive).finally(() => {
      inflight = null;
      if (queued) {
        const next = queued;
        queued = null;
        onSaveFiles(next);
      }
    });
    return inflight;
  }

  // Spielstand jetzt aus dem Emulator holen und sichern
  function flush(keepalive) {
    const g = gm();
    if (!ready || !g) return Promise.resolve();
    try {
      g.functions.saveSaveFiles();
      return onSaveFiles(g.getSaveFile(false), keepalive) || Promise.resolve();
    } catch (e) {
      return Promise.resolve();
    }
  }

  async function loadServerSave() {
    const g = gm();
    try {
      const r = await fetch("api/save/" + enc, { cache: "no-store" });
      if (r.ok) {
        const bytes = new Uint8Array(await r.arrayBuffer());
        writeSaveFile(bytes);
        g.restart();                       // Spiel liest den Stand beim Booten
        lastSent = bytes;
        setSync("ok", "Spielstand vom Server geladen");
        toast("Spielstand vom Server geladen");
      } else if (r.status === 404) {
        // Kein Stand auf dem Server – ein vorhandener im Browser wird jetzt hochgeladen
        setSync("ok", "Noch kein Spielstand auf dem Server");
        ready = true;
        const local = g.getSaveFile(false);
        if (local && local.length) onSaveFiles(local);
      } else {
        throw new Error("HTTP " + r.status);
      }
    } catch (e) {
      setSync("error", "Server nicht erreichbar");
      toast("Spielstand konnte nicht geladen werden – gespielt wird mit dem Stand im Browser", true);
    }
    ready = true;
  }

  // ------------------------------------------------------------------ Speicherstände (Slots)
  function slot() {
    try {
      const s = parseInt(window.EJS_emulator.getSettingValue("save-state-slot") || "1", 10);
      return s >= 1 && s <= 9 ? s : 1;
    } catch (e) {
      return 1;
    }
  }

  window.EJS_onSaveState = async function (e) {
    const emu = window.EJS_emulator;
    const s = slot();
    try {
      // in Teilen (upload.js): N64-Speicherstände sind größer als die 16-MB-Grenze von Ingress
      await window.chunkedUpload({ kind: "state", name: rom, slot: s }, new Blob([e.state]));
      emuMessage("Gespeichert in Slot " + s);
    } catch (err) {
      emuMessage("Speichern fehlgeschlagen");
      toast("Speicherstand nicht gesichert (" + err.message + ")", true);
      return;
    }
    // Vorschaubild für die Bibliothek (EmulatorJS 4.2.3 liefert es im Ereignis nicht mit)
    try {
      const shot = await emu.takeScreenshot(undefined, "png", 1);
      if (shot && shot.blob) {
        await fetch("api/shot/" + enc + "?slot=" + s, {
          method: "PUT", body: shot.blob, headers: { "Content-Type": "image/png" },
        });
      }
    } catch (err) { /* Bild ist nur Beiwerk */ }
  };

  window.EJS_onLoadState = async function () {
    const s = slot();
    try {
      const r = await fetch("api/state/" + enc + "?slot=" + s, { cache: "no-store" });
      if (r.status === 404) {
        emuMessage("Slot " + s + " ist leer");
        return;
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      gm().loadState(new Uint8Array(await r.arrayBuffer()));
      emuMessage("Slot " + s + " geladen");
    } catch (err) {
      emuMessage("Laden fehlgeschlagen");
      toast("Speicherstand nicht geladen (" + err.message + ")", true);
    }
  };

  // ------------------------------------------------------------------ Hinweisfenster
  const ov = document.getElementById("overlay");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Zeigt ein Fenster; mit primary/secondary wartet es auf die Wahl (true = primary)
  function askOverlay(opts) {
    document.getElementById("ov-title").textContent = opts.title;
    document.getElementById("ov-text").textContent = opts.text;
    const a = document.getElementById("ov-primary");
    const b = document.getElementById("ov-secondary");
    a.hidden = !opts.primary;
    b.hidden = !opts.secondary;
    a.textContent = opts.primary || "";
    b.textContent = opts.secondary || "";
    ov.hidden = false;
    if (!opts.primary && !opts.secondary) return Promise.resolve(null);
    return new Promise((resolve) => {
      const done = (value) => {
        a.onclick = null;
        b.onclick = null;
        ov.hidden = true;
        resolve(value);
      };
      a.onclick = () => done(true);
      b.onclick = () => done(false);
    });
  }

  function hideOverlay() {
    ov.hidden = true;
  }

  function showBanner(text, label, action) {
    const banner = document.getElementById("banner");
    document.getElementById("banner-text").textContent = text;
    const btn = document.getElementById("banner-btn");
    btn.textContent = label;
    btn.onclick = () => {
      banner.hidden = true;
      action();
    };
    document.getElementById("banner-close").onclick = () => { banner.hidden = true; };
    banner.hidden = false;
    setTimeout(() => { banner.hidden = true; }, 20000);
  }

  function clock(iso) {
    try {
      return new Date(iso).toLocaleString("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return "";
    }
  }

  // ------------------------------------------------------------------ Sitzung (Statistik, eine pro Benutzer)
  // Ein HA-Benutzer spielt immer nur auf einem Gerät. Startet er woanders, speichert das alte Gerät
  // (Spielstand + Auto-Speicherstand), pausiert und meldet sich ab; das neue wartet darauf.
  let sid = null;
  let pingTimer = null;
  let stopped = false;               // pausiert wegen Inaktivität oder Gerätewechsel

  function post(url, data) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  }

  function beginPings() {
    clearInterval(pingTimer);
    pingTimer = setInterval(pingSession, 10000);
  }

  function pauseEmu() {
    try { window.EJS_emulator.pause(); } catch (e) { /* noch nicht gestartet */ }
  }

  function playEmu() {
    try { window.EJS_emulator.play(); } catch (e) { /* noch nicht gestartet */ }
  }

  // "ok" = darf spielen, "took" = hat ein anderes Gerät abgelöst, false = abgebrochen
  async function claimSession() {
    let r;
    try {
      r = await post("api/session/start", { rom });
    } catch (e) {
      return "ok";                        // Server nicht erreichbar: trotzdem spielen
    }
    if (r.status === 200) {
      sid = (await r.json()).sid;
      beginPings();
      return "ok";
    }
    if (r.status !== 409) return "ok";
    const info = (await r.json()).conflict || {};
    pauseEmu();
    const take = await askOverlay({
      title: "Du spielst schon auf einem anderen Gerät",
      text: "„" + (info.title || "Ein Spiel") + "“ läuft dort seit " + clock(info.since) +
        ". Dort speichern und beenden und hier weiterspielen?",
      primary: "Hier spielen",
      secondary: "Zurück zur Bibliothek",
    });
    if (!take) {
      location.href = "./";
      return false;
    }
    askOverlay({ title: "Übernehme …", text: "Das andere Gerät speichert und meldet sich ab. Einen Moment …" });
    for (let i = 0; i < 30; i++) {
      try {
        r = await post("api/session/start", { rom, takeover: true });
      } catch (e) {
        r = null;
      }
      if (r && r.status === 200) {          // 202 = anderes Gerät speichert noch (zählt bei fetch als "ok")
        sid = (await r.json()).sid;
        beginPings();
        hideOverlay();
        playEmu();
        return "took";
      }
      if (r && r.status !== 202) break;
      await sleep(2000);
    }
    const retry = await askOverlay({
      title: "Übernahme nicht möglich",
      text: "Das andere Gerät hat nicht geantwortet.",
      primary: "Erneut versuchen",
      secondary: "Zurück zur Bibliothek",
    });
    if (!retry) {
      location.href = "./";
      return false;
    }
    return claimSession();
  }

  async function pingSession() {
    if (!sid) return;
    try {
      const r = await post("api/session/ping", { sid });
      if (r.status === 404) {              // Server neu gestartet oder Sitzung abgelaufen
        sid = null;
        const again = await post("api/session/start", { rom });
        if (again.status === 200) {
          sid = (await again.json()).sid;
        } else if (again.status === 409) {
          autoSave("takeover");
        }
      } else if (r.ok && (await r.json()).stop) {
        autoSave("takeover");
      }
    } catch (e) { /* nächster Versuch in 10 s */ }
  }

  function endSession() {
    clearInterval(pingTimer);
    if (!sid) return;
    const body = JSON.stringify({ sid });
    sid = null;
    if (navigator.sendBeacon && navigator.sendBeacon("api/session/end", new Blob([body], { type: "text/plain" }))) return;
    fetch("api/session/end", { method: "POST", body, keepalive: true }).catch(() => {});
  }

  window.addEventListener("pagehide", endSession);
  window.addEventListener("pageshow", (e) => {
    if (e.persisted && window.EJS_emulator && window.EJS_emulator.started) location.reload();
  });

  // ------------------------------------------------------------------ Automatisch speichern
  // Nach idle_minutes ohne Eingabe (Tastatur, Touch, Maus, Controller): Spielstand sichern,
  // Schnellspeicherstand in Slot 0 ("Auto"), pausieren, Sitzung beenden.
  let idleMs = 10 * 60 * 1000;
  let idleMinutes = 10;
  let lastInput = Date.now();
  let autoSaving = false;
  const padSigs = {};

  function markInput() {
    lastInput = Date.now();
  }

  ["keydown", "pointerdown", "touchstart", "wheel"].forEach((type) => {
    document.addEventListener(type, markInput, { capture: true, passive: true });
  });

  fetch("api/config", { cache: "no-store" }).then((r) => r.json()).then((cfg) => {
    idleMinutes = Number(cfg.idle_minutes) || 0;
    idleMs = idleMinutes * 60 * 1000;
  }).catch(() => {});

  setInterval(() => {
    const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
    pads.forEach((pad) => {
      const sig = pad.buttons.map((b) => (b.pressed ? 1 : 0)).join("") + pad.axes.map((x) => Math.round(x * 5)).join(",");
      if (sig !== padSigs[pad.index]) {
        padSigs[pad.index] = sig;
        markInput();
      }
    });
    const emu = window.EJS_emulator;
    if (idleMs && ready && !stopped && emu && emu.started && Date.now() - lastInput > idleMs) autoSave("idle");
  }, 1000);

  async function saveAutoState() {
    const emu = window.EJS_emulator;
    const state = gm().getState();
    await window.chunkedUpload({ kind: "state", name: rom, slot: 0 }, new Blob([state]));
    try {
      const shot = await emu.takeScreenshot(undefined, "png", 1);
      if (shot && shot.blob) {
        await fetch("api/shot/" + enc + "?slot=0", { method: "PUT", body: shot.blob, headers: { "Content-Type": "image/png" } });
      }
    } catch (e) { /* Bild ist nur Beiwerk */ }
  }

  async function autoSave(reason) {
    const emu = window.EJS_emulator;
    if (autoSaving || stopped || !emu || !emu.started) return;
    autoSaving = true;
    stopped = true;
    pauseEmu();
    setSync("busy", "Wird automatisch gespeichert …");
    await Promise.race([flush(false), sleep(5000)]);
    let ok = true;
    try {
      await saveAutoState();
    } catch (e) {
      ok = false;
    }
    endSession();
    autoSaving = false;
    setSync(ok ? "ok" : "error", ok ? "Automatisch gespeichert" : "Auto-Speicherstand fehlgeschlagen");
    const why = reason === "idle"
      ? (idleMinutes === 1 ? "1 Minute" : idleMinutes + " Minuten") + " keine Eingabe – das Spiel ist pausiert."
      : "Du spielst jetzt auf einem anderen Gerät weiter.";
    const resume = await askOverlay({
      title: reason === "idle" ? "Pausiert" : "Auf anderem Gerät weitergespielt",
      text: why + (ok ? " Spielstand und Auto-Speicherstand sind gesichert."
        : " Der Auto-Speicherstand konnte nicht gesichert werden."),
      primary: reason === "idle" ? "Weiterspielen" : "Hier weiterspielen",
      secondary: "Zur Bibliothek",
    });
    if (!resume) {
      location.href = "./";
      return;
    }
    if (reason !== "idle") {
      location.reload();                  // das andere Gerät hat den neueren Stand
      return;
    }
    const claim = await claimSession();
    if (claim === "took") {
      location.reload();                  // anderes Gerät hat inzwischen gespielt -> frisch laden
      return;
    }
    if (claim) {
      stopped = false;
      markInput();
      playEmu();
    }
  }

  async function offerAutoState() {
    try {
      const r = await fetch("api/roms", { cache: "no-store" });
      const me = (await r.json()).roms.find((x) => x.file === rom);
      const auto = me && me.slots.find((x) => x.slot === 0);
      if (!auto) return;
      showBanner("Auto-Speicherstand vom " + clock(auto.time * 1000), "Laden", async () => {
        try {
          const s = await fetch("api/state/" + enc + "?slot=0", { cache: "no-store" });
          if (!s.ok) throw new Error("HTTP " + s.status);
          gm().loadState(new Uint8Array(await s.arrayBuffer()));
          emuMessage("Auto-Speicherstand geladen");
        } catch (e) {
          toast("Auto-Speicherstand nicht geladen (" + e.message + ")", true);
        }
      });
    } catch (e) { /* ohne Angebot weiterspielen */ }
  }

  window.EJS_onGameStart = async function () {
    window.EJS_emulator.on("saveSaveFiles", (data) => onSaveFiles(data));
    const claim = await claimSession();
    if (!claim) return;
    await loadServerSave();
    markInput();
    offerAutoState();
  };

  // ------------------------------------------------------------------ Verlassen
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flush(true);
  });
  window.addEventListener("pagehide", () => flush(true));

  document.getElementById("back").addEventListener("click", async (ev) => {
    if (!ready) return;
    ev.preventDefault();
    setSync("busy", "Spielstand wird gesichert …");
    await Promise.race([flush(false), new Promise((r) => setTimeout(r, 4000))]);
    endSession();
    location.href = "./";
  });

  // ------------------------------------------------------------------ EmulatorJS
  window.EJS_player = "#game";
  window.EJS_core = window.EMU.core;
  window.EJS_pathtodata = "ejs/";
  window.EJS_gameName = rom;              // mit Endung: Dateiname der Spieldaten im Emulator
  window.EJS_language = "de-GER";
  window.EJS_color = window.EMU.accent;
  window.EJS_backgroundColor = "#101014";
  window.EJS_startOnLoaded = false;        // Klick auf „Start“ erlaubt dem Browser den Ton
  window.EJS_threads = false;
  window.EJS_defaultOptions = { "save-save-interval": "30" };
  window.EJS_Buttons = { netplay: false, exitEmulation: false };
  if (window.EMU.layout) window.EJS_VirtualGamepadSettings = window.EMU.layout;

  // ------------------------------------------------------------------ Spiel laden
  // Große Antworten über Home-Assistant-Ingress bleiben auf manchen Geräten hängen (z. B. bei 18 MB).
  // Darum holt die Seite das Spiel selbst in Teilen zu 4 MB (mit Wiederholung), merkt es sich im
  // Browser-Cache (wo verfügbar) und übergibt es erst dann als blob:-Adresse an EmulatorJS.
  const PART = 4 * 1048576;

  function loadProgress(text, fraction) {
    document.getElementById("romload").hidden = false;
    document.getElementById("romload-text").textContent = text;
    if (fraction !== undefined) document.getElementById("romload-fill").style.width = Math.round(fraction * 100) + "%";
  }

  async function fetchPart(offset, length) {
    for (let attempt = 1; ; attempt++) {
      try {
        const r = await fetch("api/rom/" + enc + "/part?offset=" + offset + "&length=" + length, { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const buf = await r.arrayBuffer();
        if (buf.byteLength !== length) throw new Error("unvollständig");
        return buf;
      } catch (e) {
        if (attempt >= 4) throw e;
        await sleep(1500 * attempt);
      }
    }
  }

  async function prepareRom() {
    const info = await (await fetch("api/rom/" + enc + "/info", { cache: "no-store" })).json();
    const key = new URL("api/rom/" + enc + "?v=" + info.mtime + "-" + info.size, location.href).toString();
    let cache = null;
    try {
      cache = await caches.open("emu-roms-v1");
      const hit = await cache.match(key);
      if (hit) return URL.createObjectURL(await hit.blob());
    } catch (e) {
      cache = null;                        // kein Cache (z. B. ohne HTTPS)
    }
    const parts = [];
    const mb = (n) => (n / 1048576).toFixed(1).replace(".", ",");
    for (let offset = 0; offset < info.size; offset += PART) {
      loadProgress("Spiel wird geladen … " + mb(offset) + " von " + mb(info.size) + " MB", offset / info.size);
      parts.push(await fetchPart(offset, Math.min(PART, info.size - offset)));
    }
    loadProgress("Spiel wird gestartet …", 1);
    const blob = new Blob(parts);
    if (cache) {
      try {
        const prefix = new URL("api/rom/" + enc + "?v=", location.href).toString();
        for (const req of await cache.keys()) {
          if (req.url.startsWith(prefix) && req.url !== key) await cache.delete(req);   // alte Fassung
        }
        await cache.put(key, new Response(blob));
      } catch (e) { /* Speicher voll: nächstes Mal wieder laden */ }
    }
    return URL.createObjectURL(blob);
  }

  function startEmulator(url) {
    document.getElementById("romload").hidden = true;
    window.EJS_gameUrl = url;
    const script = document.createElement("script");
    script.src = "ejs/loader.js";
    document.body.appendChild(script);
  }

  function boot() {
    document.getElementById("romload-retry").hidden = true;
    prepareRom().then(startEmulator).catch((e) => {
      loadProgress("Spiel konnte nicht geladen werden (" + e.message + ").");
      const retry = document.getElementById("romload-retry");
      retry.hidden = false;
      retry.onclick = boot;
    });
  }

  boot();
})();
