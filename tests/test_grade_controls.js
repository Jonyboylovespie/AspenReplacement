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
  const state = {signedIn: true, snapshot,
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

test("home stays current through grade changes, polling, reload, and class navigation", () => {
  const nodes = new Map();
  function node(tag) {
    return {tag, value: "", textContent: "", children: [], listeners: {}, classList: {add() {}},
      setAttribute() {}, removeAttribute() {},
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      addEventListener(type, listener) { this.listeners[type] = listener; }};
  }
  const document = {
    createElement: node,
    querySelectorAll() { return []; },
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, node("div"));
      return nodes.get(id);
    },
  };
  const preferences = new Map();
  const location = {hash: "#home"};
  const context = vm.createContext({document, location,
    localStorage: {getItem: key => preferences.get(key) || null, setItem: (key, value) => preferences.set(key, value)},
    fetch() { assert.fail("Cached navigation must not fetch data."); },
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  vm.runInContext(source.slice(0, source.indexOf("const colorModes =")), context);
  const years = [{value: "current", label: "Current school year"}, {value: "previous", label: "Previous school year"}];
  const quarters = [{value: "current", label: "Current quarter"}, {value: "all", label: "All quarters"},
    {value: "q2", label: "Q2"}];
  function period(year, quarter, name, grade, possible) {
    return {gradeFilters: {year, quarter, years, quarters}, classes: [{
      studentScheduleOid: "shared-id", courseName: name, courseNumber: "PRIVATE-101", displayGrade: grade,
      terms: [{oid: "q2", gradeTermId: "Q2"}],
      assignments: [{oid: "quiz", name: "Quiz", totalPoints: possible, termOid: "q2"}],
    }]};
  }
  const current = period("current", "current", "Current math", "95", 10);
  const otherQuarter = period("current", "q2", "Current math", "70", 20);
  const previous = period("previous", "all", "Previous math", "80", 30);
  previous.classes.push({studentScheduleOid: "old-only", courseName: "Old class", displayGrade: "85"});
  const state = {signedIn: true, snapshot: {mode: "live", syncedAt: "saved", ...current,
    gradePeriods: {"current:current": current, "current:q2": otherQuarter, "previous:all": previous},
    activityFeed: {available: true, events: [{type: "grade", studentScheduleOid: "shared-id",
      className: "Current math", assignmentOid: "quiz", assignmentName: "Quiz", grade: "9", date: "2026-10-01"}]},
  }};
  document.getElementById("feed-type").value = "all";
  context.setState(state);
  function assertCurrentHome() {
    const cards = nodes.get("home-classes").children;
    assert.equal(cards.length, 1);
    assert.equal(cards[0].children[0].children[0].textContent, "Current math");
    assert.equal(cards[0].children[1].textContent, "95 A");
    const activity = nodes.get("activity-feed").children.find(child => child.tag === "article");
    assert.equal(activity.children[2].children[0].textContent, "9 / 10");
    assert.equal(activity.children[1].children[0].children[0].href, cards[0].href);
  }
  assertCurrentHome();
  nodes.get("grade-quarter").value = "q2";
  context.changeGradePeriod(false);
  const classCard = nodes.get("classes").children[0];
  assert.equal(classCard.children.find(child => child.className === "course-bottom").children[0].textContent, "70 C−");
  assert.equal(classCard.children.some(child => child.className === "course-code"), false);
  assertCurrentHome();
  // The same class ID opens the current grade from Home and Q2 from Grades.
  location.hash = nodes.get("classes").children[0].href;
  context.route();
  assert.equal(nodes.get("course-grade").textContent, "70 C−");
  assert.doesNotMatch(nodes.get("course-info").textContent, /PRIVATE-101/);
  assert.equal(nodes.get("term-select").value, "q2");
  location.hash = nodes.get("home-classes").children[0].href;
  context.route();
  assert.equal(nodes.get("course-grade").textContent, "95 A");
  assert.equal(nodes.get("term-select").value, "all");
  assert.match(nodes.get("course-info").textContent, /Current school year · Current quarter/);
  nodes.get("class-select").listeners.change();
  context.route();
  assert.equal(nodes.get("course-grade").textContent, "95 A");
  assert.equal(nodes.get("grade-quarter").value, "q2");

  location.hash = "#grades";
  nodes.get("grade-year").value = "previous";
  context.changeGradePeriod(true);
  assert.equal(nodes.get("classes").children.length, 2);
  assertCurrentHome();
  location.hash = nodes.get("classes").children[1].href;
  context.route();
  assert.equal(nodes.get("details-title").textContent, "Old class");
  location.hash = nodes.get("home-classes").children[0].href;
  context.route();
  assert.equal(nodes.get("details-title").textContent, "Current math");
  assert.equal(nodes.get("class-select").children.length, 1);
  for (const nextState of [state, {...state, syncing: true},
    {...state, snapshot: {...state.snapshot, syncedAt: "refreshed", ...previous}}]) {
    context.setState(nextState);
    assertCurrentHome();
    assert.equal(nodes.get("grade-year").value, "previous");
    assert.equal(nodes.get("course-grade").textContent, "95 A");
  }
  vm.runInContext('gradePeriodOwner = ""; selectedGradePeriod = null; renderedSnapshot = null;', context);
  location.hash = "#home";
  context.setState(state);
  assertCurrentHome();
  assert.equal(nodes.get("grade-year").value, "previous");
});
