/* Conversations stay in this tab; all AI requests go through our own server. */
(() => {
  const node = id => document.getElementById(id);
  const root = node("ai-chat");
  const panel = node("chat-panel");
  const toggle = node("chat-toggle");
  const input = node("chat-input");
  const log = node("chat-messages");
  const welcome = node("chat-welcome");
  let state = null;
  let owner = null;
  let history = [];
  let pending = false;
  let controller = null;
  let generation = 0;

  function open(value, restoreFocus = false) {
    panel.hidden = !value;
    toggle.setAttribute("aria-expanded", String(value));
    toggle.setAttribute("aria-label", value ? "Close AI chat" : "Open AI chat");
    root.classList.toggle("is-open", value);
    if (value) {
      if (typeof setSyncPanelOpen === "function") setSyncPanelOpen(false);
      // Avoid automatically summoning the mobile keyboard on opening the panel.
      if (matchMedia("(min-width: 601px)").matches) input.focus();
      else node("chat-close").focus();
      log.scrollTop = log.scrollHeight;
    } else if (restoreFocus && !root.hidden) toggle.focus();
  }

  function controls() {
    input.disabled = pending || !state?.snapshot;
    node("chat-send").disabled = input.disabled || !input.value.trim();
    node("chat-reset").disabled = pending;
    node("chat-status").hidden = !pending;
    node("chat-status").textContent = pending ? "Looking through your saved records…" : "";
    node("chat-form").setAttribute("aria-busy", String(pending));
  }

  function error(message = "") {
    node("chat-error").textContent = message;
    node("chat-error").hidden = !message;
  }

  function reset() {
    generation++;
    controller?.abort();
    controller = null;
    pending = false;
    history = [];
    log.replaceChildren(welcome);
    welcome.hidden = false;
    input.value = "";
    error();
    controls();
  }

  function message(role, content) {
    const article = document.createElement("article");
    article.className = `chat-message chat-message-${role}`;
    const label = document.createElement("span");
    label.className = "chat-message-label";
    label.textContent = role === "user" ? "You" : "BetterAspen";
    const text = document.createElement("p");
    text.textContent = content;
    article.append(label, text);
    log.append(article);
    log.scrollTop = log.scrollHeight;
    return article;
  }

  function update(next) {
    state = next;
    const identity = next.signedIn && next.aiChatEnabled ? `${next.account.email}:${next.snapshot?.student?.studentOid || ""}:${next.snapshot?.mode || ""}` : null;
    if (identity !== owner) { owner = identity; reset(); }
    root.hidden = !next.signedIn || !next.aiChatEnabled;
    if (root.hidden) open(false);
    controls();
  }

  async function send(event) {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || pending || root.hidden || !state?.snapshot) return;
    error();
    welcome.hidden = true;
    const bubble = message("user", question);
    input.value = "";
    pending = true;
    controls();
    const version = generation;
    const requestController = new AbortController();
    controller = requestController;
    // Keep complete pairs and bound history; older bubbles remain visible.
    const messages = [...history.slice(-20), {role: "user", content: question}];
    while (messages.length > 1 && messages.reduce((total, item) => total + item.content.length, 0) > 40000) messages.splice(0, 2);
    const timeout = setTimeout(() => requestController.abort(), 75000);
    try {
      const response = await fetch("/api/chat", {
        method: "POST", signal: requestController.signal,
        headers: {"Content-Type": "application/json", "X-CSRF-Token": state.csrfToken},
        body: JSON.stringify({messages}),
      });
      const data = await response.json();
      if (version !== generation) return;
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          reset(); root.hidden = true; open(false);
          return;
        }
        throw new Error(data.error || "Chat could not respond. Try again.");
      }
      if (typeof data.reply !== "string" || !data.reply.trim()) throw new Error("No reply arrived. Try again.");
      history = [...messages, {role: "assistant", content: data.reply}];
      message("assistant", data.reply);
    } catch (failure) {
      if (version !== generation) return;
      bubble.remove();
      input.value = question;
      error(failure.name === "AbortError" ? "The reply took too long. Your question is ready to retry." : failure.message);
    } finally {
      clearTimeout(timeout);
      if (version === generation) {
        pending = false; controller = null; controls();
        if (!panel.hidden && matchMedia("(min-width: 601px)").matches) input.focus();
      }
    }
  }

  toggle.addEventListener("click", () => open(panel.hidden));
  node("chat-close").addEventListener("click", () => open(false, true));
  node("chat-reset").addEventListener("click", () => { reset(); input.focus(); });
  node("chat-form").addEventListener("submit", send);
  input.addEventListener("input", controls);
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && matchMedia("(min-width: 601px)").matches) {
      event.preventDefault(); node("chat-form").requestSubmit();
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden && !node("connection-dialog").open) open(false, true);
  });
  document.addEventListener("pointerdown", event => {
    if (!panel.hidden && !root.contains(event.target)) open(false);
  });
  function viewport() {
    const view = window.visualViewport;
    root.style.setProperty("--chat-viewport-height", `${view?.height || window.innerHeight}px`);
    root.style.setProperty("--chat-keyboard-offset", `${Math.max(0, window.innerHeight - (view?.height || window.innerHeight) - (view?.offsetTop || 0))}px`);
  }
  window.visualViewport?.addEventListener("resize", viewport);
  window.visualViewport?.addEventListener("scroll", viewport);
  window.addEventListener("resize", viewport);
  viewport();
  window.betterAspenChat = {update};
  if (currentState) update(currentState);
})();
