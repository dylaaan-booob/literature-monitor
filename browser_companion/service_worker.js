"use strict";

importScripts("handoff_protocol.js");
importScripts("acquisition_protocol.js");
importScripts("acquisition_runtime.js");

const STATE_KEY = "claimedHandoff";
const CLAIM_PATH = "/browser-handoff/claim";
const EVENT_PATH = "/browser-handoff/events";
const SESSION_FIELDS = ["eventCapability", "origin", "readyDelivered", "tabId", "taskId"];
let busy = false;

// MV3 suspension loses globals; only the post-claim authority survives in RAM.
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
      !BrowserHandoffProtocol.validCapability(state.eventCapability) ||
      typeof state.readyDelivered !== "boolean") {
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

async function emitEvent(state, eventType, payload) {
  if (busy) return false;
  let reply;
  busy = true;
  try {
    const current = await loadState();
    if (!current || !matchesOwner(current, state) || current.eventCapability !== state.eventCapability) return false;
    const response = await postJson(state.origin, EVENT_PATH, {
      task_id: state.taskId, tab_binding: BrowserHandoffProtocol.tabBinding(state.tabId),
      capability: state.eventCapability, event_type: eventType, payload
    });
    if (response.status === 403) {
      await chrome.storage.session.remove(STATE_KEY);
      await badge(state.tabId, "X");
      return false;
    }
    reply = eventReply(response, state.taskId);
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
  }
  if (!reply) return false;
  // Execute only after releasing the network mutex: bounded commands can emit
  // their own events. Recheck authority to reject stale replacement-task replies.
  const current = await loadState();
  if (!current || !matchesOwner(current, state) || current.eventCapability !== state.eventCapability) return false;
  if (!reply.command) return true;
  if (await browserAcquisition.executeCommand(reply.command, state.taskId)) return true;
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
  if (state.readyDelivered) return true;
  if (!await emitEvent(state, "tab_ready", {})) return false;
  if (busy) return false;
  busy = true;
  try {
    const current = await loadState();
    if (!current || !matchesOwner(current, state) || current.eventCapability !== state.eventCapability) return false;
    current.readyDelivered = true;
    await chrome.storage.session.set({[STATE_KEY]: current});
    await badge(state.tabId, "");
    return true;
  } finally { busy = false; }
}

async function claimHandoff(message, sender) {
  let handoff = BrowserHandoffProtocol.parseInitialUrl(message.handoffUrl);
  delete message.handoffUrl;
  if (!handoff) return null;
  try {
    const owner = trustedSender(sender);
    if (!owner || owner.origin !== handoff.origin || owner.taskId !== handoff.taskId) return null;
    const existing = await loadState();
    if (existing) {
      // Recover a lost content-script acknowledgement, without another claim
      // or another automatic ready event.
      if (matchesOwner(existing, owner)) return {claimed: true, freshState: null};
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
                   eventCapability: reply.event_capability, readyDelivered: false};
    try {
      await chrome.storage.session.set({[STATE_KEY]: state});
    } catch {
      // The server consumed the initial capability even if session storage
      // failed. Scrub its fragment, discard local authority and require recovery.
      await chrome.storage.session.remove(STATE_KEY).catch(() => {});
      await badge(owner.tabId, "X");
      return {claimed: true, freshState: null};
    }
    await badge(owner.tabId, "!");
    return {claimed: true, freshState: state};
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
  if (message === null || typeof message !== "object" || Array.isArray(message) ||
      Object.keys(message).sort().join(",") !== "handoffUrl,type" || message.type !== "claim_handoff" || busy) {
    sendResponse({ok: false});
    return false;
  }
  busy = true;
  (async () => {
    let responded = false;
    let freshState = null;
    try {
      const result = await claimHandoff(message, sender);
      responded = true;
      sendResponse({ok: result?.claimed === true});
      freshState = result?.freshState;
    } catch {
      if (!responded) sendResponse({ok: false});
    } finally { busy = false; }
    if (freshState) {
      try { await sendReady(freshState); } catch { /* Explicit retry remains available. */ }
    }
  })();
  return true;
});

const browserAcquisition = BrowserAcquisitionRuntime.create({
  loadAuthority: loadState,
  emitAuthenticated: emitEvent
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
    return {ok: true, task}; // No capability, URL, path or application credential.
  }
  if (!["ready", "arm", "download", "check", "fallback", "choose", "human"].includes(message.action) ||
      (message.action !== "choose" && message.choice_id !== null) ||
      (message.action === "choose" && (!Number.isSafeInteger(message.choice_id) || message.choice_id < 0 || message.choice_id >= 6))) return {ok: false};
  if (message.action === "ready") {
    const page = BrowserHandoffProtocol.parsePageUrl(tab.url);
    return {ok: Boolean(page && matchesOwner(state, {...page, tabId: tab.id}) && await sendReady(state))};
  }
  return {ok: await browserAcquisition.userAction(state.taskId, message.action, message.choice_id, tab)};
}

// Kept as a finite ready retry for hosts without a popup; the packaged popup
// supplies the same gesture explicitly along with the bounded task actions.
chrome.action.onClicked.addListener(async (tab) => {
  if (busy || !BrowserHandoffProtocol.tabBinding(tab.id) || tab.incognito === true) return;
  try {
    const page = BrowserHandoffProtocol.parsePageUrl(tab.url);
    if (!page) return;
    const state = await loadState();
    if (!state || !matchesOwner(state, {...page, tabId: tab.id})) return;
    await sendReady(state);
  } catch { /* No polling or queue; retry is an explicit human gesture. */ }
});
