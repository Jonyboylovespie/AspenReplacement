"use strict";

globalThis.betterAspenData = {
  expandSnapshot(snapshot) {
    if (snapshot?.snapshotFormat !== "betteraspen-snapshot-v1") return snapshot;
    const pool = snapshot.recordPool;
    const courses = pool.courses.map(record => {
      const {assignmentsRef, termsRef, summaryRef, ...course} = record;
      if (assignmentsRef !== undefined) course.assignments = assignmentsRef.map(index => pool.assignments[index]);
      if (termsRef !== undefined) course.terms = pool.terms[termsRef];
      if (summaryRef !== undefined) course.averageSummary = pool.summaries[summaryRef];
      return course;
    });
    const {snapshotFormat, recordPool, ...result} = snapshot;
    result.gradePeriods = Object.fromEntries(Object.entries(snapshot.gradePeriods)
      .map(([key, period]) => [key, {...period, classes: period.classes.map(index => courses[index])}]));
    if (snapshot.classes) result.classes = snapshot.classes.map(index => courses[index]);
    return result;
  }
};

if (typeof module !== "undefined") module.exports = betterAspenData;
