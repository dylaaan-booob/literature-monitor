"use strict";

// A7 browser primitives. A8 will deliver app-authenticated commands to this API.
// None of these commands are exposed to arbitrary page/content-script messages.
const BrowserAcquisitionRuntime = (() => {
  const KEY = "browserAcquisition";
  const RECORD_KEY = "browserEbscoRecord";
  const ARM_WINDOW_MS = 10000;
  const PROVIDER_PREPARATION_WINDOW_MS = 120000;
  let operation = false;
  let overlappingDownload = false;

  function create({loadAuthority, emitAuthenticated}) {
    const snapshots = new WeakMap();
    let mutationTail = Promise.resolve();
    const MUTABLE_FIELDS = ["route", "publisherState", "navigationUrl", "previousUrl", "navigationTime",
      "category", "choices", "candidate", "ambiguous", "recordEvidence", "userArm"];
    async function context() {
      const owner = await loadAuthority();
      const value = (await chrome.storage.session.get(KEY))[KEY];
      if (!owner || !value || value.taskId !== owner.taskId || value.tabId !== owner.tabId ||
          value.origin !== owner.origin || !AcquisitionProtocol.plan(value.plan)) return null;
      snapshots.set(value, structuredClone(value));
      const record = (await chrome.storage.session.get(RECORD_KEY))[RECORD_KEY];
      if (record?.taskId === value.taskId && record.tabId === value.tabId && record.origin === value.origin &&
          record.evidence.recordUrl === value.navigationUrl) {
        value.recordEvidence = record.evidence;
        const arm = value.userArm || value.candidate?.userArm;
        if (verifiedProviderAction(value, record.providerAction, arm, record.evidence)) {
          value.providerAction = record.providerAction;
          if (value.candidate?.userArm) value.candidate.providerAction = record.providerAction;
        }
        if (value.candidate?.userArm && value.candidate.navigationUrl === record.evidence.recordUrl) {
          value.candidate.recordEvidence = record.evidence;
        }
      }
      if (overlappingDownload) value.ambiguous = true;
      // This proof is derived only from the independently validated PDF action.
      // A plain arm retains its original short expiry.
      if (value.userArm && (value.ambiguous || value.userArm.navigationUrl !== value.navigationUrl ||
          (value.providerAction ? Date.now() - value.providerAction.actionTime > PROVIDER_PREPARATION_WINDOW_MS
            : Date.now() - value.userArm.armedAt > ARM_WINDOW_MS))) {
        value.userArm = null;
        await save(value);
      }
      return value;
    }

    async function save(ctx, {navigationAccepted = false} = {}) {
      const before = snapshots.get(ctx);
      const changes = Object.fromEntries(MUTABLE_FIELDS.filter(key =>
        !before || JSON.stringify(ctx[key]) !== JSON.stringify(before[key]))
        .map(key => [key, structuredClone(ctx[key])]));
      // Serialize only verified session read/modify/write work. Browser calls
      // and authenticated network events never hold this critical section.
      const write = mutationTail.then(async () => {
        const owner = await loadAuthority();
        if (!owner || owner.taskId !== ctx.taskId || owner.tabId !== ctx.tabId || owner.origin !== ctx.origin) return false;
        const latest = (await chrome.storage.session.get(KEY))[KEY];
        if (before) {
          if (!latest || latest.taskId !== ctx.taskId || latest.tabId !== ctx.tabId || latest.origin !== ctx.origin ||
              !AcquisitionProtocol.plan(latest.plan) || JSON.stringify(latest.plan) !== JSON.stringify(ctx.plan)) return false;
        } else if (latest?.taskId === ctx.taskId) return false; // Never reset an existing task.
        // A newly claimed task may replace retired task state; stale transitions
        // from that retired task still fail the authority check above.
        const next = before ? latest : structuredClone(ctx);
        const moved = before && next.navigationUrl !== before.navigationUrl;
        if (moved && !Object.hasOwn(changes, "navigationUrl") &&
            ["candidate", "recordEvidence", "userArm"].some(key => Object.hasOwn(changes, key))) return false;
        // A browser navigation may have advanced again while a commanded
        // tabs.update was being accepted. Do not restore its earlier URL.
        if (navigationAccepted && moved && next.navigationUrl !== ctx.navigationUrl) {
          for (const key of ["navigationUrl", "previousUrl", "navigationTime"]) delete changes[key];
        } else if (Object.hasOwn(changes, "navigationUrl") && next.navigationUrl !== changes.navigationUrl) {
          changes.previousUrl = next.navigationUrl;
        }
        Object.assign(next, changes);
        await chrome.storage.session.set({[KEY]: next});
        Object.assign(ctx, next);
        snapshots.set(ctx, structuredClone(next));
        return true;
      });
      mutationTail = write.catch(() => {});
      return write;
    }

    async function emit(ctx, type, payload) {
      const owner = await loadAuthority();
      if (!owner || owner.taskId !== ctx.taskId || owner.tabId !== ctx.tabId || owner.origin !== ctx.origin ||
          !AcquisitionProtocol.boundedPayload(payload)) return false;
      if (type === "download_candidate" && overlappingDownload) return false;
      return emitAuthenticated(owner, type, payload);
    }

    async function navigate(ctx, target) {
      const url = AcquisitionProtocol.safeUrl(target);
      const owner = await loadAuthority();
      if (!url || !owner || owner.taskId !== ctx.taskId || owner.tabId !== ctx.tabId || owner.origin !== ctx.origin) return false;
      // The sole authoritative tab identity comes from A6 session authority.
      // Persist only after Chrome accepts: rejected START/fallback/choice must
      // not look like a successful transition on the next command.
      await chrome.tabs.update(ctx.tabId, {url});
      await chrome.storage.session.remove(RECORD_KEY);
      ctx.userArm = null;
      if (ctx.navigationUrl !== url) {
        ctx.previousUrl = ctx.navigationUrl;
        ctx.navigationUrl = url;
        ctx.navigationTime = Date.now();
      }
      return save(ctx, {navigationAccepted: true});
    }

    async function start(rawPlan) {
      const plan = AcquisitionProtocol.plan(rawPlan);
      const owner = await loadAuthority();
      if (!plan || !owner || owner.taskId !== plan.task_id) return false;
      const current = await context();
      if (current) return false; // Never reset an active path or candidate.
      overlappingDownload = false;
      const ctx = {taskId: owner.taskId, tabId: owner.tabId, origin: owner.origin, plan,
        route: "direct", publisherState: "accessible", navigationUrl: plan.direct_url,
        previousUrl: "", navigationTime: Date.now(), category: "", choices: [],
        candidate: null, ambiguous: false, recordEvidence: null, userArm: null};
      return navigate(ctx, plan.direct_url);
    }

    async function publisherStatus(taskId, status) {
      const ctx = await context();
      if (!ctx || ctx.taskId !== taskId || ctx.route !== "direct" ||
          !["accessible", "human_required", "exhausted"].includes(status)) return false;
      ctx.publisherState = status;
      // Human waits do not establish unobtainability and never enter fallback.
      if (status !== "exhausted" || ctx.candidate) {
        if (!await save(ctx)) return false;
        return emit(ctx, "publisher_state", {state: status});
      }
      ctx.route = "xmu";
      ctx.recordEvidence = null;
      if (!await navigate(ctx, AcquisitionProtocol.resolverUrl(ctx.plan.doi))) return false;
      return emit(ctx, "publisher_state", {state: status});
    }

    async function storeRecordEvidence(ctx, value, providerAction = null) {
      // Separate storage preserves record/onCreated interleaves. The action
      // also freezes navigation epoch, so a late store cannot survive reload.
      await chrome.storage.session.set({[RECORD_KEY]: {taskId: ctx.taskId,
        tabId: ctx.tabId, origin: ctx.origin, evidence: structuredClone(value), providerAction}});
      return true;
    }

    function sameTaskRecord(ctx, url) {
      return ctx.route === "xmu" && ctx.publisherState === "exhausted" &&
        ["FullText", "SmartLinks"].includes(ctx.category) &&
        AcquisitionProtocol.sameEbscoRecordPage(ctx.navigationUrl, url);
    }

    function verifiedProviderAction(ctx, proof, arm, evidence) {
      return !ctx.ambiguous && proof && arm && sameTaskRecord(ctx, proof.recordUrl) &&
        proof.taskId === ctx.taskId && proof.tabId === ctx.tabId && proof.origin === ctx.origin &&
        proof.recordUrl === ctx.navigationUrl && proof.navigationTime === ctx.navigationTime &&
        proof.doi === ctx.plan.doi && arm.navigationUrl === ctx.navigationUrl &&
        proof.armedAt === arm.armedAt && Number.isSafeInteger(proof.armedAt) &&
        Number.isSafeInteger(proof.actionTime) && proof.armedAt >= ctx.navigationTime &&
        proof.actionTime >= proof.armedAt && proof.actionTime - proof.armedAt <= ARM_WINDOW_MS &&
        proof.actionTime <= Date.now() && evidence?.recordUrl === proof.recordUrl &&
        evidence.actionTime === proof.actionTime && evidence.observed_doi === ctx.plan.doi;
    }

    async function ebscoMessage(message, sender) {
      const ctx = await context();
      if (!ctx || ctx.route !== "xmu" || ctx.publisherState !== "exhausted" ||
          !["FullText", "SmartLinks"].includes(ctx.category) ||
          ctx.ambiguous || sender.id !== chrome.runtime.id || sender.frameId !== 0 ||
          sender.tab?.id !== ctx.tabId || !sameTaskRecord(ctx, sender.url)) return {ok: false};
      const tab = await chrome.tabs.get(ctx.tabId);
      if (!sameTaskRecord(ctx, tab.url) || tab.incognito) return {ok: false};
      if (message.type === "ebsco_context") return {task_id: ctx.taskId, doi: ctx.plan.doi};
      // Once verified, the first action remains frozen through preparation.
      // Repeated content messages cannot renew its authority or deadline.
      if (ctx.providerAction || ctx.candidate?.providerAction) return {ok: false};
      const arm = ctx.userArm || ctx.candidate?.userArm;
      const record = message.record;
      if (message.type !== "ebsco_pdf_action" || message.task_id !== ctx.taskId || !sameTaskRecord(ctx, message.pageUrl) ||
          !AcquisitionProtocol.boundedPayload(message) ||
          Object.keys(message).sort().join(",") !== "action_time,pageUrl,record,task_id,type" ||
          !arm || arm.navigationUrl !== ctx.navigationUrl || !Number.isSafeInteger(message.action_time) ||
          message.action_time < arm.armedAt || message.action_time - arm.armedAt > ARM_WINDOW_MS ||
          message.action_time > Date.now() || Date.now() - message.action_time > ARM_WINDOW_MS ||
          !record || Object.keys(record).join(",") !== "doi" ||
          record.doi !== ctx.plan.doi) return {ok: false};
      const evidence = {observed_doi: record.doi, recordUrl: ctx.navigationUrl, actionTime: message.action_time};
      const providerAction = {taskId: ctx.taskId, tabId: ctx.tabId, origin: ctx.origin,
        doi: ctx.plan.doi, recordUrl: ctx.navigationUrl, navigationTime: ctx.navigationTime,
        armedAt: arm.armedAt, actionTime: message.action_time};
      if (!await storeRecordEvidence(ctx, evidence, providerAction)) return {ok: false};
      // Chrome can report onCreated while the content-script message is in
      // flight. Bind the late evidence to this same pending armed candidate.
      const current = await context();
      if (!current || current.taskId !== ctx.taskId || current.tabId !== ctx.tabId ||
          !verifiedProviderAction(current, providerAction, current.userArm || current.candidate?.userArm,
            current.recordEvidence)) return {ok: false};
      if (current.candidate?.userArm) {
        await completed(current.candidate.id);
      }
      return {ok: true};
    }

    async function resolverMessage(message, sender) {
      const ctx = await context();
      if (!ctx || ctx.route !== "xmu" ||
          sender.id !== chrome.runtime.id || sender.frameId !== 0 || sender.tab?.id !== ctx.tabId ||
          !AcquisitionProtocol.resolverContext(sender.url, ctx.plan.doi)) return {ok: false};
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
      if (!ctx || ctx.taskId !== taskId || ctx.route !== "xmu" || ctx.publisherState !== "exhausted" ||
          !Number.isSafeInteger(choiceId) || choiceId < 0 || !ctx.choices[choiceId] || ctx.candidate) return false;
      const choice = ctx.choices[choiceId];
      ctx.category = choice.category;
      ctx.recordEvidence = null;
      return navigate(ctx, choice.target);
    }

    async function observeNavigation(details) {
      const ctx = await context();
      if (!ctx || details.tabId !== ctx.tabId || details.frameId !== 0) return;
      const url = AcquisitionProtocol.safeUrl(details.url);
      if (!url) return;
      await chrome.storage.session.remove(RECORD_KEY);
      if (ctx.candidate?.providerAction ||
          (ctx.candidate?.userArm && sameTaskRecord(ctx, ctx.candidate.navigationUrl))) ctx.candidate = null;
      if (url === ctx.navigationUrl) {
        if (ctx.route === "xmu") {
          ctx.navigationTime = Date.now();
          ctx.recordEvidence = null;
          ctx.userArm = null;
          await save(ctx);
        }
        return;
      }
      ctx.previousUrl = ctx.navigationUrl;
      ctx.navigationUrl = url;
      ctx.navigationTime = Date.now();
      ctx.recordEvidence = null; // Evidence never carries across a new document.
      ctx.userArm = null;
      if (!await save(ctx)) return;
      await emit(ctx, "navigation_state", {identity: AcquisitionProtocol.sanitizedIdentity(url), route: ctx.route});
    }

    async function armUserDownload(tab) {
      const ctx = await context();
      if (!ctx || tab.id !== ctx.tabId || tab.incognito || ctx.ambiguous || ctx.candidate ||
          !AcquisitionProtocol.safeUrl(tab.url) || (tab.url !== ctx.navigationUrl && !sameTaskRecord(ctx, tab.url))) return false;
      // Research record downloads require a resolver-approved category even
      // when the current URL is byte-identical to the task navigation URL.
      if (AcquisitionProtocol.ebscoRecordContext(tab.url) && !sameTaskRecord(ctx, tab.url)) return false;
      const liveTab = await chrome.tabs.get(ctx.tabId);
      if (liveTab.id !== ctx.tabId || liveTab.incognito || (liveTab.url !== ctx.navigationUrl && !sameTaskRecord(ctx, liveTab.url))) return false;
      // A human toolbar gesture opens one short attribution window for a
      // publisher/PDF-viewer download.
      if (ctx.route === "xmu") {
        await chrome.storage.session.remove(RECORD_KEY);
        ctx.recordEvidence = null;
      }
      ctx.userArm = {navigationUrl: ctx.navigationUrl, armedAt: Date.now()};
      if (!await save(ctx)) return false;
      await chrome.action.setBadgeText({tabId: ctx.tabId, text: "D"});
      return true;
    }

    async function navigationFailed(details) {
      const ctx = await context();
      if (!ctx || details.tabId !== ctx.tabId || details.frameId !== 0 || details.error === "net::ERR_ABORTED") return;
      // A transport failure is not proof of publisher exhaustion or auth state.
      await emit(ctx, "browser_path_failure", {reason: "navigation_failed"});
    }

    function ebscoBlobRootReferrer(ctx, transport, referrer, arm) {
      // Chrome may report only the origin root. Record identity still comes
      // from the armed, resolver-approved task record, never from that root.
      return transport.kind === "blob" && arm?.navigationUrl === ctx.navigationUrl &&
        sameTaskRecord(ctx, arm.navigationUrl) && transport.origin === new URL(arm.navigationUrl).origin &&
        referrer === transport.origin + "/";
    }

    async function uniqueTaskSource(ctx, item, arm = ctx.userArm, armedOnly = false,
        providerAction = ctx.providerAction, evidence = ctx.recordEvidence) {
      const started = Date.parse(item.startTime);
      const transport = AcquisitionProtocol.downloadTransport(item);
      if (!Number.isFinite(started) ||
          item.incognito || (item.byExtensionId && item.byExtensionId !== chrome.runtime.id) ||
          !transport) return null;
      const blob = transport.kind === "blob";
      if (blob && (transport.origin !== new URL(ctx.navigationUrl).origin || started > Date.now())) return null;
      const exactSource = !blob && !armedOnly && [item.url, item.finalUrl].includes(ctx.navigationUrl) &&
        started >= ctx.navigationTime && started - ctx.navigationTime <= ARM_WINDOW_MS &&
        (!item.referrer || [ctx.navigationUrl, ctx.previousUrl].includes(item.referrer));
      // A referrer alone grants nothing. Only a trusted toolbar arm permits a
      // landing-page link to a different safe PDF URL within its short window.
      const providerSource = blob && verifiedProviderAction(ctx, providerAction, arm, evidence);
      if (providerAction && !providerSource) return null;
      const armedSource = arm && arm.navigationUrl === ctx.navigationUrl &&
        (item.referrer === arm.navigationUrl || (blob && (!item.referrer || sameTaskRecord(ctx, item.referrer))) ||
          ebscoBlobRootReferrer(ctx, transport, item.referrer, arm)) && Number.isFinite(arm.armedAt) &&
        (providerSource ? started >= providerAction.actionTime &&
          started - providerAction.actionTime <= PROVIDER_PREPARATION_WINDOW_MS
          : started >= arm.armedAt && started - arm.armedAt <= ARM_WINDOW_MS);
      if (!exactSource && !armedSource) return null;
      const useArm = !exactSource;
      const tab = await chrome.tabs.get(ctx.tabId);
      if (tab.id !== ctx.tabId || tab.incognito ||
          (useArm ? tab.url !== arm.navigationUrl && !(blob && sameTaskRecord(ctx, tab.url))
            : ![ctx.navigationUrl, ctx.previousUrl].includes(tab.url))) return null;
      // Read only current tabs at these relevant origins, to rule out a competing
      // source. No browser history and no unrelated tab content is inspected.
      const origins = [...new Set([ctx.navigationUrl, ctx.previousUrl,
        transport.url, transport.finalUrl, item.referrer, blob ? transport.origin : null]
        .filter(Boolean).map(url => new URL(url).origin + "/*"))];
      const competitors = await chrome.tabs.query({url: origins});
      if (competitors.some(t => t.id !== ctx.tabId)) return null;
      return {navigationTime: providerSource ? ctx.navigationTime : useArm ? arm.armedAt : ctx.navigationTime,
        userArm: useArm ? {...arm} : null, providerAction: providerSource ? {...providerAction} : null};
    }

    async function created(item) {
      const ctx = await context();
      if (!ctx || ctx.ambiguous || item.byExtensionId === chrome.runtime.id) return;
      // A second start is checked against the first candidate's frozen proof,
      // even after its live arm has been consumed.
      const proof = ctx.candidate ? await uniqueTaskSource(ctx, item, ctx.candidate.userArm,
        Boolean(ctx.candidate.userArm), ctx.candidate.providerAction, ctx.candidate.recordEvidence)
        : await uniqueTaskSource(ctx, item);
      if (!proof) {
        // An ambiguous/unattributable start must not leave a reusable arm.
        ctx.userArm = null;
        if (ctx.candidate?.providerAction) ctx.candidate = null;
        await save(ctx);
        return;
      }
      if (ctx.candidate && ctx.candidate.id !== item.id) {
        ctx.ambiguous = true;
        ctx.candidate = null;
        ctx.userArm = null;
        await save(ctx);
        await emit(ctx, "browser_path_failure", {reason: "ambiguous_download_ownership"});
        return;
      }
      ctx.candidate = {id: item.id, ownership: "task_navigation", navigationUrl: ctx.navigationUrl,
        navigationTime: proof.navigationTime, userArm: proof.userArm, recordEvidence: ctx.recordEvidence,
        providerAction: proof.providerAction};
      ctx.userArm = null;
      if (await save(ctx)) await completed(item.id);
    }

    async function download(taskId, target) {
      const ctx = await context();
      if (!ctx || ctx.taskId !== taskId || ctx.ambiguous || ctx.candidate ||
          AcquisitionProtocol.safeUrl(target) !== ctx.navigationUrl) return false;
      const tab = await chrome.tabs.get(ctx.tabId);
      if (tab.incognito || tab.url !== ctx.navigationUrl) return false;
      // Reservation precedes the Chrome call; never set cookies, headers or path.
      ctx.candidate = {id: null, ownership: "extension_id", navigationUrl: ctx.navigationUrl,
        navigationTime: Date.now(), recordEvidence: ctx.recordEvidence};
      ctx.userArm = null;
      if (!await save(ctx)) return false;
      try {
        const id = await chrome.downloads.download({url: ctx.navigationUrl, saveAs: true, conflictAction: "uniquify"});
        const current = await context();
        if (!current || current.taskId !== ctx.taskId || current.tabId !== ctx.tabId || current.candidate?.id !== null) return false;
        current.candidate.id = id;
        if (await save(current)) await completed(id);
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

    async function completed(id) {
      const ctx = await context();
      if (!ctx || ctx.ambiguous || ctx.candidate?.id !== id) return;
      const items = await chrome.downloads.search({id});
      const item = items.length === 1 ? items[0] : null;
      if (!item || item.state === "interrupted") {
        ctx.candidate = null;
        await save(ctx);
        await emit(ctx, "browser_path_failure", {reason: "download_unavailable"});
        return;
      }
      if (item.state !== "complete") return;
      if (item.exists === false) {
        ctx.candidate = null;
        await save(ctx);
        await emit(ctx, "browser_path_failure", {reason: "download_unavailable"});
        return;
      }
      const candidate = ctx.candidate;
      if (candidate.ownership === "extension_id") {
        if (item.byExtensionId !== chrome.runtime.id || item.url !== candidate.navigationUrl) return;
      } else {
        if (candidate.userArm && ctx.navigationUrl !== candidate.navigationUrl) return;
        if (!await uniqueTaskSource({...ctx, navigationUrl: candidate.navigationUrl,
            navigationTime: candidate.providerAction ? ctx.navigationTime : candidate.navigationTime},
            item, candidate.userArm, Boolean(candidate.userArm), candidate.providerAction, candidate.recordEvidence)) return;
      }
      const transport = AcquisitionProtocol.downloadTransport(item);
      if (!transport || (transport.kind === "blob" && !candidate.userArm) || typeof item.filename !== "string" ||
          !item.filename.startsWith("/") || item.filename.length > 512 ||
          !Number.isSafeInteger(item.fileSize) || item.fileSize <= 0 ||
          !Number.isSafeInteger(item.totalBytes) || item.totalBytes < 0) return;
      const recordEvidence = candidate.recordEvidence;
      if (transport.kind === "blob" && ctx.route === "xmu") {
        // A blob PDF action must come from this exact visible record, after
        // its explicit arm and before Chrome's download start.
        if (!recordEvidence?.recordUrl) return;
        const started = Date.parse(item.startTime);
        if (recordEvidence.recordUrl !== candidate.navigationUrl ||
            !Number.isSafeInteger(recordEvidence.actionTime) ||
            recordEvidence.actionTime < candidate.userArm.armedAt ||
            recordEvidence.actionTime > started || started - recordEvidence.actionTime >
              (candidate.providerAction ? PROVIDER_PREPARATION_WINDOW_MS : ARM_WINDOW_MS)) return;
      }
      // Verified same-record SPA and EBSCO blob root referrers use the task's stable identity;
      // generic HTTP referrers retain byte equality and their original value.
      const referrer = transport.kind === "blob" && item.referrer &&
        (sameTaskRecord(ctx, item.referrer) || ebscoBlobRootReferrer(ctx, transport, item.referrer, candidate.userArm))
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
        payload.ownership = "user_arm";
        payload.provider_record_url = recordEvidence?.recordUrl || null;
      }
      if (candidate.providerAction) {
        payload.attribution = "ebsco_pdf_action";
        payload.navigation_time = candidate.providerAction.navigationTime;
        payload.arm_time = candidate.providerAction.armedAt;
        payload.action_time = candidate.providerAction.actionTime;
      }
      if (!AcquisitionProtocol.boundedPayload(payload)) return;
      // One candidate, no history/replay queue. Source remains entirely user-owned.
      ctx.candidate = null;
      ctx.ambiguous = true; // Freeze this path after reporting one artifact.
      ctx.userArm = null;
      if (await save(ctx)) await emit(ctx, "download_candidate", payload);
    }

    async function checkDownload(taskId) {
      const ctx = await context();
      if (!ctx || ctx.taskId !== taskId || !Number.isSafeInteger(ctx.candidate?.id)) return false;
      // Explicit revival/recheck of one known ID, never a latest-file search.
      await completed(ctx.candidate.id);
      return true;
    }

    async function taskUi(taskId) {
      const ctx = await context();
      if (!ctx || ctx.taskId !== taskId) return null;
      return {task_id: ctx.taskId,
        can_fallback: ctx.route === "direct" && !ctx.candidate && !ctx.ambiguous,
        choices: ctx.choices.map((c, id) => ({id, category: c.category, label: c.label})),
        candidate: Boolean(ctx.candidate), ambiguous: ctx.ambiguous};
    }

    async function userAction(taskId, action, choiceId, tab) {
      const ctx = await context();
      if (!ctx || ctx.taskId !== taskId || tab.id !== ctx.tabId || tab.incognito || ctx.ambiguous) return false;
      const live = await chrome.tabs.get(ctx.tabId);
      if (live.id !== ctx.tabId || (live.url !== ctx.navigationUrl && !sameTaskRecord(ctx, live.url)) || live.incognito) return false;
      if (action === "arm") return armUserDownload(live);
      if (action === "human") return emit(ctx, "human_action_needed", {route: ctx.route});
      if (action === "check") return checkDownload(taskId);
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
      if (!value || !owner || owner.taskId !== taskId) return false;
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

    async function guarded(fn) {
      if (operation) return; // Overlapping observations fail closed, never queue.
      operation = true;
      try { await fn(); } catch { /* No browser URL/path/payload logging. */ }
      finally { operation = false; }
    }

    chrome.webNavigation.onBeforeNavigate.addListener(d => { void guarded(() => observeNavigation(d)); });
    chrome.webNavigation.onCommitted.addListener(d => { void guarded(() => observeNavigation(d)); });
    chrome.webNavigation.onErrorOccurred.addListener(d => { void guarded(() => navigationFailed(d)); });
    chrome.downloads.onCreated.addListener(item => {
      if (operation) {
        // An overlapping global event cannot be discarded as "unrelated".
        // Conservatively freeze adoption and persist that ambiguity in session.
        overlappingDownload = true;
        void context().then(ctx => ctx && save(ctx)).catch(() => {});
        return;
      }
      void guarded(() => created(item));
    });
    chrome.downloads.onChanged.addListener(delta => { void guarded(() => completed(delta.id)); });
    chrome.action.onClicked.addListener(tab => { void guarded(() => armUserDownload(tab)); });
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
      choose: command(choose), download: command(download), checkDownload: command(checkDownload),
      taskUi: command(taskUi),
      userAction: command(userAction), executeCommand: command(executeCommand)});
  }

  return Object.freeze({create});
})();
