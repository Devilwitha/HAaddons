"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => (window.marked && window.DOMPurify) ? DOMPurify.sanitize(marked.parse(s || "", { breaks: false, gfm: true })) : esc(s).replace(/\n/g, "<br>");
const short = (p) => String(p || "").replace(/^\/config\//, "").replace(/^\/homeassistant\//, "");

const ICON = {
  file: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>',
  term: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/></svg>',
  tool: '<svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 00-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 005.4-5.4l-2.5 2.5-2.5-2.5z"/></svg>',
  ask: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 015 .5c0 1.7-2.5 2-2.5 3.5M12 17h.01"/></svg>',
  plan: '<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  ok: '<svg class="st" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
  err: '<svg class="st" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
  spark: '<span class="logo"><svg viewBox="0 0 24 24"><path d="M12 2.5l1.9 6.2 6.1-2.3-3.9 5.1 4.9 4-6.4.2-.9 6.3-2.6-5.8-5.7 2.9 2.3-6-5.8-2.8 6.3-1.2-.9-6.3z"/></svg></span>',
};

// ------------------------------------------------------------------ Werkzeug-Beschriftung
function toolInfo(name, input = {}) {
  const f = short(input.file_path || input.notebook_path || input.path);
  switch (name) {
    case "Read": return { icon: ICON.file, label: `Liest <b>${esc(f)}</b>` };
    case "Write": return { icon: ICON.edit, label: `Schreibt <b>${esc(f)}</b>` };
    case "Edit": case "MultiEdit": case "NotebookEdit": return { icon: ICON.edit, label: `Bearbeitet <b>${esc(f)}</b>` };
    case "Bash": return { icon: ICON.term, label: esc(input.description || "Führt einen Befehl aus") };
    case "Glob": case "Grep": case "LS": return { icon: ICON.search, label: `Durchsucht Dateien${input.pattern ? ` nach <b>${esc(input.pattern)}</b>` : ""}` };
    case "WebFetch": return { icon: ICON.globe, label: `Öffnet <b>${esc(input.url)}</b>` };
    case "WebSearch": return { icon: ICON.globe, label: `Sucht im Web: <b>${esc(input.query)}</b>` };
    case "Task": case "Agent": return { icon: ICON.tool, label: esc(input.description || "Arbeitet an einer Teilaufgabe") };
    default: return { icon: ICON.tool, label: esc(name) };
  }
}

function diffHtml(input) {
  const blk = (txt, cls) => String(txt ?? "").split("\n").map((l) => `<div class="${cls}">${esc(l) || " "}</div>`).join("");
  if (input.edits) return input.edits.map((e) => blk(e.old_string, "d") + blk(e.new_string, "a")).join('<div> </div>');
  if (input.old_string != null) return blk(input.old_string, "d") + blk(input.new_string, "a");
  if (input.content != null) return blk(input.content, "a");
  return "";
}

// ------------------------------------------------------------------ Zustand
const S = { session: null, busy: false, ws: null, stream: null, streamText: "", tools: new Map(), thinkingEl: null, sessions: [] };
const msgs = $("#messages"), scroller = $("#scroll");

function atBottom() { return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120; }
function scrollDown(force) { if (force || atBottom()) requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; }); }
function add(node, force) { const stick = force || atBottom(); hideThinking(); msgs.append(node); $("#empty").classList.add("hidden"); if (S.busy) showThinking(); if (stick) scrollDown(true); return node; }

function clearChat() {
  msgs.innerHTML = ""; S.stream = null; S.streamText = ""; S.tools.clear(); S.thinkingEl = null;
  $("#empty").classList.remove("hidden");
}

function showThinking() {
  if (S.thinkingEl || S.stream) return;
  S.thinkingEl = el("div", "thinking", `${ICON.spark}<span>Denkt nach …</span>`);
  msgs.append(S.thinkingEl); scrollDown();
}
function hideThinking() { if (S.thinkingEl) { S.thinkingEl.remove(); S.thinkingEl = null; } }

// ------------------------------------------------------------------ Rendering
function renderUser(text) { add(el("div", "msg-user", esc(text)), true); }

let renderPending = false;
function onDelta(text) {
  if (!S.stream) { hideThinking(); S.stream = el("div", "msg-ai cursor"); S.streamText = ""; add(S.stream); }
  S.streamText += text;
  if (!renderPending) {
    renderPending = true;
    requestAnimationFrame(() => { renderPending = false; if (S.stream) { const b = atBottom(); S.stream.innerHTML = md(S.streamText); if (b) scrollDown(true); } });
  }
}

function renderAssistant(message) {
  for (const b of message.content || []) {
    if (b.type === "text" && b.text) {
      if (S.stream) { S.stream.classList.remove("cursor"); S.stream.innerHTML = md(b.text); S.stream = null; }
      else add(el("div", "msg-ai", md(b.text)));
    } else if (b.type === "tool_use") {
      if (S.stream) { S.stream.classList.remove("cursor"); S.stream = null; }
      renderTool(b);
    }
  }
}

function renderTool(b) {
  if (b.name === "TodoWrite") return renderTodo(b.input);
  if (b.name === "AskUserQuestion" || b.name === "ExitPlanMode") return; // erscheinen als Abfrage
  const info = toolInfo(b.name, b.input);
  const d = el("details", "tool run");
  d.innerHTML = `<summary><span class="ti">${info.icon}</span><span class="tl">${info.label}</span><span class="st"></span>${ICON.chev}</summary><div class="tbody"></div>`;
  const body = d.querySelector(".tbody");
  if (b.name === "Bash") body.append(el("pre", null, esc(b.input.command)));
  else if (["Edit", "MultiEdit", "Write"].includes(b.name)) body.append(el("div", "diff", diffHtml(b.input)));
  else body.append(el("pre", null, esc(JSON.stringify(b.input, null, 2))));
  S.tools.set(b.id, d);
  add(d);
}

function renderTodo(input) {
  const box = el("div", "todo", `<div class="th">Plan</div>`);
  for (const t of input.todos || []) {
    const mark = t.status === "completed" ? "☑" : t.status === "in_progress" ? "▸" : "☐";
    const cls = t.status === "completed" ? "done" : t.status === "in_progress" ? "active" : "";
    box.append(el("div", "it", `<span>${mark}</span><span class="${cls}">${esc(t.status === "in_progress" && t.activeForm ? t.activeForm : t.content)}</span>`));
  }
  add(box);
}

function resultText(c) {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map((x) => x.type === "text" ? x.text : x.type === "image" ? "[Bild]" : "").join("\n");
  return "";
}

function renderToolResults(message) {
  if (!Array.isArray(message.content)) return;
  for (const r of message.content) {
    if (r.type !== "tool_result") continue;
    const d = S.tools.get(r.tool_use_id);
    if (!d) continue;
    d.classList.remove("run");
    d.classList.add(r.is_error ? "err" : "ok");
    d.querySelector(".st").outerHTML = r.is_error ? ICON.err : ICON.ok;
    const txt = resultText(r.content).trim();
    if (txt) {
      const body = d.querySelector(".tbody");
      body.append(el("div", "lbl", r.is_error ? "Fehler" : "Ergebnis"));
      body.append(el("pre", null, esc(txt.length > 6000 ? txt.slice(0, 6000) + "\n…" : txt)));
    }
  }
}

function finishRunningTools() { for (const d of S.tools.values()) if (d.classList.contains("run")) { d.classList.remove("run"); d.querySelector(".st").outerHTML = ""; } }

// ------------------------------------------------------------------ Erlaubnis-Abfragen
const permCards = new Map();
function renderPerm(p) {
  if (permCards.has(p.id)) return;
  const card = el("div", "perm");
  const input = p.input || {};
  const f = short(input.file_path);
  let icon = ICON.tool, title = `Claude möchte <b>${esc(p.tool)}</b> verwenden`, body = "";
  if (p.tool === "Bash") { icon = ICON.term; title = "Claude möchte einen Befehl ausführen"; body = `${input.description ? `<div class="pd">${esc(input.description)}</div>` : ""}<pre>${esc(input.command)}</pre>`; }
  else if (["Edit", "MultiEdit", "Write"].includes(p.tool)) { icon = ICON.edit; title = `Claude möchte <b>${esc(f)}</b> ${p.tool === "Write" ? "schreiben" : "ändern"}`; body = `<div class="diff">${diffHtml(input)}</div>`; }
  else if (p.tool === "WebFetch") { icon = ICON.globe; title = "Claude möchte eine Webseite öffnen"; body = `<pre>${esc(input.url)}</pre>`; }
  else if (p.tool === "ExitPlanMode") { icon = ICON.plan; title = "Claude hat einen Plan erstellt"; body = `<div class="plan">${md(input.plan)}</div>`; }
  else if (p.tool === "AskUserQuestion") return renderQuestion(p);
  else body = `<pre>${esc(JSON.stringify(input, null, 2))}</pre>`;

  card.innerHTML = `<div class="ph"><span class="ti">${icon}</span><span>${title}</span></div>${body}<div class="pa"></div>`;
  const pa = card.querySelector(".pa");
  const allowLabel = p.tool === "ExitPlanMode" ? "Plan umsetzen" : "Erlauben";
  const b1 = el("button", "btn primary", allowLabel); b1.onclick = () => answer(p.id, true, false);
  pa.append(b1);
  if (p.can_always && p.tool !== "ExitPlanMode") { const b2 = el("button", "btn", "Immer erlauben"); b2.onclick = () => answer(p.id, true, true); pa.append(b2); }
  const b3 = el("button", "btn", "Ablehnen"); b3.onclick = () => answer(p.id, false); pa.append(b3);
  permCards.set(p.id, card);
  add(card, true);
}

function renderQuestion(p) {
  const card = el("div", "perm");
  card.innerHTML = `<div class="ph"><span class="ti">${ICON.ask}</span><span>Claude hat eine Frage</span></div>`;
  const answers = {};
  const qs = (p.input && p.input.questions) || [];
  const submit = el("button", "btn primary", "Antworten");
  const check = () => { submit.disabled = qs.some((q) => !answers[q.question] || !answers[q.question].length); };
  for (const q of qs) {
    const box = el("div", "q", `<div class="qt">${esc(q.question)}</div>`);
    const opts = el("div", "opts");
    answers[q.question] = [];
    for (const o of q.options || []) {
      const b = el("button", "opt", `${esc(o.label)}${o.description ? `<small>${esc(o.description)}</small>` : ""}`);
      b.onclick = () => {
        let sel = answers[q.question];
        if (q.multiSelect) { sel = sel.includes(o.label) ? sel.filter((x) => x !== o.label) : [...sel, o.label]; }
        else { sel = [o.label]; opts.querySelectorAll(".opt").forEach((x) => x.classList.remove("sel")); }
        answers[q.question] = sel;
        b.classList.toggle("sel", sel.includes(o.label));
        check();
      };
      opts.append(b);
    }
    box.append(opts); card.append(box);
  }
  const pa = el("div", "pa");
  submit.onclick = () => {
    const out = {}; for (const k in answers) out[k] = answers[k].join(", ");
    send({ t: "perm", id: p.id, allow: true, answers: out });
  };
  const cancel = el("button", "btn", "Überspringen"); cancel.onclick = () => answer(p.id, false);
  pa.append(submit, cancel); card.append(pa); check();
  permCards.set(p.id, card);
  add(card, true);
}

function answer(id, allow, always) { send({ t: "perm", id, allow, always }); }
function permDone(id, allow) {
  const c = permCards.get(id); if (!c || c.classList.contains("answered")) return;
  c.classList.add("answered");
  c.append(el("div", "verdict muted", allow ? "✓ Erlaubt" : "✕ Abgelehnt"));
}

// ------------------------------------------------------------------ WebSocket
function send(o) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(o)); }

function connect() {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${location.pathname.replace(/[^/]*$/, "")}ws`;
  const ws = new WebSocket(url);
  S.ws = ws;
  ws.onmessage = (ev) => handle(JSON.parse(ev.data));
  ws.onclose = () => { S.ws = null; setTimeout(connect, 2000); };
}

function handle(m) {
  switch (m.t) {
    case "delta": onDelta(m.text); break;
    case "thinking": showThinking(); break;
    case "assistant": renderAssistant(m.message); break;
    case "user": renderToolResults(m.message); break;
    case "perm": renderPerm(m); break;
    case "perm_done": permDone(m.id, m.allow); break;
    case "busy": setBusy(m.busy); break;
    case "session":
      if (m.id !== S.session) { S.session = m.id; }
      loadSessions();
      break;
    case "result":
      if (S.stream) { S.stream.classList.remove("cursor"); S.stream = null; }
      finishRunningTools();
      if (!m.ok && m.text) add(el("div", "notice", esc(m.text)));
      break;
  }
}

function setBusy(b) {
  S.busy = b;
  $("#btn-send").classList.toggle("hidden", b);
  $("#btn-stop").classList.toggle("hidden", !b);
  if (b) showThinking(); else { hideThinking(); if (S.stream) { S.stream.classList.remove("cursor"); S.stream = null; } }
}

// ------------------------------------------------------------------ Sitzungen
async function loadSessions() {
  try { S.sessions = await (await fetch("api/sessions")).json(); } catch { return; }
  const nav = $("#sessions"); nav.innerHTML = "";
  for (const s of S.sessions) {
    const b = el("button", "sess" + (s.id === S.session ? " active" : ""), esc(s.title));
    b.title = s.title;
    b.onclick = () => openSession(s.id);
    nav.append(b);
  }
  const cur = S.sessions.find((s) => s.id === S.session);
  $("#chat-title").textContent = cur ? cur.title : "";
}

async function openSession(id, notify = true) {
  closeSide();
  if (notify) send({ t: "open", session: id });
  S.session = id; clearChat(); permCards.clear();
  const hist = await (await fetch(`api/sessions/${id}`)).json();
  for (const h of hist) {
    if (h.t === "user_text") renderUser(h.text);
    else if (h.t === "assistant") renderAssistant(h.message);
    else if (h.t === "user") renderToolResults(h.message);
  }
  finishRunningTools();
  loadSessions(); scrollDown(true);
}

function newChat() { closeSide(); send({ t: "new" }); S.session = null; clearChat(); permCards.clear(); loadSessions(); $("#input").focus(); }

// ------------------------------------------------------------------ Eingabe
const input = $("#input");
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 220) + "px"; $("#btn-send").disabled = !input.value.trim(); }
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && matchMedia("(min-width: 821px)").matches) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || S.busy) return;
  renderUser(text);
  send({ t: "send", text, session: S.session });
  setBusy(true);
  input.value = ""; autosize();
});
$("#btn-stop").onclick = () => send({ t: "stop" });
document.querySelectorAll(".chip").forEach((c) => c.onclick = () => { input.value = c.textContent; autosize(); input.focus(); });
$("#btn-new").onclick = newChat; $("#btn-new2").onclick = newChat;

function closeSide() { $("#app").classList.remove("side-open"); }
$("#btn-menu").onclick = () => $("#app").classList.add("side-open");
$("#btn-close-side").onclick = closeSide; $("#scrim").onclick = closeSide;

// ------------------------------------------------------------------ Status / Login
async function status() { return (await fetch("api/status")).json(); }

function showRc(r) {
  const box = $("#rc-status");
  box.classList.remove("on", "wait");
  if (!r.enabled) { box.querySelector(".rc-text").textContent = "Remote Control aus"; return; }
  if (r.state === "aktiv") { box.classList.add("on"); box.querySelector(".rc-text").textContent = `Remote Control · „${r.name}“`; }
  else { box.classList.add("wait"); box.querySelector(".rc-text").textContent = `Remote Control ${r.state}`; }
}

function greet() {
  const h = new Date().getHours();
  $("#greeting").textContent = h < 11 ? "Guten Morgen! Was steht an?" : h < 18 ? "Hallo! Was soll ich erledigen?" : "Guten Abend! Was steht an?";
}

async function boot() {
  let st;
  try { st = await status(); } catch { setTimeout(boot, 2000); return; }
  if (!st.auth.loggedIn) { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); return; }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#acc-mail").textContent = st.auth.email || "Angemeldet";
  $("#acc-plan").textContent = st.auth.subscriptionType ? `Claude ${st.auth.subscriptionType[0].toUpperCase()}${st.auth.subscriptionType.slice(1)}` : "";
  $("#avatar").textContent = (st.auth.email || "?")[0].toUpperCase();
  showRc(st.remote); greet();
  if (!S.ws) connect();
  if (st.session) await openSession(st.session, false); else await loadSessions();
  setBusy(st.busy);
  setInterval(async () => { try { showRc((await status()).remote); } catch {} }, 15000);
}

$("#btn-login").onclick = async () => {
  const btn = $("#btn-login"); btn.disabled = true; btn.textContent = "Einen Moment …";
  $("#login-error").classList.add("hidden");
  const win = window.open("about:blank", "_blank");
  try {
    const r = await (await fetch("api/auth/login", { method: "POST" })).json();
    if (!r.url) throw new Error(r.error || "Kein Link");
    if (win) win.location = r.url;
    $("#login-link").href = r.url;
    $("#login-step1").classList.add("hidden"); $("#login-step2").classList.remove("hidden");
    $("#login-code").focus();
  } catch (e) {
    if (win) win.close();
    $("#login-error").textContent = e.message; $("#login-error").classList.remove("hidden");
  } finally { btn.disabled = false; btn.textContent = "Anmelden"; }
};

$("#btn-code").onclick = async () => {
  const code = $("#login-code").value.trim(); if (!code) return;
  const btn = $("#btn-code"); btn.disabled = true; btn.textContent = "Verbinde …";
  $("#login-error").classList.add("hidden");
  try {
    const res = await fetch("api/auth/code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    const r = await res.json();
    if (!res.ok) { const err = new Error(r.error || "Fehlgeschlagen"); err.retry = r.retry; throw err; }
    boot();
  } catch (e) {
    $("#login-error").textContent = e.message; $("#login-error").classList.remove("hidden");
    if (e.retry) { $("#login-code").select(); }
    else { $("#login-step2").classList.add("hidden"); $("#login-step1").classList.remove("hidden"); }
  } finally { btn.disabled = false; btn.textContent = "Verbinden"; }
};
$("#login-code").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#btn-code").click(); });

$("#btn-logout").onclick = async () => {
  if (!confirm("Wirklich von Claude abmelden? Remote Control wird dann ebenfalls beendet.")) return;
  await fetch("api/auth/logout", { method: "POST" });
  location.reload();
};

boot();
