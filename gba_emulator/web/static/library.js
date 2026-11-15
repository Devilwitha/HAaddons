/* Emulator – Bibliothek: Spiele anzeigen, hochladen, löschen. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const grid = $("grid");
  const tpl = $("card");
  const search = $("search");
  const EMU = window.EMU;
  const extText = EMU.exts.join(", ");
  let roms = [];
  let romMax = 0;                    // größte erlaubte Datei in Bytes (vom Server)
  let toastTimer = null;

  document.title = EMU.name;
  $("appname").textContent = EMU.name;
  $("addbtn").title = "ROM-Dateien hochladen (" + extText + ")";
  $("file").accept = EMU.exts.join(",");
  $("folder").textContent = EMU.folder;

  function toast(text, isError) {
    const t = $("toast");
    t.textContent = text;
    t.classList.toggle("error", !!isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 3000);
  }

  function ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return "gerade eben";
    if (s < 3600) return "vor " + Math.round(s / 60) + " Min.";
    if (s < 86400) return "vor " + Math.round(s / 3600) + " Std.";
    if (s < 86400 * 30) {
      const d = Math.round(s / 86400);
      return d === 1 ? "gestern" : "vor " + d + " Tagen";
    }
    return new Date(ts * 1000).toLocaleDateString("de-DE");
  }

  function size(n) {
    return n >= 1048576 ? (n / 1048576).toFixed(1).replace(".", ",") + " MB"
                        : Math.max(1, Math.round(n / 1024)) + " KB";
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const list = roms
      .filter((r) => !q || r.title.toLowerCase().includes(q))
      .sort((a, b) => (b.played || 0) - (a.played || 0) || a.title.localeCompare(b.title, "de"));
    grid.replaceChildren();
    for (const r of list) {
      const node = tpl.content.firstElementChild.cloneNode(true);
      const enc = encodeURIComponent(r.file);
      node.querySelector(".cover").href = "play?rom=" + enc;
      node.querySelector("h3").textContent = r.title;
      node.querySelector("h3").title = r.file;
      node.querySelector(".badge").textContent = r.system;
      node.querySelector(".badge").dataset.system = r.system;
      const parts = [];
      if (r.played) parts.push("Gespielt " + ago(r.played));
      else parts.push("Neu · " + size(r.size));
      const manual = r.slots.filter((s) => s.slot > 0).length;      // Slot 0 = Auto-Speicherstand
      if (manual) parts.push(manual === 1 ? "1 Speicherstand" : manual + " Speicherstände");
      if (r.slots.some((s) => s.slot === 0)) parts.push("Auto-Stand");
      node.querySelector(".meta").textContent = parts.join(" · ");
      const shot = r.slots.filter((s) => s.shot).sort((a, b) => b.time - a.time)[0];
      if (shot) {
        const img = node.querySelector("img");
        img.src = "api/shot/" + enc + "?slot=" + shot.slot + "&t=" + shot.time;
        img.hidden = false;
      }
      node.querySelector(".more").addEventListener("click", () => remove(r));
      grid.appendChild(node);
    }
    $("empty").hidden = roms.length > 0;
    search.hidden = roms.length < 6;
    $("footer").textContent = roms.length
      ? roms.length + (roms.length === 1 ? " Spiel" : " Spiele") + " in " + $("folder").textContent
      : "";
  }

  async function load() {
    try {
      const r = await fetch("api/roms", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      roms = data.roms;
      romMax = data.rom_max || 0;
      $("folder").textContent = data.folder;
      render();
    } catch (e) {
      toast("Spieleliste konnte nicht geladen werden (" + e.message + ")", true);
    }
  }

  async function remove(r) {
    const msg = "„" + r.title + "“ löschen?\n\nDie ROM-Datei wird entfernt. Spielstände bleiben erhalten "
      + "und sind wieder da, wenn du das Spiel erneut hinzufügst.";
    if (!confirm(msg)) return;
    const res = await fetch("api/rom/" + encodeURIComponent(r.file), { method: "DELETE" });
    if (res.ok) {
      toast(r.title + " gelöscht");
      load();
    } else {
      toast("Löschen fehlgeschlagen: " + (await res.text()), true);
    }
  }

  // ------------------------------------------------------------------ Upload
  const lower = (n) => n.toLowerCase();
  const allowed = (f) => EMU.exts.some((e) => lower(f.name).endsWith(e));

  // In Teilen zu 8 MB (upload.js) – Home-Assistant-Ingress nimmt höchstens 16 MB pro Anfrage an
  async function uploadOne(file) {
    const bar = $("progress");
    bar.hidden = false;
    bar.querySelector("span").textContent = file.name;
    try {
      await window.chunkedUpload({ kind: "rom", name: file.name }, file, (sent, total) => {
        bar.firstElementChild.style.width = (100 * sent / total) + "%";
        bar.querySelector("span").textContent = file.name + " – " + Math.round(100 * sent / total) + " %";
      });
      return true;
    } catch (err) {
      toast(file.name + ": " + err.message, true);
      return false;
    }
  }

  async function uploadAll(files) {
    const list = Array.from(files);
    const bad = list.filter((f) => !allowed(f));
    if (bad.length) toast("Übersprungen: " + bad.map((f) => f.name).join(", ") + " (erlaubt: " + extText + ")", true);
    const big = romMax ? list.filter((f) => allowed(f) && f.size > romMax) : [];
    if (big.length) toast("Zu groß: " + big.map((f) => f.name).join(", ") + " (höchstens " + Math.round(romMax / 1048576) + " MB)", true);
    let ok = 0;
    for (const f of list.filter((f) => allowed(f) && !big.includes(f))) {
      if (await uploadOne(f)) ok++;
    }
    $("progress").hidden = true;
    $("progress").firstElementChild.style.width = "0";
    if (ok) toast(ok === 1 ? "Spiel hinzugefügt" : ok + " Spiele hinzugefügt");
    load();
  }

  $("file").addEventListener("change", (e) => {
    if (e.target.files.length) uploadAll(e.target.files);
    e.target.value = "";
  });

  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => {
    if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes("Files")) return;
    dragDepth++;
    $("drop").hidden = false;
  });
  window.addEventListener("dragleave", () => {
    if (--dragDepth <= 0) { dragDepth = 0; $("drop").hidden = true; }
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    $("drop").hidden = true;
    if (e.dataTransfer.files.length) uploadAll(e.dataTransfer.files);
  });

  search.addEventListener("input", render);
  window.addEventListener("pageshow", (e) => { if (e.persisted) load(); });
  load();
})();
