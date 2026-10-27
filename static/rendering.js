"use strict";

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

let selectedScheduleDate = "";
let scheduleCountdownTimer = null;

function scheduleFallback(snapshot) {
  const classes = snapshot?.classes || [];
  if (!classes.length) return {available: false, days: [], letterDays: [], error: "Aspen did not return a student schedule."};
  const periods = classes.map((course, index) => ({period: String(course.period || course.periodNumber || index + 1),
    courseName: course.courseName || "Open period", teacherName: course.teacherName || "",
    meetingTime: course.meetingTime || "", startTime: course.startTime || "", endTime: course.endTime || "",
    studentScheduleOid: course.studentScheduleOid}));
  return {available: true, fallback: true,
          note: "Aspen did not expose letter-day dates; showing the imported class order across the rotation.",
          days: ["A", "B", "C", "D", "E", "F", "G", "H"].map((label, index) => ({
            date: new Date(Date.now() + index * 86400000).toISOString().slice(0, 10), label, unique: false, periods
          })),
          letterDays: []};
}

function dhsSchedule(snapshot) {
  const source = globalThis.betterAspenSchedule;
  if (!source?.calendar || !source?.bell || !source?.order) return null;
  const classes = snapshot?.classes || [];
  const days = Object.entries(source.calendar).map(([date, label]) => {
    const letter = String(label).match(/[A-H](?= Day)/)?.[0] || "";
    const definition = label.includes("Early Dismissal") ? source.bell.earlyDismissal :
      label.includes("PLC") ? source.bell.plc : label.includes("Advisory") ? source.bell.advisory : source.bell.regular;
    const slots = source.order[letter] || [];
    let academic = 0;
    const periods = [];
    for (const block of definition.blocks || []) {
      if (block.name.includes("Advisory")) {
        periods.push({period: "Advisory", courseName: "Advisory", startTime: block.start, endTime: block.end, meetingTime: `${block.start}-${block.end}`});
        continue;
      }
      const minis = block.minis?.length ? block.minis : [{name: block.name, start: block.start, end: block.end}];
      for (const mini of minis) {
        const slot = slots[academic++] || 0;
        const course = classes.find(item => Number(item.period || item.periodNumber) === slot) || classes[slot - 1] || {};
        periods.push({period: `${block.name} ${mini.name}`, courseName: course.courseName || "Open period",
          teacherName: course.teacherName || "", startTime: mini.start, endTime: mini.end,
          meetingTime: `${mini.start}-${mini.end}`, studentScheduleOid: course.studentScheduleOid});
      }
    }
    return {date, label, letterDay: letter, unique: !/^[A-H] Day$/.test(label), periods};
  });
  return {available: days.length > 0, days, letterDays: [...new Set(days.map(day => day.letterDay).filter(Boolean))],
    source: source.source, fetchedAt: new Date().toISOString()};
}

function renderSchedule() {
  const schedule = dhsSchedule(currentState?.snapshot) || currentState?.snapshot?.schedule || scheduleFallback(currentState?.snapshot);
  const days = schedule?.days || [];
  const periods = $("schedule-periods");
  const gridHead = $("schedule-grid-head");
  const gridBody = $("schedule-grid-body");
  periods.replaceChildren();
  gridHead.replaceChildren();
  gridBody.replaceChildren();
  if (!schedule?.available || !days.length) {
    $("schedule-status").textContent = schedule?.error || "Aspen did not return schedule data yet.";
    $("schedule-day-description").textContent = "Connect Aspen with schedule access to load your letter days.";
    periods.append(Object.assign(element("p", "No schedule days are available."), {className: "empty-result"}));
    $("schedule-countdown").textContent = "—";
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  const active = days.find(day => day.date === today) || days.find(day => day.date === selectedScheduleDate) || days[0];
  selectedScheduleDate = active.date;
  $("schedule-status").textContent = schedule.fallback ? schedule.note : "";
  $("schedule-day-heading").textContent = active.label || formatDate(active.date);
  $("schedule-day-description").textContent = active.unique ? "A unique schedule today" : "Your classes in period order";
  for (const [index, period] of active.periods.entries()) {
    const row = element("article");
    row.className = "schedule-period";
    row.append(element("span", period.period || String(index + 1)), element("div"));
    row.children[1].append(element("strong", period.courseName || "Open period"));
    const detail = [period.teacherName, period.meetingTime].filter(Boolean).join(" · ");
    if (detail) row.children[1].append(element("small", detail));
    periods.append(row);
  }
  renderScheduleGrid(days, active);
  updateScheduleCountdown(active);
}

function renderScheduleGrid(days, active) {
  const columns = ["A", "B", "C", "D", "E", "F", "G", "H"].map(letter =>
    days.find(day => day.date === active.date && day.letterDay === letter) ||
    days.find(day => day.label === `${letter} Day`) || days.find(day => day.letterDay === letter) ||
    {date: `letter-${letter}`, label: `${letter} Day`, letterDay: letter, periods: []});
  const headRow = element("tr");
  headRow.append(element("th", "Period"));
  for (const day of columns) {
    const th = element("th", day.label || formatDate(day.date));
    if (day.date === active.date) th.className = "schedule-grid-current";
    headRow.append(th);
  }
  $("schedule-grid-head").append(headRow);
  const maxPeriod = Math.max(0, ...columns.flatMap(day => day.periods.map((item, index) => Number(item.period) || index + 1)));
  for (let periodNumber = 1; periodNumber <= maxPeriod; periodNumber++) {
    const row = element("tr");
    row.append(element("th", String(periodNumber)));
    for (const day of columns) {
      const item = day.periods.find((candidate, index) => (Number(candidate.period) || index + 1) === periodNumber);
      const cell = element("td", item?.courseName || "—");
      if (day.date === active.date) cell.className = "schedule-grid-current";
      row.append(cell);
    }
    $("schedule-grid-body").append(row);
  }
}

function clockMinutes(value) {
  const match = String(value || "").match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (!match) {
    const military = String(value || "").match(/^(\d{1,2}):(\d{2})$/);
    return military ? Number(military[1]) * 60 + Number(military[2]) : null;
  }
  let hour = Number(match[1]) % 12;
  if (match[3].toUpperCase() === "PM") hour += 12;
  return hour * 60 + Number(match[2] || 0);
}

function updateScheduleCountdown(day) {
  const countdown = $("schedule-countdown");
  const times = day.periods.map((item, index) => {
    const match = String(item.meetingTime || `${item.startTime || ""} ${item.endTime || ""}`).match(/(\d{1,2}:\d{2}|\d{1,2}(?::\d{2})?\s*(?:AM|PM))/ig);
    return {period: item.period || String(index + 1), name: item.courseName, start: clockMinutes(match?.[0]), end: clockMinutes(match?.[1])};
  }).filter(item => item.start !== null);
  if (!times.length) {
    countdown.textContent = "Next class time unavailable";
    return;
  }
  const now = new Date();
  const current = now.getHours() * 60 + now.getMinutes();
  const currentClass = times.find(item => item.end !== null && item.start <= current && current < item.end);
  if (currentClass) {
    const remaining = Math.max(0, currentClass.end - current);
    countdown.textContent = `Class change in ${Math.floor(remaining / 60)}h ${String(remaining % 60).padStart(2, "0")}m`;
    return;
  }
  const next = times.find(item => item.start > current);
  if (!next) { countdown.textContent = "School day complete"; return; }
  const remaining = Math.max(0, next.start - current);
  countdown.textContent = `${next.name || `Period ${next.period}`} in ${Math.floor(remaining / 60)}h ${String(remaining % 60).padStart(2, "0")}m`;
}

function classHref(course, currentPeriod = false) {
  return `#class/${currentPeriod ? "current/" : ""}${encodeURIComponent(course.studentScheduleOid)}`;
}

function renderClasses() {
  const snapshot = currentPeriodSnapshot();
  const courses = classesWithGradesFirst(snapshot.classes || []);
  $("home-title").textContent = "Welcome back";
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

