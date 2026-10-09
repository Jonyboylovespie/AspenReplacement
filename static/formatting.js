"use strict";

function formatDate(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number" || /^\d{13}$/.test(String(value))) {
    const date = new Date(Number(value));
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString();
  }
  // Aspen also sends dates without a timezone. Keep those calendar dates intact.
  const match = String(value).match(/^\d{4}-\d{2}-\d{2}/);
  return match ? match[0] : String(value);
}

function dateValue(value) {
  if (typeof value === "number" || /^\d{13}$/.test(String(value))) return Number(value);
  return Date.parse(value) || 0;
}

function classGradeDisplay(value) {
  const display = String(value ?? "").trim();
  if (!display) return "—";
  // Numeric averages come from Aspen; letters use the site's scale.
  if (!/^\d+(?:\.\d+)?\s*%?$/.test(display)) return display;
  const percentage = Number(display.replace(/\s*%$/, ""));
  // Half-point cutoffs follow the chart's whole-number grade bands.
  const bands = [
    [92.5, "A"], [89.5, "A−"],
    [86.5, "B+"], [82.5, "B"], [79.5, "B−"],
    [76.5, "C+"], [72.5, "C"], [69.5, "C−"],
    [66.5, "D+"], [62.5, "D"], [59.5, "D−"],
  ];
  const letter = bands.find(([minimum]) => percentage >= minimum)?.[1] || "F";
  return `${display} ${letter}`;
}

function letterGradeClass(value) {
  const match = classGradeDisplay(value).toUpperCase()
    .match(/(?:^|\s)([A-F])([+\-−])?(?=\s|$)/);
  if (!match) return "grade-none";
  if (match[1] === "A") return ["-", "−"].includes(match[2]) ? "grade-a-minus" : "grade-a";
  if (match[1] === "B") return "grade-b";
  return "grade-low";
}

function activityGradeDisplay(score) {
  const match = String(score).match(/^\s*(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)\s*$/);
  if (!match || Number(match[2]) <= 0) return "—";
  const percentage = Number(match[1]) / Number(match[2]) * 100;
  if (!Number.isFinite(percentage)) return "—";
  const letter = classGradeDisplay(percentage).split(" ").pop();
  return `${Number(percentage.toFixed(1))}% ${letter}`;
}

function assignmentGradeDisplay(score, possible) {
  const values = [score, possible].map(value => String(value ?? "").trim());
  if (!values.every(value => /^\d+(?:\.\d+)?$/.test(value)) || Number(values[1]) <= 0) return "—";
  const percentage = Number(values[0]) / Number(values[1]) * 100;
  return Number.isFinite(percentage) ? `${Number(percentage.toFixed(1))}%` : "—";
}

function activityColorClass(item, grade) {
  if (item.type === "grade") return letterGradeClass(grade);
  if (item.absent === true || item.tardy === true) {
    if (item.excused === true) return "attendance-excused";
    if (item.excused === false) return "grade-low";
  }
  return "grade-none";
}

function scoreDisplay(item) {
  const scores = item.scoreLightModels || [];
  if (!scores.length) return {score: "—", status: "Not graded"};
  const display = scores.map((score) => {
    const value = [score.specialCode, score.score].filter((v) => v !== undefined && v !== null && v !== "").join(" ");
    return value || "—";
  }).join("; ");
  const flags = new Set();
  for (const score of scores) {
    if (score.exempt) flags.add("Exempt");
    if (score.dropped) flags.add("Dropped");
    if (score.behavior) flags.add(score.behavior);
  }
  if (!flags.size && scores.every((s) => s.score === undefined || s.score === null || s.score === "")) flags.add("Not graded");
  return {score: display, status: [...flags].join(", ") || "Graded"};
}

function classesWithGradesFirst(courses) {
  const hasGrade = course => classGradeDisplay(course.displayGrade) !== "—";
  return [...courses].sort((a, b) => Number(hasGrade(b)) - Number(hasGrade(a)));
}

function friendlyDate(value) {
  const formatted = formatDate(value);
  const date = /^\d{4}-\d{2}-\d{2}$/.test(formatted) ? new Date(`${formatted}T12:00:00`) : new Date(dateValue(value));
  return Number.isNaN(date.getTime()) ? formatted : date.toLocaleDateString(undefined, {weekday: "long", month: "long", day: "numeric", year: "numeric"});
}


if (typeof module !== "undefined") module.exports = {formatDate, dateValue, classGradeDisplay, letterGradeClass, activityGradeDisplay, assignmentGradeDisplay, activityColorClass, scoreDisplay, classesWithGradesFirst, friendlyDate};
