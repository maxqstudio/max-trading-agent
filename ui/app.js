"use strict";
const $ = (id) => document.getElementById(id);
const state = { runId: null, after: 0, events: [], filter: "ALL", selected: null, busy: false };
const teams = [
  {name: "SUPERVISOR", roles: ["Supervisor"]},
  {name: "MARKET INTELLIGENCE", roles: ["MT5 Specialist", "Market Watcher", "Market Analyst"]},
  {name: "RESEARCH & DEBATE", roles: ["Bull Researcher", "Bear Researcher", "Research Manager", "Hypothesis Strategist", "Bull / Bear Researchers"]},
  {name: "KNOWLEDGE", roles: ["RAG Curator"]},
  {name: "EVIDENCE & RISK", roles: ["Market Data Auditor", "Evidence Lab", "Shadow Lab", "Paper Orchestrator", "Paper Auditor", "Independent Policy Guard"]}
];

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
  for (const team of teams) {
    $("roles").append(node("div", "team-heading", team.name));
    for (const name of team.roles) {
      const events = state.events.filter((x) => x.role === name);
      const last = events.length ? events[events.length - 1] : null;
      const row = node("div", "role");
      const label = node("div");
      label.append(node("strong", "", name), node("span", "", last ? last.event_type : "NOT_INVOKED"));
      const roleState = last ? (last.event_type === "ROLE_STARTED" ? "RUNNING" : last.status) : "NOT INVOKED";
      row.append(label, node("span", "state", roleState));
      $("roles").append(row);
    }
  }
}

function matchesFilter(event) {
  if (state.filter === "ALL") return true;
  if (state.filter === "TOOL") return event.event_type.includes("TOOL") || event.event_type.includes("KNOWLEDGE") || event.event_type.includes("RAG");
  if (state.filter === "DECISION") return event.event_type === "DECISION" || event.event_type === "REPORT_READY";
  return event.status === "BLOCKED" || event.status === "FAILED";
}

function showEvent(event) {
  state.selected = event.event_id;
  $("inspector-title").textContent = event.role + " · " + event.event_type;
  $("inspector-message").textContent = event.message;
  $("report-body").textContent = typeof event.details?.report === "string" ? event.details.report : "";
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
  const decision = [...state.events].reverse().find((event) => event.event_type === "DECISION");
  const action = decision?.details?.action;
  if (action !== "NO_TRADE" && action !== "HOLD") {
    $("final-decision").textContent = "NONE — awaiting verified NO_TRADE/HOLD decision";
    return;
  }
  const vetoes = Array.isArray(decision.details.vetoes)
    ? decision.details.vetoes.filter((item) => typeof item === "string")
    : [];
  $("final-decision").textContent = action +
    (vetoes.length ? " · " + vetoes.join(", ") : " · no veto details recorded");
}

async function loadRuns() {
  const data = await requestJson("/api/runs");
  $("runs").replaceChildren();
  if (!data.runs.length) $("runs").append(node("p", "muted", "No missions recorded yet."));
  for (const run of data.runs) {
    const item = node("button", "run-entry" + (state.runId === run.run_id ? " active" : ""));
    item.append(node("strong", "", run.task), node("small", "", run.mode + " · " + run.status + " · " + run.run_id.slice(0, 8)));
    item.addEventListener("click", () => selectRun(run.run_id));
    $("runs").append(item);
  }
  const current = data.runs.find((x) => x.run_id === state.runId);
  $("run-status").textContent = current ? current.status : "IDLE";
  $("active-mode").textContent = "MODE: " + (current?.mode || "REFERENCE");
  const invoked = state.events.some((e) => e.event_type === "ROLE_STARTED");
  $("llm-status").textContent = invoked ? "LLM: role invocations recorded" : "LLM: not invoked in this run";
}

async function selectRun(runId) {
  state.runId = runId;
  state.after = 0;
  state.events = [];
  state.selected = null;
  $("inspector-title").textContent = "Select an event";
  $("inspector-message").textContent = "Inspect a recorded tool call or decision.";
  $("report-body").textContent = "";
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


function addEvidenceMetric(parent, label, value) {
  const item = node("div", "evidence-metric");
  item.append(node("span", "muted", label), node("strong", "", String(value)));
  parent.append(item);
}

async function loadEvidence() {
  try {
    const evidence = await requestJson("/api/evidence");
    $("evidence-status").textContent = evidence.status;
    const totals = $("evidence-totals");
    totals.replaceChildren();
    const count = evidence.totals;
    for (const [label, value] of [
      ["Attempted cycles", count.attempted_cycles],
      ["ABSTAIN", count.abstained],
      ["Rejected", count.rejected],
      ["Failed / blocked", count.failed_jobs + count.blocked_jobs],
      ["Needs review", count.review_required_jobs],
      ["Pending outcomes", count.pending_episodes],
      ["Settled outcomes", count.settled_episodes],
      ["Unmatched cycles", count.unmatched_cycles],
      ["Legacy manual excluded", count.legacy_manual_excluded],
    ]) addEvidenceMetric(totals, label, value);
    const symbols = $("evidence-symbols");
    symbols.replaceChildren();
    for (const item of evidence.symbols) {
      const panel = node("article", "evidence-symbol");
      panel.append(node("strong", "", item.symbol + " · " + item.evidence_grade));
      panel.append(node("p", "muted",
        item.attempts + " attempted · " + item.settled + " settled · " +
        item.pending + " pending · " + item.abstained + " abstained"));
      if (item.account_scope === "MIXED") {
        panel.append(node("p", "evidence-warning",
          "Multiple broker accounts detected. P/L aggregation blocked."));
      } else if (item.hypothetical_pnl) {
        const data = item.hypothetical_pnl;
        panel.append(node("p", "",
          "Simulated net: " + data.net_price_units_per_unit +
          " price units per one unit · " + data.winners + " wins / " +
          data.losers + " losses / " + data.flat + " flat"));
        panel.append(node("p", "muted",
          "Synthetic realized drawdown: " +
          data.max_realized_drawdown_price_units_per_unit +
          " price units per unit. No real fill or account-currency return."));
      }
      if (item.execution_assumptions) {
        const costs = item.execution_assumptions;
        const span = (range) => !range ? "not observed" :
          range.min === range.max ? String(range.min) : range.min + " to " + range.max;
        panel.append(node("p", "muted",
          "Modeled fee/side: " + span(costs.modeled_fee_per_side) +
          " · adverse slippage/side: " + span(costs.modeled_slippage_per_side) +
          " · recorded entry spread: " + span(costs.recorded_entry_spread) +
          " · recorded exit spread: " + span(costs.recorded_exit_spread) +
          " (price units per asset unit; hypothetical fills)."));
      }
      symbols.append(panel);
    }
    if (!evidence.symbols.length) {
      symbols.append(node("p", "muted", "No opt-in forward shadow episodes recorded."));
    }
    $("evidence-limitations").textContent = evidence.limitations;
  } catch (error) {
    // Integrity errors must never be styled as an empty or profitable sample.
    $("evidence-status").textContent = "UNVERIFIED";
    $("evidence-totals").textContent = "Evidence currently unavailable or inconsistent.";
    $("evidence-symbols").replaceChildren();
    $("evidence-limitations").textContent =
      "Do not infer profitability from unavailable or blocked evidence.";
  }
}

async function loadReadiness() {
  try {
    const report = await requestJson("/api/readiness");
    // Readiness is an observation endpoint, NEVER a trading-permission source.
    if (report.status !== "BLOCKED" || report.decision !== "NO_TRADE" ||
        report.broker_execution !== false || report.demo_authorized !== false ||
        report.live_authorized !== false || report.owner_acceptance !== "PENDING" ||
        !Array.isArray(report.gates)) {
      throw new Error("Unsafe readiness status");
    }
    $("readiness-status").textContent = "BLOCKED";
    $("readiness-observed").textContent = report.observations_status +
      " · local mission counts only";
    const counts = $("readiness-counts");
    counts.replaceChildren();
    for (const [key, label] of [
      ["terminal_checks", "Read-only terminal attempts"],
      ["signal_research", "SIGNAL research"],
      ["paper_drills", "Paper simulations"],
      ["shadow_intents", "Shadow intents"],
    ]) {
      const value = report.observations?.[key]?.attempts;
      if (!Number.isSafeInteger(value) || value < 0) throw new Error("Invalid observation count");
      addEvidenceMetric(counts, label, value);
    }
    const gates = $("readiness-gates");
    gates.replaceChildren();
    for (const gate of report.gates) {
      const line = node("article", "readiness-gate");
      const heading = node("div", "readiness-gate-heading");
      heading.append(node("strong", "", gate.id + " · " + gate.name),
                     node("span", "evidence-warning", gate.status));
      line.append(heading, node("p", "muted", gate.why));
      gates.append(line);
    }
    $("readiness-limitations").textContent = report.limitations;
  } catch (error) {
    // Missing or corrupted journal never becomes an empty-success claim.
    $("readiness-status").textContent = "INTEGRITY BLOCKED";
    $("readiness-observed").textContent = "Readiness evidence unavailable or inconsistent.";
    $("readiness-counts").replaceChildren();
    $("readiness-gates").replaceChildren();
    $("readiness-limitations").textContent = "NO_TRADE remains enforced; do not infer readiness.";
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

let refreshNumber = 0;
async function poll() {
  try {
    await loadEvents();
    await loadRuns();
    refreshNumber += 1;
    if (refreshNumber % 4 === 0) {
      await loadEvidence();
      await loadReadiness();
    }
  } catch (error) {
    $("run-status").textContent = "DISCONNECTED";
  }
}

loadEvidence();
loadReadiness();
loadRuns().then(async () => {
  const data = await requestJson("/api/runs");
  if (data.runs.length) await selectRun(data.runs[0].run_id);
}).catch(() => { $("run-status").textContent = "DISCONNECTED"; });
setInterval(poll, 1500);
