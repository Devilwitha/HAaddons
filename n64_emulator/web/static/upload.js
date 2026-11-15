/* Emulator – Upload in Stücken.
 *
 * Home-Assistant-Ingress nimmt höchstens 16 MB pro Anfrage an. Größere Dateien (N64-ROMs, N64-
 * Speicherstände) gehen deshalb in Teilen zu höchstens 8 MB: start -> Teile -> finish.
 *
 *   chunkedUpload({kind: "rom", name}, blob, onProgress)          ROM in die Bibliothek
 *   chunkedUpload({kind: "state", name, slot}, blob, onProgress)  Speicherstand
 *
 * onProgress(gesendet, gesamt). Fehler werfen ein Error mit der Meldung des Servers.
 */
(function () {
  "use strict";

  async function fail(res) {
    let text = "";
    try { text = await res.text(); } catch (e) { /* leer */ }
    try {
      const data = JSON.parse(text);
      text = data.error || text;
    } catch (e) { /* war kein JSON */ }
    return new Error(text || "Fehler " + res.status);
  }

  // Ein Teil mit XHR, damit es auch innerhalb des Teils Fortschritt gibt
  function sendPart(url, part, onPartProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onPartProgress(e.loaded); };
      xhr.onload = () => resolve({ status: xhr.status, text: xhr.responseText });
      xhr.onerror = () => reject(new Error("Verbindung unterbrochen"));
      xhr.send(part);
    });
  }

  async function chunkedUpload(fields, blob, onProgress) {
    const progress = onProgress || (() => {});
    const res = await fetch("api/blob/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({}, fields, { size: blob.size })),
    });
    if (!res.ok) throw await fail(res);
    const { id, chunk } = await res.json();
    const size = Math.min(chunk || 8388608, 8388608);
    let offset = 0;
    try {
      while (offset < blob.size) {
        const part = blob.slice(offset, offset + size);
        let attempt = 0;
        for (;;) {
          let answer;
          try {
            answer = await sendPart("api/blob/" + id + "?offset=" + offset, part, (n) => progress(offset + n, blob.size));
          } catch (err) {
            if (++attempt >= 3) throw err;          // kurzer Netzaussetzer: bis zu 3 Versuche
            await new Promise((r) => setTimeout(r, 1500));
            continue;
          }
          if (answer.status === 200) {
            offset = JSON.parse(answer.text).received;
            break;
          }
          if (answer.status === 409) {               // Server hat einen anderen Stand -> dort weitermachen
            offset = JSON.parse(answer.text).received;
            break;
          }
          throw new Error(answer.text || "Fehler " + answer.status);
        }
        progress(offset, blob.size);
      }
      const done = await fetch("api/blob/" + id + "/finish", { method: "POST" });
      if (!done.ok) throw await fail(done);
      return done.json();
    } catch (err) {
      fetch("api/blob/" + id, { method: "DELETE" }).catch(() => {});
      throw err;
    }
  }

  window.chunkedUpload = chunkedUpload;
})();
