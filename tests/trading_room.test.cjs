"use strict";
// Execute the actual Trading Room script against a small deterministic DOM.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const test = require("node:test");

function room() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        textContent: "", disabled: false,
        replaceChildren() {}, append() {}, addEventListener() {},
      });
    }
    return elements.get(id);
  }
  const context = vm.createContext({
    document: {
      getElementById: element,
      querySelectorAll() { return []; },
      createElement(tag) { return {tag, textContent: "", append() {}}; },
    },
    fetch: async () => ({ok: true, json: async () => ({runs: [], totals: {}, symbols: []})}),
    setInterval() { return 0; },
    console: {error() {}},
  });
  const code = fs.readFileSync(path.join(__dirname, "..", "ui", "app.js"), "utf8");
  vm.runInContext(code, context, {filename: "ui/app.js"});
  return {context, element};
}

test("paper final NO_TRADE without vetoes must render, not throw", () => {
  const {context, element} = room();
  vm.runInContext('state.events = [{event_type: "DECISION", details: {action: "NO_TRADE", order_send_called: false}}]; updateDecision();', context);
  assert.match(element("final-decision").textContent, /^NO_TRADE/);
  assert.match(element("final-decision").textContent, /no veto details recorded/);
});

test("non-empty policy vetoes retain NO_TRADE reasoning", () => {
  const {context, element} = room();
  vm.runInContext('state.events = [{event_type: "DECISION", details: {action: "NO_TRADE", vetoes: ["KILL_SWITCH", "NON_DEMO_ACCOUNT"]}}]; updateDecision();', context);
  assert.equal(element("final-decision").textContent, "NO_TRADE · KILL_SWITCH, NON_DEMO_ACCOUNT");
});

test("malformed details cannot crash or invent permitted trade", () => {
  const {context, element} = room();
  const cases = [
    'state.events = [{event_type: "DECISION", details: {action: "BUY"}}];',
    'state.events = [{event_type: "DECISION", details: {action: "NO_TRADE", vetoes: "text"}}];',
    'state.events = [{event_type: "DECISION", details: null}];',
    'state.events = [];',
  ];
  for (const code of cases) {
    vm.runInContext(code + "updateDecision();", context);
    assert.doesNotMatch(element("final-decision").textContent, /^BUY/);
  }
});

test("paper roles appear in Trading Room roster", () => {
  const {context} = room();
  const names = vm.runInContext("teams.flatMap((team) => team.roles)", context);
  assert.ok(names.includes("Paper Orchestrator"));
  assert.ok(names.includes("Paper Auditor"));
});

test("readiness stays BLOCKED even with successful local observations", async () => {
  const {context, element} = room();
  const report = {
    status: "BLOCKED", decision: "NO_TRADE", broker_execution: false,
    demo_authorized: false, live_authorized: false, owner_acceptance: "PENDING",
    observations_status: "LOCAL_RUNS_NOT_INDEPENDENTLY_VERIFIED",
    observations: {
      terminal_checks: {attempts: 2}, signal_research: {attempts: 4},
      paper_drills: {attempts: 1}, shadow_intents: {attempts: 2},
    },
    gates: [{id: "M07", name: "Live trade", status: "LIVE_NOT_IMPLEMENTED",
             why: "No real broker authorization"}],
    limitations: "Local activity is not a financial release.",
  };
  context.fetch = async () => ({ok: true, json: async () => report});
  await vm.runInContext("loadReadiness()", context);
  assert.equal(element("readiness-status").textContent, "BLOCKED");
  assert.match(element("readiness-observed").textContent, /LOCAL_RUNS_NOT_INDEPENDENTLY_VERIFIED/);
  assert.match(element("readiness-limitations").textContent, /not a financial release/);
});

test("forged READY or live authorization never renders as ready", async () => {
  const {context, element} = room();
  const report = {
    status: "READY", decision: "BUY", broker_execution: true,
    demo_authorized: true, live_authorized: true, owner_acceptance: "PASS",
    observations_status: "FORGED", gates: [], observations: {},
  };
  context.fetch = async () => ({ok: true, json: async () => report});
  await vm.runInContext("loadReadiness()", context);
  assert.equal(element("readiness-status").textContent, "INTEGRITY BLOCKED");
  assert.match(element("readiness-limitations").textContent, /NO_TRADE/);
});
