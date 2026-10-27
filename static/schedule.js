"use strict";

// Ported from Jupitr: the DHS day type picks a bell schedule, and Aspen's
// schedule matrix supplies each letter day's classes, two periods per block.
// A block whose two periods hold different classes meets as two minis.
const ALL_PERIODS = /All Periods Meet|First Day of Classes/i;
const CALENDAR_REFRESH_MS = 60 * 60 * 1000;
const COURSE_COLORS = 8;
let scheduleCalendar = null;
let scheduleCalendarError = "";
let scheduleCalendarLoadedAt = 0;
let scheduleCalendarRequest = null;
let scheduleRenderKey = "";

function scheduleMinutes(value) {
  const [hours, minutes] = String(value).split(":").map(Number);
  return hours * 60 + minutes;
}

function scheduleDateKey(date) {
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()].map(part => String(part).padStart(2, "0")).join("-");
}

function scheduleLetter(dayType) {
  return String(dayType || "").match(/\b([A-H]) Day\b/)?.[1] || "";
}

function bellDefinition(bell, dayType) {
  if (ALL_PERIODS.test(dayType)) return bell.allPeriods;
  if (/Early Dismissal/i.test(dayType)) return bell.earlyDismissal;
  if (/PLC/i.test(dayType)) return bell.plc;
  return /Advisory/i.test(dayType) ? bell.advisory : bell.regular;
}

function sameCourse(first, second) {
  return (first?.courseName ?? null) === (second?.courseName ?? null) &&
    (first?.teacherName ?? null) === (second?.teacherName ?? null);
}

// Without Aspen's matrix (days is null), courses are undefined rather than free.
function scheduleItems(dayType, bell, days) {
  const letter = scheduleLetter(dayType);
  if (!letter || !bell) return [];
  const allPeriods = ALL_PERIODS.test(dayType);
  const items = [];
  let academic = 0;
  for (const [index, block] of bellDefinition(bell, dayType).blocks.entries()) {
    if (/Advisory/i.test(block.name)) {
      items.push({name: block.name, start: scheduleMinutes(block.start), end: scheduleMinutes(block.end), advisory: true});
      continue;
    }
    // When all periods meet, the A-day blocks run first and the B-day blocks follow.
    const source = allPeriods ? (index < 4 ? "A" : "B") : letter;
    const position = allPeriods ? index % 4 : academic++;
    const slots = days ? (days[source] || []).slice(position * 2, position * 2 + 2).map(course => course || null) : [];
    if (sameCourse(slots[0], slots[1]) || block.minis.length < 2) {
      items.push({name: block.name, start: scheduleMinutes(block.start), end: scheduleMinutes(block.end),
                  course: days ? slots[0] || slots[1] || null : undefined});
      continue;
    }
    for (const [mini, course] of block.minis.slice(0, 2).map((mini, slot) => [mini, slots[slot]])) {
      items.push({name: `${block.name} · ${mini.name}`, start: scheduleMinutes(mini.start), end: scheduleMinutes(mini.end), course});
    }
  }
  return items;
}

function formatRemaining(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return hours ? `${hours}h ${minutes}m ${seconds % 60}s` : minutes ? `${minutes}m ${seconds % 60}s` : `${seconds % 60}s`;
}

function formatClock(minutes) {
  const hour = Math.floor(minutes / 60);
  return `${hour % 12 || 12}:${String(minutes % 60).padStart(2, "0")} ${hour >= 12 ? "PM" : "AM"}`;
}

function scheduleItemName(item) {
  return item.course?.courseName || (item.course === null ? `Free · ${item.name}` : item.name);
}

function scheduleStatus(items, seconds) {
  const current = items.findIndex(item => item.start * 60 <= seconds && seconds < item.end * 60);
  if (current >= 0) {
    const item = items[current];
    const remaining = item.end * 60 - seconds;
    return {current, timer: formatRemaining(remaining), tone: remaining < 5 * 60 ? "soon" : "class",
            subtitle: `${scheduleItemName(item)} ends at ${formatClock(item.end)}`};
  }
  const next = items.findIndex(item => seconds < item.start * 60);
  if (next < 0) return {current, timer: "Done", tone: "idle", subtitle: "School is over for today."};
  const item = items[next];
  return {current, timer: formatRemaining(item.start * 60 - seconds), tone: "idle",
          subtitle: `${next ? "Passing · " : ""}${scheduleItemName(item)} starts at ${formatClock(item.start)}`};
}

// Today when it is a school day, otherwise the next date on the DHS calendar.
function scheduleDay(calendar, now) {
  const today = scheduleDateKey(now);
  if (calendar[today]) return {date: today, dayType: calendar[today], today: true};
  const next = Object.keys(calendar).sort().find(date => date > today);
  return next ? {date: next, dayType: calendar[next], today: false} : null;
}

// Snapshots saved before the matrix import hold a list of dated days instead.
function scheduleMatrix(snapshot) {
  const schedule = snapshot?.schedule;
  return schedule?.available && schedule.days && !Array.isArray(schedule.days) ? schedule.days : null;
}

function scheduleColors(days) {
  const colors = new Map();
  for (const letter of "ABCDEFGH") {
    for (const course of days?.[letter] || []) {
      if (course && !colors.has(course.courseName)) colors.set(course.courseName, colors.size % COURSE_COLORS);
    }
  }
  return colors;
}

function loadScheduleCalendar() {
  if (scheduleCalendarRequest || Date.now() - scheduleCalendarLoadedAt < CALENDAR_REFRESH_MS) return;
  scheduleCalendarRequest = api("/api/schedule").then(data => {
    scheduleCalendar = data;
    scheduleCalendarError = "";
    scheduleCalendarLoadedAt = Date.now();
  }, error => {
    scheduleCalendarError = error.message || "The DHS calendar could not be loaded.";
    // Try again in five minutes.
    scheduleCalendarLoadedAt = Date.now() - CALENDAR_REFRESH_MS + 5 * 60 * 1000;
  }).finally(() => {
    scheduleCalendarRequest = null;
    renderSchedule();
  });
}

function renderSchedule() {
  if (!currentState?.snapshot) return;
  loadScheduleCalendar();
  renderScheduleRotation();
  scheduleRenderKey = "";
  tickSchedule();
}

function scheduleCourseNode(course, tag) {
  const match = currentPeriodSnapshot()?.classes?.find(item => item.courseName === course.courseName);
  const node = element(tag);
  if (!match) {
    node.textContent = course.courseName;
    return node;
  }
  const link = element("a", course.courseName);
  link.href = classHref(match, true);
  node.append(link);
  return node;
}

function tickSchedule(now = new Date()) {
  const snapshot = currentState?.snapshot;
  if (!snapshot) return;
  const calendar = scheduleCalendar?.calendar;
  const day = calendar && scheduleDay(calendar, now);
  const matrix = scheduleMatrix(snapshot);
  const items = day ? scheduleItems(day.dayType, scheduleCalendar.bell, matrix) : [];
  const seconds = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
  const status = day?.today ? scheduleStatus(items, seconds) :
    {current: -1, timer: "—", tone: "idle", subtitle: !calendar ? "" : day ? "No school today." : ""};
  $("schedule-timer").textContent = status.timer;
  $("schedule-timer").dataset.tone = status.tone;
  $("schedule-subtitle").textContent = status.subtitle;
  const key = `${day?.date}:${day?.dayType}:${status.current}`;
  if (key === scheduleRenderKey) return;
  scheduleRenderKey = key;
  const description = !calendar ? scheduleCalendarError || "Loading the DHS calendar…" :
    !day ? "No upcoming school days are on the DHS calendar." :
    `${day.today ? "Today" : "Next school day"} · ${new Date(`${day.date}T12:00`).toLocaleDateString(undefined,
      {weekday: "long", month: "long", day: "numeric"})} · ${day.dayType}`;
  $("schedule-day-type").textContent = description;
  const colors = scheduleColors(matrix);
  const list = $("schedule-today");
  list.replaceChildren();
  for (const [index, item] of items.entries()) {
    const row = element("li");
    row.className = "schedule-item";
    if (item.advisory) row.classList.add("schedule-advisory");
    if (index === status.current) {
      row.classList.add("schedule-current");
      row.setAttribute("aria-current", "time");
    }
    if (colors.has(item.course?.courseName)) row.classList.add(`schedule-color-${colors.get(item.course.courseName)}`);
    const details = element("div");
    details.append(element("span", item.name));
    if (item.course) {
      details.append(scheduleCourseNode(item.course, "strong"));
      const where = [item.course.teacherName, item.course.room].filter(Boolean).join(" · ");
      if (where) details.append(element("small", where));
    } else {
      details.append(element("strong", item.course === null ? "Free" : item.advisory ? "Advisory" : "—"));
    }
    row.append(element("time", `${formatClock(item.start)} – ${formatClock(item.end)}`), details);
    list.append(row);
  }
  const empty = $("schedule-today-empty");
  empty.hidden = items.length > 0 || !calendar;
  empty.textContent = day ? `The ${day.dayType} schedule could not be read.` : "Check back when the next school year’s calendar is published.";
}

function renderScheduleRotation() {
  const schedule = currentState?.snapshot?.schedule;
  const days = scheduleMatrix(currentState?.snapshot);
  const head = $("schedule-rotation-head");
  const body = $("schedule-rotation-body");
  head.replaceChildren();
  body.replaceChildren();
  if (!days) {
    $("schedule-rotation-note").textContent = (!schedule?.available && schedule?.error) || "Aspen has not returned your schedule yet.";
    return;
  }
  $("schedule-rotation-note").textContent = "Periods 1–8 from Aspen’s schedule matrix";
  const letters = [..."ABCDEFGH"].filter(letter => days[letter]);
  const today = scheduleLetter(scheduleCalendar?.calendar?.[scheduleDateKey(new Date())]);
  const colors = scheduleColors(days);
  const headRow = element("tr");
  headRow.append(Object.assign(element("th", "Period"), {scope: "col"}));
  for (const letter of letters) {
    const cell = Object.assign(element("th", `${letter} Day`), {scope: "col"});
    if (letter === today) cell.className = "schedule-rotation-today";
    headRow.append(cell);
  }
  head.append(headRow);
  for (let period = 0; period < 8; period++) {
    const row = element("tr");
    row.append(Object.assign(element("th", `Period ${period + 1}`), {scope: "row"}));
    for (const letter of letters) {
      const slots = days[letter];
      // Periods pair into blocks (1–2, 3–4 …); one class across a pair is one cell.
      if (period % 2 && sameCourse(slots[period - 1], slots[period])) continue;
      const course = slots[period];
      const cell = element("td");
      if (period % 2 === 0 && sameCourse(course, slots[period + 1])) cell.rowSpan = 2;
      if (course) {
        cell.classList.add(`schedule-color-${colors.get(course.courseName)}`);
        cell.append(scheduleCourseNode(course, "strong"));
        for (const detail of [course.teacherName, course.room]) if (detail) cell.append(element("small", detail));
      } else {
        cell.classList.add("schedule-free");
        cell.textContent = "Free";
      }
      if (letter === today) cell.classList.add("schedule-rotation-today");
      row.append(cell);
    }
    body.append(row);
  }
}

if (typeof module !== "undefined") {
  module.exports = {scheduleItems, scheduleStatus, scheduleDay, formatRemaining, formatClock, bellDefinition};
}
