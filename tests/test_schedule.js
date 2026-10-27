"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const {test} = require("node:test");
const {scheduleItems, scheduleStatus, scheduleDay, formatRemaining} = require("../static/schedule.js");
const bell = require(path.join(__dirname, "../schedule/bell-schedule.json"));

const course = name => ({courseName: name, teacherName: `${name} teacher`, room: "B101"});
const [micro, calc, stats, wind, physics, english, health] =
  ["Micro", "Calc", "Stats", "Wind", "Physics", "English", "Health"].map(course);
// Periods 1–8 per letter day, as read from Aspen's matrix.
const days = {
  A: [micro, micro, calc, calc, null, health, physics, physics],
  B: [wind, wind, stats, stats, english, english, null, null],
  D: [stats, stats, wind, physics, null, null, english, english],
};
const clock = value => {
  const [hours, minutes, seconds = 0] = value.split(":").map(Number);
  return hours * 3600 + minutes * 60 + seconds;
};

test("blocks with one class span both periods; split blocks meet as minis", () => {
  const items = scheduleItems("D Day", bell, days);
  assert.deepEqual(items.map(item => [item.name, item.course?.courseName ?? item.course]), [
    ["Block 1", "Stats"], ["Block 2 · M1", "Wind"], ["Block 2 · M2", "Physics"],
    ["Block 3", null], ["Block 4", "English"]]);
  assert.deepEqual([items[1].start, items[1].end, items[2].start, items[2].end], [554, 594, 600, 640]);
  // The lunch block is one long block; lunch waves are not modeled.
  assert.deepEqual([items[3].start, items[3].end], [648, 766]);
});

test("advisory days insert advisory without shifting academic blocks", () => {
  const items = scheduleItems("A Day - Advisory Schedule", bell, days);
  assert.deepEqual(items.map(item => item.name), ["Block 1", "Advisory", "Block 2", "Block 3 · M1", "Block 3 · M2", "Block 4"]);
  assert.equal(items[1].advisory, true);
  assert.equal(items[2].course, calc);
  assert.deepEqual([items[3].course, items[4].course], [null, health]);
});

test("all-periods days run the A-day blocks, then the B-day blocks", () => {
  const items = scheduleItems("A Day - All Periods Meet", bell, days);
  assert.deepEqual(items.map(item => `${item.name}=${item.course?.courseName ?? ""}`).slice(0, 6), [
    "Period 2=Micro", "Period 4=Calc", "Period 6 · M1=", "Period 6 · M2=Health", "Period 8=Physics", "Period 1=Wind"]);
});

test("special schedules use their own bell times", () => {
  assert.equal(scheduleItems("C Day - PLC Late Start Schedule", bell, days)[0].start, 9 * 60 + 40);
  assert.equal(scheduleItems("B Day - PLC Early Dismissal Schedule", bell, days).at(-1).end, 12 * 60 + 18);
  assert.deepEqual(scheduleItems("NO SCHOOL", bell, days), []);
});

test("without Aspen's matrix, blocks still have times but unknown classes", () => {
  const items = scheduleItems("B Day", bell, null);
  assert.equal(items.length, 4);
  assert.ok(items.every(item => item.course === undefined));
});

test("the timer counts down classes, passing time, and the day's start and end", () => {
  const items = scheduleItems("D Day", bell, days);
  assert.deepEqual(scheduleStatus(items, clock("09:40")), {current: 1, timer: "14m 0s", tone: "class",
    subtitle: "Wind ends at 9:54 AM"});
  assert.equal(scheduleStatus(items, clock("09:50:30")).tone, "soon");
  assert.deepEqual(scheduleStatus(items, clock("09:57")), {current: -1, timer: "3m 0s", tone: "idle",
    subtitle: "Passing · Physics starts at 10:00 AM"});
  assert.equal(scheduleStatus(items, clock("11:00")).subtitle, "Free · Block 3 ends at 12:46 PM");
  assert.equal(scheduleStatus(items, clock("06:40")).subtitle, "Stats starts at 7:40 AM");
  assert.equal(scheduleStatus(items, clock("06:40")).timer, "1h 0m 0s");
  assert.equal(scheduleStatus(items, clock("15:00")).timer, "Done");
});

test("weekends and holidays show the next school day", () => {
  const calendar = {"2026-10-23": "A Day", "2026-10-26": "B Day"};
  assert.deepEqual(scheduleDay(calendar, new Date(2026, 9, 23, 8)), {date: "2026-10-23", dayType: "A Day", today: true});
  assert.deepEqual(scheduleDay(calendar, new Date(2026, 9, 24, 8)), {date: "2026-10-26", dayType: "B Day", today: false});
  assert.equal(scheduleDay(calendar, new Date(2026, 9, 27)), null);
  assert.equal(formatRemaining(59), "59s");
});
