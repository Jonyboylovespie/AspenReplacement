"use strict";

const $ = (id) => document.getElementById(id);
let currentState = null;
let renderedSnapshot = null;
let selectedClass = "";
let selectedClassPeriod = "";
let selectedTerm = "all";
let busy = false;
let feedLimit = 12;
let selectedGradePeriod = null;
let gradePeriodOwner = "";

function gradePeriodStorageKey() {
  return `aspen-grade-period:${gradePeriodOwner}`;
}

function gradePeriodSnapshot(snapshot) {
  const owner = snapshot ? `${snapshot.mode}:${snapshot.student?.studentOid || ""}` : "";
  if (owner !== gradePeriodOwner) {
    gradePeriodOwner = owner;
    selectedGradePeriod = null;
    try { selectedGradePeriod = JSON.parse(localStorage.getItem(gradePeriodStorageKey())); } catch { /* Use the snapshot's default. */ }
  }
  if (!snapshot) return snapshot;
  const requested = selectedGradePeriod || snapshot.gradeFilters;
  const period = snapshot.gradePeriods[`${requested.year}:${requested.quarter}`] ||
    snapshot.gradePeriods[`${requested.year}:${requested.year === "previous" ? "all" : "current"}`] ||
    snapshot.gradePeriods["current:current"];
  selectedGradePeriod = {year: period.gradeFilters.year, quarter: period.gradeFilters.quarter};
  return {...snapshot, ...period};
}

function gradePeriod(snapshot = currentState?.snapshot) {
  return snapshot ? snapshot.gradeFilters : {year: "current", quarter: "current",
    years: [{value: "current", label: "Current school year"}, {value: "previous", label: "Previous school year"}],
    quarters: [{value: "current", label: "Current quarter"}, {value: "all", label: "All quarters"}]};
}

function currentPeriodSnapshot(snapshot = currentState?.snapshot) {
  if (!snapshot) return snapshot;
  return {...snapshot, ...snapshot.gradePeriods["current:current"]};
}

function isCurrentClassRoute() {
  return location.hash.startsWith("#class/current/");
}

function detailSnapshot() {
  return isCurrentClassRoute() ? currentPeriodSnapshot() : currentState?.snapshot;
}

function gradePeriodLabel(filters) {
  const year = filters.years.find(option => option.value === filters.year)?.label || "Current school year";
  const quarter = filters.quarters.find(option => option.value === filters.quarter)?.label || "Current quarter";
  return `${year} · ${quarter}`;
}

function renderGradeFilters() {
  const filters = gradePeriod();
  for (const [id, choices, value] of [["grade-year", filters.years, filters.year],
                                     ["grade-quarter", filters.quarters, filters.quarter]]) {
    $(id).replaceChildren(...choices.map(choice => {
      const option = element("option", choice.label);
      option.value = choice.value;
      return option;
    }));
    $(id).value = value;
  }
}

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text ?? "");
  return node;
}

function showError(message) {
  $("error").textContent = message || "";
  $("error").hidden = !message;
  $("connection-error").textContent = message || "";
  $("connection-error").hidden = !message;
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : {"Content-Type": "application/json", "X-CSRF-Token": currentState?.csrfToken || ""},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

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

function setState(state) {
  const previousPeriod = gradePeriod();
  const previousError = currentState?.error;
  currentState = {...state, snapshot: gradePeriodSnapshot(state.snapshot)};
  const snapshot = currentState.snapshot;
  const demo = snapshot?.mode === "demo";
  const signedIn = state.signedIn;
  $("account-status").textContent = state.account ? `Signed in as ${state.account.email}` : "";
  $("logout").hidden = !state.signedIn;
  $("status").textContent = state.syncing ? "Syncing from Aspen…" : demo ? "Showing sample data." :
    state.error ? state.error : state.connected ? "Connected to Aspen. Automatically refreshes every minute." :
    snapshot ? "Showing saved data. Connect Aspen to get updates." : "Aspen is not connected.";
  $("updated").textContent = snapshot?.syncedAt ? `${demo ? "Sample loaded" : "Last successful sync"}: ${new Date(snapshot.syncedAt).toLocaleString()}${!demo && state.stale ? " (saved data; may be out of date)" : ""}` : "";
  const disabled = busy || state.syncing;
  const canChangePeriod = !!snapshot;
  $("grade-year").disabled = !canChangePeriod;
  $("grade-quarter").disabled = !canChangePeriod;
  const filters = gradePeriod();
  if (previousPeriod.year !== filters.year || previousPeriod.quarter !== filters.quarter) {
    selectedTerm = ["current", "all"].includes(filters.quarter) ? "all" : filters.quarter;
    $("assignment-search").value = "";
  }
  $("grade-period-status").textContent = `${gradePeriodLabel(filters)}${state.syncing ? " · Refreshing…" : ""}`;
  $("refresh").disabled = disabled || !signedIn || !state.canRetry;
  $("disconnect").disabled = disabled || !signedIn || !state.canRetry;
  $("saved-connection").hidden = !state.signedIn || !state.canRetry || state.connected;
  $("retry-session").disabled = disabled || !state.signedIn || !state.canRetry;
  $("retry-session").textContent = state.syncing ? "Retrying saved session…" : "Retry saved session";
  for (const id of ["clear", "demo", "empty-demo"]) $(id).disabled = disabled || !signedIn;
  $("google-connection").hidden = !!state.signedIn;
  $("google-sign-in").setAttribute("aria-disabled", String(disabled || !state.googleConfigured));
  $("cookie-form").hidden = !state.signedIn;
  for (const id of ["app-session", "csrf-cookie", "desktop-session", "clearance-cookie", "connect-aspen"]) {
    $(id).disabled = disabled || !state.signedIn;
  }
  $("connect-aspen").textContent = busy ? "Connecting…" : "Save and connect";
  if (state.error) showError(state.error);
  else if (previousError) showError("");
  $("warnings").replaceChildren(...(snapshot?.warnings || []).map((warning) => element("li", warning)));
  $("notice").hidden = !(snapshot?.warnings?.length);
  $("notice-title").textContent = `${snapshot?.warnings?.length || 0} data note${snapshot?.warnings?.length === 1 ? "" : "s"}`;
  $("data-label").textContent = demo ? "Sample data" : state.syncing ? "Syncing…" : snapshot ? (state.stale ? "Saved data" : "Connected") : "Not connected";
  $("sync-heading").textContent = demo ? "You’re exploring sample data" :
    state.syncing ? "Syncing with Aspen…" : !state.signedIn ? "Sign in to see your school day" : !snapshot ? "Aspen is not connected" :
    state.stale ? "Showing your saved snapshot" : "Synced with Aspen";
  $("empty").hidden = !!snapshot;
  $("dashboard").hidden = !snapshot;
  const key = snapshot ? `${snapshot.mode}:${snapshot.syncedAt}:${filters.year}:${filters.quarter}` : null;
  if (key !== renderedSnapshot) {
    renderedSnapshot = key;
    if (snapshot) {
      renderGradeFilters();
      renderClasses();
      renderFeed();
      renderAttendance();
      route();
    }
  }
  $("grade-year").value = filters.year;
  $("grade-quarter").value = filters.quarter;

  globalThis.betterAspenChat?.update(currentState);
  globalThis.betterAspenActivity?.update(currentState);
}

function featureStatus(data) {
  if (!data?.available) return data?.error || "Not loaded yet. Refresh Aspen to load this data.";
  const scope = data.scope || "Aspen records";
  return `${scope}.${data.partial ? " Partial page; additional records are not included." : ""}${data.stale ?
    ` Saved data; refresh failed. ${data.fetchedAt ? `Last retrieved: ${new Date(data.fetchedAt).toLocaleString()}. ` : ""}${data.error || ""}` : ""}`;
}

function attendanceFlags(item) {
  const flags = [];
  for (const [key, label] of [["absent", "Absent"], ["tardy", "Tardy"], ["dismissed", "Dismissed"]]) {
    if (item[key] === true) flags.push(label);
  }
  if (item.excused === true) flags.push("Excused");
  else if (item.excused === false) flags.push("Unexcused");
  return flags.join(", ");
}

function activityScore(item, course) {
  const grade = item.grade ?? "—";
  // Sample data and some Aspen scores already include points possible.
  if (String(grade).includes("/")) return grade;
  const assignment = item.assignmentOid ? course?.assignments?.find(a => a.oid === item.assignmentOid) : null;
  const total = assignment?.totalPoints;
  const possible = total === undefined || total === null || total === "" ? "—" :
    Number.isFinite(Number(total)) ? Number(total) : total;
  return `${grade} / ${possible}`;
}

function renderFeed() {
  const snapshot = currentPeriodSnapshot();
  const feed = snapshot?.activityFeed;
  const type = $("feed-type").value;
  const search = $("feed-search").value.trim().toLowerCase();
  const reasons = new Map((snapshot?.attendance?.records || []).filter(r => r.oid).map(r => [r.oid, r.reason]));
  const events = (feed?.events || []).filter(item => (type === "all" || item.type === type) &&
    [item.className, item.assignmentName, item.code, item.period, reasons.get(item.oid)].join(" ").toLowerCase().includes(search))
    .slice().sort((a, b) => dateValue(b.date) - dateValue(a.date));
  $("feed-count").textContent = feed?.available ? `${events.length} matching ${events.length === 1 ? "entry" : "entries"}${events.length ? "." : "; no activity returned for this filter."}` : "Activity is unavailable.";
  $("activity-feed").replaceChildren();
  const labels = {grade: "Grade posted", dailyAttendance: "Daily attendance", classAttendance: "Class attendance"};
  $("feed-more").hidden = events.length <= feedLimit;
  if (events.length > feedLimit) $("feed-count").textContent = `Showing ${feedLimit} of ${events.length} matching entries.`;
  let lastDate = "";
  for (const item of events.slice(0, feedLimit)) {
    const date = formatDate(item.date);
    if (date !== lastDate) {
      const heading = element("h3", friendlyDate(item.date));
      heading.className = "date-heading";
      $("activity-feed").append(heading);
      lastDate = date;
    }
    const row = element("article");
    row.className = "activity-item";
    const icon = element("span", item.type === "grade" ? "▤" : "▦");
    icon.className = `event-icon${item.type === "grade" ? "" : " attendance"}`;
    icon.setAttribute("aria-hidden", "true");
    const content = element("div");
    const title = element("h3");
    const course = snapshot.classes?.find(c => item.studentScheduleOid ?
      c.studentScheduleOid === item.studentScheduleOid : c.courseName === item.className);
    const titleText = item.assignmentName || item.className || labels[item.type];
    if (course) {
      const link = element("a", titleText);
      link.href = classHref(course, true);
      title.append(link);
    } else title.textContent = titleText;
    content.append(title, element("p", item.type === "grade" ? item.className :
      [labels[item.type], item.period ? `Period ${item.period}` : "", attendanceFlags(item), reasons.get(item.oid)].filter(Boolean).join(" • ")));
    content.append(element("small", item.type === "grade" ? labels[item.type] : "Attendance recorded"));
    const score = item.type === "grade" ? activityScore(item, course) : (item.code || "—");
    const gradeText = item.type === "grade" ? activityGradeDisplay(score) : "—";
    row.classList.add(activityColorClass(item, gradeText));
    const value = element("div", score);
    value.className = "event-value";
    value.append(element("small", item.type === "grade" ? "Score" : "Code"));
    const values = element("div");
    values.className = "event-values";
    values.append(value);
    if (item.type === "grade") {
      const grade = element("div", gradeText);
      grade.className = "event-value";
      grade.append(element("small", "Grade"));
      values.append(grade);
    }
    row.append(icon, content, values);
    $("activity-feed").append(row);
  }
  if (!events.length) {
    const empty = element("p", feed?.available ? "No activity matches. Try another search or activity type." : "Activity is unavailable. Connect to Aspen or refresh to try again.");
    empty.className = "empty-result";
    $("activity-feed").append(empty);
  }
}

function renderAttendance() {
  const snapshot = currentState?.snapshot;
  const feed = snapshot?.activityFeed;
  const dailyEvents = new Map((feed?.events || [])
    .filter(item => item.type === "dailyAttendance" && item.oid)
    .map(item => [item.oid, item]));
  const isUnexcused = item => item.excused === false ||
    (item.excused !== true && /(?:^|-)U$/i.test(String(item.code || "").trim()));
  const daily = snapshot?.attendance;
  $("daily-attendance").replaceChildren();
  for (const item of daily?.records || []) {
    const row = element("tr");
    if (isUnexcused({...item, excused: item.excused ?? dailyEvents.get(item.oid)?.excused})) {
      row.className = "attendance-unexcused";
    }
    for (const value of [formatDate(item.date), item.code, item.reason || "—"]) row.append(element("td", value));
    $("daily-attendance").append(row);
  }
  const periods = (feed?.events || []).filter(item => item.type === "classAttendance");
  $("period-attendance").replaceChildren();
  for (const item of periods) {
    const row = element("tr");
    if (isUnexcused(item)) row.className = "attendance-unexcused";
    for (const value of [formatDate(item.date), item.className, item.period || "—", item.code, attendanceFlags(item) || "—"]) row.append(element("td", value));
    $("period-attendance").append(row);
  }
}

function classHref(course, currentPeriod = false) {
  return `#class/${currentPeriod ? "current/" : ""}${encodeURIComponent(course.studentScheduleOid)}`;
}

function renderClasses() {
  const snapshot = currentPeriodSnapshot();
  const courses = classesWithGradesFirst(snapshot.classes || []);
  $("home-title").textContent = snapshot.mode === "demo" ? "Your school day, at a glance." : "Welcome back";
  $("home-classes").replaceChildren();
  for (const course of courses) {
    const link = element("a");
    link.href = classHref(course, true);
    link.className = `mini-course ${letterGradeClass(course.displayGrade)}`;
    const info = element("div");
    info.append(element("strong", course.courseName), element("small", course.meetingTime || course.teacherName));
    const grade = element("span", classGradeDisplay(course.displayGrade));
    grade.className = "mini-grade";
    link.append(info, grade);
    $("home-classes").append(link);
  }
  if (!courses.length) $("home-classes").append(element("p", "No current classes returned by Aspen."));
  renderClassCards();
  renderDetails();
}

function renderClassCards() {
  const search = $("class-search").value.trim().toLowerCase();
  const courses = classesWithGradesFirst(currentState?.snapshot?.classes || []).filter(course =>
    [course.courseName, course.teacherName].join(" ").toLowerCase().includes(search));
  $("classes").replaceChildren();
  $("class-empty").hidden = !!courses.length;
  $("class-empty").textContent = search ? "No classes match. Try a class name or teacher." : "No classes returned for this school year and quarter.";
  for (const course of courses) {
    const card = element("a");
    card.className = `course-card ${letterGradeClass(course.displayGrade)}`;
    card.href = classHref(course);
    const info = element("p", course.teacherName || "Teacher not listed");
    info.append(element("br"), element("span", course.meetingTime || ""));
    const bottom = element("div");
    bottom.className = "course-bottom";
    bottom.append(element("strong", classGradeDisplay(course.displayGrade)));
    card.append(element("h3", course.courseName), info, bottom);
    $("classes").append(card);
  }
}

function selectedCourse() {
  return detailSnapshot()?.classes?.find((course) => course.studentScheduleOid === selectedClass);
}

function renderDetails() {
  const courses = classesWithGradesFirst(detailSnapshot()?.classes || []);
  if (!courses.some(course => course.studentScheduleOid === selectedClass)) selectedClass = courses[0]?.studentScheduleOid || "";
  $("class-select").replaceChildren(...courses.map(course => {
    const option = element("option", course.courseName);
    option.value = course.studentScheduleOid;
    return option;
  }));
  const course = selectedCourse();
  $("details-title").textContent = course?.courseName || "Class breakdown";
  $("course-grade").textContent = classGradeDisplay(course?.displayGrade);
  $("course-grade").className = letterGradeClass(course?.displayGrade);
  $("grade-source").textContent = course?.gradeSource ? `Source: ${course.gradeSource}` : "No average available";
  $("class-select").value = selectedClass;
  $("course-info").textContent = course ? [course.teacherName, course.teacherEmail, gradePeriodLabel(gradePeriod(detailSnapshot()))].filter(Boolean).join(" · ") : "No classes returned for this period.";
  const summary = course?.averageSummary || [];
  $("summary-table").hidden = !summary.length;
  $("summary-empty").hidden = !!summary.length;
  $("summary-empty").textContent = summary.length ? "" : "No category-weight summary is available for this class.";
  $("average-summary").replaceChildren();
  for (const cells of summary) {
    const row = element("tr");
    for (const cell of cells) {
      const node = element(cell.header ? "th" : "td", cell.text);
      node.rowSpan = cell.rowspan || 1;
      node.colSpan = cell.colspan || 1;
      row.append(node);
    }
    $("average-summary").append(row);
  }
  const terms = course?.terms || [];
  if (!terms.some((term) => term.oid === selectedTerm)) selectedTerm = "all";
  const all = element("option", "All terms");
  all.value = "all";
  $("term-select").replaceChildren(all);
  for (const term of terms) {
    const option = element("option", term.gradeTermId);
    option.value = term.oid;
    $("term-select").append(option);
  }
  $("term-select").value = selectedTerm;
  renderAssignments();
}

function renderAssignments() {
  const search = $("assignment-search").value.trim().toLowerCase();
  const assignments = (selectedCourse()?.assignments || []).filter((item) =>
    (selectedTerm === "all" || item.termOid === selectedTerm) &&
    `${item.name || ""} ${item.categoryName || ""}`.toLowerCase().includes(search))
    .sort((a, b) => dateValue(b.dueDate) - dateValue(a.dueDate));
  $("assignment-count").textContent = `${assignments.length} assignment${assignments.length === 1 ? "" : "s"}.`;
  $("assignments").replaceChildren();
  $("assignments-empty").hidden = !!assignments.length;
  for (const item of assignments) {
    const score = scoreDisplay(item);
    const row = element("tr");
    for (const value of [item.name, item.termName, item.categoryName, formatDate(item.assignedDate), formatDate(item.dueDate), score.score, item.totalPoints ?? "—", assignmentGradeDisplay(score.score, item.totalPoints), score.status]) row.append(element("td", value));
    const cell = element("td");
    if (item.description) {
      const details = element("details");
      details.append(element("summary", "View description"), element("p", item.description));
      cell.append(details);
    } else cell.textContent = "—";
    row.append(cell);
    $("assignments").append(row);
  }
}

async function action(path, body = {}) {
  if (busy || currentState?.syncing) return false;
  busy = true;
  showError("");
  if (currentState) setState(currentState);
  try {
    const state = await api(path, body);
    setState(state);
    return true;
  } catch (error) {
    showError(error.message);
    return false;
  } finally {
    busy = false;
    if (currentState) setState(currentState);
  }
}

$("google-sign-in").addEventListener("click", (event) => {
  if ($("google-sign-in").getAttribute("aria-disabled") === "true") {
    event.preventDefault();
    return;
  }
  if (!currentState?.signedIn && !currentState?.googleConfigured) {
    event.preventDefault();
    showError("Google sign-in hasn't been configured on this server yet.");
    return;
  }

});
$("logout").addEventListener("click", async () => {
  try {
    await api("/auth/logout", {});
    location.assign("/");
  } catch (error) { showError(error.message); }
});
$("cookie-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const cookieValues = {
    appSession: $("app-session").value.trim(),
    csrf: $("csrf-cookie").value.trim(),
    desktopSession: $("desktop-session").value.trim(),
    clearance: $("clearance-cookie").value.trim()
  };
  if (await action("/api/session", {cookieValues})) {
    $("cookie-form").reset();
    $("connection-dialog").close();
  }
});
$("connection-dialog").addEventListener("close", () => $("cookie-form").reset());
$("refresh").addEventListener("click", () => action("/api/refresh"));
$("retry-session").addEventListener("click", () => action("/api/refresh"));
$("disconnect").addEventListener("click", () => action("/api/disconnect"));
$("clear").addEventListener("click", () => action("/api/clear"));
async function loadDemo() {
  if (await action("/api/demo")) {
    $("connection-dialog").close();
    location.hash = "home";
  }
}
$("demo").addEventListener("click", loadDemo);
$("empty-demo").addEventListener("click", loadDemo);
$("class-select").addEventListener("change", () => {
  location.hash = classHref({studentScheduleOid: $("class-select").value}, isCurrentClassRoute());
});
$("term-select").addEventListener("change", () => { selectedTerm = $("term-select").value; renderAssignments(); });
function changeGradePeriod(yearChanged) {
  const year = $("grade-year").value;
  const quarter = yearChanged ? (year === "previous" ? "all" : "current") : $("grade-quarter").value;
  if (!currentState?.snapshot?.gradePeriods?.[`${year}:${quarter}`]) {
    showError("That period is not cached. Refresh Aspen to download all school years and quarters.");
    renderGradeFilters();
    return;
  }
  selectedGradePeriod = {year, quarter};
  try { localStorage.setItem(gradePeriodStorageKey(), JSON.stringify(selectedGradePeriod)); } catch { /* Selection still works for this visit. */ }
  setState(currentState);
}
$("grade-year").addEventListener("change", () => changeGradePeriod(true));
$("grade-quarter").addEventListener("change", () => changeGradePeriod(false));
$("assignment-search").addEventListener("input", renderAssignments);
$("feed-type").addEventListener("change", () => { feedLimit = 12; renderFeed(); });
$("feed-search").addEventListener("input", () => { feedLimit = 12; renderFeed(); });
$("feed-more").addEventListener("click", () => { feedLimit += 12; renderFeed(); });

function openRecentActivity() {
  $("feed-type").value = "all";
  $("feed-search").value = "";
  feedLimit = 12;
  location.hash = "home";
  route();
  renderFeed();
  $("activity-title").focus();
  $("activity-title").scrollIntoView({block: "start"});
}

function friendlyDate(value) {
  const formatted = formatDate(value);
  const date = /^\d{4}-\d{2}-\d{2}$/.test(formatted) ? new Date(`${formatted}T12:00:00`) : new Date(dateValue(value));
  return Number.isNaN(date.getTime()) ? formatted : date.toLocaleDateString(undefined, {weekday: "long", month: "long", day: "numeric", year: "numeric"});
}

function route(focus = false) {
  const hash = location.hash.slice(1) || "home";
  let view = ["home", "grades", "attendance"].includes(hash) ? hash : "grades";
  if (hash.startsWith("class/")) {
    let id = "";
    const prefix = isCurrentClassRoute() ? "class/current/" : "class/";
    try { id = decodeURIComponent(hash.slice(prefix.length)); } catch { /* Invalid links return to the class list. */ }
    if (detailSnapshot()?.classes?.some(c => c.studentScheduleOid === id)) {
      const filters = gradePeriod(detailSnapshot());
      const period = `${filters.year}:${filters.quarter}`;
      if (selectedClass !== id || selectedClassPeriod !== period) {
        selectedClass = id;
        selectedClassPeriod = period;
        const quarter = filters.quarter;
        selectedTerm = ["current", "all"].includes(quarter) ? "all" : quarter;
        $("assignment-search").value = "";
      }
      renderDetails();
      view = "detail";
    }
  }
  for (const name of ["home", "grades", "attendance", "detail"]) $(`${name}-view`).hidden = name !== view;
  document.querySelectorAll("[data-view]").forEach(link => {
    if (link.dataset.view === (view === "detail" ? "grades" : view)) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const label = view === "detail" ? selectedCourse()?.courseName : {home: "Home", grades: "Grades", attendance: "Attendance"}[view];
  document.title = `BetterAspen · ${label}`;
  if (focus) { $("main").focus({preventScroll: true}); window.scrollTo(0, 0); }
}

const colorModes = {
  light: {label: "Dark mode", action: "Use dark mode", theme: "#f5f8f7"},
  dark: {label: "Light mode", action: "Use light mode", theme: "#0d2231"}
};
function chooseColorMode(mode, persist = true) {
  if (!Object.hasOwn(colorModes, mode)) mode = "light";
  document.body.dataset.mode = mode;
  $("theme-color").content = colorModes[mode].theme;
  $("mode-label").textContent = colorModes[mode].label;
  $("mode-toggle").setAttribute("aria-label", colorModes[mode].action);
  $("mode-toggle").setAttribute("aria-pressed", String(mode === "dark"));
  $("mode-toggle").dataset.modeReady = "";
  if (persist) {
    $("mode-toggle").dataset.modeAnimated = "";
    try { localStorage.setItem("aspen-color-mode", mode); } catch { /* The toggle still works when storage is unavailable. */ }
  }
}
let initialMode = "light";
try {
  const savedMode = localStorage.getItem("aspen-color-mode");
  initialMode = Object.hasOwn(colorModes, savedMode) ? savedMode :
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
} catch { /* Use light mode. */ }
chooseColorMode(initialMode, false);
$("mode-toggle").addEventListener("click", () => chooseColorMode(document.body.dataset.mode === "dark" ? "light" : "dark"));
document.querySelectorAll(".connection-open").forEach(button => button.addEventListener("click", () => $("connection-dialog").showModal()));
$("close-connection").addEventListener("click", () => $("connection-dialog").close());
$("class-search").addEventListener("input", renderClassCards);
window.addEventListener("hashchange", () => route(true));
window.addEventListener("betteraspen:activity", openRecentActivity);
route();

async function poll() {
  try {
    if (!busy) setState(await api("/api/state"));
  }
  catch (error) { showError(`The server is unavailable: ${error.message}`); }
  setTimeout(poll, currentState?.syncing ? 1000 : 5000);
}

if (new URLSearchParams(location.search).get("login") === "failed") {
  $("connection-dialog").showModal();
  showError("Google sign-in couldn't finish. Try signing in again.");
}
poll();
