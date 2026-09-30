"use strict";
// This bridge accepts replies only for requests made by this page. The extension
// injects its listener only on the BetterASSpen origin chosen by its owner.
class AspenConnector {
  requestId() {
    // getRandomValues also works while testing the dashboard on a LAN HTTP IP.
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
  }
  constructor(onReady) {
    this.ready = false;
    this.pending = new Map();
    this.helloId = this.requestId();
    window.addEventListener("message", event => {
      if (event.source !== window || event.origin !== location.origin || event.data?.source !== "betterasspen-connect") return;
      if (event.data.type === "ready" && event.data.id === this.helloId) {
        if (!this.ready) {
          this.ready = true;
          clearInterval(this.timer);
          onReady();
        }
      } else if (event.data.type === "session" && this.pending.has(event.data.id)) {
        const {resolve, timeout} = this.pending.get(event.data.id);
        this.pending.delete(event.data.id);
        clearTimeout(timeout);
        resolve(event.data);
      }
    });
    const hello = () => window.postMessage({source: "betterasspen-page", type: "hello", id: this.helloId}, location.origin);
    this.timer = setInterval(hello, 1000);
    hello();
  }
  capture({open = false, force = false} = {}) {
    const id = this.requestId();
    return new Promise(resolve => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        resolve({error: "BetterASSpen Connect is unavailable. Reload this page."});
      }, 15000);
      this.pending.set(id, {resolve, timeout});
      window.postMessage({source: "betterasspen-page", type: "capture", id, open, force}, location.origin);
    });
  }
}
