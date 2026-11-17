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
