"use strict";

(() => {
  const node = id => document.getElementById(id);
  let owner = "";
  let seen = null;
  let enabled = false;
  let requesting = false;
  let workerReady = null;
  let notice = "";
  let currentState = null;
  let backgroundReady = false;
  let pushSubscription = null;
  let attemptedOwner = "";
  let pushTask = null;
  let lastRead = "";

  function pushSupported() {
    return Boolean(currentState?.pushPublicKey && "serviceWorker" in navigator && "PushManager" in window);
  }

  async function pushApi(path, body, state = currentState) {
    return betterAspenApi.request(path, body, {csrfToken: state.csrfToken, fallback: "Notification setup failed"});
  }

  function applicationKey(value) {
    const bytes = atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4));
    return Uint8Array.from(bytes, character => character.charCodeAt(0));
  }

  async function enablePush(expectedOwner) {
    const state = currentState;
    const registration = await prepareWorker();
    if (owner !== expectedOwner || !registration) throw new Error("Notification setup changed. Try enabling again.");
    let subscription = await registration.pushManager.getSubscription();
    if (owner !== expectedOwner) return;
    if (!subscription) subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: applicationKey(state.pushPublicKey)
    });
    if (owner !== expectedOwner) return;
    await pushApi("/api/push/subscribe", subscription.toJSON(), state);
    if (owner !== expectedOwner) return;
    pushSubscription = subscription;
    backgroundReady = true;
    attemptedOwner = expectedOwner;
    notice = "";
    renderControls();
    markRead();
  }

  function markRead() {
    if (!backgroundReady || !pushSubscription || !owner || document.visibilityState === "hidden" ||
        (window.location?.hash && window.location.hash !== "#home")) return;
    const key = `${owner}:${currentState?.snapshot?.syncedAt || ""}`;
    if (lastRead === key) return;
    lastRead = key;
    if (navigator.clearAppBadge) void navigator.clearAppBadge().catch(() => {});
    void prepareWorker().then(registration => registration?.getNotifications?.({tag: "betteraspen-activity"}))
      .then(notifications => notifications?.forEach(notification => notification.close())).catch(() => {});
    void pushApi("/api/push/read", {endpoint: pushSubscription.endpoint}).catch(() => {
      if (lastRead === key) lastRead = "";
    });
  }

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
    button.setAttribute("aria-pressed", String(enabled && permission === "granted" && (!pushSupported() || backgroundReady)));
    button.textContent = requesting ? "Updating…" : enabled && permission === "granted" && (!pushSupported() || backgroundReady) ? "Disable notifications" : "Enable notifications";
    node("notification-status").textContent = notice || (permission === "unavailable" ?
      "On iPhone (iOS 16.4+), add BetterAspen to your Home Screen and enable notifications inside that app. Other browsers need HTTPS and notification support." :
      permission === "denied" ? "Allow notifications in your browser’s site settings to enable browser alerts." :
      enabled && permission === "granted" && backgroundReady ? "Notifications are on, even when the app is closed." :
      pushSupported() ? "Enable notifications for updates even when the app is closed." :
      enabled && permission === "granted" ? "This browser supports alerts only while the site is open." :
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
    // The server delivers to this browser's push subscription; page polling must not duplicate it.
    if (pushSupported()) return;
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
    currentState = state;
    const snapshot = state?.snapshot;
    const nextOwner = state?.signedIn && snapshot && snapshot.mode !== "demo" && snapshot.student?.studentOid && state.account?.email ?
      JSON.stringify([state.account.email, snapshot.student.studentOid]) : "";
    if (owner !== nextOwner) {
      owner = nextOwner;
      seen = owner ? storedEvents() : null;
      enabled = owner && read(`aspen-activity-enabled:${owner}`) === true;
      notice = "";
      backgroundReady = false;
      pushSubscription = null;
      attemptedOwner = "";
      lastRead = "";
      if (!owner && navigator.clearAppBadge) void navigator.clearAppBadge().catch(() => {});
    }
    renderControls();
    if (owner && enabled && supported() && Notification.permission === "granted" && pushSupported() &&
        attemptedOwner !== owner && !pushTask && !requesting) {
      const expectedOwner = owner;
      attemptedOwner = owner;
      pushTask = enablePush(expectedOwner).catch(error => {
        if (owner === expectedOwner) { notice = error.message; renderControls(); }
      }).finally(() => { pushTask = null; });
    }
    markRead();
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
    const expectedOwner = owner;
    const expectedState = currentState;
    const disabling = enabled && Notification.permission === "granted" && (!pushSupported() || backgroundReady);
    requesting = true;
    notice = "";
    renderControls();
    try {
      if (pushTask) await pushTask;
      if (owner !== expectedOwner) return;
      if (disabling) {
        if (pushSupported()) {
          const registration = await prepareWorker();
          const subscription = await registration.pushManager.getSubscription();
          if (owner !== expectedOwner) return;
          if (subscription) {
            await pushApi("/api/push/unsubscribe", {endpoint: subscription.endpoint}, expectedState);
            if (owner !== expectedOwner) return;
            await subscription.unsubscribe();
          }
          if (owner !== expectedOwner) return;
        }
        enabled = false;
        backgroundReady = false;
        pushSubscription = null;
        notice = "";
        save(`aspen-activity-enabled:${owner}`, false);
        if (navigator.clearAppBadge) void navigator.clearAppBadge().catch(() => {});
        return;
      }
      // Request directly from the click, before any worker registration awaits.
      const permission = await Notification.requestPermission();
      if (owner !== expectedOwner) return;
      if (permission === "granted" && pushSupported()) await enablePush(expectedOwner);
      if (owner !== expectedOwner) return;
      enabled = permission === "granted";
      save(`aspen-activity-enabled:${owner}`, enabled);
      if (enabled) await prepareWorker();
    } catch (error) {
      if (owner === expectedOwner) notice = error.message || "Browser notifications could not be enabled. Try again in your browser’s site settings.";
    } finally {
      requesting = false;
      renderControls();
    }
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.addEventListener("message", event => {
    if (event.data?.type === "betteraspen:activity") openActivity();
    if (event.data?.type === "betteraspen:push") { lastRead = ""; markRead(); }
  });
  window.addEventListener("hashchange", () => { lastRead = ""; markRead(); });
  document.addEventListener?.("visibilitychange", () => { lastRead = ""; markRead(); });
  window.addEventListener("storage", event => {
    if (owner && (event.key === `aspen-activity-enabled:${owner}` || event.key === null)) {
      enabled = read(`aspen-activity-enabled:${owner}`) === true;
      attemptedOwner = "";
      backgroundReady = false;
      notice = "";
      renderControls();
    }
  });
  globalThis.betterAspenActivity = {update};
})();
