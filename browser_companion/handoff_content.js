"use strict";

(() => {
  const initialUrl = window.location.href;
  let handoff = BrowserHandoffProtocol.parseInitialUrl(initialUrl);
  if (!handoff || window.top !== window) return;
  // This message is ephemeral. No tab identity or event authority crosses it.
  chrome.runtime.sendMessage({type: "claim_handoff", handoffUrl: initialUrl}, (result) => {
    if (chrome.runtime.lastError || result?.ok !== true) return;
    if (window.location.href !== initialUrl) return;
    const cleanUrl = new URL(window.location.href);
    cleanUrl.hash = "";
    window.history.replaceState(null, "", cleanUrl.href);
  });
  handoff.capability = "";
  handoff = null;
})();
