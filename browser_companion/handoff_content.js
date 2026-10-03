"use strict";

(() => {
  const initialUrl = window.location.href;
  if (window.top !== window) return;
  const activate = () => {
    if (BrowserHandoffProtocol.parseActivationUrl(window.location.href)) {
      chrome.runtime.sendMessage({type: "activate_handoff"}, () => { void chrome.runtime.lastError; });
    }
  };
  if (BrowserHandoffProtocol.parseActivationUrl(initialUrl)) {
    activate();
    return;
  }
  let handoff = BrowserHandoffProtocol.parseInitialUrl(initialUrl);
  if (!handoff) return;
  // This message is ephemeral. No tab identity or event authority crosses it.
  chrome.runtime.sendMessage({type: "claim_handoff", handoffUrl: initialUrl}, (result) => {
    if (chrome.runtime.lastError || result?.ok !== true) return;
    if (window.location.href !== initialUrl) return;
    const cleanUrl = new URL(window.location.href);
    cleanUrl.hash = "";
    window.history.replaceState(null, "", cleanUrl.href);
    activate();
  });
  handoff.capability = "";
  handoff = null;
})();
