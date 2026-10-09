"use strict";

self.addEventListener("install", event => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  event.waitUntil((async () => {
    let payload = {};
    try { payload = event.data?.json() || {}; } catch { /* Still show a visible alert. */ }
    const count = Number.isSafeInteger(payload.count) && payload.count > 0 ? payload.count : 1;
    await self.registration.showNotification("BetterAspen · Recent activity", {
      body: `${count} new or updated ${count === 1 ? "entry" : "entries"} in Recent activity.`,
      icon: "/icons/icon-192.png", tag: "betteraspen-activity", data: {url: "/#home"}
    });
    if (self.navigator?.setAppBadge) {
      const badge = Number.isSafeInteger(payload.badge) && payload.badge > 0 ? payload.badge : count;
      await self.navigator.setAppBadge(badge).catch(() => {});
    }
    const windows = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    for (const client of windows) client.postMessage({type: "betteraspen:push"});
  })());
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil((async () => {
    if (self.navigator?.clearAppBadge) await self.navigator.clearAppBadge().catch(() => {});
    const windows = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    const dashboard = windows.find(client => new URL(client.url).origin === self.location.origin &&
      new URL(client.url).pathname === "/");
    if (dashboard) {
      await dashboard.focus();
      dashboard.postMessage({type: "betteraspen:activity"});
    } else await self.clients.openWindow("/#home");
  })());
});
