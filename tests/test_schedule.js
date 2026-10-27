"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const {test} = require("node:test");
const {scheduleItems, scheduleStatus, scheduleDay, scheduleLunchLabel, lunchWave, formatRemaining} = require("../static/schedule.js");
const bell = require(path.join(__dirname, "../schedule/bell-schedule.json"));
const catalog = require(path.join(__dirname, "lunch_wave_catalog.json"));

const course = (name, department) => ({courseName: name, teacherName: `${name} teacher`, room: "B101", department});
const micro = course("400 AP Microeconomics", "HST");
const calc = course("400 Multivariable Calculus", "MAT");
const stats = course("400 AP Statistics", "MAT");
const wind = course("400 Wind Ensemble Honors PT", "MUS");
const physics = course("400 AP Physics C: Electricity and Magnetism", "SCI");
const english = course("400 AP English Literature and Composition", "ENG");
const health = course("12th Grade Health", "PE");
// Periods 1–8 per letter day, as read from Aspen's matrix.
const days = {
  A: [micro, micro, calc, calc, null, health, physics, physics],
  B: [wind, wind, stats, stats, english, english, null, null],
  C: [calc, calc, micro, micro, physics, physics, null, health],
  D: [stats, stats, wind, physics, null, null, english, english],
  E: [micro, micro, physics, physics, calc, calc, null, health],
  G: [calc, calc, null, health, micro, micro, physics, physics],
  H: [stats, stats, english, english, wind, physics, null, null],
};
const time = minutes => `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
const timeline = (dayType, matrix = days) => scheduleItems(dayType, bell, matrix).map(item => [
  item.continued ? `${item.name} (cont.)` : item.name,
  item.lunch ? "Lunch" : item.course?.courseName.replace(/^400 (AP )?/, "") ?? item.course,
  `${time(item.start)}-${time(item.end)}`]);
const clock = value => {
  const [hours, minutes, seconds = 0] = value.split(":").map(Number);
  return hours * 3600 + minutes * 60 + seconds;
};

test("blocks with one class span both periods; split blocks meet as minis", () => {
  assert.deepEqual(timeline("D Day"), [
    ["Block 1", "Statistics", "7:40-9:06"], ["Block 2 · M1", "Wind Ensemble Honors PT", "9:14-9:54"],
    ["Block 2 · M2", "Physics C: Electricity and Magnetism", "10:00-10:40"],
    // A free lunch block has no lunch.
    ["Block 3", null, "10:48-12:46"], ["Block 4", "English Literature and Composition", "12:54-14:20"]]);
});

test("the lunch block's class picks the wave and keeps the published class segments", () => {
  assert.deepEqual(timeline("B Day").slice(2, 4), [
    ["Wave 1", "Lunch", "10:48-11:18"], ["Block 3", "English Literature and Composition", "11:20-12:46"]]);
  assert.deepEqual(timeline("E Day").slice(2, 5), [
    ["Block 3", "Multivariable Calculus", "10:48-11:18"], ["Wave 2", "Lunch", "11:19-11:49"],
    ["Block 3 (cont.)", "Multivariable Calculus", "11:50-12:46"]]);
  assert.deepEqual(timeline("G Day").slice(3, 6), [
    ["Block 3", "Microeconomics", "10:48-11:44"], ["Wave 3", "Lunch", "11:45-12:15"],
    ["Block 3 (cont.)", "Microeconomics", "12:16-12:46"]]);
  assert.deepEqual(timeline("C Day").slice(2, 4), [
    ["Block 3", "Physics C: Electricity and Magnetism", "10:48-12:14"], ["Wave 4", "Lunch", "12:16-12:46"]]);
  // Advisory and PLC days use their own wave times.
  assert.deepEqual(timeline("C Day - Advisory Schedule").slice(3, 5), [
    ["Block 3", "Physics C: Electricity and Magnetism", "11:04-12:24"], ["Wave 4", "Lunch", "12:24-12:52"]]);
  assert.deepEqual(timeline("E Day - PLC Late Start Schedule").slice(2, 5), [
    ["Block 3", "Multivariable Calculus", "11:46-12:06"], ["Wave 2", "Lunch", "12:06-12:36"],
    ["Block 3 (cont.)", "Multivariable Calculus", "12:39-13:17"]]);
  assert.ok(!scheduleItems("B Day - PLC Early Dismissal Schedule", bell, days).some(item => item.lunch));
});

test("split minis eat in wave 4, except Wind Ensemble in M1 takes lunch in M1", () => {
  assert.deepEqual(timeline("A Day").slice(2, 5), [
    ["Block 3 · M1", null, "10:48-11:28"], ["Block 3 · M2", "12th Grade Health", "11:36-12:16"], ["Wave 4", "Lunch", "12:16-12:46"]]);
  assert.deepEqual(timeline("H Day").slice(2, 5), [
    ["Block 3 · M1", "Lunch", "10:48-11:28"], ["Block 3 · M2", "Physics C: Electricity and Magnetism", "11:36-12:16"],
    ["Block 3 · Wave 4", "Wind Ensemble Honors PT", "12:16-12:46"]]);
  assert.deepEqual(["A", "B", "C", "D", "E", "G", "H"].map(letter => scheduleLunchLabel(days[letter])),
    ["Lunch wave 4", "Lunch wave 1", "Lunch wave 4", "No lunch", "Lunch wave 2", "Lunch wave 3", "Lunch in M1"]);
});

test("all-periods days run the A-day blocks, then the B-day blocks, with lunch in Period 1", () => {
  assert.deepEqual(timeline("A Day - All Periods Meet").slice(0, 7), [
    ["Period 2", "Microeconomics", "7:40-8:20"], ["Period 4", "Multivariable Calculus", "8:25-9:05"],
    ["Period 6 · M1", null, "9:10-9:27"], ["Period 6 · M2", "12th Grade Health", "9:33-9:50"],
    ["Period 8", "Physics C: Electricity and Magnetism", "9:55-10:35"], ["Wave 1", "Lunch", "10:40-11:10"],
    ["Period 1", "Wind Ensemble Honors PT", "11:10-12:05"]]);
  // Minis are cut around the wave; Jupitr's Wind rule is for regular blocks only.
  assert.deepEqual(timeline("A Day - All Periods Meet", {A: days.A, B: [wind, physics, ...days.B.slice(2)]}).slice(5, 9), [
    ["Period 1 · M1", "Wind Ensemble Honors PT", "10:40-11:07"], ["Period 1 · M2", "Physics C: Electricity and Magnetism", "11:13-11:35"],
    ["Wave 4", "Lunch", "11:35-12:05"], ["Period 3", "Statistics", "12:10-12:50"]]);
});

test("every catalog course maps to its department's lunch wave", () => {
  for (const wave of ["1", "2", "3", "4", "unknown"]) {
    for (const title of catalog[wave]) {
      const expected = wave === "unknown" ? null : Number(wave);
      // Aspen prefixes titles with their level, e.g. "400 AP Statistics".
      for (const name of [title, `400 ${title}`]) assert.equal(lunchWave({courseName: name}), expected, name);
    }
  }
  // Known Aspen section prefixes decide over the title.
  assert.equal(lunchWave({courseName: "Video Broadcasting", department: "ENG"}), 1);
  assert.equal(lunchWave({courseName: "Applications of Physics and Chemistry A", department: "SCI"}), 4);
  assert.equal(lunchWave({courseName: "12th Grade Health", department: "PE"}), 4);
  assert.equal(lunchWave({courseName: "400 AP Microeconomics", department: "XYZ"}), 3);
});

test("an unknown lunch-block class gets a note instead of a guessed lunch", () => {
  const study = course("Independent Study", "XYZ");
  const items = scheduleItems("B Day", bell, {B: [wind, wind, stats, stats, study, study, null, null]});
  assert.ok(!items.some(item => item.lunch));
  assert.equal(items[2].note, "Lunch wave unknown for this class");
  assert.equal(scheduleLunchLabel([null, null, null, null, study, study]), "Lunch wave unknown");
});

test("advisory days insert advisory without shifting academic blocks", () => {
  const items = scheduleItems("A Day - Advisory Schedule", bell, days);
  assert.deepEqual(items.map(item => item.name), ["Block 1", "Advisory", "Block 2", "Block 3 · M1", "Block 3 · M2", "Wave 4", "Block 4"]);
  assert.equal(items[1].advisory, true);
  assert.equal(items[2].course, calc);
  assert.deepEqual([items[3].course, items[4].course], [null, health]);
});

test("special schedules use their own bell times", () => {
  assert.equal(scheduleItems("C Day - PLC Late Start Schedule", bell, days)[0].start, 9 * 60 + 40);
  assert.equal(scheduleItems("B Day - PLC Early Dismissal Schedule", bell, days).at(-1).end, 12 * 60 + 18);
  assert.deepEqual(scheduleItems("NO SCHOOL", bell, days), []);
});

test("without Aspen's matrix, blocks still have times but unknown classes and no lunch", () => {
  const items = scheduleItems("B Day", bell, null);
  assert.equal(items.length, 4);
  assert.ok(items.every(item => item.course === undefined && !item.lunch));
});

test("the timer counts down classes, lunch, passing time, and the day's start and end", () => {
  const items = scheduleItems("D Day", bell, days);
  assert.deepEqual(scheduleStatus(items, clock("09:40")), {current: 1, timer: "14m 0s", tone: "class",
    subtitle: "400 Wind Ensemble Honors PT ends at 9:54 AM"});
  assert.equal(scheduleStatus(items, clock("09:50:30")).tone, "soon");
  assert.deepEqual(scheduleStatus(items, clock("09:57")), {current: -1, timer: "3m 0s", tone: "idle",
    subtitle: "Passing · 400 AP Physics C: Electricity and Magnetism starts at 10:00 AM"});
  assert.equal(scheduleStatus(items, clock("11:00")).subtitle, "Free · Block 3 ends at 12:46 PM");
  assert.equal(scheduleStatus(items, clock("06:40")).subtitle, "400 AP Statistics starts at 7:40 AM");
  assert.equal(scheduleStatus(items, clock("06:40")).timer, "1h 0m 0s");
  assert.equal(scheduleStatus(items, clock("15:00")).timer, "Done");
  const lunch = scheduleItems("E Day", bell, days);
  assert.equal(scheduleStatus(lunch, clock("11:30")).subtitle, "Lunch · Wave 2 ends at 11:49 AM");
  assert.equal(scheduleStatus(lunch, clock("11:49:30")).subtitle,
    "Passing · 400 Multivariable Calculus starts at 11:50 AM");
});

test("weekends and holidays show the next school day", () => {
  const calendar = {"2026-10-23": "A Day", "2026-10-26": "B Day"};
  assert.deepEqual(scheduleDay(calendar, new Date(2026, 9, 23, 8)), {date: "2026-10-23", dayType: "A Day", today: true});
  assert.deepEqual(scheduleDay(calendar, new Date(2026, 9, 24, 8)), {date: "2026-10-26", dayType: "B Day", today: false});
  assert.equal(scheduleDay(calendar, new Date(2026, 9, 27)), null);
  assert.equal(formatRemaining(59), "59s");
});
