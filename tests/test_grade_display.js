"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

// Load the app's pure formatting helpers without starting the dashboard.
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf("function setState(")), context);
const display = context.classGradeDisplay;
const gradeClass = context.letterGradeClass;

test("assignment grades show score divided by possible as a percentage", () => {
  const grade = context.assignmentGradeDisplay;
  assert.equal(grade("10", "10.00"), "100%");
  assert.equal(grade("18", "20.00"), "90%");
  assert.equal(grade("9.5", "10.00"), "95%");
  assert.equal(grade("2.5", "3"), "83.3%");
  assert.equal(grade(0, 10), "0%");
  assert.equal(grade(11, 10), "110%");
  for (const [score, possible] of [["—", 10], ["EX", 10], ["", 10], [null, 10],
    [undefined, 10], [10, null], [10, ""], [10, 0], [10, -1]]) {
    assert.equal(grade(score, possible), "—");
  }
});

test("activity colors follow assignment grades and explicit attendance status", () => {
  const color = context.activityColorClass;
  assert.equal(color({type: "grade"}, "100% A"), "grade-a");
  assert.equal(color({type: "grade"}, "91% A−"), "grade-a-minus");
  assert.equal(color({type: "grade"}, "83.3% B"), "grade-b");
  assert.equal(color({type: "grade"}, "73.3% C"), "grade-low");
  assert.equal(color({type: "grade"}, "—"), "grade-none");
  for (const type of ["dailyAttendance", "classAttendance"]) {
    for (const flag of ["absent", "tardy"]) {
      assert.equal(color({type, [flag]: true, excused: true}), "attendance-excused");
      assert.equal(color({type, [flag]: true, excused: false}), "grade-low");
      assert.equal(color({type, [flag]: true, excused: null}), "grade-none");
    }
  }
});

test("activity grades use points possible and the class letter cutoffs", () => {
  const grade = context.activityGradeDisplay;
  assert.equal(grade("10 / 10"), "100% A");
  assert.equal(grade("2.5 / 3"), "83.3% B");
  assert.equal(grade("0 / 10"), "0% F");
  assert.equal(grade("11 / 10"), "110% A");
  assert.equal(grade("92.5 / 100"), "92.5% A");
  assert.equal(grade("92.49 / 100"), "92.5% A−");
  for (const score of ["10 / —", "— / 10", "0 / 0", "EX / 10", "10", null]) {
    assert.equal(grade(score), "—");
  }
});

test("letters change at every half-point cutoff", () => {
  const transitions = [
    [59.5, "F", "D−"], [62.5, "D−", "D"], [66.5, "D", "D+"],
    [69.5, "D+", "C−"], [72.5, "C−", "C"], [76.5, "C", "C+"],
    [79.5, "C+", "B−"], [82.5, "B−", "B"], [86.5, "B", "B+"],
    [89.5, "B+", "A−"], [92.5, "A−", "A"],
  ];
  for (const [cutoff, below, at] of transitions) {
    const previous = (cutoff - 0.01).toFixed(2);
    assert.equal(display(previous), `${previous} ${below}`);
    assert.equal(display(cutoff), `${cutoff} ${at}`);
  }
});

test("numeric averages keep their precision and optional percent sign", () => {
  assert.equal(display("100.0"), "100.0 A");
  assert.equal(display("82.0"), "82.0 B−");
  assert.equal(display("97.9"), "97.9 A");
  assert.equal(display("96.5"), "96.5 A");
  assert.equal(display(" 92.5% "), "92.5% A");
  assert.equal(display(0), "0 F");
  assert.equal(display("105"), "105 A");
});

test("existing Aspen letters, special codes, and missing grades are preserved", () => {
  for (const value of ["73.33 C", "99.6 A", "97.9 A", "A−", "P", "N/A", "—", "4 / 5"]) {
    assert.equal(display(value), value);
  }
  for (const value of [undefined, null, "", "  "]) {
    assert.equal(display(value), "—");
  }
});

test("class colors follow the displayed letter grade", () => {
  for (const value of ["A", "A+", "93.4"]) {
    assert.equal(gradeClass(value), "grade-a");
  }
  for (const value of ["A-", "A−", "91.0", "91.0% A−"]) {
    assert.equal(gradeClass(value), "grade-a-minus");
  }
  for (const value of ["B", "B+", "B-", "B−", "82.0", "88.5 B+"]) {
    assert.equal(gradeClass(value), "grade-b");
  }
  for (const value of ["C+", "C", "C−", "D+", "D", "D−", "F", "73.2"]) {
    assert.equal(gradeClass(value), "grade-low");
  }
  for (const value of [undefined, null, "", "—", "P", "N/A", "4 / 5"]) {
    assert.equal(gradeClass(value), "grade-none");
  }
});
