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
      const r = await fetch("api/state/" + enc + "?slot=" + s, {
        method: "PUT", body: e.state, headers: { "Content-Type": "application/octet-stream" },
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
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

  window.EJS_onGameStart = function () {
    window.EJS_emulator.on("saveSaveFiles", (data) => onSaveFiles(data));
    loadServerSave();
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
    location.href = "./";
  });

  // ------------------------------------------------------------------ EmulatorJS
  window.EJS_player = "#game";
  window.EJS_core = window.EMU.core;
  window.EJS_pathtodata = "ejs/";
  window.EJS_gameUrl = "api/rom/" + enc;
  window.EJS_gameName = stem;
  window.EJS_language = "de-GER";
  window.EJS_color = window.EMU.accent;
  window.EJS_backgroundColor = "#101014";
  window.EJS_startOnLoaded = false;        // Klick auf „Start“ erlaubt dem Browser den Ton
  window.EJS_threads = false;
  window.EJS_defaultOptions = { "save-save-interval": "30" };
  window.EJS_Buttons = { netplay: false, exitEmulation: false };
  if (window.EMU.layout) window.EJS_VirtualGamepadSettings = window.EMU.layout;
})();
