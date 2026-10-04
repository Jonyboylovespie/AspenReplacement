"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../static/activity-notifications.js"), "utf8");
const settle = () => new Promise(setImmediate);
const grade = {type: "grade", oid: "score-1", date: "2026-10-01", assignmentName: "Quiz", grade: "8"};
const attendance = {type: "dailyAttendance", oid: "attendance-1", date: "2026-10-02", code: "T", tardy: true};

function state(events = [grade], options = {}) {
  return {signedIn: true, account: {email: "alice@school.example"}, snapshot: {
    mode: "live", student: {studentOid: "alice"}, syncedAt: "first",
    activityFeed: {available: true, events}, ...options
  }};
}

function harness({storage = new Map(), permission = "granted", supported = true, secure = true,
  serviceWorker, brokenStorage = false, permissionRequest} = {}) {
  const nodes = new Map();
  const delivered = [];
  const events = [];
  const listeners = {};
  let requests = 0;
  class Notification {
    static permission = permission;
    static async requestPermission() {
      requests++;
      const result = permissionRequest ? await permissionRequest() : permission;
      Notification.permission = result;
      return result;
    }
    constructor(title, options) { Object.assign(this, {title, options}); delivered.push(this); }
    close() { this.closed = true; }
  }
  const window = {isSecureContext: secure, focus() {},
    addEventListener(type, listener) { listeners[type] = listener; },
    dispatchEvent(event) { events.push(event); }};
  if (supported) window.Notification = Notification;
  const context = vm.createContext({window, Notification, Event: class Event { constructor(type) { this.type = type; } },
    navigator: serviceWorker ? {serviceWorker} : {},
    document: {getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {listeners: {}, attributes: {},
        setAttribute(key, value) { this.attributes[key] = value; },
        addEventListener(type, listener) { this.listeners[type] = listener; }});
      return nodes.get(id);
    }},
    localStorage: {
      getItem(key) { if (brokenStorage) throw new Error("Storage unavailable"); return storage.get(key) ?? null; },
      setItem(key, value) { if (brokenStorage) throw new Error("Storage unavailable"); storage.set(key, value); }
    }
  });
  vm.runInContext(source, context);
  return {update: context.betterAspenActivity.update, delivered, nodes, storage, Notification, listeners, events,
    requests: () => requests, toggle: () => nodes.get("activity-notifications").listeners.click()};
}

test("initial activity is quiet; additions and grade changes notify once per batch", async () => {
  const app = harness();
  app.update(state());
  assert.equal(app.requests(), 0, "permissions must only be requested by a click");
  await app.toggle();
  await settle();
  assert.equal(app.delivered.length, 0);
  const changed = {...grade, grade: "9"};
  app.update(state([changed, attendance, attendance]));
  await settle();
  assert.equal(app.delivered.length, 1);
  assert.match(app.delivered[0].options.body, /^2 new or updated entries/);
  app.update(state([attendance, changed], {syncedAt: "later", gradeFilters: {quarter: "q2"}}));
  app.update(state([changed]));
  app.update(state([changed, attendance]));
  await settle();
  assert.equal(app.delivered.length, 1, "polls, reordering, period switches, and returning entries stay quiet");
  app.delivered[0].onclick();
  assert.equal(app.events[0].type, "betteraspen:activity");
  assert.equal(app.delivered[0].closed, true);
});

test("disabled notifications do not send or replay entries when enabled again", async () => {
  const app = harness();
  app.update(state());
  app.update(state([grade, attendance]));
  await app.toggle();
  app.update(state([grade, attendance]));
  await settle();
  assert.equal(app.delivered.length, 0);
  await app.toggle();
  assert.equal(app.nodes.get("activity-notifications").attributes["aria-pressed"], "false");
  app.update(state([grade, attendance, {...attendance, oid: "attendance-2"}]));
  await settle();
  assert.equal(app.delivered.length, 0);
});

test("reloads retain the baseline and preference; another tab does not repeat alerts", async () => {
  const first = harness();
  first.update(state());
  await first.toggle();
  const second = harness({storage: first.storage});
  second.update(state());
  assert.equal(second.nodes.get("activity-notifications").attributes["aria-pressed"], "true");
  first.update(state([grade, attendance]));
  await settle();
  second.update(state([grade, attendance]));
  await settle();
  assert.equal(first.delivered.length, 1);
  assert.equal(second.delivered.length, 0);
  const reloaded = harness({storage: first.storage});
  reloaded.update(state([grade, attendance]));
  await settle();
  assert.equal(reloaded.delivered.length, 0);
  await first.toggle();
  second.listeners.storage({key: [...first.storage.keys()].find(key => key.startsWith("aspen-activity-enabled:"))});
  assert.equal(second.nodes.get("activity-notifications").attributes["aria-pressed"], "false");
});

test("stale and unavailable feeds preserve the baseline for recovery", async () => {
  const app = harness();
  app.update(state());
  await app.toggle();
  app.update(state([], {activityFeed: {available: false, events: []}}));
  app.update(state([], {activityFeed: {available: true, stale: true, events: [grade, attendance]}}));
  await settle();
  assert.equal(app.delivered.length, 0);
  app.update(state([grade, attendance]));
  await settle();
  assert.equal(app.delivered.length, 1);
});

test("demo, signed-out, and other accounts cannot send the first account's notifications", async () => {
  const app = harness();
  app.update(state());
  await app.toggle();
  app.update(state([grade, attendance], {mode: "demo"}));
  app.update({signedIn: false, snapshot: null});
  app.update({...state([grade, attendance]), account: {email: "bob@school.example"}});
  app.update({...state([grade, attendance, {...grade, oid: "bob-score"}]), account: {email: "bob@school.example"}});
  await settle();
  assert.equal(app.delivered.length, 0);
  assert.equal(app.nodes.get("activity-notifications").attributes["aria-pressed"], "false");
});

test("missing support, insecure origins, denied permission, and broken storage are handled", async () => {
  for (const options of [{supported: false}, {secure: false}, {permission: "denied"}]) {
    const app = harness(options);
    app.update(state());
    assert.equal(app.nodes.get("activity-notifications").disabled, true);
    app.update(state([grade, attendance]));
    await settle();
    assert.equal(app.delivered.length, 0);
  }
  const app = harness({brokenStorage: true});
  app.update(state());
  await app.toggle();
  app.update(state([grade, attendance]));
  app.update(state([grade, attendance]));
  await settle();
  assert.equal(app.delivered.length, 1);
});

test("service worker sends browser notifications and routes notification clicks", async () => {
  const delivered = [];
  const registration = {showNotification: async (title, options) => delivered.push({title, options})};
  const workerListeners = {};
  const serviceWorker = {ready: Promise.resolve(registration), register: async url => {
    assert.equal(url, "/notification-worker.js"); return registration;
  }, addEventListener(type, callback) { workerListeners[type] = callback; }};
  const app = harness({serviceWorker});
  app.update(state());
  await app.toggle();
  app.update(state([grade, attendance]));
  await settle();
  assert.equal(delivered.length, 1);
  assert.equal(app.delivered.length, 0);
  workerListeners.message({data: {type: "betteraspen:activity"}});
  assert.equal(app.events[0].type, "betteraspen:activity");
});

test("pending permission and worker operations cannot enable or notify another account", async () => {
  let grant;
  const app = harness({permissionRequest: () => new Promise(resolve => { grant = resolve; })});
  app.update(state());
  const enabling = app.toggle();
  app.update({...state(), account: {email: "bob@school.example"}});
  grant("granted");
  await enabling;
  assert.equal(app.nodes.get("activity-notifications").attributes["aria-pressed"], "false");
  assert.equal([...app.storage.keys()].some(key => key.startsWith("aspen-activity-enabled:")), false);
});

test("an account change while notification delivery is pending cannot disable the new account", async () => {
  let failDelivery;
  const registration = {showNotification: () => new Promise((resolve, reject) => { failDelivery = reject; })};
  const serviceWorker = {ready: Promise.resolve(registration), register: async () => registration, addEventListener() {}};
  const app = harness({serviceWorker});
  app.update(state());
  await app.toggle();
  app.update(state([grade, attendance]));
  await settle();
  app.update({...state(), account: {email: "bob@school.example"}});
  await app.toggle();
  failDelivery(new Error("Delivery failed"));
  await settle();
  assert.equal(app.nodes.get("activity-notifications").attributes["aria-pressed"], "true");
});

test("notification worker focuses an existing dashboard or opens one when it has closed", async () => {
  const workerSource = fs.readFileSync(path.join(__dirname, "../static/notification-worker.js"), "utf8");
  const listeners = {};
  const messages = [];
  const opened = [];
  let closed = 0;
  let focused = 0;
  let windows = [{url: "https://school.example/", focus: async () => { focused++; },
    postMessage: message => messages.push(message)}];
  const context = vm.createContext({URL, self: {
    location: {origin: "https://school.example"},
    addEventListener(type, callback) { listeners[type] = callback; },
    clients: {matchAll: async () => windows, openWindow: async url => opened.push(url)}
  }});
  vm.runInContext(workerSource, context);
  let pending;
  const click = {notification: {close() { closed++; }}, waitUntil(promise) { pending = promise; }};
  listeners.notificationclick(click);
  await pending;
  assert.equal(focused, 1);
  assert.equal(messages[0].type, "betteraspen:activity");
  assert.equal(opened.length, 0);
  windows = [{url: "https://another.example/"}];
  listeners.notificationclick(click);
  await pending;
  assert.equal(opened[0], "/#home");
  assert.equal(closed, 2);
});
