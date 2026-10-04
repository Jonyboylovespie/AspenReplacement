"use strict";

(() => {
  const node = id => document.getElementById(id);
  let owner = "";
  let seen = null;
  let enabled = false;
  let requesting = false;
  let workerReady = null;
  let notice = "";

  function read(key) {
    try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
  }

  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Keep working for this visit. */ }
  }

  function supported() {
    return window.isSecureContext && "Notification" in window;
  }

  function renderControls() {
    const button = node("activity-notifications");
    const permission = supported() ? Notification.permission : "unavailable";
    button.disabled = !owner || requesting || permission === "unavailable" || permission === "denied";
    button.setAttribute("aria-pressed", String(enabled && permission === "granted"));
    button.textContent = requesting ? "Enabling…" : enabled && permission === "granted" ? "Disable notifications" : "Enable notifications";
    node("notification-status").textContent = notice || (permission === "unavailable" ?
      "Browser notifications need HTTPS and a supported browser." :
      permission === "denied" ? "Allow notifications in your browser’s site settings to enable browser alerts." :
      enabled && permission === "granted" ? "Browser notifications are on while this site is open." :
      "Enable browser notifications for updates while this site is open.");
  }

  function fingerprint(item) {
    // Use only event data, so sync times, order, and display filters cannot create alerts.
    return JSON.stringify([item.type, item.oid, item.date, item.studentScheduleOid,
      item.assignmentOid, item.termOid, item.className, item.assignmentName,
      item.grade, item.code, item.period, item.absent, item.tardy, item.dismissed, item.excused]);
  }

  function storedEvents() {
    const value = read(`aspen-activity-seen:${owner}`);
    return Array.isArray(value) && value.every(item => typeof item === "string") ? new Set(value) : null;
  }

  function prepareWorker() {
    if (!workerReady && "serviceWorker" in navigator) {
      workerReady = navigator.serviceWorker.register("/notification-worker.js")
        .then(() => navigator.serviceWorker.ready)
        .catch(() => { workerReady = null; return null; });
    }
    return workerReady || Promise.resolve(null);
  }

  function openActivity() {
    window.dispatchEvent(new Event("betteraspen:activity"));
  }

  async function notifyBrowser(count, expectedOwner) {
    if (!enabled || !supported() || Notification.permission !== "granted") return;
    const registration = await prepareWorker();
    // Account changes or turning alerts off can happen while the worker starts.
    if (owner !== expectedOwner || !enabled || Notification.permission !== "granted") return;
    const options = {
      body: `${count} new or updated ${count === 1 ? "entry" : "entries"} in Recent activity.`,
      icon: "/icons/icon-192.png", tag: "betteraspen-activity", data: {url: "/#home"}
    };
    try {
      if (registration) await registration.showNotification("BetterAspen · Recent activity", options);
      else {
        const notification = new Notification("BetterAspen · Recent activity", options);
        notification.onclick = () => { window.focus(); openActivity(); notification.close(); };
      }
    } catch {
      if (owner !== expectedOwner) return;
      notice = "Browser notification delivery failed. Try enabling notifications again.";
      enabled = false;
      save(`aspen-activity-enabled:${owner}`, false);
      renderControls();
    }
  }

  function update(state) {
    const snapshot = state?.snapshot;
    const nextOwner = state?.signedIn && snapshot && snapshot.mode !== "demo" && snapshot.student?.studentOid && state.account?.email ?
      JSON.stringify([state.account.email, snapshot.student.studentOid]) : "";
    if (owner !== nextOwner) {
      owner = nextOwner;
      seen = owner ? storedEvents() : null;
      enabled = owner && read(`aspen-activity-enabled:${owner}`) === true;
      notice = "";
    }
    renderControls();
    // Feed failures keep the previous baseline; recovery must not replay old entries.
    const feed = snapshot?.activityFeed;
    if (!owner || !feed?.available || feed.stale || !Array.isArray(feed.events)) return;
    const latest = new Set(feed.events.map(fingerprint));
    const stored = storedEvents();
    if (seen && stored) for (const key of stored) seen.add(key);
    const firstLoad = seen === null;
    const added = seen ? [...latest].filter(key => !seen.has(key)) : [];
    seen = new Set([...(seen || []), ...latest]);
    if (firstLoad || added.length) save(`aspen-activity-seen:${owner}`, [...seen]);
    if (added.length) {
      void notifyBrowser(added.length, owner);
    }
  }

  node("activity-notifications").addEventListener("click", async () => {
    if (!owner || !supported() || requesting) return;
    if (enabled && Notification.permission === "granted") {
      enabled = false;
      notice = "";
      save(`aspen-activity-enabled:${owner}`, false);
      renderControls();
      return;
    }
    const expectedOwner = owner;
    requesting = true;
    notice = "";
    renderControls();
    try {
      // Request directly from the click, before any worker registration awaits.
      const permission = await Notification.requestPermission();
      if (owner !== expectedOwner) return;
      enabled = permission === "granted";
      save(`aspen-activity-enabled:${owner}`, enabled);
      if (enabled) await prepareWorker();
    } catch {
      if (owner === expectedOwner) notice = "Browser notifications could not be enabled. Try again in your browser’s site settings.";
    } finally {
      requesting = false;
      renderControls();
    }
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.addEventListener("message", event => {
    if (event.data?.type === "betteraspen:activity") openActivity();
  });
  window.addEventListener("storage", event => {
    if (owner && (event.key === `aspen-activity-enabled:${owner}` || event.key === null)) {
      enabled = read(`aspen-activity-enabled:${owner}`) === true;
      notice = "";
      renderControls();
    }
  });
  globalThis.betterAspenActivity = {update};
})();
