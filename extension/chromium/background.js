"use strict";
const api = globalThis.browser || chrome;
const ASPEN = "https://aspen.darienps.org";
const ALLOWED = new Set(["JSESSIONID", "VITHAR_CSRF", "deploymentId", "locale", "cf_clearance"]);
const SIGN_IN = ASPEN + "/aspen-login/?deploymentId=x2sis&districtIdSSO=%2Adst&idpName=Aspen+Darien+Google+SAML";

async function registerBridge(origin) {
  await api.scripting.unregisterContentScripts({ids: ["betteraspen"]}).catch(() => {});
  if (origin) await api.scripting.registerContentScripts([{
    id: "betteraspen", matches: [origin + "/*"], js: ["bridge.js"], runAt: "document_start"
  }]);
}

async function collectCookies(storeId) {
  // Never read Google cookies or any unrelated website's cookies.
  const selected = new Map();
  for (const url of [ASPEN + "/app/", ASPEN + "/aspen/"]) {
    const cookies = await api.cookies.getAll({url, ...(storeId ? {storeId} : {})});
    for (const cookie of cookies) {
      if (!ALLOWED.has(cookie.name) || !["aspen.darienps.org", "darienps.org"].includes(cookie.domain.replace(/^\./, ""))) continue;
      selected.set(`${cookie.name}:${cookie.domain}:${cookie.path}`, {
        name: cookie.name, value: cookie.value, domain: cookie.domain, path: cookie.path,
        secure: cookie.secure, httpOnly: cookie.httpOnly, expires: cookie.expirationDate
      });
    }
  }
  const cookies = [...selected.values()];
  return cookies.some(c => c.name === "JSESSIONID" && c.path.startsWith("/app")) &&
    cookies.some(c => c.name === "VITHAR_CSRF") ? cookies : null;
}

api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const {origin} = await api.storage.local.get("origin");
    if (message.type === "configure" && sender.url === api.runtime.getURL("options.html")) {
      const url = new URL(message.origin);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Use your BetterAspen web address.");
      const allowed = await api.permissions.contains({origins: [url.origin + "/*"]});
      if (!allowed) throw new Error("Allow access to your BetterAspen address first.");
      await registerBridge(url.origin);
      await api.storage.local.set({origin: url.origin});
      return {ok: true};
    }
    if (!sender.tab || !origin || new URL(sender.url).origin !== origin) throw new Error("Unrecognized BetterAspen address.");
    if (message.type === "capture") {
      const cookies = await collectCookies(sender.tab.cookieStoreId);
      if (message.open && (!cookies || message.force)) {
        await api.tabs.create({url: SIGN_IN, ...(sender.tab.cookieStoreId ? {cookieStoreId: sender.tab.cookieStoreId} : {})});
      }
      return {cookies};
    }
    throw new Error("Unknown request.");
  })().then(sendResponse, error => sendResponse({error: error.message}));
  return true;
});
api.action.onClicked.addListener(() => api.runtime.openOptionsPage());
api.runtime.onInstalled.addListener(async details => {
  const {origin} = await api.storage.local.get("origin");
  if (origin) await registerBridge(origin);
  else if (details.reason === "install") await api.runtime.openOptionsPage();
});
