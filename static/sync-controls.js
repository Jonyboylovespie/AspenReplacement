"use strict";

const syncElement = (id) => document.getElementById(id);

function setSyncPanelOpen(open, restoreFocus = false) {
  syncElement("sync-panel").hidden = !open;
  syncElement("sync-toggle").setAttribute("aria-expanded", String(open));
  const label = open ? "Hide sync controls" : "Show sync controls";
  syncElement("sync-toggle").setAttribute("aria-label", label);
  syncElement("sync-toggle").title = label;
  if (restoreFocus) syncElement("sync-toggle").focus({preventScroll: true});
}
syncElement("sync-toggle").addEventListener("click", () => setSyncPanelOpen(syncElement("sync-panel").hidden));
document.addEventListener("pointerdown", (event) => {
  if (!syncElement("sync-panel").hidden && !syncElement("sync-drawer").contains(event.target)) setSyncPanelOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !syncElement("sync-panel").hidden && !syncElement("connection-dialog").open) {
    setSyncPanelOpen(false, true);
  }
});
