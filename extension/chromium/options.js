"use strict";
const api = globalThis.browser || chrome;
api.storage.local.get("origin").then(({origin}) => { document.querySelector("input").value = origin || ""; });
document.querySelector("form").addEventListener("submit", async event => {
  event.preventDefault();
  const status = document.getElementById("status");
  try {
    const url = new URL(document.querySelector("input").value);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("Enter your BetterASSpen web address.");
    const granted = await api.permissions.request({origins: [url.origin + "/*"]});
    if (!granted) throw new Error("Website permission is needed to connect your account.");
    const result = await api.runtime.sendMessage({type: "configure", origin: url.origin});
    if (!result.ok) throw new Error(result.error);
    status.textContent = "Saved. Open or reload BetterASSpen, then sign in with Google.";
  } catch (error) { status.textContent = error.message; }
});
