"use strict";

// Shared deterministic validation for the isolated content script and worker.
const BrowserHandoffProtocol = (() => {
  const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const CAPABILITY_PATTERN = /^[A-Za-z0-9_-]{43}$/;
  const HANDOFF_PATH = /^\/browser-handoff\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;

  function validTaskId(value) {
    return typeof value === "string" && value.length === 36 && UUID_PATTERN.test(value);
  }

  function validCapability(value) {
    return typeof value === "string" && value.length === 43 && CAPABILITY_PATTERN.test(value);
  }

  function loopbackUrl(value) {
    if (typeof value !== "string" || value.length > 256) return null;
    try {
      const url = new URL(value);
      if (url.href !== value || url.protocol !== "http:" ||
          !["localhost", "127.0.0.1"].includes(url.hostname) ||
          url.username !== "" || url.password !== "" || url.search !== "") return null;
      return url;
    } catch {
      return null;
    }
  }

  function parsePageUrl(value) {
    const url = loopbackUrl(value);
    if (!url) return null;
    const path = url.pathname.match(HANDOFF_PATH);
    if (!path || !validTaskId(path[1])) return null;
    if (url.hash !== "" && !validCapability(url.hash.slice(1))) return null;
    return {origin: url.origin, taskId: path[1]};
  }

  function parseInitialUrl(value) {
    const page = parsePageUrl(value);
    if (!page) return null;
    const capability = new URL(value).hash.slice(1);
    if (!validCapability(capability)) return null;
    return {...page, capability};
  }

  function validOrigin(value) {
    const url = loopbackUrl(typeof value === "string" ? value + "/" : value);
    return url !== null && url.origin === value && url.pathname === "/" && url.hash === "";
  }

  function tabBinding(tabId) {
    return Number.isSafeInteger(tabId) && tabId >= 0 ? `tab-${tabId}` : null;
  }

  return Object.freeze({validTaskId, validCapability, parsePageUrl, parseInitialUrl, validOrigin, tabBinding});
})();
