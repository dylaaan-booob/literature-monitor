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
const {runContentCases, recordFixture, RECORD, REDIRECT, execute, e} = require('./browser_companion_content_runtime.cjs');
const BLOB = 'blob:https://research.ebsco.com/12345678-1234-1234-1234-123456789abc';

function plan() {
  return {task_id: TASK, doi: '10.5555/test', direct_url: 'https://doi.org/10.5555/test'};
}

function harness() {
  const data = {claimedHandoff: {taskId: TASK, tabId: 42, origin: ORIGIN,
    eventCapability: 'a'.repeat(43), readyDelivered: false}};
  const requests = [], updates = [], downloads = new Map();
  const tab = {id: 42, url: ORIGIN + '/browser-handoff/' + TASK, incognito: false};
  const listeners = {};
  const event = name => ({addListener: fn => (listeners[name] ??= []).push(fn)});
  const h = {data, requests, updates, tab, downloads, listeners, competitor: false,
    active: true, statusRequests: [], downloadCalls: 0,
    reply: body => body.event_type === 'tab_ready' ? {command: {type: 'START', task_id: TASK, plan: plan()}} : null};
  h.status = async () => h.active ? 200 : 404;
  const chrome = {
    runtime: {id: 'synthetic-extension', getURL: name => 'chrome-extension://synthetic-extension/' + name,
      onMessage: event('message')},
    storage: {session: {
      setAccessLevel: async () => {}, get: async key => ({[key]: structuredClone(data[key])}),
      set: async values => Object.assign(data, structuredClone(values)),
      remove: async key => { delete data[key]; }
    }},
    tabs: {
      get: async id => { assert.equal(id, tab.id); return {...tab}; },
      update: async (id, value) => { assert.equal(id, tab.id); updates.push(value.url); tab.url = value.url; },
      query: async query => h.competitor && query.url ? [tab, {id: 99}] : [{...tab}]
    },
    action: {onClicked: event('click'), setBadgeText: async () => {}},
    webNavigation: {onBeforeNavigate: event('before'), onCommitted: event('committed'), onErrorOccurred: event('error')},
    downloads: {
      onCreated: event('created'), onChanged: event('changed'),
      search: async ({id}) => downloads.has(id) ? [structuredClone(downloads.get(id))] : [],
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
  const context = vm.createContext({Date: ClockDate, chrome, URL, URLSearchParams, TextEncoder, AbortController,
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
      assert.equal(url, ORIGIN + '/browser-handoff/events');
      const body = JSON.parse(options.body); requests.push(body);
      const reply = await h.reply(body);
      if (body.event_type === 'browser_path_failure') h.active = false;
      return {status: reply ? 200 : 204, headers: {get: () => 'application/json'},
        text: async () => reply ? JSON.stringify(reply) : ''};
    }});
  context.importScripts = name => vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context, {filename: name});
  vm.runInContext(fs.readFileSync(path.join(root, 'service_worker.js'), 'utf8'), context, {filename: 'service_worker.js'});
  h.runtime = vm.runInContext('browserAcquisition', context);
  h.emit = vm.runInContext('emitEvent', context);
  h.state = () => structuredClone(data.claimedHandoff);
  h.message = message => new Promise(resolve => {
    const sender = {id: chrome.runtime.id, url: chrome.runtime.getURL('task_popup.html')};
    assert(listeners.message.some(fn => fn(message, sender, resolve)));
  });
  h.contentMessage = (message, overrides = {}) => new Promise(resolve => {
    const sender = {id: chrome.runtime.id, frameId: 0, tab: {id: 42}, url: tab.url, ...overrides};
    assert(listeners.message.some(fn => fn(message, sender, resolve)));
  });
  h.ready = () => h.message({type: 'task_action', action: 'ready', choice_id: null});
  h.navigate = async url => {
    tab.url = url;
    for (const fn of listeners.committed) fn({tabId: 42, frameId: 0, url});
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

(async () => {
  let cases = 0, providerEvidence;
  {
    const h = harness(); assert.equal((await h.ready()).ok, true);
    assert.deepEqual(h.updates, [plan().direct_url]);
    assert.equal(h.data.claimedHandoff.readyDelivered, true);
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
    assert(h.requests.some(r => r.event_type === 'publisher_state')); cases++;
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
    assert.equal((await h.message({type: 'task_action', action: 'check', choice_id: null})).ok, true);
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
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, mode === 'armed' ? 1 : 0);
    assert.equal(h.data.browserAcquisition.userArm, null); cases++;
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
      // The stored path is still the last stable state while Chrome rejects.
      assert.equal(JSON.stringify(h.data.browserAcquisition), JSON.stringify(stable));
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
      assert.equal(h.data.browserAcquisition, undefined);
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
    assert.equal(h.data.claimedHandoff.readyDelivered, false);
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
    assert.equal(reports.length, ['armed', 'empty_referrer'].includes(mode) ? 1 : 0, mode);
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
  async function approvedRecord(h, single = false, armDelay = 0) {
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
    await h.message({type: 'task_action', action: 'arm', choice_id: null});
  }
  // Shipped content -> worker -> delayed blob evidence, without publication proof.
  for (const label of ['Accepted author manuscript', 'Preprint']) {
    const h = harness(); await approvedRecord(h);
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
    const action = {type: 'ebsco_pdf_action', task_id: TASK, pageUrl: RECORD, action_time: actionTime,
      record: {doi: plan().doi}};
    if (mode === 'wrong_doi') action.record.doi = '10.9999/wrong';
    if (['race_navigation', 'race_task'].includes(mode)) {
      let release; const set = h.chrome.storage.session.set;
      h.chrome.storage.session.set = async values => {
        if (values.browserEbscoRecord) await new Promise(resolve => { release = resolve; });
        return set(values);
      };
      const pending = h.contentMessage(action); await flush(); assert(release);
      h.now += 1;
      if (mode === 'race_navigation') await h.navigate(RECORD);
      else h.data.claimedHandoff.taskId = OTHER;
      release(); assert.equal((await pending).ok, false, mode);
    } else if (!generic && mode !== 'no_action') {
      assert.equal((await h.contentMessage(action)).ok, !['late_action', 'wrong_doi'].includes(mode), mode);
    }
    if (mode === 'repeated_action') {
      h.now += 1000;
      assert.equal((await h.contentMessage({...action, action_time: h.now})).ok, false);
    }
    h.now = generic ? armedAt + (mode === 'generic_boundary' ? 10000 : 24000)
      : actionTime + (mode === 'boundary' ? 120000 : mode === 'over_bound' ? 120001 : 24000);
    if (mode === 'expired_proof_rearm') {
      h.now = actionTime + 120001;
      assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
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
    const valid = ['delayed', 'boundary', 'generic_boundary', 'late_completion', 'repeated_action'].includes(mode);
    assert.equal(reports.length, valid ? 1 : 0, mode);
    if (valid && !generic) {
      const p = reports[0].payload;
      if (mode === 'delayed') providerEvidence = p;
      assert.equal(p.ownership, 'user_arm'); assert.equal(p.attribution, 'ebsco_pdf_action');
      assert.equal(p.arm_time, armedAt); assert.equal(p.action_time, actionTime);
      assert.equal(p.navigation_time, armedAt - 2000);
      assert.equal(p.start_time - p.action_time, mode === 'boundary' ? 120000 : 24000);
      assert.equal(h.data.browserAcquisition.userArm, null);
    }
    cases++;
  }
  for (const mode of ['valid', 'single_choice', 'late_record_event', 'racing_record_store', 'wrong_doi', 'missing_doi', 'malformed_record',
    'wrong_task', 'wrong_tab', 'subframe', 'other_extension', 'other_record', 'no_arm', 'invalid_task', 'extra_record_field']) {
    const h = harness(); await approvedRecord(h, mode === 'single_choice');
    const action = {type: 'ebsco_pdf_action', task_id: TASK, pageUrl: RECORD, action_time: Date.now(),
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
    const valid = ['valid', 'single_choice', 'late_record_event', 'racing_record_store'].includes(mode);
    if (mode === 'racing_record_store') {
      let release;
      const set = h.chrome.storage.session.set;
      h.chrome.storage.session.set = async values => {
        if (values.browserEbscoRecord) await new Promise(resolve => { release = resolve; });
        return set(values);
      };
      const pending = h.contentMessage(action, sender); await flush(); assert(release);
      await h.created(item); assert.equal(h.data.browserAcquisition.candidate.id, item.id);
      release(); assert.equal((await pending).ok, true);
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
    const valid = ['modal', 'arm_in_modal', 'late_modal', 'sender_query', 'page_query'].includes(mode);
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
    const action = {type: 'ebsco_pdf_action', task_id: TASK,
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
      assert.equal(payload.ownership, 'user_arm');
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
    assert.equal((await h.contentMessage({type: 'ebsco_pdf_action', task_id: TASK, pageUrl: RECORD,
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
    if (mode === 'expired_arm') h.data.browserAcquisition.userArm.armedAt -= 20000;
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
    if (mode === 'missing_record') delete h.data.browserEbscoRecord;
    const valid = ['root', 'root_modal', 'fulltext_root', 'empty', 'exact', 'spa'].includes(mode);
    item.state = 'in_progress'; await h.created(item);
    assert.equal(h.requests.filter(r => r.event_type === 'download_candidate').length, 0);
    if (valid || mode === 'missing_record') {
      assert(h.data.browserAcquisition.candidate, mode + ' must retain an attributed candidate');
      assert.equal(h.data.browserAcquisition.candidate.id, item.id, mode);
      assert.deepEqual(h.data.browserAcquisition.candidate.userArm, arm, mode);
      assert.equal(h.data.browserAcquisition.userArm, null, 'live arm is consumed');
    } else assert.equal(h.data.browserAcquisition.candidate, null, mode);
    item.state = 'complete'; h.downloads.set(item.id, item);
    for (const fn of h.listeners.changed) fn({id: item.id, state: {current: 'complete'}});
    await flush();
    const reports = h.requests.filter(r => r.event_type === 'download_candidate');
    assert.equal(reports.length, valid ? 1 : 0, mode);
    if (valid) {
      const payload = reports[0].payload;
      assert.equal(payload.ownership, 'user_arm');
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
      // Destination content runs inside tabs.update, before navigate saves.
      h.tab.url = value.url;
      assert.deepEqual(h.data.browserAcquisition, stable);
      assert.equal((await h.contentMessage({type: 'resolver_context'})).ok, false);
      if (mode === 'rejected_navigation') throw new Error('Synthetic navigation rejection');
      return update(id, value);
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
      assert.equal(h.data.browserAcquisition.choices.length, 1);
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
      assert.equal(h.data.browserAcquisition.choices.length, 1);
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
      assert.equal(h.data.browserAcquisition.choices.length, 1);
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
      // Let the old navigation snapshot overwrite a just-stored observation
      // before its single-choice continuation reloads state (FIX_6 failure).
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
    release(); await navigation; assert.equal((await response).ok, mode !== 'authority_replaced', mode); await flush();
    if (mode === 'authority_replaced') {
      assert.equal(h.data.browserAcquisition.category, ''); assert.equal(h.data.browserAcquisition.choices.length, 0);
      assert.equal((await h.contentMessage({type: 'ebsco_context'})).ok, false);
      cases++; continue;
    }
    assert.equal(h.data.browserAcquisition.category, multiple ? '' : 'SmartLinks', mode);
    assert.equal(h.data.browserAcquisition.choices.length, multiple ? 2 : 1, mode);
    if (multiple) {
      assert.equal(h.updates.filter(url => url === linkout).length, 0, 'no silent selection');
      assert.equal(h.requests.filter(r => r.event_type === 'resolver_choices').length, 1);
      h.reply = body => body.event_type === 'resolver_choice_request'
        ? {command: {type: 'CHOOSE', task_id: TASK, choice_id: 0}} : null;
      assert.equal((await h.message({type: 'task_action', action: 'choose', choice_id: 0})).ok, true);
      assert.equal(h.data.browserAcquisition.category, 'SmartLinks');
    }
    assert.equal(h.data.browserAcquisition.navigationUrl, linkout, mode);
    assert.equal(h.requests.filter(r => r.event_type === 'browser_path_failure').length, 0, mode);
    await h.navigate(RECORD);
    assert.equal((await h.contentMessage({type: 'ebsco_context'})).doi, plan().doi);
    assert.equal((await h.message({type: 'task_action', action: 'arm', choice_id: null})).ok, true);
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
    assert.equal(h.data.browserAcquisition.choices.length, 1);
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
      assert.equal(h.data.browserAcquisition.taskId, TASK, 'new task saves only after browser acceptance');
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
  const contentCases = runContentCases();
  process.stdout.write(`Shipped content adapters: ${contentCases} cases passed. Synthetic DOM only.\n`);
  process.stdout.write(`Shipped worker simulation: ${cases} cases passed. No browser/network used.\n`);
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
