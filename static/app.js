"use strict";

const $ = (id) => document.getElementById(id);
let currentState = null;
let renderedSnapshot = null;
let selectedClass = "";
let selectedClassPeriod = "";
let selectedTerm = "all";
let busy = false;
let feedLimit = 12;
let selectedGradePeriod = null;
let gradePeriodOwner = "";

function gradePeriodStorageKey() {
  return `aspen-grade-period:${gradePeriodOwner}`;
}

function gradePeriodSnapshot(snapshot) {
  const owner = snapshot ? `${snapshot.mode}:${snapshot.student?.studentOid || ""}` : "";
  if (owner !== gradePeriodOwner) {
    gradePeriodOwner = owner;
    selectedGradePeriod = null;
    try { selectedGradePeriod = JSON.parse(localStorage.getItem(gradePeriodStorageKey())); } catch { /* Use the snapshot's default. */ }
  }
  if (!snapshot) return snapshot;
  const requested = selectedGradePeriod || snapshot.gradeFilters;
  const period = snapshot.gradePeriods[`${requested.year}:${requested.quarter}`] ||
    snapshot.gradePeriods[`${requested.year}:${requested.year === "previous" ? "all" : "current"}`] ||
    snapshot.gradePeriods["current:current"];
  selectedGradePeriod = {year: period.gradeFilters.year, quarter: period.gradeFilters.quarter};
  return {...snapshot, ...period};
}

function gradePeriod(snapshot = currentState?.snapshot) {
  return snapshot ? snapshot.gradeFilters : {year: "current", quarter: "current",
    years: [{value: "current", label: "Current school year"}, {value: "previous", label: "Previous school year"}],
    quarters: [{value: "current", label: "Current quarter"}, {value: "all", label: "All quarters"}]};
}

function currentPeriodSnapshot(snapshot = currentState?.snapshot) {
  if (!snapshot) return snapshot;
  return {...snapshot, ...snapshot.gradePeriods["current:current"]};
}

function isCurrentClassRoute() {
  return location.hash.startsWith("#class/current/");
}

function detailSnapshot() {
  return isCurrentClassRoute() ? currentPeriodSnapshot() : currentState?.snapshot;
}

function gradePeriodLabel(filters) {
  const year = filters.years.find(option => option.value === filters.year)?.label || "Current school year";
  const quarter = filters.quarters.find(option => option.value === filters.quarter)?.label || "Current quarter";
  return `${year} · ${quarter}`;
}

function renderGradeFilters() {
  const filters = gradePeriod();
  for (const [id, choices, value] of [["grade-year", filters.years, filters.year],
                                     ["grade-quarter", filters.quarters, filters.quarter]]) {
    $(id).replaceChildren(...choices.map(choice => {
      const option = element("option", choice.label);
      option.value = choice.value;
      return option;
    }));
    $(id).value = value;
  }
}

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text ?? "");
  return node;
}

function showError(message) {
  $("error").textContent = message || "";
  $("error").hidden = !message;
  $("connection-error").textContent = message || "";
  $("connection-error").hidden = !message;
}

function api(path, body) {
  return betterAspenApi.request(path, body, {csrfToken: currentState?.csrfToken});
}

function mergeState(next) {
  // A status-only poll retains this tab's existing period and record references.
  if (Object.hasOwn(next, "snapshot")) next.snapshot = betterAspenData.expandSnapshot(next.snapshot);
  else if (next.signedIn && next.snapshotRevision === currentState?.snapshotRevision &&
           next.account?.email === currentState?.account?.email) next.snapshot = currentState.snapshot;
  else throw new Error("Dashboard data changed. Retry the status check.");
  setState(next);
}

function setState(state) {
  const previousPeriod = gradePeriod();
  const previousError = currentState?.error;
  currentState = {...state, snapshot: gradePeriodSnapshot(state.snapshot)};
  const snapshot = currentState.snapshot;
  const demo = snapshot?.mode === "demo";
  const signedIn = state.signedIn;
  $("account-status").textContent = state.account ? `Signed in as ${state.account.email}` : "";
  $("logout").hidden = !state.signedIn;
  $("status").textContent = state.syncing ? "Syncing from Aspen…" : demo ? "Showing sample data." :
    state.error ? state.error : state.connected ? "Connected to Aspen. Automatically refreshes every minute." :
    snapshot ? "Showing saved data. Connect Aspen to get updates." : "Aspen is not connected.";
  $("updated").textContent = snapshot?.syncedAt ? `${demo ? "Sample loaded" : "Last successful sync"}: ${new Date(snapshot.syncedAt).toLocaleString()}${!demo && state.stale ? " (saved data; may be out of date)" : ""}` : "";
  const disabled = busy || state.syncing;
  const canChangePeriod = !!snapshot;
  $("grade-year").disabled = !canChangePeriod;
  $("grade-quarter").disabled = !canChangePeriod;
  const filters = gradePeriod();
  if (previousPeriod.year !== filters.year || previousPeriod.quarter !== filters.quarter) {
    selectedTerm = ["current", "all"].includes(filters.quarter) ? "all" : filters.quarter;
    $("assignment-search").value = "";
  }
  const historical = filters.year !== "current" || !["current", "all"].includes(filters.quarter);
  const periodTime = historical && !demo && snapshot?.fetchedAt ? ` · Last updated ${new Date(snapshot.fetchedAt).toLocaleString()}` : "";
  $("grade-period-status").textContent = `${gradePeriodLabel(filters)}${periodTime}${state.syncing ? " · Refreshing…" : ""}`;
  $("refresh").disabled = disabled || !signedIn || !state.canRetry;
  $("disconnect").disabled = disabled || !signedIn || !state.canRetry;
  $("saved-connection").hidden = !state.signedIn || !state.canRetry || state.connected;
  $("retry-session").disabled = disabled || !state.signedIn || !state.canRetry;
  $("retry-session").textContent = state.syncing ? "Retrying saved session…" : "Retry saved session";
  for (const id of ["clear", "demo", "empty-demo"]) $(id).disabled = disabled || !signedIn;
  $("google-connection").hidden = !!state.signedIn;
  $("google-sign-in").setAttribute("aria-disabled", String(disabled || !state.googleConfigured));
  $("cookie-form").hidden = !state.signedIn;
  for (const id of ["app-session", "csrf-cookie", "desktop-session", "clearance-cookie", "connect-aspen"]) {
    $(id).disabled = disabled || !state.signedIn;
  }
  $("connect-aspen").textContent = busy ? "Connecting…" : "Save and connect";
  if (state.error) showError(state.error);
  else if (previousError) showError("");
  $("warnings").replaceChildren(...(snapshot?.warnings || []).map((warning) => element("li", warning)));
  $("notice").hidden = !(snapshot?.warnings?.length);
  $("notice-title").textContent = `${snapshot?.warnings?.length || 0} data note${snapshot?.warnings?.length === 1 ? "" : "s"}`;
  $("data-label").textContent = demo ? "Sample data" : state.syncing ? "Syncing…" : snapshot ? (state.stale ? "Saved data" : "Connected") : "Not connected";
  $("sync-heading").textContent = demo ? "You’re exploring sample data" :
    state.syncing ? "Syncing with Aspen…" : !state.signedIn ? "Sign in to see your school day" : !snapshot ? "Aspen is not connected" :
    state.stale ? "Showing your saved snapshot" : "Synced with Aspen";
  $("empty").hidden = !!snapshot;
  $("dashboard").hidden = !snapshot;
  const key = snapshot ? `${state.snapshotRevision || snapshot.syncedAt}:${snapshot.mode}:${filters.year}:${filters.quarter}` : null;
  if (key !== renderedSnapshot) {
    renderedSnapshot = key;
    if (snapshot) {
      renderGradeFilters();
      renderClasses();
      renderFeed();
      renderAttendance();
      route();
    }
  }
  $("grade-year").value = filters.year;
  $("grade-quarter").value = filters.quarter;

  globalThis.betterAspenChat?.update(currentState);
  globalThis.betterAspenActivity?.update(currentState);
}

async function action(path, body = {}) {
  if (busy || currentState?.syncing) return false;
  busy = true;
  showError("");
  if (currentState) setState(currentState);
  try {
    const state = await api(`${path}?compact=1`, body);
    mergeState(state);
    return true;
  } catch (error) {
    showError(error.message);
    return false;
  } finally {
    busy = false;
    if (currentState) setState(currentState);
  }
}

function changeGradePeriod(yearChanged) {
  const year = $("grade-year").value;
  const quarter = yearChanged ? (year === "previous" ? "all" : "current") : $("grade-quarter").value;
  if (!currentState?.snapshot?.gradePeriods?.[`${year}:${quarter}`]) {
    showError("That period is not cached. Refresh Aspen to download all school years and quarters.");
    renderGradeFilters();
    return;
  }
  selectedGradePeriod = {year, quarter};
  try { localStorage.setItem(gradePeriodStorageKey(), JSON.stringify(selectedGradePeriod)); } catch { /* Selection still works for this visit. */ }
  setState(currentState);
}
function openRecentActivity() {
  $("feed-type").value = "all";
  $("feed-search").value = "";
  feedLimit = 12;
  location.hash = "home";
  // Notification links need the activity heading visible before focusing it.
  route(false, false);
  renderFeed();
  $("activity-title").focus();
  $("activity-title").scrollIntoView({block: "start"});
}

let activeView = null;
let visibleView = null;
let viewAnimation = null;
let navigationVersion = 0;

function switchView(view, focus, animate) {
  if (view === activeView && animate) {
    if (focus) { $("main").focus({preventScroll: true}); window.scrollTo(0, 0); }
    return;
  }
  const previous = visibleView;
  const interrupted = !!viewAnimation;
  const version = ++navigationVersion;
  activeView = view;
  viewAnimation?.cancel();
  viewAnimation = null;
  const incoming = $(`${view}-view`);
  const outgoing = previous && $(`${previous}-view`);
  const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const canAnimate = animate && previous && previous !== view && !$("dashboard").hidden &&
    !reducedMotion && incoming.animate && outgoing.animate;
  const order = {home: 0, grades: 1, detail: 1.5, attendance: 2};
  const direction = order[view] > order[previous] ? 1 : -1;
  const enter = () => {
    if (version !== navigationVersion) return;
    for (const name of ["home", "grades", "attendance", "detail"]) {
      const section = $(`${name}-view`);
      section.hidden = name !== view;
      section.inert = name !== view;
    }
    visibleView = view;
    if (focus) { $("main").focus({preventScroll: true}); window.scrollTo(0, 0); }
    if (!canAnimate) return;
    const entrance = incoming.animate([
      {opacity: 0, transform: `translateX(${direction * 20}px)`},
      {opacity: 1, transform: "translateX(0)"},
    ], {duration: 240, easing: "cubic-bezier(.2, .7, .2, 1)"});
    viewAnimation = entrance;
    entrance.finished.then(() => {
      if (viewAnimation === entrance) viewAnimation = null;
    }, () => {});
  };
  // A fast second click goes straight to its destination. Cancelled exits never
  // get to restore an older page, and old/new text never shares the same frame.
  if (!canAnimate || interrupted) {
    enter();
    return;
  }
  outgoing.inert = true;
  const departure = outgoing.animate([
    {opacity: 1, transform: "none"},
    {opacity: 0, transform: `translateX(${direction * -12}px)`},
  ], {duration: 90, easing: "cubic-bezier(.4, 0, 1, 1)", fill: "forwards"});
  viewAnimation = departure;
  departure.finished.then(() => {
    enter();
    departure.cancel();
  }, () => {});
}

function route(focus = false, animate = true) {
  const hash = location.hash.slice(1) || "home";
  let view = ["home", "grades", "attendance"].includes(hash) ? hash : "grades";
  if (hash.startsWith("class/")) {
    let id = "";
    const prefix = isCurrentClassRoute() ? "class/current/" : "class/";
    try { id = decodeURIComponent(hash.slice(prefix.length)); } catch { /* Invalid links return to the class list. */ }
    if (detailSnapshot()?.classes?.some(c => c.studentScheduleOid === id)) {
      const filters = gradePeriod(detailSnapshot());
      const period = `${filters.year}:${filters.quarter}`;
      if (selectedClass !== id || selectedClassPeriod !== period) {
        selectedClass = id;
        selectedClassPeriod = period;
        const quarter = filters.quarter;
        selectedTerm = ["current", "all"].includes(quarter) ? "all" : quarter;
        $("assignment-search").value = "";
      }
      renderDetails();
      view = "detail";
    }
  }
  // Polling the same route should never restart an entrance animation.
  if (view !== activeView || focus || !animate) switchView(view, focus, animate);
  const navigation = view === "detail" ? "grades" : view;
  if (focus) $("main-navigation").setAttribute("data-animated", "");
  document.querySelectorAll("[data-view]").forEach(link => {
    if (link.dataset.view === navigation) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  // The first reveal uses the final theme and selection, without a transition.
  $("main-navigation").setAttribute("data-ready", "");
  const label = view === "detail" ? selectedCourse()?.courseName : {home: "Home", grades: "Grades", attendance: "Attendance"}[view];
  document.title = `BetterAspen · ${label}`;
}

const colorModes = {
  light: {label: "Dark mode", action: "Use dark mode", theme: "#f5f8f7"},
  dark: {label: "Light mode", action: "Use light mode", theme: "#0d2231"}
};
function chooseColorMode(mode, persist = true) {
  if (!Object.hasOwn(colorModes, mode)) mode = "light";
  document.body.dataset.mode = mode;
  $("theme-color").content = colorModes[mode].theme;
  $("mode-label").textContent = colorModes[mode].label;
  $("mode-toggle").setAttribute("aria-label", colorModes[mode].action);
  $("mode-toggle").setAttribute("aria-pressed", String(mode === "dark"));
  $("mode-toggle").dataset.modeReady = "";
  if (persist) {
    $("mode-toggle").dataset.modeAnimated = "";
    try { localStorage.setItem("aspen-color-mode", mode); } catch { /* The toggle still works when storage is unavailable. */ }
  }
}
async function poll() {
  try {
    if (!busy) {
      const query = new URLSearchParams({compact: "1"});
      if (currentState?.snapshotRevision) query.set("revision", currentState.snapshotRevision);
      mergeState(await api(`/api/state?${query}`));
    }
  }
  catch (error) { showError(`The server is unavailable: ${error.message}`); }
  setTimeout(poll, currentState?.syncing ? 1000 : 5000);
}

function startDashboard() {
  $("google-sign-in").addEventListener("click", (event) => {
    if ($("google-sign-in").getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      return;
    }
    if (!currentState?.signedIn && !currentState?.googleConfigured) {
      event.preventDefault();
      showError("Google sign-in hasn't been configured on this server yet.");
      return;
    }

  });
  $("logout").addEventListener("click", async () => {
    try {
      await api("/auth/logout", {});
      location.assign("/");
    } catch (error) { showError(error.message); }
  });
  $("cookie-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const cookieValues = {
      appSession: $("app-session").value.trim(),
      csrf: $("csrf-cookie").value.trim(),
      desktopSession: $("desktop-session").value.trim(),
      clearance: $("clearance-cookie").value.trim()
    };
    if (await action("/api/session", {cookieValues})) {
      $("cookie-form").reset();
      $("connection-dialog").close();
    }
  });
  $("connection-dialog").addEventListener("close", () => $("cookie-form").reset());
  $("refresh").addEventListener("click", () => action("/api/refresh"));
  $("retry-session").addEventListener("click", () => action("/api/refresh"));
  $("disconnect").addEventListener("click", () => action("/api/disconnect"));
  $("clear").addEventListener("click", () => action("/api/clear"));
  async function loadDemo() {
    if (await action("/api/demo")) {
      $("connection-dialog").close();
      location.hash = "home";
    }
  }
  $("demo").addEventListener("click", loadDemo);
  $("empty-demo").addEventListener("click", loadDemo);
  $("class-select").addEventListener("change", () => {
    location.hash = classHref({studentScheduleOid: $("class-select").value}, isCurrentClassRoute());
  });
  $("term-select").addEventListener("change", () => { selectedTerm = $("term-select").value; renderAssignments(); });
  $("grade-year").addEventListener("change", () => changeGradePeriod(true));
  $("grade-quarter").addEventListener("change", () => changeGradePeriod(false));
  $("assignment-search").addEventListener("input", renderAssignments);
  $("feed-type").addEventListener("change", () => { feedLimit = 12; renderFeed(); });
  $("feed-search").addEventListener("input", () => { feedLimit = 12; renderFeed(); });
  $("feed-more").addEventListener("click", () => { feedLimit += 12; renderFeed(); });

  let initialMode = "light";
  try {
    const savedMode = localStorage.getItem("aspen-color-mode");
    initialMode = Object.hasOwn(colorModes, savedMode) ? savedMode :
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  } catch { /* Use light mode. */ }
  chooseColorMode(initialMode, false);
  $("mode-toggle").addEventListener("click", () => chooseColorMode(document.body.dataset.mode === "dark" ? "light" : "dark"));
  document.querySelectorAll(".connection-open").forEach(button => button.addEventListener("click", () => $("connection-dialog").showModal()));
  $("close-connection").addEventListener("click", () => $("connection-dialog").close());
  $("class-search").addEventListener("input", renderClassCards);
  window.addEventListener("hashchange", () => route(true));
  window.addEventListener("betteraspen:activity", openRecentActivity);

  window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", event => {
    if (event.matches && activeView) switchView(activeView, false, false);
  });
  route();

  if (new URLSearchParams(location.search).get("login") === "failed") {
    $("connection-dialog").showModal();
    showError("Google sign-in couldn't finish. Try signing in again.");
  }
  poll();
}
