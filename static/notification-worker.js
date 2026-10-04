"use strict";

self.addEventListener("install", event => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    const dashboard = windows.find(client => new URL(client.url).origin === self.location.origin &&
      new URL(client.url).pathname === "/");
    if (dashboard) {
      await dashboard.focus();
      dashboard.postMessage({type: "betteraspen:activity"});
    } else await self.clients.openWindow("/#home");
  })());
});
