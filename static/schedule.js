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

// DHS lunch waves follow the department of the class in the lunch block:
// 1 English, Art, Music · 2 World Language, Math · 3 Social Studies, Tech Ed ·
// 4 Science, Special Ed, PE/Health. Aspen section prefixes seen on DHS schedules
// decide first; otherwise the course title does. The title rules are checked
// against every course in the 2026–27 catalog (tests/lunch_wave_catalog.json),
// so their order matters: cross-listed and look-alike titles come first.
// Media and Theater follow English and the Arts; Business and Computer Science
// share Career Technical Education with Tech Ed; Reading & Study Skills is
// Special Ed.
const LUNCH_DEPARTMENTS = {ENG: 1, MUS: 1, MAT: 2, HST: 3, SCI: 4, PE: 4};
const LUNCH_TITLES = [
  [1, /Media Studies|Video Broadcasting|Approaches to Film|Music Technology|Theatrical|Art and Design|Historical Art|Literature|Fiction|Writing|Composition|Capstone|\bEnglish\b/i],
  [2, /Sign Language|French|\bLatin\b|Mandarin|Chinese|Spanish/i],
  [4, /Neuroscience|Applications of Physics and Chemistry/i],
  [3, /Computer|Cybersecurity|Programming|Web Design|Business|Entrepreneurship|Marketing|Investing|Finance|Accounting|Pre-Law|Architecture|\bCADD\b|Drafting|\bDIY\b|Woodworking|Engineering|Electronics/i],
  [3, /History|Civilization|World Studies|Government|Politics|\bLaw\b|Studies|America Since|Geography|economics|Psycholog|Philosophy|Contemporary Issues/i],
  [2, /Math|Algebra|Geometry|Calculus|Statistics|Probability/i],
  [4, /Science|Biology|Chemistry|Physics|Astronomy|Botany|Anatomy|Physiology|Health|Physical Education|Study Skills|Special Education/i],
  [1, /\bArts?\b|Drawing|Painting|Graphic Design|Photography|Ceramics|Sculpture|Mural|Print ?Making|Clay|Band|Ensemble|Orchestra|Choir|Chorus|Lessons|Music|Jazz|Drama|Theat/i],
];

function lunchWave(course) {
  return LUNCH_DEPARTMENTS[course.department] ||
    LUNCH_TITLES.find(([, title]) => title.test(course.courseName || ""))?.[0] || null;
}

// The lunch for one lunch-block class pair: a free block has none, and split
// minis eat in wave 4 ("PE/H minis"). From Jupitr: with Wind Ensemble in M1,
// M1 is lunch and Wind meets during wave 4.
function scheduleLunch(first, second) {
  if (!sameCourse(first, second)) return {wave: 4, mini: true, inM1: /Wind Ensemble/i.test(first?.courseName || "")};
  if (!first) return null;
  return {wave: lunchWave(first)};
}

// Without Aspen's matrix (days is null), courses are undefined rather than free.
function scheduleItems(dayType, bell, days) {
  const letter = scheduleLetter(dayType);
  if (!letter || !bell) return [];
  const allPeriods = ALL_PERIODS.test(dayType);
  const definition = bellDefinition(bell, dayType);
  const waves = definition.lunchWaves || {};
  const lunchStart = waves[1] ? scheduleMinutes(waves[1].start) : null;
  const items = [];
  let academic = 0;
  let lunch = null;
  for (const [index, block] of definition.blocks.entries()) {
    const start = scheduleMinutes(block.start);
    const end = scheduleMinutes(block.end);
    if (/Advisory/i.test(block.name)) {
      items.push({name: block.name, start, end, advisory: true});
      continue;
    }
    // When all periods meet, the A-day blocks run first and the B-day blocks follow.
    const source = allPeriods ? (index < 4 ? "A" : "B") : letter;
    const position = allPeriods ? index % 4 : academic++;
    const slots = days ? (days[source] || []).slice(position * 2, position * 2 + 2).map(course => course || null) : [];
    const lunchBlock = days && lunchStart !== null && start <= lunchStart && lunchStart < end;
    if (lunchBlock) lunch = {block, slots, ...scheduleLunch(slots[0], slots[1])};
    if (sameCourse(slots[0], slots[1]) || block.minis.length < 2) {
      items.push({name: block.name, start, end, lunchBlock, course: days ? slots[0] || slots[1] || null : undefined});
      continue;
    }
    for (const [mini, course] of block.minis.slice(0, 2).map((mini, slot) => [mini, slots[slot]])) {
      items.push({name: `${block.name} · ${mini.name}`, start: scheduleMinutes(mini.start), end: scheduleMinutes(mini.end),
                  course, mini: true, lunchBlock});
    }
  }
  if (!lunch?.wave) {
    if (lunch) for (const item of items) if (item.lunchBlock) item.note = "Lunch wave unknown for this class";
    return items;
  }
  const window = wave => ({start: scheduleMinutes(waves[wave].start), end: scheduleMinutes(waves[wave].end),
    segments: (waves[wave].classSegments || []).map(part => ({start: scheduleMinutes(part.start), end: scheduleMinutes(part.end)}))});
  if (lunch.inM1 && !allPeriods) {
    const wind = items.find(item => item.lunchBlock && item.course === lunch.slots[0]);
    const four = window(4);
    return [...items.filter(item => item !== wind),
      {name: wind.name, start: wind.start, end: wind.end, lunch: true},
      {...wind, name: `${lunch.block.name} · Wave 4`, start: four.start, end: four.end}].sort((a, b) => a.start - b.start);
  }
  return splitAroundLunch(items, window(lunch.wave), `Wave ${lunch.wave}`);
}

// Full blocks keep the published class segments around their wave; minis are cut.
function splitAroundLunch(items, lunch, name) {
  const result = [];
  for (const item of items) {
    if (item.end <= lunch.start || item.start >= lunch.end) {
      result.push(item);
      continue;
    }
    const parts = !item.mini && lunch.segments.length ? lunch.segments :
      [{start: item.start, end: lunch.start}, {start: lunch.end, end: item.end}];
    for (const part of parts) {
      const start = Math.max(item.start, part.start);
      const end = Math.min(item.end, part.end);
      if (start < end) result.push({...item, start, end, continued: item.start < lunch.start && start >= lunch.end});
    }
  }
  result.push({name, start: lunch.start, end: lunch.end, lunch: true});
  return result.sort((a, b) => a.start - b.start || (b.lunch ? 1 : 0) - (a.lunch ? 1 : 0));
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

function scheduleLunchLabel(slots) {
  const lunch = scheduleLunch(slots[4] || null, slots[5] || null);
  return !lunch ? "No lunch" : lunch.inM1 ? "Lunch in M1" : lunch.wave ? `Lunch wave ${lunch.wave}` : "Lunch wave unknown";
}

function scheduleItemName(item) {
  if (item.lunch) return `Lunch · ${item.name}`;
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
    `${day.today ? "" : "Next school day · "}${new Date(`${day.date}T12:00`).toLocaleDateString(undefined,
      {weekday: "long", month: "long", day: "numeric"})} · ${day.dayType}`;
  $("schedule-day-type").textContent = description;
  const colors = scheduleColors(matrix);
  const list = $("schedule-today");
  list.replaceChildren();
  for (const [index, item] of items.entries()) {
    const row = element("li");
    row.className = "schedule-item";
    if (item.advisory) row.classList.add("schedule-advisory");
    if (item.lunch) row.classList.add("schedule-lunch");
    if (index === status.current) {
      row.classList.add("schedule-current");
      row.setAttribute("aria-current", "time");
    }
    if (colors.has(item.course?.courseName)) row.classList.add(`schedule-color-${colors.get(item.course.courseName)}`);
    const details = element("div");
    details.append(element("span", item.continued ? `${item.name} (cont.)` : item.name));
    if (item.course) {
      details.append(scheduleCourseNode(item.course, "strong"));
      const where = [item.course.teacherName, item.course.room].filter(Boolean).join(" · ");
      if (where) details.append(element("small", where));
    } else {
      details.append(element("strong", item.lunch ? "Lunch" : item.course === null ? "Free" : item.advisory ? "Advisory" : "—"));
    }
    if (item.note) details.append(element("small", item.note));
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
    $("schedule-rotation-note").hidden = false;
    return;
  }
  $("schedule-rotation-note").hidden = true;
  const letters = [..."ABCDEFGH"].filter(letter => days[letter]);
  const today = scheduleLetter(scheduleCalendar?.calendar?.[scheduleDateKey(new Date())]);
  const colors = scheduleColors(days);
  const headRow = element("tr");
  headRow.append(Object.assign(element("th", "Period"), {scope: "col"}));
  for (const letter of letters) {
    const cell = Object.assign(element("th", `${letter} Day`), {scope: "col"});
    cell.append(element("small", scheduleLunchLabel(days[letter])));
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
  module.exports = {scheduleItems, scheduleStatus, scheduleDay, scheduleLunchLabel, lunchWave, formatRemaining, formatClock, bellDefinition};
}
