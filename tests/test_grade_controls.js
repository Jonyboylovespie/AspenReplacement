"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

test("an access error keeps saved-session retry available and clears after recovery", () => {
  const nodes = new Map();
  const document = {
    createElement() { return {}; },
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {value: "", setAttribute() {}, replaceChildren() {}});
      return nodes.get(id);
    },
  };
  const context = vm.createContext({document, localStorage: {getItem() { return null; }}});
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  vm.runInContext(source.slice(0, source.indexOf("function featureStatus(")), context);
  const rejected = {signedIn: true, connected: false, needsAuth: true, canRetry: true,
    syncing: false, error: "Aspen denied access.", snapshot: null};
  context.setState(rejected);
  assert.equal(nodes.get("saved-connection").hidden, false);
  for (const id of ["retry-session", "refresh", "disconnect"]) assert.equal(nodes.get(id).disabled, false);
  context.setState({...rejected, syncing: true});
  for (const id of ["retry-session", "refresh", "disconnect"]) assert.equal(nodes.get(id).disabled, true);
  assert.equal(nodes.get("retry-session").textContent, "Retrying saved session…");
  context.setState({...rejected, connected: true, needsAuth: false, error: null});
  assert.equal(nodes.get("saved-connection").hidden, true);
  assert.equal(nodes.get("connection-error").hidden, true);
  assert.equal(nodes.get("error").hidden, true);
  context.setState({...rejected, canRetry: false, error: null});
  assert.equal(nodes.get("saved-connection").hidden, true);
  for (const id of ["retry-session", "refresh", "disconnect"]) assert.equal(nodes.get(id).disabled, true);
  context.setState({...rejected, signedIn: false});
  assert.equal(nodes.get("saved-connection").hidden, true);
  assert.equal(nodes.get("retry-session").disabled, true);
});

test("cached periods switch immediately offline and during refresh without fetching", () => {
  const nodes = new Map();
  const document = {
    createElement() { return {}; },
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {value: "", setAttribute() {}, replaceChildren() {}, close() {}});
      return nodes.get(id);
    },
  };
  const preferences = new Map();
  const context = vm.createContext({document,
    localStorage: {getItem: key => preferences.get(key) || null, setItem: (key, value) => preferences.set(key, value)},
    fetch() { assert.fail("Changing a period must not fetch data."); },
    renderClasses() {}, renderFeed() {}, renderAttendance() {}, route() {},
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  vm.runInContext(source.slice(0, source.indexOf("function featureStatus(")), context);
  vm.runInContext(source.slice(source.indexOf("function changeGradePeriod("), source.indexOf('$("grade-year").addEventListener')), context);
  const years = [{value: "current", label: "Current year"}, {value: "previous", label: "Previous year"}];
  const current = {classes: [{courseName: "Current class"}], gradeFilters: {year: "current", quarter: "current", years,
    quarters: [{value: "current", label: "Current quarter"}]}};
  const previous = {classes: [{courseName: "Previous class"}], gradeFilters: {year: "previous", quarter: "all", years,
    quarters: [{value: "all", label: "All quarters"}]}};
  const snapshot = {mode: "live", syncedAt: "saved", ...current,
    gradePeriods: {"current:current": current, "previous:all": previous}};
  const state = {snapshot,
    connected: false, needsAuth: true, syncing: false};
  context.setState(state);
  for (const id of ["grade-year", "grade-quarter"]) assert.equal(nodes.get(id).disabled, false);
  context.setState({...state, syncing: true});
  for (const id of ["grade-year", "grade-quarter"]) assert.equal(nodes.get(id).disabled, false);
  nodes.get("grade-year").value = "previous";
  assert.equal(context.changeGradePeriod(true), undefined);
  assert.equal(vm.runInContext("currentState.snapshot.classes[0].courseName", context), "Previous class");
  assert.equal(nodes.get("grade-quarter").value, "all");
  assert.equal(preferences.size, 1);
  // A new poll carries the default current view, while the local choice remains.
  context.setState(state);
  assert.equal(vm.runInContext("currentState.snapshot.gradeFilters.year", context), "previous");
  vm.runInContext('gradePeriodOwner = ""; selectedGradePeriod = null;', context);
  context.setState(state);
  assert.equal(nodes.get("grade-year").value, "previous");
});
