"use strict";

// A7 browser primitives. A8 will deliver app-authenticated commands to this API.
// None of these commands are exposed to arbitrary page/content-script messages.
const BrowserAcquisitionRuntime = (() => {
  const KEY = "browserAcquisition";
  const TERMINAL_KEY = "browserTerminal";
  const ARM_WINDOW_MS = 10000;
  const PROVIDER_PREPARATION_WINDOW_MS = 120000;
  const ACTION_MESSAGE_WINDOW_MS = 10000;
  const MAX_NAVIGATION_SEQUENCES = 8;
  const MAX_OBSERVED_DOWNLOADS = 8;

  function create({loadAuthority, emitAuthenticated}) {
    const snapshots = new WeakMap();
    let mutationTail = Promise.resolve();
    let navigationDelivery = null;
    let navigationDeliveryRequested = false;
    let downloadDelivery = null;
    let downloadDeliveryRequested = false;
    let terminalDelivery = null;
    let terminalDeliveryRequested = false;
    const MUTABLE_FIELDS = ["route", "publisherState", "navigationUrl", "previousUrl", "navigationTime",
      "navigationEpoch", "reportedEpoch", "committedTime", "navigationSequences", "navigationOverflow",
      "navigationFailure", "pendingNavigation", "category", "choices",
      "candidate", "ambiguous", "downloadReported", "downloadOutcome", "observedDownloads", "recordEvidence", "providerAction", "userArm", "taskTabClosed"];
    function sameOwner(a, b) {
      return a && b && a.taskId === b.taskId && a.tabId === b.tabId && a.origin === b.origin;
    }

    function tabCloseOutcome(owner) {
      return {taskId: owner.taskId, tabId: owner.tabId, origin: owner.origin,
        id: crypto.randomUUID(), eventType: "browser_path_failure", payload: {reason: "task_tab_closed"}};
    }

    async function persistClosedClaim(owner) {
      // Worker-only: a fresh validated claim, before activation, under the
      // claim mutex. The known removal must survive even if session GET fails.
      const write = mutationTail.then(async () => {
        await chrome.storage.session.set({[TERMINAL_KEY]: tabCloseOutcome(owner)});
        return true;
      });
      mutationTail = write.catch(() => {});
      return write;
    }

    async function isTabClosed(owner) {
      const ctx = (await chrome.storage.session.get(KEY))[KEY];
      const terminal = (await chrome.storage.session.get(TERMINAL_KEY))[TERMINAL_KEY];
      return Boolean(sameOwner(ctx, owner) && ctx.taskTabClosed || sameOwner(terminal, owner));
    }

    async function tabRemoved(tabId) {
      const write = mutationTail.then(async () => {
        const owner = await loadAuthority();
        if (!owner || owner.tabId !== tabId) return false;
        const latest = (await chrome.storage.session.get(KEY))[KEY];
        const ctx = sameOwner(latest, owner) ? latest : null;
        if (ctx?.taskTabClosed || await isTabClosed(owner)) return false;
        if (ctx) {
          ctx.taskTabClosed = true;
          ctx.pendingNavigation = null;
          ctx.navigationSequences = [];
          ctx.navigationOverflow = false;
          ctx.navigationFailure = null;
          ctx.choices = [];
          ctx.userArm = null;
          ctx.observedDownloads = [];
        }
        const frozen = ctx && (Number.isSafeInteger(ctx.candidate?.id) && ctx.candidate.id >= 0 ||
          ctx.downloadOutcome || ctx.downloadReported);
        if (!frozen) {
          if (ctx) { ctx.candidate = null; ctx.providerAction = null; ctx.recordEvidence = null; }
          await chrome.storage.session.set({[TERMINAL_KEY]: tabCloseOutcome(owner)});
        }
        if (ctx) await chrome.storage.session.set({[KEY]: ctx});
        return true;
      });
      mutationTail = write.catch(() => {});
      if (await write) {
        await reconcileTerminal();
        await reconcileClosedDownload();
      }
    }

    async function reconcileClosedDownload() {
      const ctx = await context();
      if (ctx?.taskTabClosed && Number.isSafeInteger(ctx.candidate?.id)) await completed(ctx.candidate.id);
    }

    async function reconcileTerminal() {
      if (terminalDelivery) { terminalDeliveryRequested = true; return terminalDelivery; }
      const delivery = (async () => {
        const owner = await loadAuthority();
        const terminal = (await chrome.storage.session.get(TERMINAL_KEY))[TERMINAL_KEY];
        if (!sameOwner(terminal, owner)) return false;
        // One close outcome, authenticated ACK retirement is handled by worker.
        return emitAuthenticated(owner, terminal.eventType, terminal.payload, true);
      })();
      terminalDelivery = delivery;
      try { return await delivery; }
      finally {
        terminalDelivery = null;
        const requested = terminalDeliveryRequested;
        terminalDeliveryRequested = false;
        if (requested) void reconcileTerminal().catch(() => {});
      }
    }
    async function context() {
      const owner = await loadAuthority();
      const value = (await chrome.storage.session.get(KEY))[KEY];
      if (!owner || !value || value.taskId !== owner.taskId || value.tabId !== owner.tabId ||
          value.origin !== owner.origin || !AcquisitionProtocol.plan(value.plan)) return null;
      snapshots.set(value, structuredClone(value));
      if (value.userArm && (value.ambiguous || value.userArm.navigationUrl !== value.navigationUrl ||
          value.userArm.navigationEpoch !== value.navigationEpoch ||
          Date.now() - value.userArm.armedAt > ARM_WINDOW_MS)) {
        value.userArm = null;
        value.observedDownloads = [];
        await save(value);
      }
      return value;
    }

    function sequenceEvidence(event) {
      return {url: event.url, timeStamp: event.timeStamp,
        processId: Number.isSafeInteger(event.processId) && event.processId >= 0 ? event.processId : null};
    }

    function compatibleSequence(start, event) {
      return start.processId === null || event.processId === null || start.processId === event.processId;
    }

    function commandSequence(next, event, redirect = false) {
      const pending = next.pendingNavigation;
      const starts = next.navigationSequences;
      const root = starts.find(s => s.commandId === pending?.id && s.timeStamp === pending?.startedAt);
      if (!root || next.navigationOverflow || root.timeStamp > event.timeStamp) return null;
      // An unresolved, superseded sequence cannot donate its events to a new
      // command, even if Chrome reuses the process or target URL.
      if (starts.some(s => s.retired && compatibleSequence(s, event) &&
          (s.url === event.url || redirect))) return null;
      const other = starts.filter(s => s !== root && !s.retired && !s.ended && s.timeStamp <= event.timeStamp);
      if (other.some(s => s.url === pending.target && compatibleSequence(s, event))) return null;
      if (!redirect) {
        const ambiguous = root.processId === null && other.some(s => compatibleSequence(s, event) && !s.aborted);
        return !root.aborted && !ambiguous && event.url === root.url && compatibleSequence(root, event) ? root : null;
      }
      if (root.processId !== null && event.processId !== null && root.processId === event.processId) return root;
      // ERR_ABORTED plus a redirect qualifier does not prove that a different
      // process/start is the command's continuation: a user navigation can
      // abort that command and redirect too. Unknown starts are not one shared
      // sequence. Without a browser-provided association, fail closed.
      return root.processId === null && other.length === 0 ? root : null;
    }

    async function save(ctx, {commit = null, navigationStart = null, navigationError = null, downloadChange = null} = {}) {
      const observation = ctx === null;
      const before = ctx && snapshots.get(ctx);
      const changes = ctx ? Object.fromEntries(MUTABLE_FIELDS.filter(key =>
        !before || JSON.stringify(ctx[key]) !== JSON.stringify(before[key]))
        .map(key => [key, structuredClone(ctx[key])])) : {};
      // Serialize only verified session read/modify/write work. Browser calls
      // and authenticated network events never hold this critical section.
      const write = mutationTail.then(async () => {
        const owner = await loadAuthority();
        const latest = (await chrome.storage.session.get(KEY))[KEY];
        if (observation) {
          const event = commit || navigationStart || navigationError;
          if (!owner || event.tabId !== owner.tabId || !latest || latest.taskId !== owner.taskId ||
              latest.tabId !== owner.tabId || latest.origin !== owner.origin || !AcquisitionProtocol.plan(latest.plan)) return false;
          ctx = structuredClone(latest);
        }
        if (!owner || owner.taskId !== ctx.taskId || owner.tabId !== ctx.tabId || owner.origin !== ctx.origin) return false;
        if (before) {
          if (!latest || latest.taskId !== ctx.taskId || latest.tabId !== ctx.tabId || latest.origin !== ctx.origin ||
              !AcquisitionProtocol.plan(latest.plan) || JSON.stringify(latest.plan) !== JSON.stringify(ctx.plan)) return false;
        } else if (!observation && latest?.taskId === ctx.taskId) return false; // Never reset an existing task.
        // A newly claimed task may replace retired task state; stale transitions
        // from that retired task still fail the authority check above.
        const next = before || observation ? latest : structuredClone(ctx);
        if (sameOwner((await chrome.storage.session.get(TERMINAL_KEY))[TERMINAL_KEY], owner) ||
            next.taskTabClosed && !(downloadChange && ["complete", "unavailable"].includes(downloadChange.type) &&
              next.candidate?.id === downloadChange.id)) return false;
        let failedNavigation = false;
        if (navigationStart !== null) {
          if (navigationStart.timeStamp <= (next.committedTime ?? -1)) return false;
          const start = sequenceEvidence(navigationStart);
          if (next.navigationSequences.some(s => s.timeStamp === start.timeStamp && s.url === start.url &&
              s.processId === start.processId)) return false;
          if (next.navigationSequences.length === MAX_NAVIGATION_SEQUENCES) next.navigationOverflow = true;
          else {
            const pending = next.pendingNavigation;
            if (pending && pending.startedAt === null && start.url === pending.target) {
              pending.startedAt = start.timeStamp;
              start.commandId = pending.id;
            }
            next.navigationSequences.push(start);
          }
        } else if (navigationError !== null) {
          const event = sequenceEvidence(navigationError);
          const root = commandSequence(next, event);
          // Errors have no redirect qualifiers. A process alone cannot prove
          // that a different URL is part of the command's redirect chain.
          if (!root) {
            const matching = next.navigationSequences.filter(s => s.url === event.url &&
              s.timeStamp <= event.timeStamp && compatibleSequence(s, event));
            if (matching.length !== 1 || matching[0].commandId === next.pendingNavigation?.id) return false;
            // An unrelated sequence may finish, but has no command-failure
            // authority. Aborted provisional branches remain redirect evidence.
            if (matching[0].retired) next.navigationSequences = next.navigationSequences.filter(s => s !== matching[0]);
            else if (navigationError.error === "net::ERR_ABORTED") matching[0].aborted = true;
            else matching[0].ended = true;
          } else if (navigationError.error === "net::ERR_ABORTED") root.aborted = true;
          else {
            next.navigationFailure = {id: next.pendingNavigation.id, epoch: next.navigationEpoch};
            failedNavigation = true;
            next.pendingNavigation = null;
            root.retired = true;
          }
        } else if (commit !== null) {
          // Compare Chrome event clocks only to Chrome event clocks. Persisted
          // ordering rejects delayed events even after MV3 worker restart.
          if (next.committedTime !== null && commit.timeStamp <= next.committedTime) return false;
          const candidate = next.candidate;
          const invalidateCandidate = candidate?.providerAction ||
            (candidate?.userArm && sameTaskRecord(next, candidate.navigationUrl));
          const pending = next.pendingNavigation;
          const qualifiers = commit.transitionQualifiers || [];
          const redirect = qualifiers.includes("server_redirect") || qualifiers.includes("client_redirect");
          const commanded = !qualifiers.some(q => ["from_address_bar", "forward_back"].includes(q)) &&
            !["typed", "auto_bookmark", "generated", "keyword", "keyword_generated", "reload", "form_submit"].includes(commit.transitionType) &&
            commandSequence(next, sequenceEvidence(commit), redirect);
          const transition = commanded ? pending.transition : {};
          // Keep only unresolved losing sequences, without their transition.
          // This is bounded in-flight evidence, not committed navigation history.
          const committed = sequenceEvidence(commit);
          next.navigationSequences = next.navigationSequences.filter(s =>
            (pending || s.retired) && !s.ended && !s.aborted &&
            !(compatibleSequence(s, committed) && (s.url === commit.url ||
              s.processId !== null && committed.processId !== null)) &&
            !(commanded && !s.retired && s.processId === null))
            .map(s => ({...s, retired: true}));
          next.navigationOverflow = false;
          next.navigationFailure = null;
          Object.assign(next, transition, {previousUrl: next.navigationUrl || "",
            navigationUrl: commit.url, navigationTime: Date.now(),
            navigationEpoch: next.navigationEpoch + 1, committedTime: commit.timeStamp,
            pendingNavigation: null, choices: [], observedDownloads: [], recordEvidence: null, providerAction: null, userArm: null});
          if (invalidateCandidate) next.candidate = null;
        } else {
          if (before && (next.navigationEpoch !== before.navigationEpoch ||
              JSON.stringify(next.navigationSequences) !== JSON.stringify(before.navigationSequences) ||
              next.navigationOverflow !== before.navigationOverflow ||
              JSON.stringify(next.navigationFailure) !== JSON.stringify(before.navigationFailure) ||
              JSON.stringify(next.pendingNavigation) !== JSON.stringify(before.pendingNavigation))) return false;
          if (downloadChange) {
            if (next.ambiguous || next.downloadReported || next.downloadOutcome ||
                JSON.stringify(downloadExpectation(next)) !== JSON.stringify(downloadChange.expectation)) return false;
            next.observedDownloads ??= [];
            if (downloadChange.type === "observe") {
              if (!next.observedDownloads.some(o => o.id === downloadChange.id)) {
                if (next.observedDownloads.length === MAX_OBSERVED_DOWNLOADS) return false; // Drop newest; never infer ambiguity.
                next.observedDownloads.push({id: downloadChange.id, ...downloadChange.expectation});
              }
            } else if (downloadChange.type === "discard") {
              next.observedDownloads = next.observedDownloads.filter(o => o.id !== downloadChange.id);
            } else if (downloadChange.type === "claim") {
              if (next.candidate && next.candidate.id !== downloadChange.id) {
                next.ambiguous = true;
                next.downloadOutcome = {id: crypto.randomUUID(), eventType: "browser_path_failure",
                  payload: {reason: "ambiguous_download_ownership"}};
                next.candidate = null;
                next.userArm = null;
                next.observedDownloads = [];
              } else if (!next.candidate) {
                next.candidate = structuredClone(downloadChange.candidate);
                next.userArm = null;
                next.observedDownloads = [];
              }
            } else {
              if (next.candidate?.id !== downloadChange.id) return false;
              next.candidate = null;
              next.userArm = null;
              next.observedDownloads = [];
              next.downloadOutcome = {id: crypto.randomUUID(),
                eventType: downloadChange.type === "complete" ? "download_candidate" : "browser_path_failure",
                payload: downloadChange.type === "complete" ? structuredClone(downloadChange.payload)
                  : {reason: "download_unavailable"}};
            }
          } else {
            // A navigation acknowledgement changes only reportedEpoch. Its
            // epoch/sequence CAS above remains mandatory, but an independent
            // download acknowledgement cannot block this progress marker.
            const navigationAck = Object.keys(changes).length === 1 && Object.hasOwn(changes, "reportedEpoch");
            if (before && !navigationAck && ["candidate", "observedDownloads", "userArm", "ambiguous", "downloadReported", "downloadOutcome", "providerAction", "recordEvidence"].some(key =>
                JSON.stringify(next[key]) !== JSON.stringify(before[key]))) return false;
            Object.assign(next, changes);
          }
        }
        await chrome.storage.session.set({[KEY]: next});
        Object.assign(ctx, next);
        snapshots.set(ctx, structuredClone(next));
        return failedNavigation || downloadChange ? structuredClone(next) : true;
      });
      mutationTail = write.catch(() => {});
      return write;
    }

    async function reconcileNavigation() {
      if (navigationDelivery) {
        navigationDeliveryRequested = true;
        return navigationDelivery;
      }
      const delivery = (async () => {
        const ctx = await context();
        if (!ctx?.navigationUrl || ctx.taskTabClosed || ctx.reportedEpoch === ctx.navigationEpoch) return false;
        if (!await emit(ctx, "navigation_state", {
          identity: AcquisitionProtocol.sanitizedIdentity(ctx.navigationUrl), route: ctx.route})) return false;
        ctx.reportedEpoch = ctx.navigationEpoch;
        // A newer commit may have arrived during the POST. Its epoch is still
        // unreported; this acknowledgement cannot mark that newer state synced.
        return save(ctx);
      })();
      navigationDelivery = delivery;
      try { return await delivery; }
      finally {
        navigationDelivery = null;
        // One wake bit covers commits or a mutex release during this attempt.
        // No request retries itself; a failed transport remains unreported.
        const requested = navigationDeliveryRequested;
        navigationDeliveryRequested = false;
        if (requested) void reconcileNavigation().catch(() => {});
      }
    }

    async function emit(ctx, type, payload, downloadOutcome = false) {
      const owner = await loadAuthority();
      if (!owner || owner.taskId !== ctx.taskId || owner.tabId !== ctx.tabId || owner.origin !== ctx.origin ||
          !downloadOutcome && await isTabClosed(owner) ||
          !AcquisitionProtocol.boundedPayload(payload)) return false;
      return emitAuthenticated(owner, type, payload, downloadOutcome);
    }

    async function clearPending(ctx, pending) {
      const current = await context();
      if (!current || current.taskId !== ctx.taskId || current.tabId !== ctx.tabId ||
          current.pendingNavigation?.id !== pending.id) return false;
      current.pendingNavigation = null;
      current.navigationSequences = current.navigationSequences.map(s => s.commandId === pending.id ? {...s, retired: true} : s);
      return save(current);
    }

    async function navigate(ctx, target, transition = {}) {
      const url = AcquisitionProtocol.safeUrl(target);
      const owner = await loadAuthority();
      if (!url || ctx.taskTabClosed || ctx.pendingNavigation || !owner || owner.taskId !== ctx.taskId ||
          owner.tabId !== ctx.tabId || owner.origin !== ctx.origin) return false;
      const pending = {id: crypto.randomUUID(), target: url, epoch: ctx.navigationEpoch, transition, startedAt: null};
      ctx.pendingNavigation = pending;
      ctx.navigationFailure = null;
      if (!await save(ctx)) return false;
      try {
        await chrome.tabs.update(ctx.tabId, {url});
      } catch {
        await clearPending(ctx, pending);
        return false;
      }
      // Only onCommitted may publish actual navigation. No post-update save can
      // overwrite a commit that arrived before this Promise resolved.
      return true;
    }

    async function start(rawPlan) {
      const plan = AcquisitionProtocol.plan(rawPlan);
      const owner = await loadAuthority();
      if (!plan || !owner || owner.taskId !== plan.task_id || await isTabClosed(owner)) return false;
      const current = await context();
      if (current) return false; // Never reset an active path or candidate.
      const ctx = {taskId: owner.taskId, tabId: owner.tabId, origin: owner.origin, plan,
        route: "direct", publisherState: "accessible", navigationUrl: null,
        previousUrl: "", navigationTime: null, navigationEpoch: 0, reportedEpoch: 0, committedTime: null,
        navigationSequences: [], navigationOverflow: false, navigationFailure: null,
        pendingNavigation: null, category: "", choices: [],
        candidate: null, ambiguous: false, downloadReported: false, downloadOutcome: null, observedDownloads: [], recordEvidence: null, providerAction: null, userArm: null, taskTabClosed: false};
      return navigate(ctx, plan.direct_url);
    }

    async function publisherStatus(taskId, status) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || !ctx.navigationUrl || ctx.pendingNavigation || ctx.taskId !== taskId || ctx.route !== "direct" ||
          !["accessible", "human_required", "exhausted"].includes(status)) return false;
      // Human waits do not establish unobtainability and never enter fallback.
      if (status !== "exhausted" || ctx.candidate) {
        ctx.publisherState = status;
        if (!await save(ctx)) return false;
        return emit(ctx, "publisher_state", {state: status});
      }
      return navigate(ctx, AcquisitionProtocol.resolverUrl(ctx.plan.doi), {route: "xmu", publisherState: status, category: ""});
    }

    async function storeRecordEvidence(ctx, evidence, providerAction) {
      const write = mutationTail.then(async () => {
        const owner = await loadAuthority();
        const latest = (await chrome.storage.session.get(KEY))[KEY];
        if (!owner || owner.taskId !== ctx.taskId || owner.tabId !== ctx.tabId || owner.origin !== ctx.origin ||
            !latest || latest.taskId !== ctx.taskId || latest.tabId !== ctx.tabId || latest.origin !== ctx.origin ||
            latest.navigationEpoch !== ctx.navigationEpoch || latest.pendingNavigation || latest.candidate ||
            latest.taskTabClosed || sameOwner((await chrome.storage.session.get(TERMINAL_KEY))[TERMINAL_KEY], owner) ||
            latest.providerAction || latest.downloadOutcome || latest.downloadReported || latest.ambiguous ||
            Date.now() - providerAction.actionTime > ACTION_MESSAGE_WINDOW_MS ||
            !verifiedProviderAction(latest, providerAction, evidence)) return false;
        latest.providerAction = providerAction;
        latest.recordEvidence = evidence;
        latest.userArm = null;
        // Retain only bounded known IDs. Each must still prove transport,
        // referrer and start >= actionTime when reread; no retroactive trust.
        latest.observedDownloads = (latest.observedDownloads || []).map(o => ({id: o.id, ...downloadExpectation(latest)}));
        await chrome.storage.session.set({[KEY]: latest});
        return structuredClone(latest);
      });
      mutationTail = write.catch(() => {});
      return write;
    }

    function sameTaskRecord(ctx, url) {
      return ctx.route === "xmu" && ctx.publisherState === "exhausted" &&
        ["FullText", "SmartLinks"].includes(ctx.category) &&
        AcquisitionProtocol.sameEbscoRecordPage(ctx.navigationUrl, url);
    }

    function verifiedProviderAction(ctx, proof, evidence) {
      return !ctx.ambiguous && proof && sameTaskRecord(ctx, proof.recordUrl) &&
        proof.taskId === ctx.taskId && proof.tabId === ctx.tabId && proof.origin === ctx.origin &&
        proof.recordUrl === ctx.navigationUrl && proof.navigationTime === ctx.navigationTime &&
        proof.navigationEpoch === ctx.navigationEpoch && proof.doi === ctx.plan.doi &&
        Number.isSafeInteger(proof.actionTime) && proof.actionTime >= ctx.navigationTime &&
        proof.actionTime <= Date.now() && evidence?.recordUrl === proof.recordUrl &&
        evidence.actionTime === proof.actionTime && evidence.observed_doi === ctx.plan.doi;
    }

    async function ebscoMessage(message, sender) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || !ctx.navigationUrl || ctx.pendingNavigation || ctx.route !== "xmu" || ctx.publisherState !== "exhausted" ||
          !["FullText", "SmartLinks"].includes(ctx.category) ||
          ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome || sender.id !== chrome.runtime.id || sender.frameId !== 0 ||
          sender.tab?.id !== ctx.tabId || !sameTaskRecord(ctx, sender.url)) return {ok: false};
      const tab = await chrome.tabs.get(ctx.tabId);
      if (!sameTaskRecord(ctx, tab.url) || tab.incognito) return {ok: false};
      if (message.type === "ebsco_context") return {task_id: ctx.taskId, doi: ctx.plan.doi, navigation_epoch: ctx.navigationEpoch};
      // Once verified, the first action remains frozen through preparation.
      // Repeated content messages cannot renew its authority or deadline.
      if (ctx.providerAction || ctx.candidate?.providerAction) return {ok: false};
      const record = message.record;
      if (message.type !== "ebsco_pdf_action" || message.task_id !== ctx.taskId || !sameTaskRecord(ctx, message.pageUrl) ||
          !AcquisitionProtocol.boundedPayload(message) ||
          Object.keys(message).sort().join(",") !== "action_time,navigation_epoch,pageUrl,record,task_id,type" ||
          message.navigation_epoch !== ctx.navigationEpoch || !Number.isSafeInteger(message.action_time) || message.action_time < ctx.navigationTime ||
          message.action_time > Date.now() || Date.now() - message.action_time > ACTION_MESSAGE_WINDOW_MS ||
          !record || Object.keys(record).join(",") !== "doi" ||
          record.doi !== ctx.plan.doi) return {ok: false};
      const evidence = {observed_doi: record.doi, recordUrl: ctx.navigationUrl, actionTime: message.action_time};
      const providerAction = {taskId: ctx.taskId, tabId: ctx.tabId, origin: ctx.origin,
        doi: ctx.plan.doi, recordUrl: ctx.navigationUrl, navigationTime: ctx.navigationTime,
        navigationEpoch: ctx.navigationEpoch, id: crypto.randomUUID(), actionTime: message.action_time};
      const current = await storeRecordEvidence(ctx, evidence, providerAction);
      if (!current) return {ok: false};
      // An action may arrive after onCreated. Only these bounded observed IDs
      // can be reconsidered, with the same exact-ID onChanged path.
      for (const observed of current.observedDownloads) await changedDownload(observed.id, observed);
      return {ok: true};
    }

    async function resolverMessage(message, sender) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || !ctx.navigationUrl || ctx.pendingNavigation || ctx.route !== "xmu" ||
          sender.id !== chrome.runtime.id || sender.frameId !== 0 || sender.tab?.id !== ctx.tabId ||
          !AcquisitionProtocol.resolverContext(sender.url, ctx.plan.doi) ||
          !AcquisitionProtocol.sameResolverObservationContext(ctx.navigationUrl, sender.url, ctx.plan.doi)) return {ok: false};
      if (message.type === "resolver_context") return {task_id: ctx.taskId, doi: ctx.plan.doi};
      if (message.type !== "resolver_observation" ||
          !AcquisitionProtocol.sameResolverObservationContext(sender.url, message.pageUrl, ctx.plan.doi) ||
          typeof message.human_required !== "boolean" || !Array.isArray(message.choices) ||
          message.choices.length > 6 || !AcquisitionProtocol.boundedPayload(message)) return {ok: false};
      if (message.human_required) {
        await emit(ctx, "human_action_needed", {route: "xmu"});
        return {ok: true};
      }
      if (message.unavailable !== null) {
        if (!["not_ready", "choice_overflow"].includes(message.unavailable)) return {ok: false};
        await emit(ctx, "browser_path_failure", {reason: "resolver_" + message.unavailable});
        return {ok: false};
      }
      const choices = message.choices;
      if (choices.some(c => !["FullText", "SmartLinks"].includes(c.category) ||
          !AcquisitionProtocol.safeUrl(c.target) || typeof c.label !== "string" || c.label.length > 80)) return {ok: false};
      ctx.choices = choices.map(c => ({category: c.category, target: c.target, label: c.label}));
      if (!await save(ctx)) return {ok: false};
      if (choices.length === 1) {
        try {
          if (await choose(ctx.taskId, 0)) return {ok: true};
        } catch { /* Same terminal failure semantics for the single choice. */ }
        const current = await context();
        if (!current || current.navigationEpoch !== ctx.navigationEpoch) return {ok: false};
        await emit(ctx, "browser_path_failure", {reason: "navigation_failed"});
        return {ok: false};
      }
      else if (choices.length > 1) await emitChoices(ctx);
      else await emit(ctx, "browser_path_failure", {reason: "no_visible_eligible_choices"});
      return {ok: true};
    }

    async function emitChoices(ctx) {
      return emit(ctx, "resolver_choices", {choices: ctx.choices.map((c, id) => ({id, category: c.category, label: c.label}))});
    }

    async function choose(taskId, choiceId) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || !ctx.navigationUrl || ctx.pendingNavigation || ctx.taskId !== taskId || ctx.route !== "xmu" || ctx.publisherState !== "exhausted" ||
          !Number.isSafeInteger(choiceId) || choiceId < 0 || !ctx.choices[choiceId] || ctx.candidate) return false;
      const choice = ctx.choices[choiceId];
      return navigate(ctx, choice.target, {category: choice.category});
    }

    async function observeNavigationStart(details) {
      const url = AcquisitionProtocol.safeUrl(details.url);
      if (details.frameId !== 0 || !url ||
          !Number.isFinite(details.timeStamp) || details.timeStamp < 0) return;
      // Association only: no committed URL, epoch, evidence or app notification.
      await save(null, {navigationStart: {...details, url}});
    }

    async function observeNavigation(details) {
      if (details.frameId !== 0 ||
          !Number.isFinite(details.timeStamp) || details.timeStamp < 0) return;
      const url = AcquisitionProtocol.safeUrl(details.url);
      if (!url) return;
      if (!await save(null, {commit: {...details, url}})) return;
      await reconcileNavigation();
    }

    async function armUserDownload(tab) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || !ctx.navigationUrl || ctx.pendingNavigation || tab.id !== ctx.tabId || tab.incognito || ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome || ctx.candidate ||
          !AcquisitionProtocol.safeUrl(tab.url) || (tab.url !== ctx.navigationUrl && !sameTaskRecord(ctx, tab.url))) return false;
      // Research record downloads require a resolver-approved category even
      // when the current URL is byte-identical to the task navigation URL.
      if (AcquisitionProtocol.ebscoRecordContext(tab.url) && !sameTaskRecord(ctx, tab.url)) return false;
      const liveTab = await chrome.tabs.get(ctx.tabId);
      if (liveTab.id !== ctx.tabId || liveTab.incognito || (liveTab.url !== ctx.navigationUrl && !sameTaskRecord(ctx, liveTab.url))) return false;
      // A human toolbar gesture opens one short attribution window for a
      // publisher/PDF-viewer download.
      if (ctx.route === "xmu") {
        if (ctx.providerAction) return false;
        ctx.recordEvidence = null;
      }
      ctx.userArm = {id: crypto.randomUUID(), navigationEpoch: ctx.navigationEpoch,
        navigationUrl: ctx.navigationUrl, armedAt: Date.now()};
      ctx.observedDownloads = [];
      if (!await save(ctx)) return false;
      await chrome.action.setBadgeText({tabId: ctx.tabId, text: "D"});
      return true;
    }

    async function navigationFailed(details) {
      const url = AcquisitionProtocol.safeUrl(details.url);
      if (details.frameId !== 0 || !url || !Number.isFinite(details.timeStamp) || details.timeStamp < 0) return;
      // Enqueue before any awaits so before/error/commit callbacks share the
      // same session order, including immediate browser callbacks.
      const failed = await save(null, {navigationError: {...details, url}});
      if (!failed?.navigationFailure || details.error === "net::ERR_ABORTED") return;
      // Finish observations already queued in this callback batch before
      // reporting failure; a superseding commit clears the failure token.
      await mutationTail;
      const ctx = await context();
      if (!ctx || ctx.pendingNavigation || ctx.navigationEpoch !== failed.navigationFailure.epoch ||
          ctx.navigationFailure?.id !== failed.navigationFailure.id) return;
      // A transport failure is not proof of publisher exhaustion or auth state.
      await emit(ctx, "browser_path_failure", {reason: "navigation_failed"});
    }

    function ebscoBlobRootReferrer(ctx, transport, referrer) {
      return transport.kind === "blob" && sameTaskRecord(ctx, ctx.navigationUrl) &&
        transport.origin === new URL(ctx.navigationUrl).origin && referrer === transport.origin + "/";
    }

    async function uniqueTaskSource(ctx, item, arm = ctx.userArm, armedOnly = false,
        providerAction = ctx.providerAction, evidence = ctx.recordEvidence, closedCandidate = false) {
      if (!ctx.navigationUrl || ctx.pendingNavigation) return null;
      const started = Date.parse(item.startTime), transport = AcquisitionProtocol.downloadTransport(item);
      if (!Number.isFinite(started) || started > Date.now() || item.incognito ||
          item.byExtensionId && item.byExtensionId !== chrome.runtime.id || !transport) return null;
      const blob = transport.kind === "blob";
      if (blob && transport.origin !== new URL(ctx.navigationUrl).origin) return null;
      if (blob && sameTaskRecord(ctx, ctx.navigationUrl)) {
        if (!verifiedProviderAction(ctx, providerAction, evidence) || started < providerAction.actionTime ||
            started - providerAction.actionTime > PROVIDER_PREPARATION_WINDOW_MS ||
            item.referrer && !sameTaskRecord(ctx, item.referrer) && !ebscoBlobRootReferrer(ctx, transport, item.referrer)) return null;
        if (!closedCandidate) {
          const tab = await chrome.tabs.get(ctx.tabId);
          if (tab.id !== ctx.tabId || tab.incognito || !sameTaskRecord(ctx, tab.url)) return null;
        }
        return {navigationTime: providerAction.navigationTime, userArm: null, providerAction: {...providerAction}};
      }
      if (providerAction) return null;
      const exactSource = !blob && !armedOnly && [item.url, item.finalUrl].includes(ctx.navigationUrl) &&
        started >= ctx.navigationTime && started - ctx.navigationTime <= ARM_WINDOW_MS &&
        (!item.referrer || [ctx.navigationUrl, ctx.previousUrl].includes(item.referrer));
      const armedSource = arm && arm.navigationEpoch === ctx.navigationEpoch && arm.navigationUrl === ctx.navigationUrl &&
        (item.referrer === arm.navigationUrl || blob && !item.referrer) && Number.isFinite(arm.armedAt) &&
        started >= arm.armedAt && started - arm.armedAt <= ARM_WINDOW_MS;
      if (!exactSource && !armedSource) return null;
      const useArm = !exactSource;
      if (!closedCandidate) {
        const tab = await chrome.tabs.get(ctx.tabId);
        if (tab.id !== ctx.tabId || tab.incognito ||
            (useArm ? tab.url !== arm.navigationUrl : ![ctx.navigationUrl, ctx.previousUrl].includes(tab.url))) return null;
      }
      return {navigationTime: useArm ? arm.armedAt : ctx.navigationTime,
        userArm: useArm ? {...arm} : null, providerAction: null};
    }

    function downloadExpectation(ctx) {
      if (ctx.candidate?.expectation) return ctx.candidate.expectation;
      const arm = ctx.candidate ? ctx.candidate.userArm : ctx.userArm;
      const provider = ctx.candidate ? ctx.candidate.providerAction : ctx.providerAction;
      return {epoch: ctx.navigationEpoch, armId: arm?.id ?? null, armedAt: arm?.armedAt ?? null, providerId: provider?.id ?? null};
    }

    async function downloadCompatibility(ctx, item, observation = null) {
      if (!item || !Number.isSafeInteger(item.id) || item.id < 0 || !ctx.navigationUrl || ctx.pendingNavigation ||
          item.incognito === true || item.exists === false || item.state === "interrupted" ||
          item.byExtensionId && item.byExtensionId !== chrome.runtime.id) return {status: "incompatible"};
      const candidate = ctx.candidate;
      const arm = candidate ? candidate.userArm : ctx.userArm;
      const providerAction = candidate ? candidate.providerAction : ctx.providerAction;
      const evidence = candidate ? candidate.recordEvidence : ctx.recordEvidence;
      if (candidate?.ownership === "extension_id" || candidate && candidate.navigationEpoch !== ctx.navigationEpoch) return {status: "incompatible"};
      for (const value of [item.url, item.finalUrl]) {
        if (value && !AcquisitionProtocol.safeUrl(value) &&
            !AcquisitionProtocol.downloadTransport({url: value, finalUrl: value})) return {status: "incompatible"};
      }
      const started = Date.parse(item.startTime);
      if (item.startTime && (!Number.isFinite(started) || started > Date.now())) return {status: "incompatible"};
      const providerRecord = sameTaskRecord(ctx, ctx.navigationUrl);
      const blobUrl = [item.url, item.finalUrl].find(url => typeof url === "string" && url.startsWith("blob:"));
      if (providerRecord && (!item.url || !item.finalUrl || blobUrl)) {
        if (blobUrl && new URL(blobUrl).origin !== new URL(ctx.navigationUrl).origin ||
            item.referrer && !sameTaskRecord(ctx, item.referrer) && item.referrer !== new URL(ctx.navigationUrl).origin + "/" ||
            Number.isFinite(started) && started < ctx.navigationTime) return {status: "incompatible"};
        if (!providerAction) return {status: "incomplete"};
      }
      if (providerAction) {
        const observed = observation || ctx.observedDownloads?.find(o => o.id === item.id);
        const expectation = downloadExpectation(ctx);
        const bound = candidate?.id === item.id || observed?.id === item.id &&
          ["epoch", "armId", "armedAt", "providerId"].every(key => observed[key] === expectation[key]);
        // Actual start time governs eligibility. Wall time only limits first
        // admission without that evidence; a bound ID may reconcile it later.
        if (!verifiedProviderAction(ctx, providerAction, evidence) ||
            (Number.isFinite(started) ? started < providerAction.actionTime ||
              started - providerAction.actionTime > PROVIDER_PREPARATION_WINDOW_MS
              : !bound && Date.now() - providerAction.actionTime > PROVIDER_PREPARATION_WINDOW_MS)) return {status: "incompatible"};
      }
      const exactPossible = Number.isFinite(started) ? started >= ctx.navigationTime && started - ctx.navigationTime <= ARM_WINDOW_MS
        : Date.now() - ctx.navigationTime <= ARM_WINDOW_MS;
      const armedPossible = arm && arm.navigationEpoch === ctx.navigationEpoch &&
        (!Number.isFinite(started) || started >= arm.armedAt && started - arm.armedAt <= ARM_WINDOW_MS);
      if (!providerAction && !providerRecord && !exactPossible && !armedPossible) return {status: "incompatible"};
      if (item.referrer && ![ctx.navigationUrl, ctx.previousUrl].includes(item.referrer) &&
          !((arm || providerAction) && (sameTaskRecord(ctx, item.referrer) || item.referrer === new URL(ctx.navigationUrl).origin + "/" &&
            ctx.route === "xmu" && sameTaskRecord(ctx, ctx.navigationUrl)))) return {status: "incompatible"};
      if (!item.url || !item.finalUrl || !item.startTime || typeof item.referrer !== "string" ||
          typeof item.incognito !== "boolean" || !item.mime || !["in_progress", "complete"].includes(item.state)) return {status: "incomplete"};
      const proof = candidate ? await uniqueTaskSource(ctx, item, arm, Boolean(arm), providerAction, evidence)
        : await uniqueTaskSource(ctx, item);
      return proof ? {status: "compatible", proof} : {status: "incompatible"};
    }

    async function observeDownload(ctx, item, reread = false, retry = true, observation = null) {
      const result = await downloadCompatibility(ctx, item, observation);
      const expectation = downloadExpectation(ctx);
      if (result.status !== "compatible") {
        if (result.status === "incompatible" && !ctx.observedDownloads?.some(o => o.id === item.id)) return;
        const saved = await save(ctx, {downloadChange: {type: result.status === "incomplete" ? "observe" : "discard",
          id: item.id, expectation}});
        if (!saved && retry) {
          const current = await context();
          if (current?.taskId === ctx.taskId && current.navigationEpoch === ctx.navigationEpoch &&
              current.providerAction && !ctx.providerAction) await observeDownload(current, item, reread, false, observation);
        }
        return;
      }
      const proof = result.proof;
      const transport = AcquisitionProtocol.downloadTransport(item);
      const candidate = {id: item.id, ownership: proof.providerAction ? "provider_action" : "task_navigation", navigationUrl: ctx.navigationUrl,
        previousUrl: ctx.previousUrl,
        navigationEpoch: ctx.navigationEpoch, navigationTime: proof.navigationTime, expectation,
        transport: {kind: transport.kind, origin: transport.origin || null},
        userArm: proof.userArm, recordEvidence: ctx.recordEvidence, providerAction: proof.providerAction};
      const changed = await save(ctx, {downloadChange: {type: "claim", id: item.id, expectation, candidate}});
      if (!changed) return;
      if (changed.ambiguous) await reconcileDownloadOutcome();
      else await completed(item.id, reread ? item : undefined);
    }

    async function created(item) {
      if (!Number.isSafeInteger(item.id) || item.id < 0 || item.byExtensionId === chrome.runtime.id) return;
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome || ctx.candidate?.ownership === "extension_id") return;
      await observeDownload(ctx, item);
    }

    async function changedDownload(id, acceptedObservation = null) {
      if (!Number.isSafeInteger(id) || id < 0) return;
      const ctx = await context();
      if (!ctx || ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome) return;
      if (ctx.taskTabClosed && ctx.candidate?.id !== id) return;
      // A newly accepted action carries its bounded ID snapshot through the
      // first candidate freeze, so another known compatible ID still conflicts.
      // Only this same provider expectation may use that internal snapshot.
      const expectation = downloadExpectation(ctx);
      const accepted = acceptedObservation?.id === id && acceptedObservation.providerId &&
        ["epoch", "armId", "armedAt", "providerId"].every(key => acceptedObservation[key] === expectation[key]);
      const observed = ctx.observedDownloads?.find(o => o.id === id) || (accepted ? acceptedObservation : null);
      if (ctx.candidate?.id !== id && !observed) return;
      const items = await chrome.downloads.search({id});
      const item = items.length === 1 && items[0].id === id ? items[0] : null;
      if (ctx.candidate?.id === id) return completed(id, item);
      if (!item) return save(ctx, {downloadChange: {type: "discard", id, expectation: downloadExpectation(ctx)}});
      if (["epoch", "armId", "armedAt", "providerId"].some(key => observed[key] !== expectation[key])) return;
      await observeDownload(ctx, item, true, true, observed);
    }

    async function download(taskId, target) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || !ctx.navigationUrl || ctx.pendingNavigation || ctx.taskId !== taskId || ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome || ctx.candidate ||
          AcquisitionProtocol.safeUrl(target) !== ctx.navigationUrl) return false;
      const tab = await chrome.tabs.get(ctx.tabId);
      if (tab.incognito || tab.url !== ctx.navigationUrl) return false;
      // Reservation precedes the Chrome call; never set cookies, headers or path.
      ctx.candidate = {id: null, ownership: "extension_id", navigationUrl: ctx.navigationUrl,
        previousUrl: ctx.previousUrl,
        navigationTime: Date.now(), recordEvidence: ctx.recordEvidence, transport: {kind: "http", origin: null}};
      ctx.observedDownloads = [];
      if (!await save(ctx)) return false;
      try {
        const id = await chrome.downloads.download({url: ctx.navigationUrl, saveAs: true, conflictAction: "uniquify"});
        const current = await context();
        if (!current || current.taskId !== ctx.taskId || current.tabId !== ctx.tabId || current.candidate?.id !== null) return false;
        if (!Number.isSafeInteger(id) || id < 0) throw new Error("Invalid download ID");
        current.candidate.id = id;
        current.userArm = null;
        if (!await save(current)) return false;
        await completed(id);
        return true;
      } catch {
        const current = await context();
        if (current?.taskId === ctx.taskId && current.candidate?.id === null) {
          current.candidate = null;
          await save(current);
        }
        return false;
      }
    }

    async function completed(id, suppliedItem = undefined) {
      const ctx = await context();
      if (!ctx || ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome || ctx.candidate?.id !== id) return;
      const items = suppliedItem === undefined ? await chrome.downloads.search({id}) : [suppliedItem];
      const item = items.length === 1 && items[0]?.id === id ? items[0] : null;
      if (!item || item.state === "interrupted" || item.exists === false) {
        const failed = await save(ctx, {downloadChange: {type: "unavailable", id, expectation: downloadExpectation(ctx)}});
        if (failed) await reconcileDownloadOutcome();
        return;
      }
      if (item.state !== "complete") return;
      const candidate = ctx.candidate;
      if (candidate.ownership === "extension_id") {
        if (item.byExtensionId !== chrome.runtime.id || item.url !== candidate.navigationUrl) return;
        const started = Date.parse(item.startTime);
        if (ctx.taskTabClosed && (!Number.isFinite(started) || started > Date.now() ||
            started < candidate.navigationTime || started - candidate.navigationTime > ARM_WINDOW_MS)) return;
      } else {
        if (candidate.userArm && ctx.navigationUrl !== candidate.navigationUrl) return;
        if (!await uniqueTaskSource({...ctx, navigationUrl: candidate.navigationUrl,
            previousUrl: ctx.taskTabClosed ? candidate.previousUrl : ctx.previousUrl,
            navigationTime: candidate.providerAction ? ctx.navigationTime : candidate.navigationTime},
            item, candidate.userArm, Boolean(candidate.userArm), candidate.providerAction, candidate.recordEvidence,
            ctx.taskTabClosed === true)) return;
      }
      const transport = AcquisitionProtocol.downloadTransport(item);
      if (ctx.taskTabClosed && (item.incognito !== false || typeof item.referrer !== "string" ||
          !transport || candidate.transport?.kind !== transport.kind ||
          candidate.transport.origin !== (transport.origin || null))) return;
      if (!transport || (transport.kind === "blob" && !candidate.userArm && !candidate.providerAction) || typeof item.mime !== "string" || !item.mime ||
          typeof item.filename !== "string" ||
          !item.filename.startsWith("/") || item.filename.length > 512 ||
          !Number.isSafeInteger(item.fileSize) || item.fileSize <= 0 ||
          !Number.isSafeInteger(item.totalBytes) || item.totalBytes < 0) return;
      const recordEvidence = candidate.recordEvidence;
      if (transport.kind === "blob" && ctx.route === "xmu") {
        // The PDF action must bind this exact record and precede download start.
        if (!recordEvidence?.recordUrl) return;
        const started = Date.parse(item.startTime);
        if (recordEvidence.recordUrl !== candidate.navigationUrl ||
            !Number.isSafeInteger(recordEvidence.actionTime) ||
            recordEvidence.actionTime < (candidate.providerAction ? candidate.providerAction.navigationTime : candidate.userArm.armedAt) ||
            recordEvidence.actionTime > started || started - recordEvidence.actionTime >
              (candidate.providerAction ? PROVIDER_PREPARATION_WINDOW_MS : ARM_WINDOW_MS)) return;
      }
      // Verified same-record SPA and EBSCO blob root referrers use the task's stable identity;
      // generic HTTP referrers retain byte equality and their original value.
      const referrer = transport.kind === "blob" && item.referrer &&
        (sameTaskRecord(ctx, item.referrer) || ebscoBlobRootReferrer(ctx, transport, item.referrer))
        ? candidate.navigationUrl : item.referrer || "";
      const payload = {doi: ctx.plan.doi, download_id: id, route: ctx.route, ownership: candidate.ownership,
        navigation_url: candidate.navigationUrl, path: item.filename, url: transport.url,
        final_url: transport.finalUrl, referrer, mime: item.mime.slice(0, 128),
        total_bytes: item.totalBytes, file_size: item.fileSize, state: "complete", category: ctx.category,
        observed_doi: recordEvidence?.observed_doi || null,
        navigation_time: candidate.navigationTime, start_time: Date.parse(item.startTime)};
      if (transport.kind === "blob") {
        payload.transport_kind = "blob";
        payload.download_origin = transport.origin;
        payload.ownership = candidate.providerAction ? "provider_action" : "user_arm";
        payload.provider_record_url = recordEvidence?.recordUrl || null;
      }
      if (candidate.providerAction) {
        payload.attribution = "ebsco_pdf_action";
        payload.navigation_time = candidate.providerAction.navigationTime;
        payload.action_time = candidate.providerAction.actionTime;
      }
      if (!AcquisitionProtocol.boundedPayload(payload)) return;
      // One candidate, no history/replay queue. Source remains entirely user-owned.
      const finished = await save(ctx, {downloadChange: {type: "complete", id, expectation: downloadExpectation(ctx), payload}});
      if (finished) await reconcileDownloadOutcome();
    }

    async function reconcileDownloadOutcome() {
      if (downloadDelivery) {
        downloadDeliveryRequested = true;
        return downloadDelivery;
      }
      const delivery = (async () => {
        const ctx = await context();
        const outcome = ctx?.downloadOutcome;
        if (!outcome) return false;
        // One frozen bounded outcome; only an authenticated acknowledgement
        // settles it. Busy/reply/transport failure leaves session evidence intact.
        if (!await emit(ctx, outcome.eventType, outcome.payload, true)) return false;
        const write = mutationTail.then(async () => {
          const owner = await loadAuthority();
          const latest = (await chrome.storage.session.get(KEY))[KEY];
          if (!owner || owner.taskId !== ctx.taskId || owner.tabId !== ctx.tabId || owner.origin !== ctx.origin ||
              !latest || latest.taskId !== ctx.taskId || latest.tabId !== ctx.tabId || latest.origin !== ctx.origin ||
              latest.downloadOutcome?.id !== outcome.id) return false;
          // Navigation may have changed while delivery waited. Acknowledgement
          // belongs to this frozen outcome ID, not to a later navigation epoch.
          latest.downloadOutcome = null;
          latest.downloadReported = true;
          await chrome.storage.session.set({[KEY]: latest});
          return true;
        });
        mutationTail = write.catch(() => {});
        return write;
      })();
      downloadDelivery = delivery;
      try { return await delivery; }
      finally {
        downloadDelivery = null;
        const requested = downloadDeliveryRequested;
        downloadDeliveryRequested = false;
        if (requested) void reconcileDownloadOutcome().catch(() => {});
      }
    }

    async function retireDownloads(owner) {
      // Serialize with START and never erase a replacement task/owner.
      const write = mutationTail.then(async () => {
        if (await loadAuthority()) return false;
        const terminal = (await chrome.storage.session.get(TERMINAL_KEY))[TERMINAL_KEY];
        if (sameOwner(terminal, owner)) await chrome.storage.session.remove(TERMINAL_KEY);
        const latest = (await chrome.storage.session.get(KEY))[KEY];
        if (!latest || latest.taskId !== owner.taskId || latest.tabId !== owner.tabId || latest.origin !== owner.origin) return false;
        latest.observedDownloads = [];
        latest.userArm = null;
        latest.candidate = null;
        latest.downloadOutcome = null;
        latest.providerAction = null;
        latest.recordEvidence = null;
        latest.pendingNavigation = null;
        latest.navigationSequences = [];
        await chrome.storage.session.set({[KEY]: latest});
        return true;
      });
      mutationTail = write.catch(() => {});
      return write;
    }

    async function taskUi(taskId) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || ctx.taskId !== taskId) return null;
      return {task_id: ctx.taskId,
        can_fallback: Boolean(ctx.navigationUrl && !ctx.pendingNavigation && ctx.route === "direct" && !ctx.candidate && !ctx.ambiguous && !ctx.downloadReported && !ctx.downloadOutcome),
        choices: ctx.choices.map((c, id) => ({id, category: c.category, label: c.label})),
        candidate: Boolean(ctx.candidate), ambiguous: ctx.ambiguous};
    }

    async function userAction(taskId, action, choiceId, tab) {
      const ctx = await context();
      if (!ctx || ctx.taskTabClosed || !ctx.navigationUrl || ctx.pendingNavigation || ctx.taskId !== taskId || tab.id !== ctx.tabId || tab.incognito || ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome) return false;
      const live = await chrome.tabs.get(ctx.tabId);
      if (live.id !== ctx.tabId || (live.url !== ctx.navigationUrl && !sameTaskRecord(ctx, live.url)) || live.incognito) return false;
      if (action === "arm") return armUserDownload(live);
      if (ctx.candidate) return false;
      if (action === "fallback") {
        if (ctx.route !== "direct") return false;
        return emit(ctx, "publisher_fallback_request", {});
      }
      if (action === "choose") {
        if (ctx.route !== "xmu" || !Number.isSafeInteger(choiceId) || !ctx.choices[choiceId]) return false;
        return emit(ctx, "resolver_choice_request", {choice_id: choiceId});
      }
      if (action === "download") return emit(ctx, "user_download_request", {});
      return false;
    }

    async function executeCommand(raw, taskId) {
      const value = AcquisitionProtocol.command(raw, taskId);
      const owner = await loadAuthority();
      if (!value || !owner || owner.taskId !== taskId || await isTabClosed(owner)) return false;
      if (value.type === "START") {
        const ctx = await context();
        if (ctx) return JSON.stringify(ctx.plan) === JSON.stringify(value.plan);
        return start(value.plan);
      }
      const ctx = await context();
      if (!ctx || ctx.taskId !== taskId) return false;
      if (value.type === "PUBLISHER_EXHAUSTED") {
        return publisherStatus(taskId, "exhausted");
      }
      if (value.type === "CHOOSE") return choose(taskId, value.choice_id);
      if (value.type === "DOWNLOAD_CURRENT") return download(taskId, ctx.navigationUrl);
      return false;
    }

    chrome.webNavigation.onBeforeNavigate.addListener(d => { void observeNavigationStart(d).catch(() => {}); });
    // Commits and download claims share session mutation ordering; browser
    // and network awaits never hold the session writer.
    chrome.webNavigation.onCommitted.addListener(d => { void observeNavigation(d).catch(() => {}); });
    chrome.webNavigation.onErrorOccurred.addListener(d => { void navigationFailed(d).catch(() => {}); });
    chrome.downloads.onCreated.addListener(item => { void created(item).catch(() => {}); });
    chrome.downloads.onChanged.addListener(delta => { void changedDownload(delta.id).catch(() => {}); });
    chrome.runtime.onMessage.addListener((message, sender, respond) => {
      if (["ebsco_context", "ebsco_pdf_action"].includes(message?.type)) {
        ebscoMessage(message, sender).then(respond, () => respond({ok: false}));
        return true;
      }
      if (!["resolver_context", "resolver_observation"].includes(message?.type)) return false;
      resolverMessage(message, sender).then(respond, () => respond({ok: false}));
      return true;
    });

    const command = fn => async (...args) => {
      try { return await fn(...args); } catch { return false; }
    };
    return Object.freeze({start: command(start), publisherStatus: command(publisherStatus),
      choose: command(choose), download: command(download),
      taskUi: command(taskUi),
      persistClosedClaim: command(persistClosedClaim),
      tabRemoved: command(tabRemoved), isTabClosed: command(isTabClosed),
      reconcileTerminal: command(reconcileTerminal), reconcileClosedDownload: command(reconcileClosedDownload),
      userAction: command(userAction), executeCommand: command(executeCommand),
      reconcileNavigation: command(reconcileNavigation), reconcileDownloadOutcome: command(reconcileDownloadOutcome), retireDownloads: command(retireDownloads)});
  }

  return Object.freeze({create});
})();
