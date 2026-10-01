"use strict";
(() => {
  const api = globalThis.browser || chrome;
  const SOURCE = "betteraspen-connect";
  function reply(message) {
    window.postMessage({source: SOURCE, ...message}, window.location.origin);
  }
  window.addEventListener("message", async event => {
    if (event.source !== window || event.origin !== window.location.origin || event.data?.source !== "betteraspen-page") return;
    const {type, id} = event.data;
    if (typeof id !== "string") return;
    if (type === "hello") return reply({type: "ready", id});
    if (type !== "capture") return;
    try {
      const result = await api.runtime.sendMessage({type: "capture", open: !!event.data.open, force: !!event.data.force});
      reply({type: "session", id, ...result});
    } catch {
      reply({type: "session", id, error: "BetterAspen Connect is unavailable. Reload this page."});
    }
  });
})();
