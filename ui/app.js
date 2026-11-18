"use strict";
const $ = (id) => document.getElementById(id);
const state = { runId: null, after: 0, events: [], filter: "ALL", selected: null, busy: false };
const names = ["Supervisor", "MT5 Specialist", "RAG Curator", "Market Analyst",
               "Bull / Bear Researchers", "Evidence Lab", "Independent Policy Guard"];

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error("HTTP " + response.status);
  return await response.json();
}

function renderRoles() {
  $("roles").replaceChildren();
  for (const name of names) {
    const last = [...state.events].reverse().find((x) => x.role === name);
    const row = node("div", "role");
    const label = node("div");
    label.append(node("strong", "", name), node("span", "", last ? last.event_type : "NOT_INVOKED"));
    row.append(label, node("span", "state", last ? last.status : "WAITING"));
    $("roles").append(row);
  }
}

function matchesFilter(event) {
  if (state.filter === "ALL") return true;
  if (state.filter === "TOOL") return event.event_type.includes("TOOL") || event.event_type.includes("KNOWLEDGE");
  if (state.filter === "DECISION") return event.event_type === "DECISION" || event.event_type === "REPORT_READY";
  return event.status === "BLOCKED" || event.status === "FAILED";
}

function showEvent(event) {
  state.selected = event.event_id;
  $("inspector-title").textContent = event.role + " · " + event.event_type;
  $("inspector-message").textContent = event.message;
  $("inspector-json").textContent = JSON.stringify({
    event_id: event.event_id,
    occurred_at: event.occurred_at,
    status: event.status,
    evidence: event.details
  }, null, 2);
  renderEvents();
}

function renderEvents() {
  $("stream-count").textContent = state.events.length + " recorded events";
  const list = $("stream");
  list.replaceChildren();
  const visible = state.events.filter(matchesFilter);
  if (!visible.length) list.append(node("p", "muted empty", "No matching recorded events."));
  for (const event of visible) {
    const item = node("button", "event" + (event.event_id === state.selected ? " selected" : ""));
    const header = node("div", "event-header");
    header.append(node("span", "", event.role), node("span", "", event.occurred_at.slice(11, 23) + " UTC"));
    item.append(header, node("strong", "", event.event_type),
      node("span", "message", event.message),
      node("span", "event-type" + (event.status === "BLOCKED" ? " blocked" : ""), event.status));
    item.addEventListener("click", () => showEvent(event));
    list.append(item);
  }
}

function updateDecision() {
  const d = [...state.events].reverse().find((x) => x.event_type === "DECISION");
  $("final-decision").textContent = d ? d.details.action + " · " + d.details.vetoes.join(", ") : "NONE — awaiting evidence";
}

async function loadRuns() {
  const data = await requestJson("/api/runs");
  $("runs").replaceChildren();
  if (!data.runs.length) $("runs").append(node("p", "muted", "No missions recorded yet."));
  for (const run of data.runs) {
    const item = node("button", "run-entry" + (state.runId === run.run_id ? " active" : ""));
    item.append(node("strong", "", run.task), node("small", "", run.status + " · " + run.run_id.slice(0, 8)));
    item.addEventListener("click", () => selectRun(run.run_id));
    $("runs").append(item);
  }
  const current = data.runs.find((x) => x.run_id === state.runId);
  $("run-status").textContent = current ? current.status : "IDLE";
}

async function selectRun(runId) {
  state.runId = runId;
  state.after = 0;
  state.events = [];
  state.selected = null;
  $("inspector-title").textContent = "Select an event";
  $("inspector-message").textContent = "Inspect a recorded tool call or decision.";
  $("inspector-json").textContent = "No event selected.";
  await loadEvents();
  await loadRuns();
}

async function loadEvents() {
  if (!state.runId) return;
  const data = await requestJson("/api/events?run_id=" + encodeURIComponent(state.runId) + "&after=" + state.after);
  if (data.events.length) {
    state.events.push(...data.events);
    state.after = state.events[state.events.length - 1].event_id;
    renderRoles();
    renderEvents();
    updateDecision();
  }
}

$("run-btn").addEventListener("click", async () => {
  if (state.busy) return;
  state.busy = true;
  $("run-btn").disabled = true;
  try {
    const result = await requestJson("/api/run", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({task: $("mission-task").value})
    });
    await selectRun(result.run_id);
  } catch (error) {
    $("inspector-message").textContent = "Unable to start reference mission: " + error.message;
  } finally {
    state.busy = false;
    $("run-btn").disabled = false;
  }
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === button));
    renderEvents();
  });
});

async function poll() {
  try {
    await loadEvents();
    await loadRuns();
  } catch (error) {
    $("run-status").textContent = "DISCONNECTED";
  }
}

loadRuns().then(async () => {
  const data = await requestJson("/api/runs");
  if (data.runs.length) await selectRun(data.runs[0].run_id);
}).catch(() => { $("run-status").textContent = "DISCONNECTED"; });
setInterval(poll, 1500);

/* ---------------- Trading company floor (observation only) ---------------- */
const SVGNS = "http://www.w3.org/2000/svg";
const AGENT_ROLES = {
  scanner: ["MT5 Specialist"], analyst: ["Market Analyst"], brain: ["Supervisor", "Bull / Bear Researchers"],
  check: ["RAG Curator", "Evidence Lab"], risk: ["Independent Policy Guard"], executor: []
};
const desk = { after: 0, messages: [], started: Date.now() };

function svg(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  return el;
}

// Deterministic SYNTHETIC illustration. It is labelled as such in the UI and is not market data.
function drawSampleChart() {
  const chart = $("chart");
  if (!chart) return;
  chart.replaceChildren();
  let seed = 7;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  let price = 125;
  const n = 60, w = 600 / n;
  for (let i = 0; i < n; i++) {
    const open = price, close = open + (rand() - 0.5) * 10 + Math.sin(i / 7) * 3;
    const hi = Math.max(open, close) + rand() * 5, lo = Math.min(open, close) - rand() * 5;
    price = close;
    const up = close >= open, color = up ? "#3ddc97" : "#f0647d";
    const x = i * w + w / 2, y = (v) => 240 - (v - 60) * 2.2;
    chart.append(svg("line", { x1: x, x2: x, y1: y(lo), y2: y(hi), stroke: color, "stroke-width": 1 }));
    chart.append(svg("rect", { x: x - w * 0.3, width: w * 0.6, y: y(Math.max(open, close)),
      height: Math.max(1.5, Math.abs(close - open)), fill: color }));
  }
  const scan = svg("line", { y1: 0, y2: 240, stroke: "#6be4c7", "stroke-width": 1, opacity: 0.5 });
  scan.append(svg("animate", { attributeName: "x1", values: "0;600", dur: "9s", repeatCount: "indefinite" }));
  scan.append(svg("animate", { attributeName: "x2", values: "0;600", dur: "9s", repeatCount: "indefinite" }));
  chart.append(scan);
}

function drawSpark(el, values) {
  el.replaceChildren();
  const max = Math.max(1, ...values);
  values.slice(-20).forEach((v, i) => {
    const h = Math.max(2, (v / max) * 20);
    el.append(svg("rect", { x: i * 6, y: 22 - h, width: 4, height: h }));
  });
}

function agentSummary(agent, evs) {
  const mine = evs.filter((e) => AGENT_ROLES[agent].includes(e.role));
  const last = mine[mine.length - 1];
  const find = (t) => evs.find((e) => e.event_type === t);
  switch (agent) {
    case "scanner": {
      const p = find("TOOL_PLAN");
      return { big: p ? p.details.required_tools.length : "—", sub: p ? "langkah MT5 direncanakan, belum dipanggil" : "menunggu misi", last };
    }
    case "analyst": return { big: last ? "0" : "—", sub: last ? "setup · data pasar tidak tersedia" : "menunggu misi", last };
    case "brain": return { big: evs.length || "—", sub: evs.length ? "event · keputusan NO_TRADE" : "menunggu misi", last };
    case "check": {
      const r = find("TOOL_RESULT");
      return { big: r ? r.details.documents_indexed : "—", sub: r ? "dokumen indeks diperiksa" : "menunggu misi", last };
    }
    case "risk": {
      const d = find("DECISION");
      return { big: d ? d.details.vetoes.length : "—", sub: d ? "veto aktif · NO_TRADE" : "menunggu misi", last };
    }
    default: return { big: 0, sub: "order terkirim · terkunci", last: null };
  }
}

function renderFloor() {
  const running = state.events.length && !state.events.some((e) => e.event_type === "REPORT_READY" || e.event_type === "MISSION_FAILED");
  const recent = state.events.slice(-2).map((e) => e.role);
  document.querySelectorAll(".agent").forEach((card) => {
    const agent = card.dataset.agent;
    const s = agentSummary(agent, state.events);
    card.querySelector("[data-big]").textContent = s.big;
    card.querySelector("[data-sub]").textContent = s.sub;
    if (agent !== "executor") {
      const active = running && AGENT_ROLES[agent].some((r) => recent.includes(r));
      card.classList.toggle("active", Boolean(active));
      card.querySelector("[data-st]").textContent = active ? "Working" : s.last ? s.last.status : "Idle";
      const vals = state.events.map((e) => (AGENT_ROLES[agent].includes(e.role) ? 1 : 0.15));
      drawSpark(card.querySelector("[data-spark]"), vals);
    }
  });
  const tl = $("timeline");
  tl.replaceChildren();
  const total = state.events.length;
  state.events.forEach((e, i) => {
    const mark = node("i", e.status);
    mark.style.left = (total > 1 ? (i / (total - 1)) * 98 : 0) + "%";
    mark.title = e.role + " · " + e.event_type;
    tl.append(mark);
  });
  $("tl-label").textContent = total ? "Timeline misi · " + total + " event" : "Timeline misi · belum ada misi";
}

function renderDesk() {
  const feed = $("feed");
  feed.replaceChildren();
  const items = desk.messages.filter((m) => m.kind !== "QUESTION");
  for (const m of [...items].reverse()) {
    const row = node("div", "msg" + (m.author === "jackson" ? " jackson" : ""));
    row.append(node("time", "", m.created_at.slice(11, 19) + " UTC"), node("b", "", m.author),
      node("span", "", " " + m.body));
    const card = document.querySelector('.agent[data-agent="' + m.author + '"]');
    if (card) row.style.setProperty("--c", getComputedStyle(card).getPropertyValue("--c"));
    feed.append(row);
  }
  $("feed-count").textContent = items.length + " pesan";
  const box = $("questions");
  box.replaceChildren();
  const open = desk.messages.filter((m) => m.kind === "QUESTION" && !m.answered);
  $("open-q").textContent = open.length + " terbuka";
  if (!open.length) box.append(node("p", "muted", "Tidak ada pertanyaan terbuka. Tim akan bertanya lagi nanti."));
  for (const q of open) {
    const el = node("div", "q");
    el.append(node("b", "", q.author), node("div", "", q.body));
    const opts = node("div", "opts");
    for (const o of q.options) {
      const b = node("button", "", o);
      b.type = "button";
      b.addEventListener("click", () => sendReply(o, q.msg_id, null));
      opts.append(b);
    }
    const form = node("form");
    const input = node("input");
    input.maxLength = 500;
    input.placeholder = "Jawaban sendiri…";
    input.setAttribute("aria-label", "Jawaban untuk " + q.author);
    const send = node("button", "", "Balas");
    send.type = "submit";
    form.append(input, send);
    form.addEventListener("submit", (ev) => { ev.preventDefault(); if (input.value.trim()) sendReply(input.value, q.msg_id, null); });
    el.append(opts, form);
    box.append(el);
  }
}

async function sendReply(body, questionId, to) {
  try {
    await requestJson("/api/desk/reply", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({body: body, question_id: questionId, to: to})
    });
    await pollDesk(true);
  } catch (error) {
    $("feed-count").textContent = "gagal kirim: " + error.message;
  }
}

async function pollDesk(full) {
  if (full) { desk.after = 0; desk.messages = []; }
  const data = await requestJson("/api/desk?after=" + desk.after);
  if (data.started_at) desk.started = Date.parse(data.started_at) || desk.started;
  if (data.messages.length) {
    desk.messages.push(...data.messages);
    desk.after = desk.messages[desk.messages.length - 1].msg_id;
  }
  // question state can change (answered); refresh the full list occasionally
  const fresh = await requestJson("/api/desk?after=0");
  desk.messages = fresh.messages;
  renderDesk();
}

function tickClock() {
  const now = new Date();
  $("clock").textContent = now.toISOString().slice(11, 19) + " UTC";
  const mins = Math.floor((Date.now() - desk.started) / 60000);
  $("uptime").textContent = "Running " + (mins >= 60 ? Math.floor(mins / 60) + "h " : "") + (mins % 60) + "m";
}

$("note-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const input = $("note-body");
  if (!input.value.trim()) return;
  sendReply(input.value, null, $("note-to").value);
  input.value = "";
});

drawSampleChart();
tickClock();
setInterval(tickClock, 1000);
setInterval(renderFloor, 1500);
pollDesk(true).catch(() => {});
setInterval(() => pollDesk(false).catch(() => {}), 3000);
