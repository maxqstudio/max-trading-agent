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
