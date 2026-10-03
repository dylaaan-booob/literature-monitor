// Execute shipped worker scripts against in-memory Chrome/network APIs only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'browser_companion');
const TASK = '22222222-2222-4222-8222-222222222222';
const OTHER = '33333333-3333-4333-8333-333333333333';
const ORIGIN = 'http://localhost:8765';
const LANDING = 'https://publisher.example/article';
const PDF = 'https://publisher.example/file.pdf';
const {runContentCases, recordFixture, RECORD, REDIRECT, execute, executeHandoff, e} = require('./browser_companion_content_runtime.cjs');
const BLOB = 'blob:https://research.ebsco.com/12345678-1234-1234-1234-123456789abc';

function plan() {
  return {task_id: TASK, doi: '10.5555/test', direct_url: 'https://doi.org/10.5555/test'};
}

function harness() {
  const data = {claimedHandoff: {taskId: TASK, tabId: 42, origin: ORIGIN,
    eventCapability: 'a'.repeat(43)}};
  const requests = [], updates = [], searches = [], logs = [], downloads = new Map();
  const tab = {id: 42, url: ORIGIN + '/browser-handoff/' + TASK, incognito: false};
  const listeners = {};
  const event = name => ({addListener: fn => (listeners[name] ??= []).push(fn)});
  const h = {data, requests, updates, searches, logs, tab, downloads, listeners, competitor: false,
    active: true, statusRequests: [], claimRequests: [], downloadCalls: 0, autoCommit: true,
    reply: body => body.event_type === 'tab_ready' ? {command: {type: 'START', task_id: TASK, plan: plan()}} : null};
  h.status = async () => h.active ? 200 : 404;
  const chrome = {
    runtime: {id: 'synthetic-extension', getURL: name => 'chrome-extension://synthetic-extension/' + name,
      onMessage: event('message')},
    storage: {session: {
      setAccessLevel: async () => {}, get: async key => {
        h.sessionRead?.(key);
        return {[key]: structuredClone(data[key])};
      },
      set: async values => Object.assign(data, structuredClone(values)),
      remove: async key => { delete data[key]; }
    }},
    tabs: {
      onRemoved: event('removed'),
      get: async id => { assert.equal(id, tab.id); if (h.closed) throw new Error('Tab is closed'); return {...tab}; },
      update: async (id, value) => {
        assert.equal(id, tab.id); updates.push(value.url); tab.url = value.url;
        await h.before(value.url);
        if (h.autoCommit) await h.navigate(value.url, {started: true});
      },
      query: async query => h.competitor && query.url ? [tab, {id: 99}] : [{...tab}]
    },
    action: {setBadgeText: async () => {}},
    webNavigation: {onBeforeNavigate: event('before'), onCommitted: event('committed'), onErrorOccurred: event('error')},
    downloads: {
      onCreated: event('created'), onChanged: event('changed'),
      search: async query => {
        assert.deepEqual(Object.keys(query), ['id']); assert(Number.isSafeInteger(query.id));
        searches.push({...query});
        return downloads.has(query.id) ? [structuredClone(downloads.get(query.id))] : [];
      },
      download: async ({url}) => {
        h.downloadCalls++;
        const item = download(url, url); item.byExtensionId = chrome.runtime.id;
        downloads.set(item.id, item); return item.id;
      }
    }
  };
  h.chrome = chrome;
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [h.now ?? Date.now()])); }
    static now() { return h.now ?? Date.now(); }
  }
  h.restart = () => {
    for (const name of Object.keys(listeners)) delete listeners[name];
    const context = vm.createContext({Date: ClockDate, chrome, URL, URLSearchParams, TextEncoder, AbortController,
      console: Object.fromEntries(['log', 'info', 'warn', 'error', 'debug'].map(method =>
        [method, (...args) => logs.push({method, args})])),
      crypto: require('node:crypto').webcrypto,
      structuredClone, setTimeout: (fn, ms) => h.timer ? h.timer(fn, ms) : setTimeout(fn, ms), clearTimeout,
      fetch: async (url, options) => {
        if (options.method === 'GET') {
          assert.equal(url, ORIGIN + '/browser-handoff/' + TASK);
          assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
          assert.equal(options.referrerPolicy, 'no-referrer'); assert.equal(options.cache, 'no-store');
          assert(options.signal instanceof AbortSignal);
          assert.equal(options.body, undefined); assert.equal(options.headers, undefined);
          assert(!url.includes(h.data.claimedHandoff?.eventCapability));
          h.statusRequests.push({url, options});
          return {status: await h.status(options)};
        }
        if (url === ORIGIN + '/browser-handoff/claim') {
          const body = JSON.parse(options.body); h.claimRequests.push(body);
          assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
          assert.equal(options.referrerPolicy, 'no-referrer'); assert.equal(options.cache, 'no-store');
          const reply = h.claimReply ? await h.claimReply(body) : {event_capability: 'a'.repeat(43)};
          return {status: 200, headers: {get: () => 'application/json'}, text: async () => JSON.stringify(reply)};
        }
        assert.equal(url, ORIGIN + '/browser-handoff/events');
        const body = JSON.parse(options.body); requests.push(body);
        const reply = await h.reply(body);
        if (body.event_type === 'browser_path_failure') h.active = false;
        return {status: h.eventStatus?.(body) ?? (reply ? 200 : 204), headers: {get: () => 'application/json'},
          text: async () => reply ? JSON.stringify(reply) : ''};
      }});
    context.importScripts = name => vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context, {filename: name});
    vm.runInContext(fs.readFileSync(path.join(root, 'service_worker.js'), 'utf8'), context, {filename: 'service_worker.js'});
    h.runtime = vm.runInContext('browserAcquisition', context);
    h.emit = vm.runInContext('emitEvent', context);
    h.busy = () => vm.runInContext('busy', context);
    h.claiming = () => structuredClone(vm.runInContext('claiming', context));
  };
  h.restart();
  h.state = () => structuredClone(data.claimedHandoff);
  h.deliver = (message, sender, reply) => {
    let responded = false;
    for (const listener of listeners.message) {
      const pending = listener(message, sender, value => { responded = true; reply(structuredClone(value)); });
      if (responded || pending) return;
    }
    assert.fail('Message had no response handler');
  };
  h.message = (message, overrides = {}) => new Promise(resolve => {
    const sender = {id: chrome.runtime.id, url: chrome.runtime.getURL('task_popup.html')};
    h.deliver(message, {...sender, ...overrides}, resolve);
  });
  h.contentMessage = (message, overrides = {}) => new Promise(resolve => {
    const sender = {id: chrome.runtime.id, frameId: 0, tab: {id: 42}, url: tab.url, ...overrides};
    h.deliver(message, sender, resolve);
  });
  h.ready = () => h.message({type: 'task_action', action: 'ready', choice_id: null});
  h.activate = () => h.contentMessage({type: 'activate_handoff'});
  h.claim = () => h.contentMessage({type: 'claim_handoff', handoffUrl: tab.url});
  h.popup = () => {
    const elements = new Map(), pending = [];
    const element = () => ({hidden: false, textContent: '', handlers: {}, children: [],
      addEventListener(name, callback) { this.handlers[name] = callback; },
      append(child) { this.children.push(child); }});
    const document = {getElementById: id => {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    }, createElement: element};
    const context = vm.createContext({document, chrome: {runtime: {sendMessage: message => {
      const reply = h.message(message); pending.push(reply); return reply;
    }}}});
    vm.runInContext(fs.readFileSync(path.join(root, 'task_popup.js'), 'utf8'), context, {filename: 'task_popup.js'});
    return {elements, click: async id => { elements.get(id).handlers.click(); await Promise.all(pending); }};
  };
  h.handoff = url => {
    let finish;
    const done = new Promise(resolve => { finish = resolve; });
    tab.url = url;
    const content = executeHandoff(url, (message, callback) => {
      const type = message.type;
      // History API changes window/Tab.url, not this document's source URL.
      const sender = {id: chrome.runtime.id, frameId: 0, tab: {id: tab.id}, url};
      h.deliver(message, sender, reply => {
        // Check the critical boundary synchronously inside the acknowledgement.
        if (type === 'claim_handoff' && reply.ok) assert.equal(h.busy(), false);
        callback(reply);
        if (type === 'activate_handoff' || !reply.ok) finish(reply);
      });
    }, {scrub: cleanUrl => { tab.url = cleanUrl; }});
    return {content, done};
  };
  h.before = async (url, evidence = {}) => {
    const timeStamp = h.eventTime = (h.eventTime || 1000) + 1;
    for (const fn of listeners.before || []) fn({tabId: 42, frameId: 0, url, timeStamp, processId: -1, ...evidence});
    await flush();
  };
  h.navigate = async (url, {started = false, from = url, processId = -1, transitionType = 'link',
      transitionQualifiers = from === url ? [] : ['server_redirect']} = {}) => {
    if (!started) await h.before(from, {processId});
    tab.url = url;
    const timeStamp = h.eventTime = (h.eventTime || 1000) + 1;
    for (const fn of listeners.committed) fn({tabId: 42, frameId: 0, url, timeStamp,
      processId, transitionType, transitionQualifiers});
    await flush();
  };
  h.error = async (url, error, evidence = {}) => {
    const timeStamp = h.eventTime = (h.eventTime || 1000) + 1;
    for (const fn of listeners.error || []) fn({tabId: 42, frameId: 0, url, timeStamp,
      processId: -1, error, ...evidence});
    await flush();
  };
  h.changed = async (id, item) => {
    if (item) downloads.set(id, structuredClone(item));
    for (const fn of listeners.changed || []) fn({id});
    await flush();
  };
  h.close = async (id = tab.id) => {
    if (id === tab.id) h.closed = true;
    for (const fn of listeners.removed || []) fn(id, {isWindowClosing: false});
    await flush();
  };
  h.created = async item => {
    downloads.set(item.id, item);
    for (const fn of listeners.created) fn(item);
    await flush();
  };
  return h;
}

const flush = () => new Promise(resolve => setImmediate(resolve));
function download(url = PDF, referrer = LANDING) {
  return {id: 7, url, finalUrl: url, referrer, startTime: new Date().toISOString(),
    incognito: false, state: 'complete', exists: true, filename: '/synthetic/download/file.pdf',
    mime: 'application/pdf', fileSize: 26, totalBytes: 26};
}

async function resolverChoices(h) {
  h.reply = body => body.event_type === 'publisher_fallback_request'
    ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
  assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok, true);
  const url = h.tab.url;
  const choices = [{category: 'FullText', label: 'Provider A', target: PDF},
    {category: 'SmartLinks', label: 'Provider B', target: 'https://other.example/file.pdf'}];
  const observation = {type: 'resolver_observation', pageUrl: url, human_required: false, choices, unavailable: null};
  const reply = await new Promise(resolve => {
    assert(h.listeners.message.some(fn => fn(observation,
      {id: h.chrome.runtime.id, frameId: 0, tab: {id: 42}, url}, resolve)));
  });
  assert.equal(reply.ok, true);
  return choices;
}

async function commandScenario(type) {
  const h = harness();
  if (type !== 'START') await h.ready();
  if (type === 'CHOOSE') await resolverChoices(h);
  if (type === 'DOWNLOAD_CURRENT') await h.navigate(PDF);
  const eventType = {START: 'tab_ready', PUBLISHER_EXHAUSTED: 'publisher_fallback_request',
    CHOOSE: 'resolver_choice_request', DOWNLOAD_CURRENT: 'user_download_request'}[type];
  h.reply = body => body.event_type === eventType ? {command: {type, task_id: TASK,
    ...(type === 'START' ? {plan: plan()} : type === 'CHOOSE' ? {choice_id: 1} : {})}} : null;
  h.runCommand = () => h.message({type: 'task_action',
    action: {START: 'ready', PUBLISHER_EXHAUSTED: 'fallback', CHOOSE: 'choose', DOWNLOAD_CURRENT: 'download'}[type],
    choice_id: type === 'CHOOSE' ? 1 : null});
  return h;
}

async function runProviderActionCases(approvedRecord) {
  let cases = 0;
  const deadline = setTimeout(() => { throw new Error('Provider action suite did not finish'); }, 5000);
  const setup = async (armed = false) => {
    const h = harness(); h.now = Date.now(); await approvedRecord(h, false, 0, armed);
    h.now += 1000; h.navigation = h.data.browserAcquisition.navigationTime;
    h.action = {type: 'ebsco_pdf_action', task_id: TASK, pageUrl: RECORD,
      navigation_epoch: h.data.browserAcquisition.navigationEpoch, action_time: h.now, record: {doi: plan().doi}};
    return h;
  };
  const item = (h, extra = {}) => ({...download(BLOB, 'https://research.ebsco.com/'), id: 80,
    startTime: new Date(h.now).toISOString(), ...extra});
  const reports = h => h.requests.filter(r => r.event_type === 'download_candidate');
  for (const armed of [false, true]) {
    const h = await setup(armed); if (armed) assert(h.data.browserAcquisition.userArm);
    assert.equal((await h.contentMessage(h.action)).ok, true);
    const proof = structuredClone(h.data.browserAcquisition.providerAction);
    assert(!Object.hasOwn(proof, 'armedAt')); assert(!JSON.stringify(proof).includes(h.state().eventCapability));
    assert.equal(h.data.browserAcquisition.userArm, null);
    h.now += 120000; await h.created(item(h)); const p = reports(h)[0].payload;
    assert.equal(p.ownership, 'provider_action'); assert.equal(p.attribution, 'ebsco_pdf_action');
    assert(!Object.hasOwn(p, 'arm_time')); assert.equal(p.action_time, h.action.action_time);
    assert.equal(p.navigation_time, h.navigation); assert.equal(p.start_time - p.action_time, 120000); cases++;
  }
  for (const offset of [0, 24000, 120001, -1]) {
    const h = await setup(); assert.equal((await h.contentMessage(h.action)).ok, true);
    h.now += Math.max(0, offset);
    await h.created(item(h, {startTime: new Date(h.action.action_time + offset).toISOString()}));
    assert.equal(reports(h).length, offset >= 0 && offset <= 120000 ? 1 : 0); cases++;
  }
  {
    const h = await setup(); await h.contentMessage(h.action);
    const proof = structuredClone(h.data.browserAcquisition.providerAction);
    h.now += 60000; assert.equal((await h.contentMessage({...h.action, action_time: h.now})).ok, false);
    h.restart(); await flush(); assert.deepEqual(h.data.browserAcquisition.providerAction, proof);
    h.now = proof.actionTime + 120001; await h.created(item(h)); assert.equal(reports(h).length, 0);
    assert.equal((await h.contentMessage({...h.action, action_time: h.now})).ok, false); cases++;
  }
  {
    const h = await setup(); await h.contentMessage(h.action);
    const proof = structuredClone(h.data.browserAcquisition.providerAction); h.restart(); await flush();
    assert.deepEqual(h.data.browserAcquisition.providerAction, proof);
    h.now += 24000; await h.created(item(h)); assert.equal(reports(h).length, 1); cases++;
  }
  for (const mode of ['task', 'tab', 'record', 'doi', 'missing_doi', 'epoch', 'stale', 'future', 'route', 'category', 'reload']) {
    const h = await setup(); const action = structuredClone(h.action), sender = {};
    if (mode === 'task') action.task_id = OTHER;
    if (mode === 'tab') sender.tab = {id: 99};
    if (mode === 'record') action.pageUrl = RECORD.replace('record8', 'record9');
    if (mode === 'doi') action.record.doi = '10.9999/wrong';
    if (mode === 'missing_doi') delete action.record.doi;
    if (mode === 'epoch') action.navigation_epoch--;
    if (mode === 'stale') h.now += 10001;
    if (mode === 'future') action.action_time++;
    if (mode === 'route') h.data.browserAcquisition.route = 'direct';
    if (mode === 'category') h.data.browserAcquisition.category = 'Other';
    if (mode === 'reload') await h.navigate(RECORD);
    assert.equal((await h.contentMessage(action, sender)).ok, false, mode);
    assert.equal(h.data.browserAcquisition.providerAction, null); await h.created(item(h));
    assert.equal(reports(h).length, 0); cases++;
  }
  for (const incomplete of [false, true]) {
    const h = await setup(); h.now++;
    await h.created(incomplete ? {id: 80} : item(h));
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.equal(h.data.browserAcquisition.observedDownloads[0].id, 80);
    assert.equal((await h.contentMessage(h.action)).ok, true);
    if (incomplete) await h.changed(80, item(h));
    assert.equal(reports(h).length, 1);
    assert(h.searches.every(q => q.id === 80)); cases++;
  }
  {
    const h = await setup(); h.now++;
    await h.created(item(h, {state: 'in_progress'}));
    await h.created(item(h, {id: 81, state: 'in_progress'}));
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.equal((await h.contentMessage(h.action)).ok, true);
    assert.equal(h.data.browserAcquisition.ambiguous, true);
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert(h.requests.some(r => r.payload.reason === 'ambiguous_download_ownership'));
    assert.deepEqual(h.searches.map(q => q.id), [80, 81]); cases++;
  }
  for (const mode of ['origin', 'referrer', 'before_action', 'other_extension']) {
    const h = await setup(); await h.created({id: 80});
    const extra = mode === 'origin' ? {url: BLOB.replace('research.ebsco.com', 'other.example'), finalUrl: BLOB.replace('research.ebsco.com', 'other.example')}
      : mode === 'referrer' ? {referrer: 'https://research.ebsco.com/help'}
      : mode === 'before_action' ? {startTime: new Date(h.action.action_time - 1).toISOString()} : {byExtensionId: 'other'};
    h.downloads.set(80, item(h, extra)); assert.equal((await h.contentMessage(h.action)).ok, true);
    assert.equal(reports(h).length, 0); assert.equal(h.data.browserAcquisition.candidate, null);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []); cases++;
  }
  {
    const h = await setup(); const get = h.chrome.tabs.get; let release, entered;
    const reached = new Promise(resolve => { entered = resolve; });
    h.chrome.tabs.get = async id => { h.chrome.tabs.get = get; entered();
      await new Promise(resolve => { release = resolve; }); return get(id); };
    const action = h.contentMessage(h.action); await reached; h.now++;
    await h.created(item(h)); assert.equal(h.data.browserAcquisition.candidate, null);
    release(); assert.equal((await action).ok, true); await flush(); assert.equal(reports(h).length, 1); cases++;
  }
  {
    const h = await setup(); const get = h.chrome.storage.session.get; let release, entered, paused = false;
    const reached = new Promise(resolve => { entered = resolve; });
    h.chrome.storage.session.get = async key => {
      const value = await get(key);
      if (key === 'browserAcquisition' && !paused) { paused = true; entered();
        await new Promise(resolve => { release = resolve; }); }
      return value;
    };
    h.now++; const created = h.created(item(h)); await reached;
    assert.equal((await h.contentMessage(h.action)).ok, true);
    release(); await created; await flush(); assert.equal(reports(h).length, 1); cases++;
  }
  {
    const h = await setup(); const get = h.chrome.tabs.get; let release, entered;
    const reached = new Promise(resolve => { entered = resolve; });
    h.chrome.tabs.get = async id => { h.chrome.tabs.get = get; entered();
      await new Promise(resolve => { release = resolve; }); return get(id); };
    const action = h.contentMessage(h.action); await reached; h.now += 10001;
    release(); assert.equal((await action).ok, false);
    assert.equal(h.data.browserAcquisition.providerAction, null); cases++;
  }
  {
    const h = await setup(); await h.contentMessage(h.action); h.competitor = true;
    await h.created(item(h, {state: 'in_progress'}));
    assert.equal(h.data.browserAcquisition.candidate.id, 80);
    await h.created(item(h, {id: 81, state: 'in_progress'}));
    assert.equal(h.data.browserAcquisition.ambiguous, true); assert.equal(h.data.browserAcquisition.candidate, null);
    assert(h.requests.some(r => r.payload.reason === 'ambiguous_download_ownership')); cases++;
  }
  {
    const h = await setup(); await h.contentMessage(h.action); let release, entered;
    const reached = new Promise(resolve => { entered = resolve; });
    h.reply = async body => { if (body.event_type === 'human_action_needed') { entered();
      await new Promise(resolve => { release = resolve; }); } return null; };
    const older = h.emit(h.state(), 'human_action_needed', {route: 'xmu'}); await reached;
    h.now += 24000; await h.created(item(h)); assert(h.data.browserAcquisition.downloadOutcome);
    assert.equal(reports(h).length, 0); h.restart(); await flush();
    release(); await older; await flush(); assert.equal(reports(h).length, 1);
    assert.equal(h.data.browserAcquisition.downloadReported, true); cases++;
  }
  {
    const h = await setup(); await h.contentMessage(h.action);
    assert.equal((await h.message({type: 'task_action', action: 'check', choice_id: null})).ok, false);
    assert.equal((await h.message({type: 'task_action', action: 'human', choice_id: null})).ok, false);
    assert.equal(h.runtime.checkDownload, undefined); const popup = h.popup(); await flush();
    assert(!popup.elements.has('check') && !popup.elements.has('human'));
    assert(!popup.elements.get('status').textContent.includes('version')); cases++;
  }
  clearTimeout(deadline);
  process.stdout.write(`No-arm provider action: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runProviderLateMetadataCases(approvedRecord) {
  let cases = 0;
  const deadline = setTimeout(() => { throw new Error('Provider late metadata suite did not finish'); }, 5000);
  const setup = async () => {
    const h = harness(); h.now = Date.now(); await approvedRecord(h, false, 0, false);
    h.now += 1000; h.actionTime = h.now;
    assert.equal((await h.contentMessage({type: 'ebsco_pdf_action', task_id: TASK, pageUrl: RECORD,
      navigation_epoch: h.data.browserAcquisition.navigationEpoch, action_time: h.actionTime,
      record: {doi: plan().doi}})).ok, true);
    return h;
  };
  const item = (h, offset = 119000, extra = {}) => ({...download(BLOB, 'https://research.ebsco.com/'), id: 80,
    startTime: new Date(h.actionTime + offset).toISOString(), ...extra});
  const reports = h => h.requests.filter(r => r.event_type === 'download_candidate');
  for (const [offset, restart] of [[119000, false], [120000, false], [120001, false], [-1, false], [119000, true]]) {
    const h = await setup(); h.now = h.actionTime + 119000;
    await h.created({id: 80});
    const observed = structuredClone(h.data.browserAcquisition.observedDownloads[0]);
    assert.equal(observed.providerId, h.data.browserAcquisition.providerAction.id);
    h.now = h.actionTime + 121000;
    if (restart) { h.restart(); await flush(); assert.deepEqual(h.data.browserAcquisition.observedDownloads, [observed]); }
    await h.changed(80, item(h, offset));
    const valid = offset >= 0 && offset <= 120000;
    assert.equal(reports(h).length, valid ? 1 : 0, `late metadata offset=${offset} restart=${restart}`);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []);
    if (valid) {
      assert.equal(reports(h)[0].payload.start_time - reports(h)[0].payload.action_time, offset);
      assert.equal(reports(h)[0].payload.ownership, 'provider_action');
      assert(!Object.hasOwn(reports(h)[0].payload, 'arm_time'));
    } else assert.equal(h.data.browserAcquisition.candidate, null);
    assert(h.searches.every(q => q.id === 80)); cases++;
  }
  {
    const h = await setup(); h.now = h.actionTime + 119000; await h.created({id: 80});
    const observed = structuredClone(h.data.browserAcquisition.observedDownloads[0]);
    h.now = h.actionTime + 121000; await h.changed(80, {id: 80});
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, [observed]);
    assert.equal(h.data.browserAcquisition.candidate, null);
    await h.changed(80, item(h)); assert.equal(reports(h).length, 1); cases++;
  }
  {
    const h = await setup(); h.now = h.actionTime + 120001; await h.created({id: 80});
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []);
    await h.changed(80, item(h)); assert.deepEqual(h.searches, []);
    assert.equal(reports(h).length, 0); cases++;
  }
  {
    const h = await setup(); h.now = h.actionTime + 120000; await h.created({id: 80});
    assert.equal(h.data.browserAcquisition.observedDownloads[0].id, 80);
    h.now++; await h.changed(80, item(h, 120000)); assert.equal(reports(h).length, 1); cases++;
  }
  {
    // A delayed complete onCreated uses the actual start clock, too.
    const h = await setup(); h.now = h.actionTime + 121000; await h.created(item(h));
    assert.equal(reports(h).length, 1); assert.equal(reports(h)[0].payload.start_time, h.actionTime + 119000); cases++;
  }
  {
    // Known start evidence permits late admission even if other fields lag.
    const h = await setup(); h.now = h.actionTime + 121000;
    await h.created({id: 80, startTime: new Date(h.actionTime + 119000).toISOString()});
    assert.equal(h.data.browserAcquisition.observedDownloads[0].id, 80);
    await h.changed(80, item(h)); assert.equal(reports(h).length, 1); cases++;
  }
  for (const binding of ['providerId', 'epoch']) {
    const h = await setup(); h.now = h.actionTime + 119000; await h.created({id: 80});
    const observed = h.data.browserAcquisition.observedDownloads[0];
    if (binding === 'providerId') observed.providerId = 'different-expectation'; else observed.epoch--;
    h.now = h.actionTime + 121000;
    await h.changed(80, {id: 80}); await h.changed(80, item(h));
    assert.equal(h.data.browserAcquisition.candidate, null); assert.equal(reports(h).length, 0); cases++;
  }
  {
    const h = await setup(); h.now = h.actionTime + 119000; await h.created({id: 80});
    h.now = h.actionTime + 121000;
    await h.changed(81, {...item(h), id: 81}); assert.deepEqual(h.searches, []);
    await h.changed(80, item(h, 119000, {referrer: 'https://research.ebsco.com/help'}));
    assert.equal(reports(h).length, 0); assert.equal(h.data.browserAcquisition.candidate, null);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []); cases++;
  }
  {
    const h = await setup(); h.now = h.actionTime + 119000; await h.created({id: 80});
    h.now = h.actionTime + 121000; await h.navigate(RECORD);
    await h.changed(80, item(h)); assert.deepEqual(h.searches, []);
    assert.equal(h.data.browserAcquisition.providerAction, null); assert.equal(reports(h).length, 0); cases++;
  }
  clearTimeout(deadline);
  process.stdout.write(`Provider late metadata: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runTabCloseCases(approvedRecord) {
  let cases = 0;
  const deadline = setTimeout(() => { throw new Error('Tab close suite did not finish'); }, 5000);
  const setup = async (kind = 'generic') => {
    const h = harness(); h.now = Date.now();
    if (kind === 'provider') {
      await approvedRecord(h, false, 0, false); h.now++;
      h.action = {type: 'ebsco_pdf_action', task_id: TASK, pageUrl: RECORD,
        navigation_epoch: h.data.browserAcquisition.navigationEpoch, action_time: h.now, record: {doi: plan().doi}};
      await h.contentMessage(h.action);
    } else {
      await h.ready(); await h.navigate(kind === 'extension' ? PDF : LANDING);
      if (kind === 'generic') await h.message({type: 'task_action', action: 'arm', choice_id: null});
      if (kind === 'extension') h.reply = body => body.event_type === 'user_download_request'
        ? {command: {type: 'DOWNLOAD_CURRENT', task_id: TASK}} : null;
    }
    h.item = {...download(kind === 'provider' ? BLOB : PDF, kind === 'provider' ? 'https://research.ebsco.com/' : LANDING),
      id: 80, state: 'in_progress', startTime: new Date(h.now).toISOString()};
    return h;
  };
  const terminal = h => h.requests.filter(r => r.payload.reason === 'task_tab_closed');
  const reports = h => h.requests.filter(r => r.event_type === 'download_candidate');
  const hold = async h => {
    let entered, release; const reached = new Promise(resolve => { entered = resolve; });
    h.reply = async body => { if (body.event_type === 'human_action_needed') {
      entered(); await new Promise(resolve => { release = resolve; }); } return null; };
    const pending = h.emit(h.state(), 'human_action_needed', {route: 'direct'}); await reached;
    return async () => { release(); await pending; await flush(); };
  };
  {
    const h = await setup(), before = structuredClone(h.data); await h.close(99);
    assert.deepEqual(h.data, before); assert.equal(terminal(h).length, 0); cases++;
  }
  for (const restart of [false, true]) {
    const h = await setup('provider'), release = await hold(h); await h.created({id: 80}); await h.close();
    const pending = structuredClone(h.data.browserTerminal);
    assert(pending); assert.deepEqual(pending.payload, {reason: 'task_tab_closed'});
    assert(!JSON.stringify(pending).includes(h.state().eventCapability));
    const ctx = h.data.browserAcquisition;
    for (const key of ['candidate', 'providerAction', 'recordEvidence', 'userArm', 'pendingNavigation']) assert.equal(ctx[key], null);
    assert.deepEqual(ctx.observedDownloads, []); assert.deepEqual(ctx.navigationSequences, []);
    assert.equal(ctx.taskTabClosed, true); assert.equal(terminal(h).length, 0);
    assert.equal((await h.contentMessage(h.action)).ok, false);
    await h.created(h.item); await h.changed(80, {...h.item, state: 'complete'});
    assert.equal(h.data.browserAcquisition.candidate, null);
    if (restart) { h.restart(); await flush(); }
    await release(); assert.equal(terminal(h).length, 1);
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserTerminal, undefined); cases++;
  }
  {
    const h = harness(); await h.close(); assert.equal(terminal(h).length, 1);
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserAcquisition, undefined); cases++;
  }
  {
    const h = await setup(), release = await hold(h); await h.close();
    h.eventStatus = body => body.event_type === 'browser_path_failure' ? 403 : 204;
    await release(); assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserTerminal, undefined);
    h.data.claimedHandoff = {taskId: OTHER, tabId: 99, origin: ORIGIN, eventCapability: 'b'.repeat(43)};
    h.restart(); await flush(); await h.close(42); assert.equal(terminal(h).length, 1);
    assert.equal(h.data.claimedHandoff.taskId, OTHER); cases++;
  }
  {
    const h = await setup(), release = await hold(h); await h.close();
    const pending = structuredClone(h.data.browserTerminal);
    h.eventStatus = () => 500; await release();
    assert.deepEqual(h.data.browserTerminal, pending); assert.equal(terminal(h).length, 1);
    h.eventStatus = () => 204; h.restart(); await flush();
    assert.equal(terminal(h).length, 2); assert.equal(h.data.browserTerminal, undefined); cases++;
  }
  for (const kind of ['generic', 'provider', 'extension']) {
    const h = await setup(kind);
    if (kind === 'extension') {
      h.chrome.downloads.download = async options => {
        assert.deepEqual(JSON.parse(JSON.stringify(options)), {url: PDF, saveAs: true, conflictAction: 'uniquify'});
        h.item = {...h.item, referrer: PDF, byExtensionId: h.chrome.runtime.id};
        h.downloads.set(80, h.item); return 80;
      };
      assert.equal((await h.message({type: 'task_action', action: 'download', choice_id: null})).ok, true);
    } else await h.created(h.item);
    const proof = structuredClone(h.data.browserAcquisition.candidate); assert(proof, kind); assert.equal(proof.id, 80);
    await h.close(); assert.deepEqual(h.data.browserAcquisition.candidate, proof);
    assert.equal(terminal(h).length, 0);
    const get = h.chrome.tabs.get; h.chrome.tabs.get = () => { assert.fail('Frozen close completion must not need a live tab'); };
    await h.created({...h.item, id: 81}); await h.changed(81, {...h.item, id: 81, state: 'complete'});
    assert.equal(h.data.browserAcquisition.ambiguous, false); assert.equal(h.data.browserAcquisition.candidate.id, 80);
    h.restart(); await flush(); await h.changed(80, {...h.item, state: 'complete'});
    assert.equal(reports(h).length, 1, kind); assert.equal(reports(h)[0].payload.download_id, 80);
    if (kind === 'provider') { assert.equal(reports(h)[0].payload.ownership, 'provider_action'); assert(!Object.hasOwn(reports(h)[0].payload, 'arm_time')); }
    h.chrome.tabs.get = get; await h.close(); assert.equal(terminal(h).length, 0); cases++;
  }
  {
    const h = await setup(), release = await hold(h);
    await h.created({...h.item, state: 'complete'}); const pending = structuredClone(h.data.browserAcquisition.downloadOutcome);
    await h.close(); assert.deepEqual(h.data.browserAcquisition.downloadOutcome, pending);
    h.restart(); await flush(); await release(); assert.equal(reports(h).length, 1);
    assert.equal(h.data.browserAcquisition.downloadReported, true); assert.equal(terminal(h).length, 0); cases++;
  }
  {
    const h = await setup(); await h.created({...h.item, state: 'complete'});
    assert.equal(h.data.browserAcquisition.downloadReported, true);
    await h.close(); assert.equal(terminal(h).length, 0); assert.equal(reports(h).length, 1);
    assert(h.data.claimedHandoff); cases++;
  }
  for (const startOffset of [-1, 10001]) {
    const h = await setup('extension');
    h.chrome.downloads.download = async () => {
      h.item = {...h.item, referrer: PDF, byExtensionId: h.chrome.runtime.id};
      h.downloads.set(80, h.item); return 80;
    };
    assert.equal((await h.message({type: 'task_action', action: 'download', choice_id: null})).ok, true);
    const navigationTime = h.data.browserAcquisition.candidate.navigationTime;
    await h.close(); h.now += 20000;
    await h.changed(80, {...h.item, state: 'complete', startTime: new Date(navigationTime + startOffset).toISOString()});
    assert.equal(reports(h).length, 0); cases++;
  }
  {
    const h = await setup(); const get = h.chrome.tabs.get; let entered, release;
    const reached = new Promise(resolve => { entered = resolve; });
    h.chrome.tabs.get = async id => { const tab = await get(id); entered();
      await new Promise(resolve => { release = resolve; }); return tab; };
    const created = h.created(h.item); await reached;
    await h.close(); release(); await created; await flush();
    assert.equal(terminal(h).length, 1); assert.equal(h.data.browserAcquisition.candidate, null); cases++;
  }
  {
    const h = await setup(); const set = h.chrome.storage.session.set; let entered, release, paused = false;
    const reached = new Promise(resolve => { entered = resolve; });
    h.chrome.storage.session.set = async values => {
      if (values.browserAcquisition?.candidate?.id === 80 && !paused) {
        paused = true; entered(); await new Promise(resolve => { release = resolve; }); }
      return set(values);
    };
    const created = h.created(h.item); await reached; const closed = h.close();
    release(); await created; await closed; await flush();
    assert.equal(h.data.browserAcquisition.taskTabClosed, true); assert.equal(h.data.browserAcquisition.candidate.id, 80);
    await h.changed(80, {...h.item, state: 'complete'}); assert.equal(reports(h).length, 1); assert.equal(terminal(h).length, 0); cases++;
  }
  {
    const h = await setup('extension'); let entered, release;
    const reached = new Promise(resolve => { entered = resolve; });
    h.chrome.downloads.download = async () => { entered(); await new Promise(resolve => { release = resolve; }); return 80; };
    const downloading = h.message({type: 'task_action', action: 'download', choice_id: null}); await reached;
    assert.equal(h.data.browserAcquisition.candidate.id, null); await h.close(); release(); await downloading;
    assert.equal(terminal(h).length, 1); assert.equal(h.data.browserAcquisition.candidate, null); cases++;
  }
  for (const change of [{referrer: 'https://other.example/'}, {startTime: new Date(0).toISOString()}, {incognito: true},
    {url: BLOB, finalUrl: BLOB}, {fileSize: 0}, {filename: 'relative.pdf'}]) {
    const h = await setup(); await h.created(h.item); await h.close();
    await h.changed(80, {...h.item, state: 'complete', ...change}); assert.equal(reports(h).length, 0); cases++;
  }
  clearTimeout(deadline);
  process.stdout.write(`Task tab close: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runDownloadDeliveryCases() {
  let cases = 0;
  const deadline = setTimeout(() => { throw new Error('Download delivery suite did not finish'); }, 5000);
  const setup = async () => {
    const h = harness(); h.now = Date.now(); await h.ready(); await h.navigate(LANDING);
    await h.message({type: 'task_action', action: 'arm', choice_id: null});
    h.first = {...download(), id: 80, startTime: new Date(h.now).toISOString(), state: 'in_progress'};
    await h.created(h.first); assert.equal(h.data.browserAcquisition.candidate.id, 80); return h;
  };
  const holdPost = async h => {
    let entered, release;
    const enteredPost = new Promise(resolve => { entered = resolve; });
    h.reply = async body => {
      if (body.event_type === 'human_action_needed') { entered(); await new Promise(resolve => { release = resolve; }); }
      return null;
    };
    const pending = h.emit(h.state(), 'human_action_needed', {route: 'direct'});
    await enteredPost; assert.equal(h.busy(), true);
    return async () => { release(); await pending; await flush(); };
  };
  const freeze = async (h, kind) => {
    if (kind === 'complete') await h.changed(80, {...h.first, state: 'complete'});
    if (kind === 'ambiguous') await h.created({...h.first, id: 81});
    if (kind === 'unavailable') await h.changed(80, {...h.first, state: 'interrupted'});
  };
  const events = (h, kind) => h.requests.filter(r => kind === 'complete' ? r.event_type === 'download_candidate'
    : r.event_type === 'browser_path_failure' && r.payload.reason ===
      (kind === 'ambiguous' ? 'ambiguous_download_ownership' : 'download_unavailable'));
  for (const kind of ['complete', 'ambiguous', 'unavailable']) {
    for (const restart of [false, true]) {
      const h = await setup(), release = await holdPost(h); await freeze(h, kind);
      const frozen = structuredClone(h.data.browserAcquisition.downloadOutcome);
      assert(frozen); assert.equal(h.data.browserAcquisition.downloadReported, false);
      assert.equal(h.data.browserAcquisition.candidate, null);
      assert.equal(h.data.browserAcquisition.userArm, null);
      assert.equal(h.data.browserAcquisition.ambiguous, kind === 'ambiguous');
      assert.equal(events(h, kind).length, 0);
      assert.deepEqual(Object.keys(frozen).sort(), ['eventType', 'id', 'payload']);
      assert(!JSON.stringify(frozen).includes(h.state().eventCapability));
      const searches = h.searches.length;
      await h.created({...h.first, id: 82}); await h.changed(80, {...h.first, state: 'complete'});
      await h.changed(82); await h.message({type: 'task_action', action: 'arm', choice_id: null});
      assert.deepEqual(h.data.browserAcquisition.downloadOutcome, frozen);
      assert.equal(h.searches.length, searches); assert.equal(h.data.browserAcquisition.userArm, null);
      if (restart) { h.restart(); await flush(); }
      await release();
      assert.equal(events(h, kind).length, 1);
      assert.deepEqual(events(h, kind)[0].payload, frozen.payload);
      assert.equal(h.data.browserAcquisition.downloadOutcome, null);
      assert.equal(h.data.browserAcquisition.downloadReported, true);
      assert.equal(h.data.browserAcquisition.ambiguous, kind === 'ambiguous');
      await h.runtime.reconcileDownloadOutcome(); assert.equal(events(h, kind).length, 1); cases++;
    }
  }
  {
    const h = await setup(), release = await holdPost(h);
    await Promise.all([h.changed(80, {...h.first, state: 'complete'}),
      h.changed(80, {...h.first, state: 'complete'}), h.created({...h.first, state: 'complete'})]);
    assert(h.data.browserAcquisition.downloadOutcome); await release();
    assert.equal(events(h, 'complete').length, 1); assert.equal(h.data.browserAcquisition.downloadReported, true);
    cases++;
  }
  {
    const h = await setup(), release = await holdPost(h); await freeze(h, 'complete');
    const frozen = structuredClone(h.data.browserAcquisition.downloadOutcome);
    await h.navigate(LANDING); assert.deepEqual(h.data.browserAcquisition.downloadOutcome, frozen);
    await release(); assert.equal(events(h, 'complete').length, 1);
    assert.equal(h.data.browserAcquisition.downloadReported, true);
    assert.equal(h.data.browserAcquisition.reportedEpoch, h.data.browserAcquisition.navigationEpoch); cases++;
  }
  for (const kind of ['complete', 'ambiguous', 'unavailable']) {
    const h = await setup(), release = await holdPost(h); await freeze(h, kind);
    h.eventStatus = body => body.event_type === 'human_action_needed' ? 204 : 403;
    await release(); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserAcquisition.downloadOutcome, null);
    assert.equal(h.data.browserAcquisition.userArm, null);
    h.data.claimedHandoff = {taskId: OTHER, tabId: 42, origin: ORIGIN, eventCapability: 'b'.repeat(43)};
    h.eventStatus = () => 204; await h.runtime.start({...plan(), task_id: OTHER}); h.restart(); await flush();
    assert.equal(h.data.browserAcquisition.taskId, OTHER); assert.equal(events(h, kind).length, 1); cases++;
  }
  {
    const h = await setup(); let entered, release;
    const enteredPost = new Promise(resolve => { entered = resolve; });
    h.reply = async body => {
      if (body.event_type === 'download_candidate') { entered(); await new Promise(resolve => { release = resolve; }); }
      return null;
    };
    await freeze(h, 'complete'); await enteredPost;
    const frozen = structuredClone(h.data.browserAcquisition.downloadOutcome);
    const concurrent = [h.runtime.reconcileDownloadOutcome(), h.runtime.reconcileDownloadOutcome()];
    await h.changed(80, {...h.first, state: 'interrupted'});
    await h.created({...h.first, id: 81}); await h.navigate(LANDING);
    assert.deepEqual(h.data.browserAcquisition.downloadOutcome, frozen);
    release(); await Promise.all(concurrent); await flush();
    assert.equal(events(h, 'complete').length, 1);
    assert.equal(h.data.browserAcquisition.downloadOutcome, null);
    assert.equal(h.data.browserAcquisition.downloadReported, true);
    assert.equal(h.data.browserAcquisition.reportedEpoch, h.data.browserAcquisition.navigationEpoch); cases++;
  }
  {
    const h = await setup(), original = h.chrome.storage.session.set;
    let failed = false;
    h.chrome.storage.session.set = async values => {
      if (values.browserAcquisition?.downloadReported && !failed) {
        failed = true; throw new Error('synthetic acknowledgement storage failure');
      }
      return original(values);
    };
    await freeze(h, 'complete'); await flush(); assert(failed);
    assert.equal(events(h, 'complete').length, 1);
    assert.equal(h.data.browserAcquisition.downloadReported, false);
    const frozen = structuredClone(h.data.browserAcquisition.downloadOutcome); assert(frozen);
    h.downloads.set(80, {...h.first, filename: '/different/untrusted/path', state: 'interrupted'});
    h.restart(); await flush();
    assert.equal(events(h, 'complete').length, 2);
    assert.deepEqual(events(h, 'complete').map(r => r.payload), [frozen.payload, frozen.payload]);
    assert.equal(h.data.browserAcquisition.downloadOutcome, null);
    assert.equal(h.data.browserAcquisition.downloadReported, true); cases++;
  }
  for (const status of [204, 403]) {
    const h = await setup(); let entered, release;
    const enteredPost = new Promise(resolve => { entered = resolve; });
    h.reply = async body => {
      if (body.event_type === 'download_candidate') { entered(); await new Promise(resolve => { release = resolve; }); }
      return null;
    };
    await freeze(h, 'complete'); await enteredPost;
    h.data.claimedHandoff = {taskId: OTHER, tabId: 42, origin: ORIGIN, eventCapability: 'b'.repeat(43)};
    await h.runtime.start({...plan(), task_id: OTHER});
    h.eventStatus = body => body.event_type === 'download_candidate' ? status : 204;
    release(); await flush();
    assert.equal(h.data.claimedHandoff.taskId, OTHER);
    assert.equal(h.data.browserAcquisition.taskId, OTHER);
    assert.equal(h.data.browserAcquisition.downloadReported, false);
    assert.equal(h.data.browserAcquisition.downloadOutcome, null);
    await h.runtime.reconcileDownloadOutcome(); assert.equal(events(h, 'complete').length, 1); cases++;
  }
  for (const failure of ['transport', 'reply']) {
    const h = await setup(); let accepted = false, transitions = 0;
    h.reply = body => {
      if (body.event_type === 'download_candidate') {
        if (!accepted) { accepted = true; transitions++; }
        if (failure === 'transport') throw new Error('synthetic response loss');
      }
      return null;
    };
    if (failure === 'reply') h.eventStatus = body => body.event_type === 'download_candidate' ? 200 : 204;
    await freeze(h, 'complete'); await flush();
    assert.equal(events(h, 'complete').length, 1, 'No self-retry loop after uncertain response');
    assert.equal(h.data.browserAcquisition.downloadReported, false);
    const frozen = structuredClone(h.data.browserAcquisition.downloadOutcome); assert(frozen);
    h.reply = body => { if (body.event_type === 'download_candidate' && !accepted) transitions++; return null; };
    h.eventStatus = () => 204; h.restart(); await flush();
    assert.equal(events(h, 'complete').length, 2);
    assert.deepEqual(events(h, 'complete').map(r => r.payload), [frozen.payload, frozen.payload]);
    assert.equal(transitions, 1); assert.equal(h.data.browserAcquisition.downloadOutcome, null);
    assert.equal(h.data.browserAcquisition.downloadReported, true); cases++;
  }
  {
    const h = await setup(); let accepted = false;
    h.reply = body => {
      if (body.event_type === 'browser_path_failure') { accepted = true; throw new Error('synthetic terminal response loss'); }
      return null;
    };
    await freeze(h, 'unavailable'); assert(accepted); assert(h.data.browserAcquisition.downloadOutcome);
    assert.equal(h.data.browserAcquisition.downloadReported, false);
    h.reply = () => null; h.eventStatus = () => 403; h.restart(); await flush();
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserAcquisition.downloadOutcome, null);
    assert.equal(events(h, 'unavailable').length, 2); cases++;
  }
  clearTimeout(deadline);
  process.stdout.write(`Download outcome delivery: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runDownloadObservationCases() {
  let cases = 0;
  const deadline = setTimeout(() => { throw new Error('A3 download suite did not finish'); }, 5000);
  const setup = async (armed = true) => {
    const h = harness(); h.now = Date.now(); await h.ready(); await h.navigate(LANDING);
    if (armed) assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    return h;
  };
  const item = (h, id = 80, extra = {}) => ({...download(), id,
    startTime: new Date(h.now).toISOString(), state: 'in_progress', ...extra});
  const armIntact = (h, arm) => {
    assert.deepEqual(h.data.browserAcquisition.userArm, arm);
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.equal(h.data.browserAcquisition.ambiguous, false);
  };
  const reports = h => h.requests.filter(r => r.event_type === 'download_candidate');
  for (const extra of [{byExtensionId: 'other'}, {referrer: 'https://other.example/'},
      {url: 'https://publisher.example/unrelated', finalUrl: 'https://publisher.example/unrelated', referrer: ''},
      {url: 'blob:https://other.example/opaque', finalUrl: 'blob:https://other.example/opaque'},
      {incognito: true}, {exists: false}, {url: 'file:///private/secret'}, {startTime: 'invalid'}]) {
    const h = await setup(), arm = structuredClone(h.data.browserAcquisition.userArm);
    await h.created(item(h, 80, extra)); armIntact(h, arm);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []); cases++;
  }
  {
    const h = await setup(); h.competitor = true;
    h.chrome.tabs.query = async query => { assert.equal(query.url, undefined); return [h.tab, {id: 99}]; };
    await h.created(item(h)); assert.equal(h.data.browserAcquisition.candidate.id, 80);
    assert.equal(h.data.browserAcquisition.userArm, null); cases++;
  }
  for (const field of ['url', 'finalUrl', 'startTime', 'referrer', 'incognito', 'mime', 'state']) {
    const h = await setup(), incomplete = item(h); delete incomplete[field];
    await h.created(incomplete);
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.equal(h.data.browserAcquisition.observedDownloads[0].id, 80);
    assert.equal(h.data.browserAcquisition.ambiguous, false);
    await h.changed(80, item(h)); assert.equal(h.data.browserAcquisition.candidate.id, 80);
    assert.deepEqual(h.searches, [{id: 80}]); cases++;
  }
  {
    const h = await setup(); await h.created({id: 80, url: PDF + '?SECRET_TOKEN'});
    const observation = h.data.browserAcquisition.observedDownloads[0];
    assert.deepEqual(Object.keys(observation).sort(), ['armId', 'armedAt', 'epoch', 'id', 'providerId']);
    assert(!JSON.stringify(observation).includes('SECRET'));
    await h.changed(80, {id: 80}); assert.equal(h.data.browserAcquisition.observedDownloads.length, 1);
    await h.changed(81, item(h, 81)); assert.deepEqual(h.searches, [{id: 80}]);
    await h.changed(80, item(h, 80, {state: 'complete'}));
    assert.equal(reports(h).length, 1); assert.equal(reports(h)[0].payload.download_id, 80);
    assert.equal(h.data.browserAcquisition.ambiguous, false);
    await h.changed(80); assert.equal(reports(h).length, 1); cases++;
  }
  for (const extra of [{referrer: 'https://other.example/'}, {byExtensionId: 'other'},
      {url: 'file:///secret'}, {incognito: true}, {state: 'interrupted'}, {exists: false}]) {
    const h = await setup(), arm = structuredClone(h.data.browserAcquisition.userArm);
    await h.created({id: 80}); await h.changed(80, item(h, 80, extra));
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []); armIntact(h, arm); cases++;
  }
  {
    const h = await setup(); await h.created({id: 80}); h.downloads.delete(80);
    await h.changed(80); assert.equal(h.data.browserAcquisition.observedDownloads.length, 0);
    assert(h.data.browserAcquisition.userArm); cases++;
  }
  {
    const h = await setup(); await h.created({id: 80});
    const old = structuredClone(h.data.browserAcquisition); h.restart(); await flush();
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, old.observedDownloads);
    assert.deepEqual(h.data.browserAcquisition.userArm, old.userArm);
    await h.changed(80, item(h)); assert.equal(h.data.browserAcquisition.candidate.id, 80); cases++;
  }
  for (const mode of ['navigation', 'timeout', 'rearm']) {
    const h = await setup(); await h.created({id: 80});
    const oldArm = structuredClone(h.data.browserAcquisition.userArm);
    if (mode === 'navigation') await h.navigate(LANDING);
    if (mode === 'timeout') h.now += 10001;
    if (mode === 'rearm') {
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
      assert.notEqual(h.data.browserAcquisition.userArm.id, oldArm.id);
    }
    await h.changed(80, item(h)); assert.deepEqual(h.searches, []);
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.equal(h.data.browserAcquisition.observedDownloads.length, 0);
    if (mode !== 'rearm') assert.equal(h.data.browserAcquisition.userArm, null); cases++;
  }
  {
    const h = await setup(); const first = item(h); await h.created(first);
    const frozen = structuredClone(h.data.browserAcquisition.candidate);
    await h.created(item(h, 81, {referrer: 'https://other.example/'}));
    await h.changed(80, {id: 80, state: 'in_progress'});
    assert.deepEqual(h.data.browserAcquisition.candidate, frozen);
    assert.equal(h.data.browserAcquisition.ambiguous, false);
    h.now += 20000; await h.changed(80, {...first, state: 'complete'});
    assert.equal(reports(h).length, 1); assert.equal(h.data.browserAcquisition.userArm, null);
    assert.equal(h.data.browserAcquisition.candidate, null); assert.equal(h.data.browserAcquisition.downloadReported, true);
    assert.equal(h.data.browserAcquisition.ambiguous, false); cases++;
  }
  {
    const h = await setup(); await h.created(item(h, 80, {mime: 'application/octet-stream', state: 'complete'}));
    assert.equal(reports(h).length, 1); assert.equal(reports(h)[0].payload.mime, 'application/octet-stream'); cases++;
  }
  for (const exact of [false, true]) {
    const h = await setup();
    const extra = exact ? {url: LANDING, finalUrl: LANDING, referrer: ''} : {};
    await h.created(item(h, 80, extra)); assert.equal(h.data.browserAcquisition.userArm, null);
    await h.created(item(h, 81, extra));
    assert.equal(h.data.browserAcquisition.ambiguous, true);
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.equal(h.data.browserAcquisition.userArm, null);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []);
    assert.deepEqual(h.requests.filter(r => r.event_type === 'browser_path_failure').map(r => r.payload),
      [{reason: 'ambiguous_download_ownership'}]); cases++;
  }
  for (const compatible of [false, true]) {
    const h = await setup(); await h.created(item(h));
    const frozen = structuredClone(h.data.browserAcquisition.candidate);
    await h.created({id: 81});
    assert.equal(h.data.browserAcquisition.observedDownloads[0].id, 81);
    assert.equal(h.data.browserAcquisition.userArm, null);
    const before = h.searches.length;
    await h.changed(81, item(h, 81, compatible ? {} : {referrer: 'https://other.example/'}));
    assert.deepEqual(h.searches.slice(before), [{id: 81}]);
    assert.equal(h.data.browserAcquisition.ambiguous, compatible);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []);
    assert.deepEqual(h.data.browserAcquisition.candidate, compatible ? null : frozen);
    assert.equal(h.data.browserAcquisition.userArm, null); cases++;
  }
  for (const mode of ['unrelated', 'incomplete', 'compatible', 'same_id']) {
    const h = await setup(); const original = h.chrome.tabs.get;
    let release, entered;
    const blocked = new Promise(resolve => { entered = resolve; });
    h.chrome.tabs.get = async id => { h.chrome.tabs.get = original; entered();
      await new Promise(resolve => { release = resolve; }); return original(id); };
    const first = h.created(item(h)); await blocked;
    await h.created(mode === 'incomplete' ? {id: 81} : item(h, mode === 'same_id' ? 80 : 81,
      mode === 'unrelated' ? {referrer: 'https://other.example/'} : {}));
    release(); await first; await flush();
    assert.equal(h.data.browserAcquisition.ambiguous, mode === 'compatible');
    if (mode !== 'compatible') assert.equal(h.data.browserAcquisition.candidate.id, 80);
    assert.equal(h.requests.filter(r => r.payload.reason === 'ambiguous_download_ownership').length,
      mode === 'compatible' ? 1 : 0); cases++;
  }
  {
    const h = await setup(), arm = structuredClone(h.data.browserAcquisition.userArm);
    for (let id = 80; id < 90; id++) await h.created({id});
    assert.deepEqual(h.data.browserAcquisition.observedDownloads.map(o => o.id), [80,81,82,83,84,85,86,87]);
    armIntact(h, arm); await h.changed(88, item(h, 88)); assert.deepEqual(h.searches, []);
    await h.changed(80, item(h, 80, {referrer: 'https://other.example/'})); await h.created({id: 90});
    assert.equal(h.data.browserAcquisition.observedDownloads.length, 8);
    assert.equal(h.data.browserAcquisition.observedDownloads.at(-1).id, 90);
    await h.created(item(h, 91)); assert.equal(h.data.browserAcquisition.candidate.id, 91);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []); cases++;
  }
  for (const mode of ['interrupted', 'missing', 'exists_false']) {
    const h = await setup(); await h.created(item(h));
    if (mode === 'missing') h.downloads.delete(80);
    await h.changed(80, mode === 'missing' ? undefined : item(h, 80,
      mode === 'interrupted' ? {state: 'interrupted'} : {exists: false}));
    assert.equal(h.data.browserAcquisition.candidate, null); assert.equal(h.data.browserAcquisition.userArm, null);
    assert.deepEqual(h.requests.filter(r => r.event_type === 'browser_path_failure').map(r => r.payload),
      [{reason: 'download_unavailable'}]);
    if (mode !== 'missing') assert(h.downloads.has(80)); cases++;
  }
  {
    const h = await setup(); let release, entered;
    const blocked = new Promise(resolve => { entered = resolve; });
    h.chrome.downloads.download = async options => {
      entered(options); return new Promise(resolve => { release = resolve; });
    };
    const pending = h.runtime.download(TASK, LANDING);
    assert.deepEqual(structuredClone(await blocked), {url: LANDING, saveAs: true, conflictAction: 'uniquify'});
    await h.created(item(h, 81)); await h.changed(81);
    await h.created(item(h, 80, {byExtensionId: h.chrome.runtime.id, url: LANDING, finalUrl: LANDING}));
    assert.equal(h.data.browserAcquisition.candidate.id, null);
    release(80); assert.equal(await pending, true);
    assert.equal(h.data.browserAcquisition.candidate.id, 80);
    await h.created(item(h, 82));
    await h.changed(80, item(h, 80, {byExtensionId: h.chrome.runtime.id, url: LANDING, finalUrl: LANDING, state: 'complete'}));
    assert.equal(reports(h).length, 1); assert.equal(reports(h)[0].payload.ownership, 'extension_id'); cases++;
  }
  {
    const h = await setup(); await h.created({id: 80});
    h.eventStatus = () => 403; await h.emit(h.state(), 'publisher_state', {state: 'accessible'});
    assert.equal(h.data.claimedHandoff, undefined);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []);
    assert.equal(h.data.browserAcquisition.userArm, null); cases++;
  }
  {
    const h = await setup(); await h.created({id: 80});
    h.data.claimedHandoff.taskId = OTHER;
    assert.equal(await h.runtime.start({...plan(), task_id: OTHER}), true);
    await h.changed(80, item(h)); assert.deepEqual(h.searches, []);
    assert.equal(h.data.browserAcquisition.taskId, OTHER);
    assert.deepEqual(h.data.browserAcquisition.observedDownloads, []); cases++;
  }
  for (const mode of ['navigation', 'rearm', 'replacement']) {
    const h = await setup(); const original = h.chrome.tabs.get;
    let release, entered; const blocked = new Promise(resolve => { entered = resolve; });
    h.chrome.tabs.get = async id => { h.chrome.tabs.get = original; entered();
      await new Promise(resolve => { release = resolve; }); return original(id); };
    const pending = h.created(item(h)); await blocked;
    if (mode === 'navigation') await h.navigate(LANDING);
    if (mode === 'rearm') await h.message({type: 'task_action', action: 'arm', choice_id: null});
    if (mode === 'replacement') { h.data.claimedHandoff.taskId = OTHER; await h.runtime.start({...plan(), task_id: OTHER}); }
    const stable = structuredClone(h.data.browserAcquisition.userArm);
    release(); await pending; await flush();
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.deepEqual(h.data.browserAcquisition.userArm, stable);
    assert.equal(h.data.browserAcquisition.ambiguous, false); cases++;
  }
  clearTimeout(deadline);
  process.stdout.write(`Generic download observation: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runNavigationCases(approvedRecord) {
  let cases = 0;
  const commits = h => h.requests.filter(r => r.event_type === 'navigation_state');
  const failures = h => h.requests.filter(r => r.event_type === 'browser_path_failure');
  const committed = async (h, changes) => {
    for (const fn of h.listeners.committed) fn({tabId: 42, frameId: 0, url: LANDING, timeStamp: 2000, ...changes});
    await flush();
  };
  {
    const h = harness(); h.autoCommit = false;
    const update = h.chrome.tabs.update;
    h.chrome.tabs.update = async (id, value) => {
      const ctx = h.data.browserAcquisition;
      assert.equal(ctx.taskId, TASK); assert.equal(ctx.tabId, id); assert.equal(ctx.origin, ORIGIN);
      assert.deepEqual(ctx.plan, plan()); assert.equal(ctx.navigationUrl, null);
      assert.equal(ctx.navigationTime, null); assert.equal(ctx.navigationEpoch, 0); assert.equal(ctx.committedTime, null);
      assert.equal(typeof ctx.pendingNavigation.id, 'string');
      assert.deepEqual(ctx.pendingNavigation, {id: ctx.pendingNavigation.id, target: value.url, epoch: 0, transition: {}, startedAt: null});
      await update(id, value);
    };
    assert.equal((await h.ready()).ok, true);
    const pending = structuredClone(h.data.browserAcquisition);
    assert.equal(commits(h).length, 0); assert.equal(h.data.browserAcquisition.navigationEpoch, 0);
    assert.equal(await h.runtime.executeCommand({type: 'START', task_id: TASK, plan: plan()}, TASK), true);
    assert.equal(h.updates.length, 1); assert.deepEqual(h.data.browserAcquisition, pending); cases++;
    assert.equal((await h.message({type: 'task_ui'})).task.can_fallback, false);
    for (const action of ['arm', 'download', 'fallback', 'human', 'choose']) {
      assert.equal((await h.message({type: 'task_action', action, choice_id: action === 'choose' ? 0 : null})).ok, false);
    }
    for (const type of ['resolver_context', 'ebsco_context']) {
      assert.equal((await h.contentMessage({type}, {url: type === 'resolver_context' ? REDIRECT : RECORD})).ok, false);
    }
    await h.created(download(plan().direct_url, plan().direct_url));
    assert.equal(h.data.browserAcquisition.candidate, null); assert.equal(h.downloadCalls, 0);
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 0); cases++;
    h.restart(); await committed(h, {url: LANDING});
    assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    assert.equal(h.data.browserAcquisition.navigationUrl, LANDING);
    assert.equal(h.data.browserAcquisition.navigationEpoch, 1);
    assert.equal(h.data.browserAcquisition.committedTime, 2000);
    assert.equal(commits(h).length, 1); assert.equal(h.claimRequests.length, 0);
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 1); cases++;
    const actual = structuredClone(h.data.browserAcquisition);
    assert.equal(await h.runtime.executeCommand({type: 'START', task_id: TASK, plan: plan()}, TASK), true);
    assert.equal(h.updates.length, 1); assert.deepEqual(h.data.browserAcquisition, actual); cases++;
  }
  {
    const h = harness(); let release;
    const resolution = new Promise(resolve => { release = resolve; });
    h.chrome.tabs.update = async (id, value) => {
      assert.equal(h.data.browserAcquisition.pendingNavigation.target, value.url);
      h.updates.push(value.url);
      await h.navigate(LANDING, {from: value.url}); // Redirect commit before update resolves.
      await resolution;
    };
    const activation = h.ready(); await flush(); await flush();
    assert.equal(h.data.browserAcquisition.navigationUrl, LANDING);
    assert.equal(h.data.browserAcquisition.navigationEpoch, 1);
    assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    assert.equal(commits(h).length, 1);
    assert.deepEqual(commits(h)[0].payload, {identity: {scheme: 'https:', host: 'publisher.example'}, route: 'direct'});
    const actual = structuredClone(h.data.browserAcquisition);
    release(); assert.equal((await activation).ok, true);
    assert.deepEqual(h.data.browserAcquisition, actual); assert.equal(commits(h).length, 1); cases++;
  }
  for (const changes of [{tabId: 99}, {frameId: 1}, {url: 'file:///unsafe'},
      {url: 'https://user:secret@publisher.example/'}, {timeStamp: undefined}, {timeStamp: NaN}]) {
    const h = harness(); h.autoCommit = false; await h.ready();
    const pending = structuredClone(h.data.browserAcquisition);
    await committed(h, changes);
    assert.deepEqual(h.data.browserAcquisition, pending); assert.equal(commits(h).length, 0); cases++;
  }
  {
    const h = harness(); await h.ready(); await h.navigate(LANDING);
    await h.message({type: 'task_action', action: 'arm', choice_id: null});
    const epoch = h.data.browserAcquisition.navigationEpoch;
    await h.navigate(LANDING);
    assert.equal(h.data.browserAcquisition.navigationEpoch, epoch + 1);
    assert.equal(h.data.browserAcquisition.userArm, null); cases++;
    h.restart(); const actual = structuredClone(h.data.browserAcquisition), count = commits(h).length;
    for (const timeStamp of [actual.committedTime - 1, actual.committedTime]) await committed(h, {url: PDF, timeStamp});
    assert.deepEqual(h.data.browserAcquisition, actual); assert.equal(commits(h).length, count); cases++;
    // Callback overlap cannot replace a newer commit with an older one.
    for (const details of [{url: PDF, timeStamp: 3000}, {url: LANDING, timeStamp: 2999}]) {
      for (const fn of h.listeners.committed) fn({tabId: 42, frameId: 0, ...details});
    }
    await flush();
    assert.equal(h.data.browserAcquisition.navigationUrl, PDF);
    assert.equal(h.data.browserAcquisition.committedTime, 3000);
    assert.equal(h.data.browserAcquisition.navigationEpoch, epoch + 2); cases++;
  }
  for (const type of ['START', 'PUBLISHER_EXHAUSTED', 'CHOOSE']) {
    const h = await commandScenario(type), before = structuredClone(h.data.browserAcquisition);
    h.chrome.tabs.update = async () => { throw new Error('synthetic browser rejection'); };
    assert.equal((await h.runCommand()).ok, false);
    assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    if (type === 'START') {
      assert.equal(h.data.browserAcquisition.navigationUrl, null); assert.equal(h.data.browserAcquisition.navigationEpoch, 0);
      assert.equal(h.data.browserAcquisition.navigationTime, null);
    } else assert.deepEqual(h.data.browserAcquisition, before);
    assert.equal(failures(h).length, 1); assert.equal(failures(h)[0].payload.reason, 'navigation_failed'); cases++;
  }
  for (const type of ['START', 'PUBLISHER_EXHAUSTED', 'CHOOSE']) {
    const h = await commandScenario(type), updates = h.updates.length;
    const set = h.chrome.storage.session.set;
    h.chrome.storage.session.set = async values => {
      if (values.browserAcquisition?.pendingNavigation) throw new Error('synthetic session failure');
      return set(values);
    };
    assert.equal((await h.runCommand()).ok, false);
    assert.equal(h.updates.length, updates); assert.equal(failures(h)[0].payload.reason, 'navigation_failed'); cases++;
  }
  for (const type of ['PUBLISHER_EXHAUSTED', 'CHOOSE']) {
    const h = await commandScenario(type), before = structuredClone(h.data.browserAcquisition);
    h.autoCommit = false;
    const update = h.chrome.tabs.update;
    h.chrome.tabs.update = async (id, value) => {
      assert.equal(h.data.browserAcquisition.pendingNavigation.target, value.url);
      assert.equal(h.data.browserAcquisition.navigationUrl, before.navigationUrl);
      assert.equal(h.data.browserAcquisition.navigationEpoch, before.navigationEpoch);
      assert.equal(h.data.browserAcquisition.route, before.route);
      assert.equal(h.data.browserAcquisition.category, before.category);
      await update(id, value);
    };
    assert.equal((await h.runCommand()).ok, true);
    assert.equal(h.data.browserAcquisition.navigationUrl, before.navigationUrl);
    const transition = h.data.browserAcquisition.pendingNavigation.transition;
    const actual = type === 'CHOOSE' ? 'https://other.example/redirected.pdf' : REDIRECT;
    await h.navigate(actual, {started: true, from: h.data.browserAcquisition.pendingNavigation.target});
    assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    assert.equal(h.data.browserAcquisition.navigationUrl, actual);
    assert.equal(h.data.browserAcquisition.navigationEpoch, before.navigationEpoch + 1);
    assert.equal(h.data.browserAcquisition.route, transition.route || before.route);
    assert.equal(h.data.browserAcquisition.category, transition.category);
    const stable = structuredClone(h.data.browserAcquisition);
    await committed(h, {url: before.navigationUrl, timeStamp: before.committedTime});
    assert.deepEqual(h.data.browserAcquisition, stable); cases++;
  }
  for (const error of ['net::ERR_ABORTED', 'net::ERR_NAME_NOT_RESOLVED']) {
    const h = harness(); h.autoCommit = false; await h.ready();
    const pending = structuredClone(h.data.browserAcquisition);
    for (const fn of h.listeners.error) fn({tabId: 42, frameId: 0, url: plan().direct_url, timeStamp: 2000, error});
    await flush();
    if (error === 'net::ERR_ABORTED') {
      assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending.pendingNavigation);
      assert.equal(h.data.browserAcquisition.navigationSequences[0].aborted, true);
      assert.equal(h.data.browserAcquisition.navigationEpoch, 0); assert.equal(failures(h).length, 0);
    } else {
      assert.equal(h.data.browserAcquisition.pendingNavigation, null);
      assert.equal(h.data.browserAcquisition.navigationUrl, null); assert.equal(h.data.browserAcquisition.navigationEpoch, 0);
      assert.equal(failures(h)[0].payload.reason, 'navigation_failed');
    }
    cases++;
  }
  {
    const h = harness(); h.autoCommit = false; await h.ready();
    // A download callback occupying its observation guard must not discard a
    // terminal navigation error for the pending task navigation.
    for (const fn of h.listeners.created) fn(download());
    for (const fn of h.listeners.error) fn({tabId: 42, frameId: 0, url: plan().direct_url,
      timeStamp: 2000, error: 'net::ERR_NAME_NOT_RESOLVED'});
    await flush();
    assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    assert.equal(failures(h)[0].payload.reason, 'navigation_failed'); cases++;
  }
  {
    const h = harness(); h.now = Date.now(); await approvedRecord(h);
    const action = {type: 'ebsco_pdf_action', task_id: TASK, navigation_epoch: h.data.browserAcquisition.navigationEpoch, pageUrl: RECORD,
      action_time: h.now, record: {doi: plan().doi}};
    assert.equal((await h.contentMessage(action)).ok, true);
    const old = structuredClone(h.data.browserAcquisition.providerAction), epoch = h.data.browserAcquisition.navigationEpoch;
    await h.navigate(RECORD);
    assert.equal(h.data.browserAcquisition.navigationEpoch, epoch + 1);
    assert.equal(h.data.browserAcquisition.recordEvidence, null);
    assert.equal(h.data.browserAcquisition.userArm, null);
    assert.equal(h.data.browserAcquisition.providerAction, null);
    // A content message captured by the previous document cannot establish
    // authority after a same-URL reload, even with the same millisecond clock.
    assert.equal((await h.contentMessage(action)).ok, false);
    assert.equal(h.data.browserAcquisition.providerAction, null);
    assert.equal((await h.contentMessage({...action, navigation_epoch: epoch + 1})).ok, true);
    assert.equal(h.data.browserAcquisition.providerAction.navigationEpoch, epoch + 1); cases++;
  }
  process.stdout.write(`Pending/committed navigation: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runNavigationFixCases() {
  let cases = 0;
  const commits = h => h.requests.filter(r => r.event_type === 'navigation_state');
  for (const initial of [false, true]) {
    const h = harness(); h.autoCommit = !initial; await h.ready();
    let release;
    h.reply = body => body.event_type === 'human_action_needed'
      ? new Promise(resolve => { release = resolve; }) : null;
    const older = h.emit(h.state(), 'human_action_needed', {route: 'direct'});
    await flush(); assert(h.busy());
    const count = commits(h).length;
    await h.navigate(LANDING);
    assert.equal(commits(h).length, count);
    assert(h.data.browserAcquisition.reportedEpoch < h.data.browserAcquisition.navigationEpoch);
    release(null); assert.equal(await older, true); await flush();
    assert.equal(commits(h).length, count + 1);
    assert.deepEqual(commits(h).at(-1).payload, {identity: {scheme: 'https:', host: 'publisher.example'}, route: 'direct'});
    assert.equal(h.data.browserAcquisition.reportedEpoch, h.data.browserAcquisition.navigationEpoch); cases++;
  }
  {
    const h = harness(); await h.ready(); let release;
    h.reply = body => body.event_type === 'human_action_needed'
      ? new Promise(resolve => { release = resolve; }) : null;
    const older = h.emit(h.state(), 'human_action_needed', {route: 'direct'}); await flush();
    const count = commits(h).length;
    await h.navigate(LANDING); await h.navigate(PDF); await h.navigate('https://latest.example/article');
    assert.equal(commits(h).length, count);
    release(null); await older; await flush();
    assert.equal(commits(h).length, count + 1);
    assert.equal(commits(h).at(-1).payload.identity.host, 'latest.example');
    assert.equal(h.data.browserAcquisition.reportedEpoch, h.data.browserAcquisition.navigationEpoch); cases++;
  }
  {
    const h = harness(); await h.ready(); let release;
    h.reply = body => body.event_type === 'navigation_state'
      ? new Promise(resolve => { release = resolve; }) : null;
    const oldEpoch = h.data.browserAcquisition.navigationEpoch;
    await h.navigate(LANDING); await h.navigate('https://newer.example/file.pdf');
    assert.equal(h.data.browserAcquisition.reportedEpoch, oldEpoch);
    h.reply = () => null; release(null); await flush(); await flush();
    assert.deepEqual(commits(h).slice(-2).map(r => r.payload.identity.host), ['publisher.example', 'newer.example']);
    assert.equal(h.data.browserAcquisition.navigationUrl, 'https://newer.example/file.pdf');
    assert.equal(h.data.browserAcquisition.reportedEpoch, oldEpoch + 2); cases++;
  }
  {
    const h = harness(); await h.ready(); let release;
    h.reply = body => body.event_type === 'human_action_needed'
      ? new Promise(resolve => { release = resolve; }) : null;
    const older = h.emit(h.state(), 'human_action_needed', {route: 'direct'}); await flush();
    await h.navigate(LANDING); const count = commits(h).length;
    // The suspended worker never gets the response. A new worker recovers the
    // unreported epoch directly from session, without activation or START.
    h.reply = () => null; h.restart(); await flush();
    assert.equal(commits(h).length, count + 1);
    assert.equal(h.data.browserAcquisition.reportedEpoch, h.data.browserAcquisition.navigationEpoch);
    release(null); await older; await flush();
    assert.equal(commits(h).length, count + 1); cases++;
  }
  {
    const h = harness(); await h.ready(); const count = commits(h).length;
    h.reply = () => { throw new Error('synthetic offline POST'); };
    await h.navigate(LANDING);
    assert.equal(commits(h).length, count + 1);
    assert(h.data.browserAcquisition.reportedEpoch < h.data.browserAcquisition.navigationEpoch);
    await flush(); assert.equal(commits(h).length, count + 1, 'no transport retry loop');
    h.reply = () => null; h.restart(); await flush();
    assert.equal(commits(h).length, count + 2);
    assert.equal(h.data.browserAcquisition.reportedEpoch, h.data.browserAcquisition.navigationEpoch); cases++;
  }
  for (const replaced of [false, true]) {
    const h = harness(); await h.ready(); let release;
    h.reply = body => body.event_type === 'human_action_needed'
      ? new Promise(resolve => { release = resolve; }) : null;
    h.eventStatus = body => body.event_type === 'human_action_needed' ? 403 : 204;
    const older = h.emit(h.state(), 'human_action_needed', {route: 'direct'}); await flush();
    await h.navigate(LANDING); const count = commits(h).length;
    if (replaced) h.data.claimedHandoff = {...h.state(), taskId: OTHER, eventCapability: 'b'.repeat(43)};
    release(null); assert.equal(await older, false); await flush(); h.restart(); await flush();
    assert.equal(commits(h).length, count, 'revoked navigation never replays into a replacement task');
    assert.equal(h.data.claimedHandoff?.taskId, replaced ? OTHER : undefined); cases++;
  }
  for (const type of ['PUBLISHER_EXHAUSTED', 'CHOOSE']) {
    for (const before of [false, true]) {
      const h = await commandScenario(type); h.autoCommit = false; await h.runCommand();
      const previous = structuredClone(h.data.browserAcquisition);
      const count = commits(h).length, pending = previous.pendingNavigation;
      if (before) {
        await h.before('https://unrelated.example/');
        assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
        assert.equal(h.data.browserAcquisition.navigationUrl, previous.navigationUrl);
        assert.equal(h.data.browserAcquisition.navigationEpoch, previous.navigationEpoch);
        assert.equal(commits(h).length, count, 'onBeforeNavigate does not publish committed state');
      }
      for (const fn of h.listeners.committed) fn({tabId: 42, frameId: 0, url: 'https://unrelated.example/',
        timeStamp: h.eventTime + 2, transitionType: 'typed', transitionQualifiers: ['from_address_bar']});
      await flush();
      const user = structuredClone(h.data.browserAcquisition);
      assert.equal(user.navigationUrl, 'https://unrelated.example/'); assert.equal(user.pendingNavigation, null);
      assert.equal(user.route, previous.route); assert.equal(user.category, previous.category);
      h.restart(); await flush();
      for (const fn of h.listeners.committed) fn({tabId: 42, frameId: 0, url: pending.target,
        timeStamp: pending.startedAt + 1, transitionQualifiers: ['server_redirect']});
      await flush();
      assert.deepEqual(h.data.browserAcquisition, user, 'stale command cannot resurrect its route/category'); cases++;
    }
    for (const redirect of ['server_redirect', 'client_redirect']) {
      const h = await commandScenario(type); h.autoCommit = false; await h.runCommand();
      const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
      const actual = type === 'CHOOSE' ? RECORD : REDIRECT;
      h.restart(); await flush();
      assert.equal(h.data.browserAcquisition.pendingNavigation.startedAt, pending.startedAt);
      const priorEpoch = h.data.browserAcquisition.navigationEpoch;
      for (const fn of h.listeners.committed) fn({tabId: 42, frameId: 0, url: actual,
        timeStamp: h.eventTime + 1, transitionType: 'link', transitionQualifiers: [redirect]});
      await flush();
      assert.equal(h.data.browserAcquisition.route, 'xmu');
      assert.equal(h.data.browserAcquisition.category, pending.transition.category);
      assert.equal(h.data.browserAcquisition.navigationUrl, actual);
      assert.equal(h.data.browserAcquisition.navigationEpoch, priorEpoch + 1);
      assert.equal(h.data.browserAcquisition.pendingNavigation, null); cases++;
    }
  }
  {
    const h = await commandScenario('PUBLISHER_EXHAUSTED');
    h.chrome.tabs.update = async (id, value) => {
      // Browser callbacks can arrive back-to-back before either storage read
      // finishes. Association and commit share the session mutation ordering.
      const timeStamp = h.eventTime + 1;
      for (const fn of h.listeners.before) fn({tabId: id, frameId: 0, url: value.url, timeStamp});
      for (const fn of h.listeners.committed) fn({tabId: id, frameId: 0, url: REDIRECT,
        timeStamp: timeStamp + 1, transitionQualifiers: ['server_redirect']});
      await flush();
      assert.equal(h.data.browserAcquisition.route, 'xmu');
      assert.equal(h.data.browserAcquisition.navigationUrl, REDIRECT);
      assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    };
    assert.equal((await h.runCommand()).ok, true); cases++;
  }
  {
    const h = await commandScenario('PUBLISHER_EXHAUSTED'), before = h.data.browserAcquisition.navigationUrl;
    h.chrome.tabs.update = async (id, value) => {
      await h.before(value.url);
      assert(h.data.browserAcquisition.pendingNavigation.startedAt);
      throw new Error('synthetic rejection after navigation association');
    };
    assert.equal((await h.runCommand()).ok, false);
    assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    assert.equal(h.data.browserAcquisition.navigationUrl, before);
    assert.equal(h.data.browserAcquisition.route, 'direct'); cases++;
  }
  {
    const h = harness(); await h.ready(); let release;
    h.reply = body => body.event_type === 'human_action_needed'
      ? new Promise(resolve => { release = resolve; }) : null;
    const older = h.emit(h.state(), 'human_action_needed', {route: 'direct'}); await flush();
    const count = commits(h).length;
    // Release the mutex at the same time as the commit callback, while its
    // asynchronous session write and the wake attempt are still settling.
    for (const fn of h.listeners.committed) fn({tabId: 42, frameId: 0, url: LANDING,
      timeStamp: h.eventTime + 1, transitionQualifiers: []});
    release(null); await older; await flush(); await flush();
    assert.equal(commits(h).length, count + 1);
    assert.equal(h.data.browserAcquisition.reportedEpoch, h.data.browserAcquisition.navigationEpoch); cases++;
  }
  process.stdout.write(`Navigation delivery/association fixes: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runNavigationSequenceCases() {
  let cases = 0;
  const failures = h => h.requests.filter(r => r.event_type === 'browser_path_failure');
  const reports = h => h.requests.filter(r => r.event_type === 'navigation_state');
  const pendingCommand = async (type, processId = -1) => {
    const h = await commandScenario(type);
    h.chrome.tabs.update = async (id, value) => {
      h.updates.push(value.url); h.tab.url = value.url;
      await h.before(value.url, {processId});
    };
    assert.equal((await h.runCommand()).ok, true);
    assert(h.data.browserAcquisition.pendingNavigation.startedAt);
    return h;
  };
  for (const type of ['PUBLISHER_EXHAUSTED', 'CHOOSE']) {
    for (const processId of [-1, 10]) {
      for (const restart of [false, true]) {
        const h = await pendingCommand(type, processId);
        const old = structuredClone(h.data.browserAcquisition);
        const actual = type === 'CHOOSE' ? RECORD : REDIRECT;
        const count = reports(h).length;
        await h.before(actual, {processId});
        await h.before(actual + '#provisional', {processId});
        assert.deepEqual(h.data.browserAcquisition.pendingNavigation, old.pendingNavigation);
        assert.equal(h.data.browserAcquisition.navigationUrl, old.navigationUrl);
        assert.equal(h.data.browserAcquisition.navigationEpoch, old.navigationEpoch);
        assert.equal(reports(h).length, count, 'provisional starts have no commit authority');
        if (restart) { h.restart(); await flush(); }
        await h.navigate(actual, {started: true, processId: processId < 0 ? 30 : processId,
          transitionQualifiers: ['server_redirect']});
        const associated = processId >= 0;
        assert.equal(h.data.browserAcquisition.route, associated ? 'xmu' : old.route);
        assert.equal(h.data.browserAcquisition.category, associated ? old.pendingNavigation.transition.category : old.category);
        assert.equal(h.data.browserAcquisition.navigationUrl, actual);
        assert.equal(h.data.browserAcquisition.navigationEpoch, old.navigationEpoch + 1);
        assert.equal(h.data.browserAcquisition.pendingNavigation, null);
        assert.equal(failures(h).length, 0); cases++;
      }
    }
    {
      const h = await pendingCommand(type, 10), old = structuredClone(h.data.browserAcquisition);
      const actual = type === 'CHOOSE' ? RECORD : REDIRECT;
      // Repeated renderer switches, including aborted intermediate branches.
      await h.before('https://provisional.example/one', {processId: 11});
      await h.error(old.pendingNavigation.target, 'net::ERR_ABORTED', {processId: 10});
      h.restart(); await flush();
      await h.before(actual, {processId: 12});
      await h.error('https://provisional.example/one', 'net::ERR_ABORTED', {processId: 11});
      h.restart(); await flush();
      await h.navigate(actual, {started: true, processId: 12, transitionQualifiers: ['server_redirect']});
      assert.equal(h.data.browserAcquisition.route, old.route);
      assert.equal(h.data.browserAcquisition.category, old.category, 'renderer switches need a navigation association beyond abort/redirect');
      assert.equal(failures(h).length, 0); cases++;
    }
    {
      const h = await pendingCommand(type, 10), pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
      const actual = type === 'CHOOSE' ? RECORD : REDIRECT;
      await h.before('https://unrelated.example/', {processId: 20});
      await h.error('https://unrelated.example/', 'net::ERR_NAME_NOT_RESOLVED', {processId: 20});
      assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
      assert.equal(failures(h).length, 0); assert.equal(h.active, true);
      h.restart(); await flush();
      await h.navigate(actual, {started: true, processId: 10, transitionQualifiers: ['server_redirect']});
      assert.equal(h.data.browserAcquisition.route, 'xmu');
      assert.equal(h.data.browserAcquisition.category, pending.transition.category); cases++;
    }
    {
      const h = await pendingCommand(type, 10), old = structuredClone(h.data.browserAcquisition);
      const user = 'https://unrelated.example/';
      await h.before(user, {processId: 20});
      await h.navigate(user, {started: true, processId: 20}); // User link, no address-bar qualifier.
      assert.equal(h.data.browserAcquisition.route, old.route);
      assert.equal(h.data.browserAcquisition.category, old.category);
      assert.equal(h.data.browserAcquisition.pendingNavigation, null);
      h.restart(); await flush();
      // Another command begins while the original command sequence is unresolved.
      const target = old.pendingNavigation.target;
      const command = type === 'CHOOSE' ? {type: 'CHOOSE', task_id: TASK, choice_id: 0}
        : {type: 'PUBLISHER_EXHAUSTED', task_id: TASK};
      if (type === 'CHOOSE') h.data.browserAcquisition.choices = [{category: 'FullText', target, label: 'Replacement'}];
      h.chrome.tabs.update = async (id, value) => h.before(value.url, {processId: 30});
      assert.equal(await h.runtime.executeCommand(command, TASK), true);
      const replacement = structuredClone(h.data.browserAcquisition.pendingNavigation);
      assert.notEqual(replacement.id, old.pendingNavigation.id);
      await h.error(target, 'net::ERR_NAME_NOT_RESOLVED', {processId: 10});
      assert.deepEqual(h.data.browserAcquisition.pendingNavigation, replacement);
      assert.equal(failures(h).length, 0); assert.equal(h.active, true);
      await h.navigate(target, {started: true, processId: 10, transitionQualifiers: ['server_redirect']});
      assert.equal(h.data.browserAcquisition.navigationUrl, target, 'actual late commit remains document authority');
      assert.equal(h.data.browserAcquisition.route, old.route);
      assert.equal(h.data.browserAcquisition.category, old.category);
      assert.equal(failures(h).length, 0); cases++;
    }
    {
      const h = await pendingCommand(type, 10), old = structuredClone(h.data.browserAcquisition);
      // A different live renderer's redirect cannot borrow command transition.
      await h.before('https://unrelated.example/', {processId: 20});
      await h.navigate('https://user-redirect.example/', {started: true, processId: 20,
        transitionQualifiers: ['server_redirect']});
      assert.equal(h.data.browserAcquisition.route, old.route);
      assert.equal(h.data.browserAcquisition.category, old.category); cases++;
    }
  }
  for (const processId of [-1, 10]) {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED', processId);
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    await h.error('https://unrelated.example/', 'net::ERR_NAME_NOT_RESOLVED', {processId: 20});
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    assert.equal(failures(h).length, 0);
    await h.error(pending.target, 'net::ERR_NAME_NOT_RESOLVED', {processId});
    assert.equal(h.data.browserAcquisition.pendingNavigation, null);
    assert.equal(h.data.browserAcquisition.route, 'direct');
    assert.equal(failures(h).length, 1);
    assert.deepEqual(failures(h)[0].payload, {reason: 'navigation_failed'}); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED', 10);
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    await h.before(pending.target, {processId: 20});
    await h.error(pending.target, 'net::ERR_NAME_NOT_RESOLVED', {processId: 20});
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    assert.equal(failures(h).length, 0);
    await h.navigate(pending.target, {started: true, processId: 10});
    assert.equal(h.data.browserAcquisition.route, 'xmu'); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED');
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    await h.before(pending.target); // Same unknown process and target: ambiguous.
    await h.error(pending.target, 'net::ERR_NAME_NOT_RESOLVED');
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    assert.equal(failures(h).length, 0);
    await h.navigate(pending.target, {started: true});
    assert.equal(h.data.browserAcquisition.route, 'direct', 'ambiguous commit cannot apply transition'); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED');
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    await h.before('https://unrelated.example/', {processId: 20});
    await h.error(pending.target, 'net::ERR_NAME_NOT_RESOLVED', {processId: 20});
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    assert.equal(failures(h).length, 0); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED');
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    await h.before('https://unrelated.example/');
    await h.error(pending.target, 'net::ERR_NAME_NOT_RESOLVED');
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    assert.equal(failures(h).length, 0, 'unknown concurrent sequence makes error ownership ambiguous');
    await h.navigate('https://user-redirect.example/', {started: true, processId: 20,
      transitionQualifiers: ['server_redirect']});
    assert.equal(h.data.browserAcquisition.route, 'direct', 'unobserved redirect cannot borrow target start'); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED', 10);
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    for (let i = 0; i < 12; i++) await h.before('https://provisional.example/' + i, {processId: 10});
    assert.equal(h.data.browserAcquisition.navigationSequences.length, 8);
    assert.equal(h.data.browserAcquisition.navigationOverflow, true);
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    h.restart(); await flush();
    await h.error(pending.target, 'net::ERR_NAME_NOT_RESOLVED', {processId: 10});
    assert.equal(failures(h).length, 0, 'overflow cannot establish error ownership');
    await h.navigate(REDIRECT, {started: true, processId: 10, transitionQualifiers: ['server_redirect']});
    assert.equal(h.data.browserAcquisition.navigationUrl, REDIRECT);
    assert.equal(h.data.browserAcquisition.route, 'direct'); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED', 10), pending = h.data.browserAcquisition.pendingNavigation;
    const timeStamp = h.eventTime + 1;
    // The matching error cannot terminalize a user commit already queued in
    // the same callback batch while the failure writer is awaiting session.
    for (const fn of h.listeners.error) fn({tabId: 42, frameId: 0, url: pending.target,
      timeStamp, processId: 10, error: 'net::ERR_NAME_NOT_RESOLVED'});
    for (const fn of h.listeners.committed) fn({tabId: 42, frameId: 0, url: LANDING,
      timeStamp: timeStamp + 1, processId: 20, transitionType: 'typed', transitionQualifiers: ['from_address_bar']});
    await flush(); await flush();
    assert.equal(h.data.browserAcquisition.navigationUrl, LANDING);
    assert.equal(h.data.browserAcquisition.route, 'direct');
    assert.equal(failures(h).length, 0); assert.equal(h.active, true); cases++;
  }
  for (const type of ['PUBLISHER_EXHAUSTED', 'CHOOSE']) {
    const h = await pendingCommand(type), old = structuredClone(h.data.browserAcquisition);
    const actual = type === 'CHOOSE' ? RECORD : REDIRECT;
    await h.before('https://provisional.example/one');
    await h.error(old.pendingNavigation.target, 'net::ERR_ABORTED');
    await h.before(actual);
    await h.error('https://provisional.example/one', 'net::ERR_ABORTED');
    h.restart(); await flush();
    await h.navigate(actual, {started: true, processId: 40, transitionQualifiers: ['server_redirect']});
    assert.equal(h.data.browserAcquisition.route, old.route);
    assert.equal(h.data.browserAcquisition.category, old.category, 'unknown repeated sequences cannot safely inherit transition');
    assert.equal(failures(h).length, 0); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED', 10), old = structuredClone(h.data.browserAcquisition);
    await h.before('https://unrelated.example/', {processId: 20});
    await h.navigate('https://unrelated.example/', {started: true, processId: 20});
    // The unresolved command stays bounded across a second user commit; it
    // cannot be erased just because the first superseding commit was reported.
    await h.navigate(LANDING, {processId: 30});
    assert(h.data.browserAcquisition.navigationSequences.some(s => s.commandId === old.pendingNavigation.id && s.retired));
    h.chrome.tabs.update = async (id, value) => h.before(value.url, {processId: 40});
    assert.equal(await h.runtime.executeCommand({type: 'PUBLISHER_EXHAUSTED', task_id: TASK}, TASK), true);
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    h.restart(); await flush();
    await h.error(old.pendingNavigation.target, 'net::ERR_NAME_NOT_RESOLVED', {processId: 10});
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    assert.equal(failures(h).length, 0);
    await h.navigate(pending.target, {started: true, processId: 40});
    assert.equal(h.data.browserAcquisition.route, 'xmu'); cases++;
  }
  {
    const h = await pendingCommand('PUBLISHER_EXHAUSTED'), old = structuredClone(h.data.browserAcquisition);
    await h.navigate(LANDING, {transitionType: 'typed', transitionQualifiers: ['from_address_bar']});
    h.chrome.tabs.update = async (id, value) => h.before(value.url);
    assert.equal(await h.runtime.executeCommand({type: 'PUBLISHER_EXHAUSTED', task_id: TASK}, TASK), true);
    const pending = structuredClone(h.data.browserAcquisition.pendingNavigation);
    await h.error(old.pendingNavigation.target, 'net::ERR_NAME_NOT_RESOLVED');
    assert.deepEqual(h.data.browserAcquisition.pendingNavigation, pending);
    assert.equal(failures(h).length, 0);
    h.restart(); await flush();
    await h.navigate(REDIRECT, {started: true, processId: 40, transitionQualifiers: ['server_redirect']});
    assert.equal(h.data.browserAcquisition.route, 'direct', 'unknown unresolved sequence cannot donate a redirect');
    assert.equal(failures(h).length, 0); cases++;
  }
  for (const type of ['PUBLISHER_EXHAUSTED', 'CHOOSE']) {
    const h = await pendingCommand(type, 10), old = structuredClone(h.data.browserAcquisition);
    const user = 'https://unrelated.example/';
    await h.before(user, {processId: 20});
    await h.error(old.pendingNavigation.target, 'net::ERR_ABORTED', {processId: 10});
    h.restart(); await flush();
    await h.navigate(user, {started: true, processId: 20, transitionQualifiers: ['server_redirect']});
    assert.equal(h.data.browserAcquisition.route, old.route);
    assert.equal(h.data.browserAcquisition.category, old.category,
      'user redirect after abort must not be mistaken for command process handoff');
    assert.equal(failures(h).length, 0); cases++;
  }
  process.stdout.write(`Navigation sequence association: ${cases} cases passed. Synthetic Chrome only.\n`);
  return cases;
}

async function runClaimCloseCases() {
  let cases = 0;
  const deadline = setTimeout(() => { throw new Error('Claim close suite did not finish'); }, 5000);
  const clean = ORIGIN + '/browser-handoff/' + TASK;
  const fresh = () => {
    const h = harness(); delete h.data.claimedHandoff;
    h.tab.url = clean + '#' + 'i'.repeat(43); return h;
  };
  const terminal = h => h.requests.filter(r => r.payload.reason === 'task_tab_closed');
  const gate = () => {
    let enter, release;
    const reached = new Promise(resolve => { enter = resolve; });
    const waiting = new Promise(resolve => { release = resolve; });
    return {reached, release, wait: async () => { enter(); await waiting; }};
  };
  const pauseClaim = (h, timing) => {
    const pending = gate();
    if (timing === 'response') h.claimReply = async () => {
      await pending.wait(); return {event_capability: 'a'.repeat(43)};
    };
    else if (timing === 'ack') h.chrome.action.setBadgeText = async ({text}) => {
      if (text === '!') await pending.wait();
    };
    else {
      const set = h.chrome.storage.session.set;
      h.chrome.storage.session.set = async values => {
        if (values.claimedHandoff && timing === 'before_write') await pending.wait();
        await set(values);
        if (values.claimedHandoff && timing === 'after_write') await pending.wait();
      };
    }
    return pending;
  };
  for (const timing of ['response', 'before_write', 'after_write', 'ack']) {
    const h = fresh(), paused = pauseClaim(h, timing), acknowledgement = gate();
    h.reply = async body => {
      assert.equal(body.event_type, 'browser_path_failure');
      assert.deepEqual(body.payload, {reason: 'task_tab_closed'});
      await acknowledgement.wait(); return null;
    };
    const claim = h.claim(); await paused.reached;
    assert.deepEqual(h.claiming(), {taskId: TASK, tabId: 42, origin: ORIGIN, closed: false});
    await h.close(); await h.close();
    assert.deepEqual(h.claiming(), {taskId: TASK, tabId: 42, origin: ORIGIN, closed: true});
    assert.equal(h.busy(), true);
    assert.equal(terminal(h).length, 0, 'claim mutex holds terminal delivery');
    if (['response', 'before_write'].includes(timing)) {
      assert.equal(h.data.claimedHandoff, undefined);
      assert.equal(h.data.browserTerminal, undefined);
    }
    h.tab.url = clean;
    assert.deepEqual(await h.activate(), {ok: false}, 'closed transition cannot activate');
    paused.release(); assert.deepEqual(await claim, {ok: true}, 'consumed claim still permits fragment scrub');
    await acknowledgement.reached;
    assert.equal(h.claiming(), null);
    const outcome = structuredClone(h.data.browserTerminal);
    assert(outcome); assert.deepEqual(outcome.payload, {reason: 'task_tab_closed'});
    assert.equal(Object.hasOwn(outcome, 'eventCapability'), false);
    assert.equal(Object.hasOwn(outcome, 'capability'), false);
    assert.equal(h.data.browserAcquisition, undefined);
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 0);
    assert.deepEqual(h.updates, []);
    assert.deepEqual(await h.activate(), {ok: false});
    await h.close(); assert.deepEqual(h.data.browserTerminal, outcome);
    assert.equal(terminal(h).length, 1, 'duplicate removal does not deliver another terminal');
    acknowledgement.release(); await flush();
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserTerminal, undefined);
    assert.equal(h.active, false); assert.equal(terminal(h).length, 1); cases++;
  }
  {
    const h = fresh(); assert.deepEqual(await h.claim(), {ok: true});
    assert.equal(h.claiming(), null); await h.close();
    assert.equal(terminal(h).length, 1); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserTerminal, undefined); assert.deepEqual(h.updates, []); cases++;
  }
  for (const timing of ['response', 'after_write']) {
    const h = fresh(), paused = pauseClaim(h, timing), claim = h.claim(); await paused.reached;
    await h.close(99);
    assert.equal(h.claiming().closed, false); assert.equal(h.data.browserTerminal, undefined);
    paused.release(); assert.deepEqual(await claim, {ok: true});
    assert.equal(h.claiming(), null); h.tab.url = clean;
    assert.deepEqual(await h.activate(), {ok: true});
    assert.equal(terminal(h).length, 0);
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 1);
    assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  for (const uncertain of ['500', 'lost_response']) {
    const h = fresh(), paused = pauseClaim(h, 'response'), claim = h.claim(); await paused.reached;
    await h.close();
    h.eventStatus = () => 500;
    if (uncertain === 'lost_response') h.reply = async () => { throw new Error('Synthetic uncertain response'); };
    paused.release(); assert.deepEqual(await claim, {ok: true}); await flush();
    assert.equal(terminal(h).length, 1, 'uncertain response must not create a retry loop');
    const outcome = structuredClone(h.data.browserTerminal); assert(outcome);
    h.eventStatus = () => 204; h.reply = () => null; h.restart(); await flush();
    assert.equal(terminal(h).length, 2); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserTerminal, undefined); assert.deepEqual(h.updates, []); cases++;
  }
  {
    const h = fresh(), paused = pauseClaim(h, 'response'), claim = h.claim(); await paused.reached;
    await h.close(); h.eventStatus = () => 403;
    paused.release(); assert.deepEqual(await claim, {ok: true}); await flush();
    assert.equal(terminal(h).length, 1); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserTerminal, undefined);
    const replacement = {taskId: OTHER, tabId: 99, origin: ORIGIN, eventCapability: 'b'.repeat(43)};
    h.data.claimedHandoff = replacement; h.restart(); await flush(); await h.close(42);
    assert.deepEqual(h.state(), replacement); assert.equal(terminal(h).length, 1); cases++;
  }
  for (const failure of ['authority_storage', 'terminal_storage']) {
    const h = fresh(), paused = pauseClaim(h, 'response'), claim = h.claim(); await paused.reached;
    await h.close();
    const set = h.chrome.storage.session.set;
    h.chrome.storage.session.set = async values => {
      if (failure === 'authority_storage' && values.claimedHandoff ||
          failure === 'terminal_storage' && values.browserTerminal) throw new Error('Synthetic storage failure');
      return set(values);
    };
    // Storage failures still acknowledge consumption for fragment scrub,
    // without usable local authority.
    paused.release(); assert.deepEqual(await claim, {ok: true}); await flush();
    assert.equal(terminal(h).length, 0);
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserTerminal, undefined);
    h.tab.url = clean; assert.deepEqual(await h.activate(), {ok: false});
    assert.equal(h.claiming(), null); assert.deepEqual(h.updates, []); cases++;
  }
  {
    const h = fresh(), paused = gate();
    h.claimReply = async () => { await paused.wait(); return {event_capability: 'invalid'}; };
    const claim = h.claim(); await paused.reached; await h.close(); paused.release();
    assert.deepEqual(await claim, {ok: false}); await flush();
    assert.equal(h.claiming(), null); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserTerminal, undefined); assert.equal(terminal(h).length, 0);
    assert.deepEqual(h.updates, []); cases++;
  }
  clearTimeout(deadline);
  process.stdout.write(`In-flight claim tab close: ${cases} cases passed. Synthetic lifecycle only.\n`);
  return cases;
}

async function runClosedClaimStorageCases() {
  let cases = 0;
  const deadline = setTimeout(() => { throw new Error('Closed claim storage suite did not finish'); }, 5000);
  const clean = ORIGIN + '/browser-handoff/' + TASK;
  const terminal = h => h.requests.filter(r => r.payload.reason === 'task_tab_closed');
  const pendingClaim = async () => {
    const h = harness(); delete h.data.claimedHandoff; h.tab.url = clean + '#' + 'i'.repeat(43);
    let release;
    h.claimReply = () => new Promise(resolve => { release = resolve; });
    h.claimPromise = h.claim(); await flush(); assert(release);
    h.releaseClaim = () => release({event_capability: 'a'.repeat(43)});
    return h;
  };
  const readFault = (h, persistent) => {
    let stored = false, enabled = true, failures = 0;
    const set = h.chrome.storage.session.set;
    h.chrome.storage.session.set = async values => {
      await set(values);
      if (values.claimedHandoff) stored = true;
    };
    h.sessionRead = key => {
      if (stored && enabled && key === 'claimedHandoff' && (persistent || failures === 0)) {
        failures++; throw new Error('Synthetic session read failure');
      }
    };
    return {failures: () => failures, recover: () => { enabled = false; }};
  };
  const noActivation = async h => {
    h.tab.url = clean;
    assert.deepEqual(await h.activate(), {ok: false});
    assert.equal(await h.runtime.executeCommand({type: 'START', task_id: TASK, plan: plan()}, TASK), false);
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 0);
    assert.equal(h.data.browserAcquisition, undefined); assert.deepEqual(h.updates, []);
  };
  {
    // Original reproduction: valid response pending, remembered close, successful
    // authority write, then the very next authority GET rejects exactly once.
    const h = await pendingClaim(), fault = readFault(h, false);
    await h.close(); assert.equal(h.claiming().closed, true);
    assert.equal(h.data.claimedHandoff, undefined);
    let releaseAck;
    h.reply = () => new Promise(resolve => { releaseAck = resolve; });
    h.releaseClaim(); assert.deepEqual(await h.claimPromise, {ok: true}); await flush();
    assert.equal(fault.failures(), 1); assert.equal(terminal(h).length, 1); assert(releaseAck);
    const outcome = structuredClone(h.data.browserTerminal); assert(outcome);
    assert.deepEqual(Object.keys(outcome).sort(), ['eventType', 'id', 'origin', 'payload', 'tabId', 'taskId']);
    assert.deepEqual(outcome.payload, {reason: 'task_tab_closed'});
    assert.equal(h.claiming(), null); assert(h.data.claimedHandoff);
    await noActivation(h); await h.close(); await h.close();
    assert.deepEqual(h.data.browserTerminal, outcome); assert.equal(terminal(h).length, 1);
    releaseAck(null); await flush();
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserTerminal, undefined);
    assert.equal(h.active, false); cases++;
  }
  {
    const h = await pendingClaim(), fault = readFault(h, true);
    await h.close(); h.releaseClaim(); assert.deepEqual(await h.claimPromise, {ok: true}); await flush();
    const outcome = structuredClone(h.data.browserTerminal); assert(outcome);
    assert(fault.failures() > 0); assert.equal(h.claiming(), null); assert.equal(h.busy(), false);
    await noActivation(h); await h.close(); await h.close();
    const failures = fault.failures(); await flush(); await flush();
    assert.equal(fault.failures(), failures, 'read failure never starts a retry loop');
    assert.deepEqual(h.data.browserTerminal, outcome); assert.equal(terminal(h).length, 0);
    h.restart(); await flush(); assert.deepEqual(h.data.browserTerminal, outcome);
    await noActivation(h); assert.equal(terminal(h).length, 0);
    fault.recover(); h.restart(); await flush();
    assert.equal(terminal(h).length, 1); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserTerminal, undefined); cases++;
  }
  {
    const h = await pendingClaim(), fault = readFault(h, true);
    const set = h.chrome.storage.session.set;
    h.chrome.storage.session.set = async values => {
      if (values.browserTerminal) throw new Error('Synthetic terminal persistence failure');
      return set(values);
    };
    await h.close(); h.releaseClaim(); assert.deepEqual(await h.claimPromise, {ok: true}); await flush();
    assert(fault.failures() > 0); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserTerminal, undefined); assert.equal(h.claiming(), null);
    assert.equal(h.busy(), false); assert.equal(terminal(h).length, 0);
    await noActivation(h); fault.recover(); h.restart(); await flush(); await noActivation(h);
    assert.equal(terminal(h).length, 0, 'local removal does not fake a terminal acknowledgement'); cases++;
  }
  {
    const h = await pendingClaim();
    const set = h.chrome.storage.session.set;
    h.chrome.storage.session.set = async values => {
      if (values.claimedHandoff) throw new Error('Synthetic authority persistence failure');
      return set(values);
    };
    await h.close(); h.releaseClaim(); assert.deepEqual(await h.claimPromise, {ok: true}); await flush();
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.data.browserTerminal, undefined);
    await noActivation(h); assert.equal(h.claiming(), null); assert.equal(terminal(h).length, 0); cases++;
  }
  {
    const h = await pendingClaim(), fault = readFault(h, false);
    await h.close(99); assert.equal(h.claiming().closed, false);
    h.releaseClaim(); assert.deepEqual(await h.claimPromise, {ok: true}); await flush();
    assert.equal(fault.failures(), 1); assert.equal(h.data.browserTerminal, undefined);
    h.tab.url = clean; assert.deepEqual(await h.activate(), {ok: true});
    assert.equal(terminal(h).length, 0); assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  {
    const h = await pendingClaim(), fault = readFault(h, true);
    await h.close(); h.releaseClaim(); assert.deepEqual(await h.claimPromise, {ok: true}); await flush();
    assert(h.data.browserTerminal); fault.recover(); h.eventStatus = () => 403; h.restart(); await flush();
    assert.equal(terminal(h).length, 1); assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserTerminal, undefined);
    const replacement = {taskId: OTHER, tabId: 99, origin: ORIGIN, eventCapability: 'b'.repeat(43)};
    h.data.claimedHandoff = replacement; h.restart(); await flush(); await h.close(42);
    assert.deepEqual(h.state(), replacement); assert.equal(terminal(h).length, 1); cases++;
  }
  {
    // Even if both writes and removal fail, never discard the only known close
    // fact or make the new authority usable. The app lease is the fallback.
    const h = await pendingClaim(), fault = readFault(h, true);
    const set = h.chrome.storage.session.set, remove = h.chrome.storage.session.remove;
    h.chrome.storage.session.set = async values => {
      if (values.browserTerminal) throw new Error('Synthetic terminal persistence failure');
      return set(values);
    };
    h.chrome.storage.session.remove = async key => {
      if (key === 'claimedHandoff') throw new Error('Synthetic authority removal failure');
      return remove(key);
    };
    await h.close(); h.releaseClaim(); assert.deepEqual(await h.claimPromise, {ok: true}); await flush();
    assert(h.data.claimedHandoff); assert.equal(h.data.browserTerminal, undefined);
    assert.deepEqual(h.claiming(), {taskId: TASK, tabId: 42, origin: ORIGIN, closed: true});
    assert.equal(h.busy(), true); await noActivation(h);
    const failures = fault.failures(); await flush(); await flush();
    assert.equal(fault.failures(), failures); assert.equal(terminal(h).length, 0);
    assert.deepEqual(await h.claim(), {ok: false}, 'failed persistence cannot allow a replacement claim'); cases++;
  }
  clearTimeout(deadline);
  process.stdout.write(`Closed-claim session faults: ${cases} cases passed. Synthetic storage only.\n`);
  return cases;
}

async function runActivationTabCases() {
  let cases = 0;
  const clean = ORIGIN + '/browser-handoff/' + TASK;
  const initial = clean + '#' + 'i'.repeat(43);
  const reject = async (h, source = initial) => {
    assert.deepEqual(await h.contentMessage({type: 'activate_handoff'}, {url: source}), {ok: false});
    assert.equal(h.requests.length, 0, 'rejected activation sends no tab_ready');
    assert.equal(h.updates.length, 0, 'rejected activation executes no START');
    assert.equal(h.data.browserAcquisition, undefined);
  };
  for (const currentUrl of [initial, clean + '#', clean + '#invalid', clean + '?query=1',
    clean.replace(TASK, OTHER), clean.replace('8765', '8766'),
    clean.replace('localhost', '127.0.0.1'), clean.replace('browser-handoff', 'other-path'), undefined]) {
    const h = harness(); h.tab.url = currentUrl;
    // Even a clean sender.tab snapshot cannot override the browser's live URL.
    assert.deepEqual(await h.contentMessage({type: 'activate_handoff'},
      {url: initial, tab: {id: 42, url: clean, incognito: false}}), {ok: false});
    assert.equal(h.requests.length, 0); assert.equal(h.updates.length, 0);
    assert.equal(h.data.browserAcquisition, undefined); cases++;
  }
  for (const source of [initial.replace(TASK, OTHER), initial.replace('8765', '8766'),
    'https://publisher.example/article']) {
    const h = harness(); await reject(h, source); cases++;
  }
  for (const returned of [undefined, null, {id: 99, url: clean, incognito: false},
    {id: 42, url: clean, incognito: true}]) {
    const h = harness();
    h.chrome.tabs.get = async id => { assert.equal(id, 42); return returned; };
    await reject(h); cases++;
  }
  {
    const h = harness(); h.closed = true; await reject(h); cases++;
  }
  {
    const h = harness();
    h.chrome.tabs.get = async id => { assert.equal(id, 42); throw new Error('Synthetic tabs.get rejection'); };
    await reject(h); cases++;
  }
  {
    const h = harness(); delete h.data.claimedHandoff; await reject(h); cases++;
  }
  {
    const h = harness(); h.data.claimedHandoff.taskId = OTHER; await reject(h); cases++;
  }
  {
    const h = harness();
    assert.deepEqual(await h.contentMessage({type: 'activate_handoff', taskId: TASK}, {url: initial}), {ok: false});
    assert.equal(h.requests.length, 0); assert.equal(h.updates.length, 0); cases++;
  }
  process.stdout.write(`Activation source/current tab: ${cases} cases passed. Synthetic Chrome state only.\n`);
  return cases;
}

async function runHandoffCases() {
  let cases = 0;
  const clean = ORIGIN + '/browser-handoff/' + TASK;
  const initial = clean + '#' + 'i'.repeat(43);
  const fresh = () => {
    const h = harness(); delete h.data.claimedHandoff; h.tab.url = initial; return h;
  };
  {
    const h = fresh();
    assert.deepEqual(await h.claim(), {ok: true});
    await flush();
    assert.equal(h.claimRequests.length, 1);
    assert.equal(h.requests.length, 0, 'claim never sends ready');
    assert.equal(h.updates.length, 0);
    assert.deepEqual(await h.activate(), {ok: false}, 'fragment blocks premature activation');
    assert.deepEqual(await h.ready(), {ok: false}, 'popup obeys the same fragment boundary');
    h.tab.url = clean;
    assert.deepEqual(await h.activate(), {ok: true});
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 1);
    assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  {
    const h = fresh(); let activations = 0;
    const deliver = h.deliver;
    h.deliver = (message, sender, reply) => {
      if (message.type === 'activate_handoff') {
        activations++;
        assert.deepEqual(structuredClone(message), {type: 'activate_handoff'});
        assert.equal(sender.url, initial, 'source URL survives History API scrub');
        assert.equal(h.tab.url, clean, 'current Chrome tab is independently clean');
        assert.equal(h.state().taskId, TASK, 'authority persisted before activation');
        assert.equal(h.data.browserAcquisition, undefined);
      }
      return deliver(message, sender, reply);
    };
    // Leave START's navigation pending to verify its context before any commit.
    h.chrome.tabs.update = async (id, value) => { assert.equal(id, 42); h.updates.push(value.url); };
    const {content, done} = h.handoff(initial);
    assert.deepEqual(await done, {ok: true});
    assert.equal(activations, 1);
    assert.equal(content.window.location.href, clean);
    assert.equal(h.tab.url, clean);
    assert.deepEqual(content.events.map(event => event.type), ['message', 'scrub', 'message']);
    assert.deepEqual(content.messages[1], {type: 'activate_handoff'});
    assert.equal(h.claimRequests.length, 1);
    assert.deepEqual(h.claimRequests[0], {task_id: TASK, capability: 'i'.repeat(43), tab_binding: 'tab-42'});
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 1);
    assert(!JSON.stringify(h.requests).includes('i'.repeat(43)));
    assert(!JSON.stringify(h.data).includes('i'.repeat(43)));
    assert(!JSON.stringify(h.logs).includes('i'.repeat(43)));
    assert.equal(h.data.browserAcquisition.taskId, TASK);
    assert.deepEqual(h.data.browserAcquisition.plan, plan());
    assert.equal(h.data.browserAcquisition.pendingNavigation.target, plan().direct_url);
    assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  {
    const h = fresh(); const set = h.chrome.storage.session.set;
    h.chrome.storage.session.set = async values => {
      if (values.claimedHandoff) throw new Error('Session unavailable');
      return set(values);
    };
    const {content, done} = h.handoff(initial);
    assert.deepEqual(await done, {ok: false});
    assert.equal(content.window.location.href, clean);
    assert.deepEqual(content.messages[1], {type: 'activate_handoff'});
    assert.equal(h.claimRequests.length, 1);
    assert.equal(h.data.claimedHandoff, undefined);
    assert.equal(h.data.browserAcquisition, undefined);
    assert.equal(h.requests.length, 0); assert.equal(h.updates.length, 0);
    assert.deepEqual(await h.ready(), {ok: false}); cases++;
  }
  {
    const h = fresh(); await h.claim(); h.restart();
    const {content, done} = h.handoff(clean);
    assert.deepEqual(await done, {ok: true});
    assert.deepEqual(content.messages, [{type: 'activate_handoff'}]);
    assert.equal(h.claimRequests.length, 1); assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  {
    const h = fresh(); await h.claim(); h.restart(); h.tab.url = clean;
    assert.deepEqual(await h.ready(), {ok: true});
    assert.equal(h.claimRequests.length, 1); assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  {
    const h = fresh(); let acknowledged = false;
    h.deliver({type: 'claim_handoff', handoffUrl: initial},
      {id: h.chrome.runtime.id, frameId: 0, tab: {id: 42}, url: initial}, () => { acknowledged = true; });
    await flush(); assert(acknowledged); // The original document loses this reply.
    assert.equal(h.tab.url, initial); assert.equal(h.requests.length, 0);
    const authority = h.state(); h.restart();
    const {content, done} = h.handoff(initial);
    assert.deepEqual(await done, {ok: true});
    assert.equal(content.window.location.href, clean);
    assert.equal(h.claimRequests.length, 1, 'lost acknowledgement never reclaims');
    assert.deepEqual(h.state(), authority); cases++;
  }
  {
    const h = fresh(); await h.claim(); h.tab.url = clean;
    h.reply = () => { throw new Error('START reply lost'); };
    assert.deepEqual(await h.activate(), {ok: false});
    assert.equal(h.data.browserAcquisition, undefined);
    h.restart(); h.reply = body => body.event_type === 'tab_ready'
      ? {command: {type: 'START', task_id: TASK, plan: plan()}} : null;
    const popup = h.popup(); await flush();
    assert.equal(popup.elements.get('ready').hidden, false);
    await popup.click('ready'); await flush();
    assert(popup.elements.get('status').textContent.startsWith('Request sent.'));
    assert.equal(h.claimRequests.length, 1);
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 2);
    assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  {
    const h = fresh(); await h.claim(); h.tab.url = clean;
    h.chrome.tabs.update = async (id, value) => { assert.equal(id, 42); h.updates.push(value.url); };
    assert.deepEqual(await h.activate(), {ok: true});
    await h.navigate(plan().direct_url); h.tab.url = clean;
    // A committed START with a lost document acknowledgement has existing context.
    h.data.browserAcquisition.userArm = {id: 'existing-arm', navigationEpoch: h.data.browserAcquisition.navigationEpoch,
      navigationUrl: plan().direct_url, armedAt: Date.now()};
    h.data.browserAcquisition.candidate = {id: 7};
    h.data.browserAcquisition.route = 'xmu';
    const context = structuredClone(h.data.browserAcquisition), authority = h.state();
    assert.deepEqual((await h.message({type: 'task_ui'})).can_retry, true);
    h.restart();
    for (const retry of [h.activate, h.ready, h.activate]) {
      assert.deepEqual(await retry(), {ok: true});
      assert.deepEqual(h.data.browserAcquisition, context);
      assert.deepEqual(h.state(), authority);
    }
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 4);
    assert.equal(h.claimRequests.length, 1); assert.deepEqual(h.updates, [plan().direct_url]); cases++;
  }
  {
    const h = harness(); let release;
    h.chrome.tabs.update = async (id, value) => {
      h.updates.push(value.url);
      await new Promise(resolve => { release = resolve; });
    };
    const first = h.activate(); await flush(); assert(release);
    const duplicate = h.activate(), popup = h.ready(); await flush();
    assert.equal(h.updates.length, 1, 'overlapping START cannot navigate twice');
    release();
    assert.deepEqual(await first, {ok: true});
    assert.deepEqual(await duplicate, {ok: true});
    assert.deepEqual(await popup, {ok: true});
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 1);
    assert.deepEqual(await h.activate(), {ok: true}, 'later activation remains replayable');
    assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 2);
    assert.equal(h.updates.length, 1); cases++;
  }
  for (const mode of ['fresh', 'existing', 'publisher', 'fragment']) {
    const h = harness();
    if (mode === 'existing') h.chrome.tabs.update = async (id, value) => { h.updates.push(value.url); };
    if (['existing', 'publisher'].includes(mode)) await h.ready();
    if (mode === 'fragment') h.tab.url = initial;
    const popup = h.popup(); await flush();
    assert.equal(popup.elements.get('ready').hidden, ['publisher', 'fragment'].includes(mode), mode);
    if (mode === 'existing') {
      const before = structuredClone(h.data.browserAcquisition);
      await popup.click('ready'); await flush();
      assert.deepEqual(h.data.browserAcquisition, before);
      assert.equal(h.updates.length, 1);
      assert.equal(h.requests.filter(r => r.event_type === 'tab_ready').length, 2);
    }
    assert(!popup.elements.get('status').textContent.includes('a'.repeat(43))); cases++;
  }
  for (const overrides of [
    {url: clean.replace(TASK, OTHER)}, {tab: {id: 99}}, {url: clean.replace('8765', '8766')},
    {frameId: 1}, {id: 'other-extension'}, {tab: {id: 42, incognito: true}}, {tab: undefined},
    {url: clean + '#invalid'}, {url: clean + '/extra-path'}, {url: clean + '?query=1'},
    {url: 'chrome-extension://synthetic-extension/task_popup.html'}
  ]) {
    const h = harness();
    assert.deepEqual(await h.contentMessage({type: 'activate_handoff'}, overrides), {ok: false});
    assert.equal(h.requests.length, 0); assert.equal(h.updates.length, 0); cases++;
  }
  for (const extra of [{capability: 'i'.repeat(43)}, {handoffUrl: initial}, {tabId: 42},
    {tab_binding: 'tab-42'}, {url: PDF}, {eventCapability: 'a'.repeat(43)}]) {
    const h = harness();
    assert.deepEqual(await h.contentMessage({type: 'activate_handoff', ...extra}), {ok: false});
    assert.equal(h.requests.length, 0); cases++;
  }
  {
    const h = harness();
    assert.deepEqual(await h.message({type: 'task_action', action: 'ready', choice_id: null},
      {id: 'other-extension'}), {ok: false});
    const ui = await h.message({type: 'task_ui'});
    assert.equal(ui.ok, true); assert.equal(ui.can_retry, true);
    assert(!JSON.stringify(ui).includes('a'.repeat(43)));
    assert(!JSON.stringify(ui).includes(clean));
    h.tab.url = initial;
    assert.equal((await h.message({type: 'task_ui'})).can_retry, false); cases++;
  }
  {
    const h = fresh(); let release;
    h.claimReply = () => new Promise(resolve => { release = resolve; });
    const pending = h.claim(); await flush(); assert(release);
    assert.deepEqual(await h.claim(), {ok: false}, 'in-flight claim is serialized');
    release({event_capability: 'a'.repeat(43)});
    assert.deepEqual(await pending, {ok: true});
    assert.equal(h.claimRequests.length, 1); assert.equal(h.requests.length, 0); cases++;
  }
  for (const status of [200, 404, 503, 'network']) {
    const h = harness(); const previous = h.state(); h.tab.url = initial.replace(TASK, OTHER);
    h.status = async () => {
      if (status === 'network') throw new Error('Unavailable');
      return status;
    };
    const result = await h.claim();
    assert.equal(result.ok, status === 404);
    assert.equal(h.claimRequests.length, status === 404 ? 1 : 0);
    if (status === 404) assert.equal(h.data.claimedHandoff.taskId, OTHER);
    else assert.deepEqual(h.state(), previous);
    assert.equal(h.requests.length, 0); cases++;
  }
  process.stdout.write(`Two-phase handoff: ${cases} cases passed. Synthetic lifecycle only.\n`);
  return cases;
}

(async () => {
  let cases = 0, providerEvidence;
  {
    const h = harness(); assert.equal((await h.ready()).ok, true);
    assert.deepEqual(h.updates, [plan().direct_url]);
    assert.equal(Object.hasOwn(h.data.claimedHandoff, 'readyDelivered'), false);
    // A lost ready response can repeat START without resetting navigation.
    assert.equal(await h.emit(h.state(), 'tab_ready', {}), true);
    assert.equal(h.updates.length, 1); cases++;
  }
  for (const bad of [
    {type: 'START', task_id: OTHER, plan: plan()},
    {type: 'START', task_id: TASK, plan: {...plan(), direct_url: 'https://arbitrary.example/'}},
    {type: 'START', task_id: TASK, plan: plan(), tab_id: 99},
    {type: 'START', task_id: TASK, plan: plan(), capability: 'fake'}
  ]) {
    const h = harness(); h.reply = () => ({command: bad});
    assert.equal((await h.ready()).ok, false); assert.equal(h.updates.length, 0); cases++;
  }
  {
    const h = harness(); let release;
    h.reply = () => new Promise(resolve => { release = resolve; });
    const pending = h.ready(); await flush(); assert(release);
    h.data.claimedHandoff = {...h.state(), taskId: OTHER, eventCapability: 'b'.repeat(43)};
    release({command: {type: 'START', task_id: TASK, plan: plan()}});
    assert.equal((await pending).ok, false); assert.equal(h.updates.length, 0);
    assert.equal(h.data.claimedHandoff.taskId, OTHER); cases++;
  }
  {
    const h = harness(); h.reply = () => ({command: {type: 'START', task_id: TASK, plan: plan()}});
    assert.equal((await h.ready()).ok, true);
    assert.equal(await h.runtime.executeCommand({type: 'PUBLISHER_EXHAUSTED', task_id: TASK}, TASK), true);
    assert.equal(h.updates[0], plan().direct_url);
    assert(h.updates.at(-1).startsWith('https://resolver.ebsco.com/c/45yels/result?')); cases++;
  }
  {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request' ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
    const reply = await h.message({type: 'task_action', action: 'fallback', choice_id: null});
    assert.equal(reply.ok, true); assert(h.updates.at(-1).startsWith('https://resolver.ebsco.com/c/45yels/result?'));
    assert(h.requests.some(r => r.event_type === 'navigation_state' && r.payload.route === 'xmu')); cases++;
  }
  {
    const h = harness(); await h.ready(); await h.navigate(PDF);
    h.reply = body => body.event_type === 'user_download_request' ? {command: {type: 'DOWNLOAD_CURRENT', task_id: TASK}} : null;
    assert.equal((await h.message({type: 'task_action', action: 'download', choice_id: null})).ok, true);
    assert(h.requests.some(r => r.event_type === 'download_candidate')); cases++;
  }
  {
    const h = harness(); await h.ready(); await h.navigate(PDF);
    h.chrome.downloads.download = async ({url}) => {
      const item = download(url, url); item.state = 'in_progress'; item.byExtensionId = h.chrome.runtime.id;
      h.downloads.set(item.id, item); return item.id;
    };
    h.reply = body => body.event_type === 'user_download_request' ? {command: {type: 'DOWNLOAD_CURRENT', task_id: TASK}} : null;
    assert.equal((await h.message({type: 'task_action', action: 'download', choice_id: null})).ok, true);
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 0);
    h.downloads.get(7).state = 'complete';
    await h.changed(7);
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 1); cases++;
  }
  {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request' ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}}
      : body.event_type === 'resolver_choice_request' ? {command: {type: 'CHOOSE', task_id: TASK, choice_id: body.payload.choice_id}} : null;
    await h.message({type: 'task_action', action: 'fallback', choice_id: null});
    const resolverUrl = h.tab.url; await h.navigate(resolverUrl);
    const choices = [{category: 'FullText', label: 'Provider A', target: PDF},
      {category: 'SmartLinks', label: 'Provider B', target: 'https://other.example/file.pdf'}];
    const observation = {type: 'resolver_observation', pageUrl: resolverUrl, human_required: false, choices, unavailable: null};
    const observed = await new Promise(resolve => {
      const sender = {id: h.chrome.runtime.id, frameId: 0, tab: {id: 42}, url: resolverUrl};
      assert(h.listeners.message.some(fn => fn(observation, sender, resolve)));
    });
    assert.equal(observed.ok, true); assert.equal(h.tab.url, resolverUrl); // No ambiguous auto-navigation.
    assert.equal((await h.message({type: 'task_action', action: 'choose', choice_id: 1})).ok, true);
    assert.equal(h.tab.url, choices[1].target); cases++;
  }
  for (const mode of ['armed', 'unarmed', 'wrong_referrer', 'expired', 'competitor', 'other_extension']) {
    const h = harness(); await h.ready(); await h.navigate(LANDING);
    if (mode !== 'unarmed') assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    const item = download();
    if (mode === 'wrong_referrer') item.referrer = 'https://publisher.example/other';
    if (mode === 'expired') item.startTime = new Date(Date.now() + 20000).toISOString();
    if (mode === 'competitor') h.competitor = true;
    if (mode === 'other_extension') item.byExtensionId = 'another-extension';
    await h.created(item);
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, ['armed', 'competitor'].includes(mode) ? 1 : 0);
    if (['wrong_referrer', 'other_extension', 'expired'].includes(mode)) assert(h.data.browserAcquisition.userArm);
    else assert.equal(h.data.browserAcquisition.userArm, null); cases++;
  }
  {
    const h = harness(); await h.ready(); await h.navigate(PDF);
    await h.created(download(PDF, PDF));
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 1); cases++;
  }
  assert.equal(cases, 18); // Preserve every original shipped-worker case above.
  for (const type of ['START', 'PUBLISHER_EXHAUSTED', 'CHOOSE', 'DOWNLOAD_CURRENT']) {
    const h = await commandScenario(type);
    const stableTab = h.tab.url;
    const stable = structuredClone(h.data.browserAcquisition);
    let attempted = 0;
    const fail = async () => {
      attempted++;
      const pending = h.data.browserAcquisition;
      assert(pending.pendingNavigation);
      assert.equal(pending.navigationUrl, stable?.navigationUrl ?? null);
      assert.equal(pending.navigationEpoch, stable?.navigationEpoch ?? 0);
      throw new Error('SECRET_EXCEPTION https://signed.example/?secret=PRIVATE /private/download');
    };
    if (type === 'DOWNLOAD_CURRENT') h.chrome.downloads.download = async () => {
      attempted++; throw new Error('SECRET_EXCEPTION https://signed.example/?secret=PRIVATE');
    };
    else h.chrome.tabs.update = fail;
    assert.equal((await h.runCommand()).ok, false);
    assert.equal(attempted, 1); assert.equal(h.tab.url, stableTab);
    assert.equal(h.active, false); assert.equal(h.data.claimedHandoff, undefined);
    if (type === 'START') {
      assert.equal(h.data.browserAcquisition.navigationUrl, null);
      assert.equal(h.data.browserAcquisition.navigationEpoch, 0);
      assert.equal(h.data.browserAcquisition.pendingNavigation, null);
      assert.equal((await h.ready()).ok, false); assert.equal(attempted, 1);
    } else if (type === 'PUBLISHER_EXHAUSTED') assert.equal(h.data.browserAcquisition.route, 'direct');
    else if (type === 'CHOOSE') assert.deepEqual(h.data.browserAcquisition.choices, stable.choices);
    const failures = h.requests.filter(r => r.event_type === 'browser_path_failure');
    assert.equal(failures.length, 1);
    assert.deepEqual(failures[0].payload, {reason: type === 'DOWNLOAD_CURRENT' ? 'download_unavailable' : 'navigation_failed'});
    assert(!JSON.stringify(failures).includes('SECRET_EXCEPTION'));
    assert(!JSON.stringify(failures).includes('signed.example'));
    cases++;
  }
  for (const type of ['START', 'PUBLISHER_EXHAUSTED', 'CHOOSE', 'DOWNLOAD_CURRENT']) {
    const h = await commandScenario(type); const reply = h.reply;
    const before = h.tab.url; const count = h.updates.length;
    h.reply = body => {
      const response = reply(body);
      if (response) h.active = false; // Cancel AFTER response creation, with old local session intact.
      return response;
    };
    h.status = async () => { assert.equal(h.data.claimedHandoff.taskId, TASK); return 404; };
    assert.equal((await h.runCommand()).ok, false);
    assert.equal(h.tab.url, before); assert.equal(h.updates.length, count); assert.equal(h.downloadCalls, 0);
    assert.equal(h.data.claimedHandoff, undefined); assert.equal(h.active, false);
    assert.equal(h.requests.filter(r => r.event_type === 'browser_path_failure').length, 0);
    cases++;
  }
  for (const mode of ['unexpected', 'network', 'redirect', 'timeout']) {
    const h = await commandScenario('START');
    h.status = async options => {
      if (mode === 'unexpected') return 503;
      if (mode === 'timeout') return new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new Error('Timed out')), {once: true});
      });
      throw new Error(mode);
    };
    if (mode === 'timeout') h.timer = (fn, ms) => {
      assert.equal(ms, 10000); queueMicrotask(fn); return undefined;
    };
    assert.equal((await h.runCommand()).ok, false);
    assert.equal(h.updates.length, 0); assert.equal(h.downloadCalls, 0);
    assert.equal(h.data.browserAcquisition, undefined); assert.equal(h.active, true);
    assert.equal(Object.hasOwn(h.data.claimedHandoff, 'readyDelivered'), false);
    // An unavailable check did not commit START: a later explicit retry really navigates.
    h.timer = null; h.status = async () => 200;
    assert.equal((await h.ready()).ok, true); assert.deepEqual(h.updates, [plan().direct_url]);
    cases++;
  }
  {
    const h = await commandScenario('START');
    h.status = async () => {
      h.data.claimedHandoff = {...h.state(), taskId: OTHER, eventCapability: 'b'.repeat(43)};
      return 404;
    };
    assert.equal((await h.ready()).ok, false); assert.equal(h.updates.length, 0);
    assert.equal(h.data.claimedHandoff.taskId, OTHER); cases++;
  }
  {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
    await h.message({type: 'task_action', action: 'fallback', choice_id: null});
    const url = h.tab.url;
    h.chrome.tabs.update = async () => { throw new Error('PRIVATE_NAVIGATION_ERROR'); };
    const observation = {type: 'resolver_observation', pageUrl: url, human_required: false,
      choices: [{category: 'FullText', label: 'Only provider', target: PDF}], unavailable: null};
    const reply = await new Promise(resolve => {
      assert(h.listeners.message.some(fn => fn(observation,
        {id: h.chrome.runtime.id, frameId: 0, tab: {id: 42}, url}, resolve)));
    });
    assert.equal(reply.ok, false); assert.equal(h.tab.url, url); assert.equal(h.active, false);
    assert.equal(h.data.browserAcquisition.choices.length, 1);
    const failures = h.requests.filter(r => r.event_type === 'browser_path_failure');
    assert.equal(failures.length, 1); assert.deepEqual(failures[0].payload, {reason: 'navigation_failed'});
    cases++;
  }
  {
    const h = await commandScenario('PUBLISHER_EXHAUSTED');
    const reply = h.reply;
    let releaseObservation; let observation;
    h.reply = body => body.event_type === 'navigation_state'
      ? new Promise(resolve => { releaseObservation = resolve; }) : reply(body);
    h.chrome.tabs.update = async () => {
      observation = h.emit(h.state(), 'navigation_state', {identity: {scheme: 'https:', host: 'doi.org'}, route: 'direct'});
      await flush(); assert(releaseObservation); // An observation holds the network mutex.
      throw new Error('PRIVATE_NAVIGATION_ERROR');
    };
    try {
      assert.equal((await h.runCommand()).ok, false);
      assert.equal(h.active, false);
      const failures = h.requests.filter(r => r.event_type === 'browser_path_failure');
      assert.equal(failures.length, 1); assert.deepEqual(failures[0].payload, {reason: 'navigation_failed'});
    } finally { releaseObservation(null); await observation; }
    // No local retirement can race the in-flight POST; a subsequent command
    // observes application revocation and removes the unusable old authority.
    h.reply = body => body.event_type === 'tab_ready' ? {command: {type: 'START', task_id: TASK, plan: plan()}} : null;
    assert.equal(await h.emit(h.state(), 'tab_ready', {}), false);
    assert.equal(h.data.claimedHandoff, undefined);
    cases++;
  }
  assert.equal(cases, 33); // All reviewed A6-A9 executable cases remain.
  // Generic publisher blob controls remain independent of EBSCO record authority.
  const publisherBlob = BLOB.replace('research.ebsco.com', 'publisher.example');
  for (const mode of ['armed', 'empty_referrer', 'unarmed', 'cross_origin', 'http_blob', 'opaque',
    'expired', 'future_start', 'moved_tab', 'other_task', 'competitor', 'other_extension',
    'wrong_referrer', 'different_final_blob', 'fragment']) {
    const h = harness(); await h.ready(); await h.navigate(LANDING);
    if (mode !== 'unarmed') assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    const item = download(publisherBlob, LANDING);
    item.filename = '/synthetic/download/EBSCO-FullText.pdf'; item.fileSize = item.totalBytes = 3100000;
    if (mode === 'empty_referrer') item.referrer = '';
    if (mode === 'cross_origin') item.url = item.finalUrl = publisherBlob.replace('publisher.example', 'other.example');
    if (mode === 'http_blob') item.url = item.finalUrl = publisherBlob.replace('blob:https:', 'blob:http:');
    if (mode === 'opaque') item.url = item.finalUrl = 'blob:null/12345678-1234-1234-1234-123456789abc';
    if (mode === 'expired') h.data.browserAcquisition.userArm.armedAt -= 20000;
    if (mode === 'future_start') item.startTime = new Date(Date.now() + 20000).toISOString();
    if (mode === 'moved_tab') h.tab.url = LANDING + '/other';
    if (mode === 'other_task') h.data.claimedHandoff.taskId = OTHER;
    if (mode === 'competitor') h.competitor = true;
    if (mode === 'other_extension') item.byExtensionId = 'another-extension';
    if (mode === 'wrong_referrer') item.referrer = LANDING + '/other';
    if (mode === 'different_final_blob') item.finalUrl = publisherBlob.replace('789abc', '789def');
    if (mode === 'fragment') item.url = item.finalUrl = publisherBlob + '#secret';
    await h.created(item);
    const reports = h.requests.filter(r => r.event_type === 'download_candidate');
    assert.equal(reports.length, ['armed', 'empty_referrer', 'competitor'].includes(mode) ? 1 : 0, mode);
    if (reports.length) {
      const payload = reports[0].payload;
      assert.equal(payload.transport_kind, 'blob'); assert.equal(payload.download_origin, 'https://publisher.example');
      assert.equal(payload.url, null); assert.equal(payload.final_url, null); assert.equal(payload.ownership, 'user_arm');
      assert.equal(payload.provider_record_url, null);
    }
    assert(!JSON.stringify(h.requests).includes(publisherBlob)); assert(!JSON.stringify(h.data).includes(publisherBlob)); cases++;
  }
  {
    const h = harness(); await h.ready(); await h.navigate(LANDING);
    await h.message({type: 'task_action', action: 'arm', choice_id: null});
    const first = download(publisherBlob, LANDING); first.state = 'in_progress'; await h.created(first);
    assert.equal(h.data.browserAcquisition.candidate.id, 7);
    await h.created({...first, id: 8});
    assert.equal(h.data.browserAcquisition.ambiguous, true);
    assert.equal(h.data.browserAcquisition.candidate, null);
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 0); cases++;
  }
  async function approvedRecord(h, single = false, armDelay = 0, armed = true) {
    await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}}
      : body.event_type === 'resolver_choice_request' ? {command: {type: 'CHOOSE', task_id: TASK, choice_id: 0}} : null;
    await h.message({type: 'task_action', action: 'fallback', choice_id: null});
    await h.navigate(REDIRECT);
    assert.equal((await h.contentMessage({type: 'resolver_context'})).doi, plan().doi);
    const choices = [{category: 'SmartLinks', label: 'EBSCOhost SmartLinks', target: RECORD}];
    if (!single) choices.push({category: 'FullText', label: 'Other provider', target: PDF});
    assert.equal((await h.contentMessage({type: 'resolver_observation', pageUrl: REDIRECT,
      human_required: false, unavailable: null, choices})).ok, true);
    if (!single) {
      assert.equal(h.tab.url, REDIRECT);
      assert.equal((await h.message({type: 'task_action', action: 'choose', choice_id: 0})).ok, true);
    }
    assert.equal(h.tab.url, RECORD);
    await h.navigate(RECORD);
    assert.equal((await h.contentMessage({type: 'ebsco_context'})).doi, plan().doi);
    if (armDelay) h.now += armDelay;
    if (armed) await h.message({type: 'task_action', action: 'arm', choice_id: null});
  }
  // Shipped content -> worker -> delayed blob evidence, without publication proof.
  for (const label of ['Accepted author manuscript', 'Preprint']) {
    const h = harness(); await approvedRecord(h, false, 0, false);
    const f = recordFixture();
    f.type.own = label;
    f.article.children = f.article.children.slice(0, 4); // DOI and type text only.
    f.body.children = f.body.children.filter(node => node !== f.year);
    const replies = [];
    const content = execute('ebsco_record_content.js', RECORD, f.body, undefined, (message, reply) => {
      h.contentMessage(message).then(result => { replies.push(result); reply(result); });
    });
    await flush();
    content.click(f.entry); content.click(f.button); await flush();
    assert.equal(replies.at(-1).ok, true, label);
    const action = content.messages.find(message => message.type === 'ebsco_pdf_action');
    assert.deepEqual(Object.keys(action.record), ['doi']);
    assert.equal(action.record.doi, plan().doi);
    h.now = action.action_time + 24000;
    const item = download(BLOB, RECORD); item.startTime = new Date(h.now).toISOString();
    await h.created(item);
    const reports = h.requests.filter(request => request.event_type === 'download_candidate');
    assert.equal(reports.length, 1, label);
    assert.equal(reports[0].payload.attribution, 'ebsco_pdf_action');
    assert.equal(reports[0].payload.observed_doi, plan().doi);
    assert.equal(reports[0].payload.provider_record_url, RECORD);
    assert.equal(reports[0].payload.start_time - reports[0].payload.action_time, 24000);
    cases++;
  }
  // Independent action/preparation clocks; shipped worker, no real downloads.
  for (const mode of ['delayed', 'boundary', 'no_action', 'late_action', 'over_bound',
    'before_action', 'new_document', 'reload', 'wrong_task', 'wrong_tab', 'wrong_record',
    'competitor', 'wrong_referrer', 'unsafe_referrer', 'wrong_origin', 'other_extension',
    'authority_loss', 'wrong_doi', 'wrong_category', 'second_candidate', 'completion_navigation',
    'unrelated_then_valid', 'generic_delayed', 'generic_boundary', 'late_completion', 'repeated_action',
    'expired_proof_rearm', 'race_navigation', 'race_task', 'completion_competitor']) {
    const h = harness(); h.now = Date.now();
    const generic = mode.startsWith('generic_');
    if (generic) {
      await h.ready(); await h.navigate(LANDING);
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    } else await approvedRecord(h, false, 2000);
    const armedAt = h.now; h.now += mode === 'late_action' ? 10001 : 1000;
    const actionTime = h.now;
    const action = {type: 'ebsco_pdf_action', task_id: TASK, navigation_epoch: h.data.browserAcquisition.navigationEpoch, pageUrl: RECORD, action_time: actionTime,
      record: {doi: plan().doi}};
    if (mode === 'wrong_doi') action.record.doi = '10.9999/wrong';
    if (['race_navigation', 'race_task'].includes(mode)) {
      let release; const get = h.chrome.tabs.get;
      h.chrome.tabs.get = async id => {
        h.chrome.tabs.get = get;
        await new Promise(resolve => { release = resolve; }); return get(id);
      };
      const pending = h.contentMessage(action); await flush(); assert(release);
      h.now += 1;
      if (mode === 'race_navigation') await h.navigate(RECORD);
      else h.data.claimedHandoff.taskId = OTHER;
      release(); assert.equal((await pending).ok, false, mode);
    } else if (!generic && mode !== 'no_action') {
      assert.equal((await h.contentMessage(action)).ok, mode !== 'wrong_doi', mode);
    }
    if (mode === 'repeated_action') {
      h.now += 1000;
      assert.equal((await h.contentMessage({...action, action_time: h.now})).ok, false);
    }
    h.now = generic ? armedAt + (mode === 'generic_boundary' ? 10000 : 24000)
      : actionTime + (mode === 'boundary' ? 120000 : mode === 'over_bound' ? 120001 : 24000);
    if (mode === 'expired_proof_rearm') {
      h.now = actionTime + 120001;
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, false);
      h.now += 24000;
    }
    const item = download(generic ? PDF : BLOB, generic ? LANDING : 'https://research.ebsco.com/');
    item.startTime = new Date(mode === 'before_action' ? actionTime - 1 : h.now).toISOString();
    if (mode === 'new_document') await h.navigate(PDF);
    if (mode === 'reload') await h.navigate(RECORD);
    if (mode === 'wrong_task') h.data.claimedHandoff.taskId = OTHER;
    if (mode === 'wrong_tab') h.chrome.tabs.get = async () => ({...h.tab, id: 99});
    if (mode === 'wrong_record') h.tab.url = RECORD.replace('record8', 'record9');
    if (mode === 'competitor') h.competitor = true;
    if (mode === 'wrong_referrer') item.referrer = 'https://research.ebsco.com/help';
    if (mode === 'unsafe_referrer') item.referrer = 'https://user:secret@research.ebsco.com/';
    if (mode === 'wrong_origin') item.url = item.finalUrl = BLOB.replace('research.ebsco.com', 'other.example');
    if (mode === 'other_extension') item.byExtensionId = 'other-extension';
    if (mode === 'authority_loss') delete h.data.claimedHandoff;
    if (mode === 'wrong_category') h.data.browserAcquisition.category = 'Other';
    if (mode === 'unrelated_then_valid') {
      await h.created({...item, id: 99, referrer: 'https://other.example/'});
    }
    if (['second_candidate', 'completion_navigation', 'late_completion', 'completion_competitor'].includes(mode)) item.state = 'in_progress';
    await h.created(item);
    if (mode === 'second_candidate') {
      assert.equal(h.data.browserAcquisition.candidate?.id, item.id);
      await h.created({...item, id: 8});
      assert.equal(h.data.browserAcquisition.ambiguous, true);
      assert.equal(h.data.browserAcquisition.candidate, null);
      assert(h.requests.some(r => r.payload.reason === 'ambiguous_download_ownership'));
    }
    if (mode === 'completion_navigation') {
      assert.equal(h.data.browserAcquisition.candidate?.id, item.id);
      await h.navigate(RECORD); item.state = 'complete'; h.downloads.set(item.id, item);
      for (const fn of h.listeners.changed) fn({id: item.id, state: {current: 'complete'}});
      await flush();
    }
    if (['late_completion', 'completion_competitor'].includes(mode)) {
      assert.equal(h.data.browserAcquisition.candidate?.id, item.id);
      h.now += 150000; if (mode === 'completion_competitor') h.competitor = true;
      item.state = 'complete'; h.downloads.set(item.id, item);
      for (const fn of h.listeners.changed) fn({id: item.id, state: {current: 'complete'}});
      await flush();
    }
    const reports = h.requests.filter(r => r.event_type === 'download_candidate');
    const valid = ['delayed', 'boundary', 'generic_boundary', 'late_action', 'late_completion', 'repeated_action', 'competitor', 'completion_competitor', 'unrelated_then_valid'].includes(mode);
    assert.equal(reports.length, valid ? 1 : 0, mode);
    if (valid && !generic) {
      const p = reports[0].payload;
      if (mode === 'delayed') providerEvidence = p;
      assert.equal(p.ownership, 'provider_action'); assert.equal(p.attribution, 'ebsco_pdf_action');
      assert(!Object.hasOwn(p, 'arm_time')); assert.equal(p.action_time, actionTime);
      assert.equal(p.navigation_time, armedAt - 2000);
      assert.equal(p.start_time - p.action_time, mode === 'boundary' ? 120000 : 24000);
      assert.equal(h.data.browserAcquisition.userArm, null);
    }
    cases++;
  }
  for (const mode of ['valid', 'single_choice', 'late_record_event', 'racing_record_store', 'wrong_doi', 'missing_doi', 'malformed_record',
    'wrong_task', 'wrong_tab', 'subframe', 'other_extension', 'other_record', 'no_arm', 'invalid_task', 'extra_record_field']) {
    const h = harness(); await approvedRecord(h, mode === 'single_choice');
    const action = {type: 'ebsco_pdf_action', task_id: TASK, navigation_epoch: h.data.browserAcquisition.navigationEpoch, pageUrl: RECORD, action_time: Date.now(),
      record: {doi: plan().doi}};
    const sender = {};
    if (mode === 'wrong_doi') action.record.doi = '10.9999/wrong';
    if (mode === 'missing_doi') delete action.record.doi;
    if (mode === 'malformed_record') action.record = null;
    if (mode === 'wrong_task') action.task_id = OTHER;
    if (mode === 'wrong_tab') sender.tab = {id: 99};
    if (mode === 'subframe') sender.frameId = 1;
    if (mode === 'other_extension') sender.id = 'other-extension';
    if (mode === 'other_record') action.pageUrl = RECORD.replace('record8', 'record9');
    if (mode === 'no_arm') h.data.browserAcquisition.userArm = null;
    if (mode === 'invalid_task') action.task_id = 'not-a-task';
    if (mode === 'extra_record_field') action.record.extra = 'unexpected';
    const item = download(BLOB, RECORD);
    if (mode === 'late_record_event') {
      await h.created(item);
      assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 0);
    }
    const valid = ['valid', 'single_choice', 'late_record_event', 'racing_record_store', 'no_arm'].includes(mode);
    if (mode === 'racing_record_store') {
      let release, paused = false;
      const set = h.chrome.storage.session.set;
      h.chrome.storage.session.set = async values => {
        if (values.browserAcquisition?.providerAction && !paused) {
          paused = true; await new Promise(resolve => { release = resolve; });
        }
        return set(values);
      };
      const pending = h.contentMessage(action, sender); await flush(); assert(release);
      await h.created(item); assert.equal(h.data.browserAcquisition.candidate, null);
      release(); assert.equal((await pending).ok, true); await flush();
    } else {
      assert.equal((await h.contentMessage(action, sender)).ok, valid, mode);
      if (mode !== 'late_record_event') await h.created(item);
    }
    const reports = h.requests.filter(r => r.event_type === 'download_candidate');
    assert.equal(reports.length, valid ? 1 : 0, mode);
    if (valid) {
      assert(!Object.hasOwn(reports[0].payload, 'version_labels'));
      assert(!Object.hasOwn(reports[0].payload, 'manifestation'));
      assert.equal(reports[0].payload.observed_doi, plan().doi);
      assert.equal(reports[0].payload.provider_record_url, RECORD);
    }
    assert(!JSON.stringify(h.requests).includes(BLOB)); cases++;
  }
  {
    const h = harness(); await h.ready(); await h.navigate(RECORD);
    assert.equal((await h.contentMessage({type: 'ebsco_context'})).ok, false); cases++;
  }
  {
    const h = harness(); h.reply = () => ({command: {type: 'START', task_id: TASK, plan: plan()}});
    await h.ready(); await h.navigate(RECORD);
    assert.equal((await h.contentMessage({type: 'ebsco_context'})).ok, false); cases++;
  }
  const base = RECORD + '?request-context=plink&db=bth';
  const modal = base + '&modal=details-bulk-download';
  for (const mode of ['modal', 'arm_in_modal', 'late_modal', 'sender_query', 'page_query',
    'other_record', 'other_context', 'other_origin', 'arbitrary_path', 'fragment', 'normalized_path',
    'wrong_sender', 'wrong_page', 'wrong_referrer', 'http_query_referrer', 'competing_tab']) {
    const h = harness(); await approvedRecord(h); await h.navigate(base);
    const valid = ['modal', 'arm_in_modal', 'late_modal', 'sender_query', 'page_query', 'competing_tab'].includes(mode);
    if (mode !== 'arm_in_modal') {
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    }
    // Same-document SPA transition: do not fire onCommitted or rewrite context.
    h.tab.url = modal;
    if (mode === 'other_record') h.tab.url = modal.replace('record8', 'record9');
    if (mode === 'other_context') h.tab.url = modal.replace('context7', 'context8');
    if (mode === 'other_origin') h.tab.url = modal.replace('research.ebsco.com', 'other.example');
    if (mode === 'arbitrary_path') h.tab.url = 'https://research.ebsco.com/help?modal=details-bulk-download';
    if (mode === 'fragment') h.tab.url += '#modal';
    if (mode === 'normalized_path') h.tab.url = modal.replace('/record8?', '/record9/../record8?');
    if (mode === 'arm_in_modal') {
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
      assert.equal(h.data.browserAcquisition.userArm.navigationUrl, base);
    }
    const sender = mode === 'sender_query' ? {url: base + '&ui=download'}
      : mode === 'wrong_sender' ? {url: modal.replace('record8', 'record9')} : {};
    const context = await h.contentMessage({type: 'ebsco_context'}, sender);
    const acceptsRecord = valid || ['wrong_page', 'wrong_referrer', 'http_query_referrer', 'competing_tab'].includes(mode);
    assert.equal(context.doi === plan().doi, acceptsRecord, mode);
    const action = {type: 'ebsco_pdf_action', task_id: TASK, navigation_epoch: h.data.browserAcquisition.navigationEpoch,
      pageUrl: mode === 'wrong_page' ? base.replace('context7', 'context8')
        : mode === 'page_query' ? base + '&ui=download' : base,
      action_time: Date.now(), record: {doi: plan().doi}};
    const item = download(mode === 'http_query_referrer' ? PDF : BLOB,
      mode === 'wrong_referrer' ? modal.replace('record8', 'record9') : modal);
    if (mode === 'competing_tab') h.competitor = true;
    if (mode === 'late_modal') await h.created(item);
    assert.equal((await h.contentMessage(action, sender)).ok, acceptsRecord && mode !== 'wrong_page', mode);
    if (mode !== 'late_modal') await h.created(item);
    const reports = h.requests.filter(r => r.event_type === 'download_candidate');
    assert.equal(reports.length, valid ? 1 : 0, mode);
    assert.equal(h.data.browserAcquisition.navigationUrl, base);
    if (valid) {
      const payload = reports[0].payload;
      assert.equal(payload.navigation_url, base); assert.equal(payload.provider_record_url, base);
      assert.equal(payload.referrer, base); assert.equal(payload.observed_doi, plan().doi);
      assert(!Object.hasOwn(payload, 'version_labels'));
      assert(!Object.hasOwn(payload, 'manifestation'));
      assert.equal(payload.url, null); assert.equal(payload.final_url, null);
      assert.equal(payload.ownership, 'provider_action');
    }
    assert(!JSON.stringify(h.requests).includes(BLOB)); assert(!JSON.stringify(h.data).includes(BLOB));
    cases++;
  }
  for (const mode of ['root', 'root_modal', 'fulltext_root', 'empty', 'exact', 'spa',
    'other_origin_root', 'non_root_path', 'no_arm', 'expired_arm', 'wrong_tab', 'competing_tab',
    'empty_category', 'unapproved_category', 'other_blob_origin', 'http_download',
    'malformed_blob', 'http_root', 'root_query', 'root_fragment', 'root_no_slash', 'direct_route',
    'wrong_record', 'missing_record']) {
    const h = harness(); await approvedRecord(h);
    const arm = structuredClone(h.data.browserAcquisition.userArm);
    assert.equal((await h.contentMessage({type: 'ebsco_pdf_action', task_id: TASK, navigation_epoch: h.data.browserAcquisition.navigationEpoch, pageUrl: RECORD,
      action_time: Date.now(), record: {doi: plan().doi}})).ok, true);
    const item = download(BLOB, 'https://research.ebsco.com/');
    item.byExtensionId = null; item.fileSize = item.totalBytes = 3 * 1024 * 1024 + 100;
    if (mode === 'root_modal') h.tab.url = RECORD + '?modal=details-bulk-download';
    if (mode === 'fulltext_root') h.data.browserAcquisition.category = 'FullText';
    if (mode === 'empty') item.referrer = '';
    if (mode === 'exact') item.referrer = RECORD;
    if (mode === 'spa') item.referrer = RECORD + '?modal=details-bulk-download';
    if (mode === 'other_origin_root') item.referrer = 'https://other.example/';
    if (mode === 'non_root_path') item.referrer = 'https://research.ebsco.com/foo';
    if (mode === 'no_arm') h.data.browserAcquisition.userArm = null;
    if (mode === 'expired_arm') assert.equal(h.data.browserAcquisition.userArm, null);
    if (mode === 'wrong_tab') h.chrome.tabs.get = async () => ({...h.tab, id: 99});
    if (mode === 'competing_tab') h.competitor = true;
    if (mode === 'empty_category') h.data.browserAcquisition.category = '';
    if (mode === 'unapproved_category') h.data.browserAcquisition.category = 'Other';
    if (mode === 'other_blob_origin') item.url = item.finalUrl = BLOB.replace('research.ebsco.com', 'other.example');
    if (mode === 'http_download') item.url = item.finalUrl = PDF;
    if (mode === 'malformed_blob') item.url = item.finalUrl = 'blob:https://research.ebsco.com/not-a-uuid';
    if (mode === 'http_root') item.referrer = 'http://research.ebsco.com/';
    if (mode === 'root_query') item.referrer += '?modal=download';
    if (mode === 'root_fragment') item.referrer += '#download';
    if (mode === 'root_no_slash') item.referrer = 'https://research.ebsco.com';
    if (mode === 'direct_route') h.data.browserAcquisition.route = 'direct';
    if (mode === 'wrong_record') h.tab.url = RECORD.replace('record8', 'record9');
    if (mode === 'missing_record') h.data.browserAcquisition.recordEvidence = null;
    const valid = ['root', 'root_modal', 'fulltext_root', 'empty', 'exact', 'spa', 'competing_tab', 'no_arm', 'expired_arm'].includes(mode);
    item.state = 'in_progress'; await h.created(item);
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 0);
    if (valid) {
      assert(h.data.browserAcquisition.candidate, mode + ' must retain an attributed candidate');
      assert.equal(h.data.browserAcquisition.candidate.id, item.id, mode);
      assert.deepEqual(h.data.browserAcquisition.candidate.userArm, null, mode);
      assert.equal(h.data.browserAcquisition.userArm, null, 'live arm is consumed');
    } else assert.equal(h.data.browserAcquisition.candidate, null, mode);
    item.state = 'complete'; h.downloads.set(item.id, item);
    for (const fn of h.listeners.changed) fn({id: item.id, state: {current: 'complete'}});
    await flush();
    const reports = h.requests.filter(r => r.event_type === 'download_candidate');
    assert.equal(reports.length, valid ? 1 : 0, mode);
    if (valid) {
      const payload = reports[0].payload;
      assert.equal(payload.ownership, 'provider_action');
      assert.equal(payload.referrer, mode === 'empty' ? '' : RECORD);
      assert.equal(payload.navigation_url, RECORD); assert.equal(payload.provider_record_url, RECORD);
      assert.equal(payload.download_origin, 'https://research.ebsco.com');
      assert.equal(payload.transport_kind, 'blob');
      assert.equal(payload.url, null); assert.equal(payload.final_url, null);
      assert(!Object.hasOwn(payload, 'version_labels'));
      assert.equal(payload.observed_doi, plan().doi);
      for (const fn of h.listeners.changed) fn({id: item.id, state: {current: 'complete'}});
      await flush(); assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 1);
    }
    assert(!JSON.stringify(h.requests).includes(BLOB)); assert(!JSON.stringify(h.data).includes(BLOB));
    cases++;
  }
  const linkout = 'https://apis.ebsco.com/public/linkout/v2/ftf?synthetic=browser-navigation';
  for (const mode of ['selected', 'multiple', 'no_choice', 'wrong_category', 'unapproved_route']) {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}}
      : body.event_type === 'resolver_choice_request' ? {command: {type: 'CHOOSE', task_id: TASK, choice_id: 1}} : null;
    if (mode !== 'unapproved_route') {
      assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok, true);
      await h.navigate(REDIRECT);
    }
    if (['selected', 'multiple', 'wrong_category'].includes(mode)) {
      const choices = [{category: mode === 'wrong_category' ? 'Other' : 'SmartLinks',
        label: 'EBSCOhost SmartLinks', target: linkout}];
      if (mode === 'multiple') choices.unshift({category: 'FullText', label: 'EBSCOhost Full Text', target: PDF});
      const accepted = await h.contentMessage({type: 'resolver_observation', pageUrl: REDIRECT,
        choices, human_required: false, unavailable: null});
      assert.equal(accepted.ok, mode !== 'wrong_category', mode);
      if (mode === 'multiple') {
        assert.equal(h.tab.url, REDIRECT);
        assert.equal(h.data.browserAcquisition.category, '');
        assert.equal((await h.message({type: 'task_action', action: 'choose', choice_id: 1})).ok, true);
      }
    }
    const selected = ['selected', 'multiple'].includes(mode);
    if (selected) {
      assert.equal(h.tab.url, linkout);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks');
      await h.navigate(linkout);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks');
    }
    await h.navigate(RECORD);
    assert.equal(h.data.browserAcquisition.category, selected ? 'SmartLinks' : '');
    assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, selected, mode);
    assert.equal((await h.contentMessage({type: 'ebsco_context'})).doi === plan().doi, selected, mode);
    // Public linkout is navigation only; the only fetches remain loopback handoff.
    assert(h.statusRequests.every(r => r.url.startsWith(ORIGIN + '/browser-handoff/')));
    cases++;
  }
  for (const mode of ['context_commit_race', 'rejected_navigation']) {
    const h = harness(); await h.ready(); await h.navigate(LANDING);
    const stable = structuredClone(h.data.browserAcquisition), update = h.chrome.tabs.update;
    let resolverUrl, attempts = 0;
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
    h.chrome.tabs.update = async (id, value) => {
      if (!value.url.startsWith('https://resolver.ebsco.com/')) return update(id, value);
      attempts++; resolverUrl = value.url;
      // Pending exists before tabs.update; destination content has no authority
      // until the browser commits, even if tabs already reports the target URL.
      const stored = h.data.browserAcquisition;
      assert.equal(stored.pendingNavigation.target, value.url);
      assert.equal(stored.navigationUrl, stable.navigationUrl);
      assert.equal(stored.route, 'direct');
      h.tab.url = value.url;
      assert.equal((await h.contentMessage({type: 'resolver_context'})).ok, false);
      if (mode === 'rejected_navigation') { h.tab.url = stable.navigationUrl; throw new Error('Synthetic navigation rejection'); }
      await update(id, value);
      assert.equal(h.data.browserAcquisition.pendingNavigation, null);
      assert.equal(h.data.browserAcquisition.navigationUrl, value.url);
      assert.equal(h.data.browserAcquisition.navigationEpoch, stable.navigationEpoch + 1);
    };
    assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok,
      mode === 'context_commit_race');
    assert.equal(attempts, 1);
    if (mode === 'rejected_navigation') {
      assert.deepEqual(h.data.browserAcquisition, stable);
      for (let n = 0; n < 4; n++) {
        assert.equal((await h.contentMessage({type: 'resolver_context'})).ok, false);
      }
      assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok, false);
      assert.equal(attempts, 1); assert.equal(h.data.browserAcquisition.route, 'direct');
      assert.equal(h.requests.filter(r => r.event_type === 'browser_path_failure').length, 1);
    } else {
      assert.equal(h.data.browserAcquisition.route, 'xmu');
      assert.equal(h.data.browserAcquisition.navigationUrl, resolverUrl);
      // The bounded content retry now obtains only the frozen task DOI.
      assert.deepEqual(structuredClone(await h.contentMessage({type: 'resolver_context'})), {task_id: TASK, doi: plan().doi});
      h.tab.url = REDIRECT; // Same document: no h.navigate/onCommitted here.
      assert.equal(h.data.browserAcquisition.navigationUrl, resolverUrl);
      assert.equal((await h.contentMessage({type: 'resolver_observation', pageUrl: REDIRECT,
        choices: [{category: 'SmartLinks', label: 'Find this article in full text from EBSCOhost SmartLinks', target: linkout}],
        human_required: false, unavailable: null})).ok, true);
      assert.equal(h.data.browserAcquisition.choices.length, 0);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks'); assert.equal(h.tab.url, linkout);
      await h.navigate(linkout); await h.navigate(RECORD);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks');
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    }
    cases++;
  }
  for (const mode of ['forward', 'same_result', 'same_redirect', 'wrong_doi', 'wrong_opid', 'wrong_customer',
    'wrong_group', 'wrong_profile', 'extra_query', 'duplicate_query', 'other_path', 'other_origin', 'reverse',
    'arbitrary_sender', 'wrong_sender_doi', 'wrong_sender_origin', 'wrong_tab', 'subframe', 'other_extension']) {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
    assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok, true);
    const resultUrl = h.tab.url;
    let pageUrl = mode === 'same_result' || mode === 'reverse' ? resultUrl : REDIRECT;
    const sender = {url: ['same_redirect', 'reverse'].includes(mode) ? REDIRECT : resultUrl};
    if (mode === 'wrong_doi') pageUrl = REDIRECT.replace(encodeURIComponent(plan().doi), '10.9999%2Fwrong');
    if (mode === 'wrong_opid') pageUrl = REDIRECT.replace('x-opid=45yels', 'x-opid=other');
    if (mode === 'wrong_customer') pageUrl = REDIRECT.replace('customer=s1215021', 'customer=other');
    if (mode === 'wrong_group') pageUrl = REDIRECT.replace('group=main', 'group=other');
    if (mode === 'wrong_profile') pageUrl = REDIRECT.replace('profile=ftf', 'profile=other');
    if (mode === 'extra_query') pageUrl += '&extra=value';
    if (mode === 'duplicate_query') pageUrl += '&profile=ftf';
    if (mode === 'other_path') pageUrl = REDIRECT.replace('/redirect?', '/unrelated?');
    if (mode === 'other_origin') pageUrl = REDIRECT.replace('resolver.ebsco.com', 'other.example');
    if (mode === 'arbitrary_sender') sender.url = 'https://resolver.ebsco.com/help';
    if (mode === 'wrong_sender_doi') sender.url = resultUrl.replace(encodeURIComponent(plan().doi), '10.9999%2Fwrong');
    if (mode === 'wrong_sender_origin') sender.url = resultUrl.replace('resolver.ebsco.com', 'other.example');
    if (mode === 'wrong_tab') sender.tab = {id: 99};
    if (mode === 'subframe') sender.frameId = 1;
    if (mode === 'other_extension') sender.id = 'another-extension';
    h.tab.url = pageUrl; // Same document; source URL deliberately stays fixed.
    const context = await h.contentMessage({type: 'resolver_context'}, sender);
    const invalidSender = ['arbitrary_sender', 'wrong_sender_doi', 'wrong_sender_origin', 'wrong_tab', 'subframe', 'other_extension'].includes(mode);
    assert.equal(context.doi === plan().doi, !invalidSender, mode);
    const before = structuredClone(h.data.browserAcquisition);
    const valid = ['forward', 'same_result', 'same_redirect'].includes(mode);
    assert.equal((await h.contentMessage({type: 'resolver_observation', pageUrl,
      choices: [{category: 'SmartLinks', label: 'Find this article in full text from EBSCOhost SmartLinks', target: linkout}],
      human_required: false, unavailable: null}, sender)).ok, valid, mode);
    if (valid) {
      assert.equal(h.data.browserAcquisition.choices.length, 0);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks'); assert.equal(h.tab.url, linkout);
      await h.navigate(linkout); await h.navigate(RECORD);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks');
      assert.equal((await h.contentMessage({type: 'ebsco_context'})).doi, plan().doi);
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    } else {
      assert.deepEqual(h.data.browserAcquisition, before);
      assert.equal(h.requests.filter(r => r.event_type === 'resolver_choices').length, 0);
      assert.equal(h.tab.url, pageUrl);
    }
    cases++;
  }
  for (const mode of ['forward', 'forward_settle', 'reverse', 'changed_doi']) {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
    assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok, true);
    const resultUrl = h.tab.url, sourceUrl = mode === 'reverse' ? REDIRECT : resultUrl;
    h.tab.url = sourceUrl;
    const body = e('body'), deliveries = [];
    const content = execute('resolver_content.js', sourceUrl, body, undefined, (message, reply) => {
      // Chrome MessageSender retains the document's injection/source URL.
      deliveries.push(h.contentMessage(message, {url: sourceUrl}).then(response => {
        if (reply) reply(response); return response;
      }));
    });
    assert.equal((await deliveries[0]).doi, plan().doi);
    assert.equal(content.activeObservers(), 1);
    const currentUrl = mode === 'reverse' ? resultUrl : mode === 'changed_doi'
      ? REDIRECT.replace(encodeURIComponent(plan().doi), '10.9999%2Fwrong') : REDIRECT;
    content.window.location.href = currentUrl; h.tab.url = currentUrl;
    const provider = mode === 'reverse'
      ? e('ul', '', {}, [e('li', '', {}, [e('a', 'Full Text', {href: linkout})])])
      : mode === 'forward_settle' ? e('a', 'Find this article in full text from EBSCOhost SmartLinks', {href: linkout})
      : e('p', '', {}, [e('a', 'Find this article in full text from EBSCOhost SmartLinks', {href: linkout})]);
    provider.parentElement = body; body.children.push(provider);
    if (mode === 'forward_settle') { provider.hasRects = false; provider.style.visibility = 'hidden'; }
    content.mutate();
    if (mode === 'forward_settle') {
      assert.equal(deliveries.length, 1); assert.equal(content.pendingFrames(), 1);
      assert.equal(h.data.browserAcquisition.category, '');
      // CSS/layout settles without another DOM mutation or content reinjection.
      provider.hasRects = true; provider.style.visibility = 'visible';
      content.renderFrame(); assert.equal(deliveries.length, 1);
      content.renderFrame(); assert.equal(content.frames.length, 2);
    }
    if (mode === 'reverse') content.finalSnapshot();
    const observations = content.messages.filter(m => m.type === 'resolver_observation');
    assert.equal(observations.length, mode === 'changed_doi' ? 0 : 1, mode);
    if (observations.length) {
      assert.equal(observations[0].pageUrl, currentUrl);
      assert.notEqual(observations[0].pageUrl, sourceUrl);
      assert.equal((await deliveries[1]).ok, mode.startsWith('forward'), 'combined SPA ' + mode);
    }
    content.finalSnapshot(); content.mutate();
    assert.equal(deliveries.length, mode === 'changed_doi' ? 1 : 2);
    if (mode.startsWith('forward')) {
      assert.equal(h.data.browserAcquisition.choices.length, 0);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks'); assert.equal(h.tab.url, linkout);
      await h.navigate(linkout); await h.navigate(RECORD);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks');
      assert.equal((await h.contentMessage({type: 'ebsco_context'})).doi, plan().doi);
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    } else {
      assert.equal(h.data.browserAcquisition.choices.length, 0);
      assert.equal(h.data.browserAcquisition.category, '');
    }
    cases++;
  }
  for (const mode of ['navigation_first', 'resolver_first', 'navigation_first_multiple', 'resolver_first_multiple', 'authority_replaced']) {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
    assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok, true);
    const resultUrl = h.tab.url, set = h.chrome.storage.session.set;
    let release, paused = false;
    h.chrome.storage.session.set = async values => {
      const state = values.browserAcquisition;
      if (!paused && state && (mode.startsWith('navigation_first') || mode === 'authority_replaced'
          ? state.navigationUrl === linkout && state.choices.length === 0 : state.choices.length > 0)) {
        paused = true; await new Promise(resolve => { release = resolve; });
      }
      await set(values);
      // Interleave a source-document observation with a new document commit.
      if (mode.startsWith('navigation_first') && state?.choices.length && !state.category) await flush();
    };
    const multiple = mode.endsWith('multiple');
    const choices = [{category: 'SmartLinks', label: 'Find this article in full text from EBSCOhost SmartLinks', target: linkout}];
    if (multiple) choices.push({category: 'FullText', label: 'Other provider', target: PDF});
    const observation = () => h.contentMessage({type: 'resolver_observation', pageUrl: REDIRECT,
      choices,
      human_required: false, unavailable: null}, {url: resultUrl});
    let navigation, response;
    if (mode.startsWith('navigation_first') || mode === 'authority_replaced') {
      navigation = h.navigate(linkout); await flush(); assert(release);
      response = observation(); await flush();
    } else {
      response = observation(); await flush(); assert(release);
      navigation = h.navigate(linkout); await flush();
    }
    if (mode === 'authority_replaced') h.data.claimedHandoff.taskId = OTHER;
    release(); await navigation; const reply = await response; await flush();
    if (mode.startsWith('navigation_first') || mode === 'authority_replaced') assert.equal(reply.ok, false, mode);
    assert.equal(h.data.browserAcquisition.category, '', mode);
    assert.equal(h.data.browserAcquisition.choices.length, 0, 'source-document choices cannot survive commit');
    assert.equal(h.updates.filter(url => url === linkout).length, 0, 'stale observation cannot select a provider');
    assert.equal((await h.contentMessage({type: 'ebsco_context'})).ok, false);
    assert.equal(h.requests.filter(r => r.event_type === 'browser_path_failure').length, 0, mode);
    cases++;
  }
  {
    const h = harness(); await h.ready();
    h.reply = body => body.event_type === 'publisher_fallback_request'
      ? {command: {type: 'PUBLISHER_EXHAUSTED', task_id: TASK}} : null;
    assert.equal((await h.message({type: 'task_action', action: 'fallback', choice_id: null})).ok, true);
    const resultUrl = h.tab.url, update = h.chrome.tabs.update;
    h.chrome.tabs.update = async (id, value) => {
      await update(id, value);
      if (value.url === linkout) {
        // Chrome accepts the choice, then reaches the record before returning.
        // This must neither deadlock nor restore the obsolete linkout URL.
        await h.navigate(RECORD);
        assert.equal(h.data.browserAcquisition.navigationUrl, RECORD);
      }
    };
    assert.equal((await h.contentMessage({type: 'resolver_observation', pageUrl: REDIRECT,
      choices: [{category: 'SmartLinks', label: 'EBSCOhost SmartLinks', target: linkout}],
      human_required: false, unavailable: null}, {url: resultUrl})).ok, true);
    assert.equal(h.data.browserAcquisition.navigationUrl, RECORD);
    assert.equal(h.data.browserAcquisition.category, 'SmartLinks');
    assert.equal(h.data.browserAcquisition.choices.length, 0);
    assert.equal((await h.contentMessage({type: 'ebsco_context'})).doi, plan().doi);
    assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
    assert.equal(h.requests.filter(r => r.event_type === 'browser_path_failure').length, 0);
    cases++;
  }
  {
    const h = harness(); await h.ready(); await resolverChoices(h);
    h.data.claimedHandoff.taskId = OTHER;
    const nextPlan = {...plan(), task_id: OTHER};
    const update = h.chrome.tabs.update;
    h.chrome.tabs.update = async (id, value) => {
      assert.equal(h.data.browserAcquisition.taskId, OTHER, 'new task and pending exist before browser navigation');
      assert.equal(h.data.browserAcquisition.navigationUrl, null);
      assert.equal(h.data.browserAcquisition.pendingNavigation.target, value.url);
      await update(id, value);
    };
    assert.equal(await h.runtime.start(nextPlan), true);
    assert.equal(h.data.browserAcquisition.taskId, OTHER);
    assert.equal(h.data.browserAcquisition.navigationUrl, nextPlan.direct_url);
    assert.equal(h.data.browserAcquisition.route, 'direct');
    assert.equal(h.data.browserAcquisition.category, '');
    assert.equal(h.data.browserAcquisition.choices.length, 0);
    assert.equal(await h.runtime.start(nextPlan), false, 'active task cannot reset');
    cases++;
  }
  if (process.argv.includes('--provider-evidence')) {
    process.stdout.write('Provider evidence: ' + JSON.stringify(providerEvidence) + '\n');
  }
  cases += await runProviderActionCases(approvedRecord);
  cases += await runProviderLateMetadataCases(approvedRecord);
  cases += await runTabCloseCases(approvedRecord);
  cases += await runClaimCloseCases();
  cases += await runClosedClaimStorageCases();
  cases += await runDownloadDeliveryCases();
  cases += await runDownloadObservationCases();
  cases += await runActivationTabCases();
  cases += await runHandoffCases();
  cases += await runNavigationCases(approvedRecord);
  cases += await runNavigationFixCases();
  cases += await runNavigationSequenceCases();
  const contentCases = runContentCases();
  process.stdout.write(`Shipped content adapters: ${contentCases} cases passed. Synthetic DOM only.\n`);
  process.stdout.write(`Shipped worker simulation: ${cases} cases passed. No browser/network used.\n`);
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
