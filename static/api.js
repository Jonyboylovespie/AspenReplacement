"use strict";

globalThis.betterAspenApi = {
  async request(path, body, {csrfToken = "", signal, fallback = "Request failed"} = {}) {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST", signal,
      headers: body === undefined ? {} : {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data;
    try { data = await response.json(); }
    catch (failure) {
      if (failure.name === "AbortError") throw failure;
      const error = new Error(`${fallback} (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    if (!response.ok) {
      const error = new Error(data?.error || `${fallback} (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
};

if (typeof module !== "undefined") module.exports = betterAspenApi;
