"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const {test} = require('node:test');

test('extension bridge rejects replies from other origins and unrequested IDs', async () => {
  const messages = [];
  let listener;
  const window = {
    addEventListener(type, callback) { listener = callback; },
    postMessage(message, origin) { messages.push({message, origin}); },
  };
  let ready = false;
  const context = vm.createContext({window, location: {origin: 'http://192.168.1.50:5173'},
    crypto: {getRandomValues: array => webcrypto.getRandomValues(array)}, Uint8Array,
    setInterval() { return 1; }, clearInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
    onReady() { ready = true; },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/connect.js'), 'utf8') + '\nconst connector = new AspenConnector(onReady);', context);
  const hello = messages[0].message;
  const send = (origin, data, source = window) => listener({origin, source, data: {source: 'betteraspen-connect', ...data}});
  send('https://evil.example', {type: 'ready', id: hello.id});
  send('http://192.168.1.50:5173', {type: 'ready', id: 'wrong'});
  assert.equal(ready, false);
  send('http://192.168.1.50:5173', {type: 'ready', id: hello.id});
  assert.equal(ready, true);
  const capture = vm.runInContext('connector.capture()', context);
  const request = messages[1].message;
  send('https://evil.example', {type: 'session', id: request.id, cookies: ['malicious']});
  send('http://192.168.1.50:5173', {type: 'session', id: 'unrequested', cookies: ['malicious']});
  assert.equal(vm.runInContext('connector.pending.size', context), 1);
  send('http://192.168.1.50:5173', {type: 'session', id: request.id, cookies: ['aspen']});
  assert.deepEqual((await capture).cookies, ['aspen']);
  assert.equal(vm.runInContext('connector.pending.size', context), 0);
});
