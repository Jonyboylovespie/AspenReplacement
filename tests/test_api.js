"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function api(fetch) {
  const context = vm.createContext({fetch});
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/api.js"), "utf8"), context);
  return context.betterAspenApi;
}

test("JSON requests preserve CSRF tokens and cancellation signals", async () => {
  const calls = [];
  const request = api(async (path, options) => {
    calls.push({path, options});
    return {ok: true, json: async () => ({reply: "Ready"})};
  }).request;
  await request("/api/state");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.body, undefined);
  const controller = new AbortController();
  const result = await request("/api/chat", {messages: []}, {csrfToken: "token", signal: controller.signal});
  assert.equal(result.reply, "Ready");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers["X-CSRF-Token"], "token");
  assert.deepEqual(JSON.parse(calls[1].options.body), {messages: []});
  assert.strictEqual(calls[1].options.signal, controller.signal);
});

test("authentication errors keep their status even when the server returns non-JSON", async () => {
  for (const status of [401, 403]) {
    for (const json of [async () => ({error: "Sign in again"}), async () => { throw new SyntaxError(); }]) {
      const request = api(async () => ({ok: false, status, json})).request;
      await assert.rejects(request("/api/chat", {}), error => error.status === status);
    }
  }
});

test("aborted response reads remain AbortErrors for chat retry handling", async () => {
  const aborted = new Error("Cancelled");
  aborted.name = "AbortError";
  const request = api(async () => ({ok: true, status: 200, json: async () => { throw aborted; }})).request;
  await assert.rejects(request("/api/chat", {}), error => error === aborted);
});
