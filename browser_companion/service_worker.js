"use strict";

importScripts("handoff_protocol.js");
importScripts("acquisition_protocol.js");
importScripts("acquisition_runtime.js");

const STATE_KEY = "claimedHandoff";
const CLAIM_PATH = "/browser-handoff/claim";
const EVENT_PATH = "/browser-handoff/events";
const SESSION_FIELDS = ["eventCapability", "origin", "tabId", "taskId"];
let busy = false;
let activation = null;
// The claim mutex bounds this to one transition; capabilities stay out of it.
let claiming = null;

// MV3 suspension loses globals; post-claim authority survives in session storage.
async function loadState() {
  await chrome.storage.session.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
  const values = await chrome.storage.session.get(STATE_KEY);
  const state = values[STATE_KEY];
  if (state === undefined) return null;
  if (state === null || typeof state !== "object" || Array.isArray(state) ||
      Object.keys(state).sort().join(",") !== SESSION_FIELDS.join(",") ||
      !BrowserHandoffProtocol.validTaskId(state.taskId) ||
      !BrowserHandoffProtocol.validOrigin(state.origin) ||
      !BrowserHandoffProtocol.tabBinding(state.tabId) ||
      !BrowserHandoffProtocol.validCapability(state.eventCapability)) {
    await chrome.storage.session.remove(STATE_KEY);
    throw new Error("Companion session is unavailable.");
  }
  return state;
}

function trustedSender(sender) {
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 || !sender.tab ||
      sender.tab.incognito === true || !BrowserHandoffProtocol.tabBinding(sender.tab.id)) return null;
  const page = BrowserHandoffProtocol.parsePageUrl(sender.url);
  return page ? {...page, tabId: sender.tab.id} : null;
}

function matchesOwner(state, owner) {
  return state.tabId === owner.tabId && state.taskId === owner.taskId && state.origin === owner.origin;
}

async function authorityStatus(state) {
  if (!BrowserHandoffProtocol.validOrigin(state.origin) ||
      !BrowserHandoffProtocol.validTaskId(state.taskId)) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    // This non-secret status check proves only liveness, never claim authority.
    const response = await fetch(state.origin + "/browser-handoff/" + state.taskId, {
      method: "GET", credentials: "omit", redirect: "error",
      referrerPolicy: "no-referrer", cache: "no-store", signal: controller.signal
    });
    return response.status === 200 || response.status === 404 ? response.status : null;
  } catch {
    return null; // Network failure never establishes liveness or revocation.
  } finally {
    clearTimeout(timer);
  }
}

// Call only while holding the network/claim mutex; never erase a replacement.
async function retireCurrentAuthority(state) {
  const current = await loadState();
  if (!current || !matchesOwner(current, state) || current.eventCapability !== state.eventCapability) return false;
  await chrome.storage.session.remove(STATE_KEY);
  await browserAcquisition.retireDownloads(state);
  await badge(state.tabId, "X");
  return true;
}

async function retireInactiveAuthority(state) {
  return await authorityStatus(state) === 404 && await retireCurrentAuthority(state);
}

async function postJson(origin, path, payload) {
  if (!BrowserHandoffProtocol.validOrigin(origin) || ![CLAIM_PATH, EVENT_PATH].includes(path)) {
    throw new Error("Companion request is unavailable.");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(origin + path, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload), credentials: "omit", redirect: "error",
      referrerPolicy: "no-referrer", cache: "no-store", signal: controller.signal
    });
    const text = await response.text();
    if (new TextEncoder().encode(text).length > (path === CLAIM_PATH ? 1024 : 8192)) throw new Error("Invalid companion response.");
    return {status: response.status, contentType: response.headers.get("Content-Type"), text};
  } finally {
    payload.capability = "";
    clearTimeout(timer);
  }
}

async function badge(tabId, text) {
  try {
    await chrome.action.setBadgeText({tabId, text});
  } catch {
    // A closed tab needs no notification; no protocol data is logged.
  }
}

function eventReply(response, taskId) {
  if (response.status === 204 && response.text === "") return {command: null};
  if (response.status !== 200 || response.contentType?.split(";")[0].trim().toLowerCase() !== "application/json") return null;
  try {
    const body = JSON.parse(response.text);
    if (!body || Object.keys(body).join(",") !== "command") return null;
    const command = AcquisitionProtocol.command(body.command, taskId);
    return command ? {command} : null;
  } catch { return null; }
}

async function emitEvent(state, eventType, payload, downloadOutcome = false) {
  if (busy) return false;
  let reply;
  busy = true;
  try {
    const current = await loadState();
    if (!current || !matchesOwner(current, state) || current.eventCapability !== state.eventCapability) return false;
    if (!downloadOutcome && await browserAcquisition.isTabClosed(current)) return false;
    const response = await postJson(state.origin, EVENT_PATH, {
      task_id: state.taskId, tab_binding: BrowserHandoffProtocol.tabBinding(state.tabId),
      capability: state.eventCapability, event_type: eventType, payload
    });
    if (response.status === 403) {
      await retireCurrentAuthority(state);
      return false;
    }
    reply = eventReply(response, state.taskId);
    if (reply && !reply.command && eventType === "browser_path_failure" && payload.reason === "task_tab_closed") {
      await retireCurrentAuthority(state);
      return true;
    }
    if (reply?.command) {
      // One non-secret check per returned command, never a polling loop. Local
      // session state alone cannot prove that application-side Cancel lost.
      const status = await authorityStatus(state);
      if (status !== 200) {
        if (status === 404) await retireCurrentAuthority(state);
        return false;
      }
    }
  } finally {
    busy = false;
    // Navigation is a single coalesced session state, not a generic event queue.
    // Its own delivery handles newer commits; other POSTs wake a blocked report.
    if (eventType !== "navigation_state") void browserAcquisition.reconcileNavigation();
    // Outcome delivery never wakes itself after an uncertain response. Another
    // POST releasing the mutex, or worker restart, provides a bounded retry.
    if (!downloadOutcome) void browserAcquisition.reconcileDownloadOutcome();
    if (eventType !== "browser_path_failure" || payload.reason !== "task_tab_closed") void browserAcquisition.reconcileTerminal();
  }
  if (!reply) return false;
  // Execute only after releasing the network mutex: bounded commands can emit
  // their own events. Recheck authority to reject stale replacement-task replies.
  const current = await loadState();
  if (!current || !matchesOwner(current, state) || current.eventCapability !== state.eventCapability) return false;
  if (!reply.command) return true;
  if (await browserAcquisition.executeCommand(reply.command, state.taskId)) return true;
  if (await browserAcquisition.isTabClosed(state)) return false;
  // Chrome rejected an approved operation. Terminalize with the existing fixed
  // vocabulary, and disable local reuse even if that one failure POST is lost.
  try {
    // This fixed terminal POST cannot dispatch commands or erase authority from
    // its response, so another in-flight observation need not suppress it.
    await postJson(state.origin, EVENT_PATH, {
      task_id: state.taskId, tab_binding: BrowserHandoffProtocol.tabBinding(state.tabId),
      capability: state.eventCapability, event_type: "browser_path_failure",
      payload: {reason: reply.command.type === "DOWNLOAD_CURRENT" ? "download_unavailable" : "navigation_failed"}
    });
  } finally {
    if (!busy) {
      busy = true;
      try { await retireCurrentAuthority(state); } finally { busy = false; }
    }
  }
  return false;
}

async function sendReady(state) {
  if (!await emitEvent(state, "tab_ready", {})) return false;
  const current = await loadState();
  if (!current || !matchesOwner(current, state) || current.eventCapability !== state.eventCapability) return false;
  await badge(state.tabId, "");
  return true;
}

async function activateHandoff(owner) {
  if (!owner) return false;
  if (claiming && matchesOwner(claiming, owner)) return false;
  const state = await loadState();
  if (!state || !matchesOwner(state, owner) || await browserAcquisition.isTabClosed(state)) return false;
  // Coalesce only overlapping activations, including START execution after the
  // network mutex is released. A later retry always sends tab_ready again.
  if (activation && matchesOwner(activation.state, state) &&
      activation.state.eventCapability === state.eventCapability) return activation.promise;
  const pending = {state, promise: sendReady(state)};
  activation = pending;
  try { return await pending.promise; }
  finally { if (activation === pending) activation = null; }
}

async function activateFromDocument(owner) {
  // History API scrubbing can leave MessageSender.url at the original document
  // URL. Source identity is already trusted; only Chrome's current tab proves scrub.
  const tab = await chrome.tabs.get(owner.tabId);
  if (!tab || tab.id !== owner.tabId || tab.incognito === true) return false;
  const page = BrowserHandoffProtocol.parseActivationUrl(tab.url);
  if (!page || !matchesOwner({...page, tabId: tab.id}, owner)) return false;
  return activateHandoff(owner);
}

async function claimHandoff(message, sender) {
  let handoff = BrowserHandoffProtocol.parseInitialUrl(message.handoffUrl);
  delete message.handoffUrl;
  if (!handoff) return null;
  try {
    const owner = trustedSender(sender);
    if (!owner || owner.origin !== handoff.origin || owner.taskId !== handoff.taskId) return null;
    claiming = {taskId: owner.taskId, tabId: owner.tabId, origin: owner.origin, closed: false};
    const existing = await loadState();
    if (existing) {
      // Recover a lost content-script acknowledgement, without another claim
      // or activation before the document scrubs its fragment.
      if (matchesOwner(existing, owner)) return {claimed: true};
      // A mismatched owner may claim only after the old server record is gone.
      // Otherwise no new claim is sent and the page keeps its initial fragment.
      if (!await retireInactiveAuthority(existing)) return null;
    }
    let response;
    let reply;
    try {
      response = await postJson(handoff.origin, CLAIM_PATH, {
        task_id: handoff.taskId, capability: handoff.capability,
        tab_binding: BrowserHandoffProtocol.tabBinding(sender.tab.id)
      });
      if (response.status !== 200 ||
          response.contentType?.split(";")[0].trim().toLowerCase() !== "application/json") return null;
      reply = JSON.parse(response.text);
      if (reply === null || typeof reply !== "object" || Array.isArray(reply) ||
          Object.keys(reply).length !== 1 || !Object.hasOwn(reply, "event_capability") ||
          !BrowserHandoffProtocol.validCapability(reply.event_capability) ||
          reply.event_capability === handoff.capability) return null;
    } finally {
      handoff.capability = "";
    }
    const state = {taskId: handoff.taskId, tabId: owner.tabId, origin: handoff.origin,
                   eventCapability: reply.event_capability};
    try {
      await chrome.storage.session.set({[STATE_KEY]: state});
    } catch {
      // The server consumed the initial capability even if session storage
      // failed. Scrub its fragment and discard local authority; activation fails closed.
      await chrome.storage.session.remove(STATE_KEY).catch(() => {});
      await badge(owner.tabId, "X");
      return {claimed: true};
    }
    await badge(owner.tabId, "!");
    return {claimed: true, freshOwner: owner};
  } finally {
    handoff.capability = "";
    handoff = null;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (["resolver_context", "resolver_observation", "ebsco_context", "ebsco_pdf_action"].includes(message?.type)) return false;
  if (["task_ui", "task_action"].includes(message?.type)) {
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("task_popup.html")) {
      sendResponse({ok: false}); return false;
    }
    popupMessage(message).then(sendResponse, () => sendResponse({ok: false}));
    return true;
  }
  if (message?.type === "activate_handoff") {
    const owner = trustedSender(sender);
    if (Object.keys(message).join(",") !== "type" || !owner) {
      sendResponse({ok: false}); return false;
    }
    activateFromDocument(owner).then(ok => sendResponse({ok}), () => sendResponse({ok: false}));
    return true;
  }
  if (message === null || typeof message !== "object" || Array.isArray(message) ||
      Object.keys(message).sort().join(",") !== "handoffUrl,type" || message.type !== "claim_handoff" || busy) {
    sendResponse({ok: false});
    return false;
  }
  busy = true;
  (async () => {
    let claimed = false;
    let releaseClaim = true;
    try {
      const result = await claimHandoff(message, sender);
      claimed = result?.claimed === true;
      // Removal may have preceded the session write. Keep the mutex and the
      // transition marker until the same A5 terminal is committed, never expose
      // the late claim as activatable authority for an already-closed tab.
      if (claiming?.closed && result?.freshOwner) {
        if (!await browserAcquisition.persistClosedClaim(result.freshOwner)) {
          // This mutex still exclusively owns the just-created authority. No
          // replacement can be claimed, so removal needs no fallible reread.
          try { await chrome.storage.session.remove(STATE_KEY); }
          catch {
            // With persistence unavailable, retain the closed marker/mutex.
            // No retry loop; process disappearance falls back to the app lease.
            releaseClaim = false;
          }
        }
      } else if (claiming?.closed) {
        // Recovery of an existing owner keeps its normal pre/post-freeze path.
        const state = await loadState();
        if (state && matchesOwner(state, claiming)) {
          await browserAcquisition.tabRemoved(state.tabId);
          if (!await browserAcquisition.isTabClosed(state)) await retireCurrentAuthority(state);
        }
      }
    } catch { /* No authority or protocol detail crosses the acknowledgement. */ }
    finally {
      if (releaseClaim) {
        claiming = null;
        busy = false;
        void browserAcquisition.reconcileNavigation();
        void browserAcquisition.reconcileDownloadOutcome();
        void browserAcquisition.reconcileTerminal();
      }
    }
    // A content script can activate synchronously from this acknowledgement.
    sendResponse({ok: claimed});
  })();
  return true;
});

const browserAcquisition = BrowserAcquisitionRuntime.create({
  loadAuthority: loadState,
  emitAuthenticated: emitEvent
});
// A locally committed epoch is synchronized only after its authenticated POST.
// One recovery attempt on worker wake; no timers, polling or keep-alive.
chrome.tabs.onRemoved.addListener(tabId => {
  if (claiming?.tabId === tabId) claiming.closed = true;
  void browserAcquisition.tabRemoved(tabId);
});
void browserAcquisition.reconcileTerminal().finally(() => {
  void browserAcquisition.reconcileNavigation().finally(() => {
    void browserAcquisition.reconcileClosedDownload().finally(() => { void browserAcquisition.reconcileDownloadOutcome(); });
  });
});

async function popupMessage(message) {
  const fields = Object.keys(message).sort().join(",");
  if ((message.type === "task_ui" && fields !== "type") ||
      (message.type === "task_action" && fields !== "action,choice_id,type")) return {ok: false};
  const state = await loadState();
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  const tab = tabs.length === 1 ? tabs[0] : null;
  if (!state || !tab || tab.id !== state.tabId || tab.incognito) return {ok: false};
  if (message.type === "task_ui") {
    const task = await browserAcquisition.taskUi(state.taskId);
    const page = BrowserHandoffProtocol.parseActivationUrl(tab.url);
    // No capability, URL, path or application credential crosses the popup reply.
    return {ok: true, task, can_retry: Boolean(page && matchesOwner(state, {...page, tabId: tab.id}))};
  }
  if (!["ready", "arm", "download", "fallback", "choose"].includes(message.action) ||
      (message.action !== "choose" && message.choice_id !== null) ||
      (message.action === "choose" && (!Number.isSafeInteger(message.choice_id) || message.choice_id < 0 || message.choice_id >= 6))) return {ok: false};
  if (message.action === "ready") {
    const page = BrowserHandoffProtocol.parseActivationUrl(tab.url);
    return {ok: await activateHandoff(page ? {...page, tabId: tab.id} : null)};
  }
  return {ok: await browserAcquisition.userAction(state.taskId, message.action, message.choice_id, tab)};
}
