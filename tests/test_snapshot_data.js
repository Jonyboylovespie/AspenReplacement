"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {expandSnapshot} = require("../static/snapshot-data.js");

test("shared records expand without losing period grades, missing fields, or occurrences", () => {
  const packed = {
    snapshotFormat: "betteraspen-snapshot-v1", mode: "live", classes: [0],
    recordPool: {
      assignments: [{oid: "assignment", score: "0", flag: false, comment: null}, {oid: "assignment", score: "9"}],
      terms: [[{oid: "q1", gradeTermId: "Q1"}]], summaries: [[]],
      courses: [{studentScheduleOid: "class", displayGrade: "0", assignmentsRef: [0, 0], termsRef: 0, summaryRef: 0},
                {studentScheduleOid: "class", displayGrade: "90", assignmentsRef: [1]}],
    },
    gradePeriods: {"current:current": {classes: [0]}, "previous:all": {classes: [1]}},
  };
  const snapshot = expandSnapshot(packed);
  assert.equal(snapshot.classes[0].displayGrade, "0");
  assert.equal(snapshot.gradePeriods["previous:all"].classes[0].displayGrade, "90");
  assert.equal(snapshot.classes[0].assignments.length, 2);
  assert.equal(snapshot.classes[0].assignments[0].flag, false);
  assert.equal(snapshot.classes[0].assignments[0].comment, null);
  assert.strictEqual(snapshot.classes[0], snapshot.gradePeriods["current:current"].classes[0]);
  assert.strictEqual(snapshot.classes[0].assignments[0], snapshot.classes[0].assignments[1]);
  assert.equal(Object.hasOwn(snapshot.gradePeriods["previous:all"].classes[0], "terms"), false);
  assert.deepEqual(packed.classes, [0]);
  assert.strictEqual(expandSnapshot(null), null);
  const legacy = {classes: []};
  assert.strictEqual(expandSnapshot(legacy), legacy);
});

test("status-only polls keep the selected period and reject another account's cached revision", () => {
  const nodes = new Map();
  const document = {createElement() { return {}; }, getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {value: "", setAttribute() {}, replaceChildren() {}});
    return nodes.get(id);
  }};
  const context = vm.createContext({document, localStorage: {getItem() { return null; }},
    betterAspenData: {expandSnapshot}, renderClasses() {}, renderFeed() {}, renderAttendance() {}});
  for (const file of ["formatting.js", "app.js"]) vm.runInContext(fs.readFileSync(path.join(__dirname, "../static", file), "utf8"), context);
  context.route = () => {};
  const filters = {year: "current", quarter: "current", years: [{value: "current", label: "Current year"}], quarters: [{value: "current", label: "Current quarter"}]};
  const snapshot = {mode: "live", syncedAt: "saved", gradeFilters: filters, classes: [],
    gradePeriods: {"current:current": {gradeFilters: filters, classes: []}}};
  context.mergeState({signedIn: true, account: {email: "alice@example.com"}, snapshotRevision: "alice-revision", snapshot});
  const previous = vm.runInContext("currentState.snapshot", context);
  context.mergeState({signedIn: true, account: {email: "alice@example.com"}, snapshotRevision: "alice-revision", syncing: true});
  assert.strictEqual(vm.runInContext("currentState.snapshot.gradePeriods", context), previous.gradePeriods);
  assert.equal(vm.runInContext("currentState.syncing", context), true);
  assert.throws(() => context.mergeState({signedIn: true, account: {email: "bob@example.com"}, snapshotRevision: "alice-revision"}), /data changed/);
  assert.throws(() => context.mergeState({signedIn: true, account: {email: "alice@example.com"}, snapshotRevision: "other-revision"}), /data changed/);
  context.mergeState({signedIn: false, snapshot: null});
  assert.strictEqual(vm.runInContext("currentState.snapshot", context), null);
});
