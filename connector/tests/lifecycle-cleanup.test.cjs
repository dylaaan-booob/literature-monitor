'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run ./connector/build.sh first: exercise the applied upstream source, not a copy.
const sourceRoot = path.join(__dirname, '../.work/upstream/src/browserExt');
const translateSource = fs.readFileSync(path.join(sourceRoot, 'offscreen/offscreenTranslate.js'), 'utf8');
const managerSource = fs.readFileSync(path.join(sourceRoot, 'background/offscreenManager.js'), 'utf8');
const backgroundSource = fs.readFileSync(path.join(sourceRoot, 'background.js'), 'utf8');

function event() {
  const listeners = [];
  return {
    addListener(fn) { listeners.push(fn); },
    async emit(...args) { for (const fn of listeners) await fn(...args); }
  };
}

function offscreenEnvironment() {
  const handlers = new Map();
  const errors = [];
  const browser = {tabs: {onRemoved: event()}};
  function Web() {}
  function ItemSaver() {}
  const Zotero = {
    isManifestV3: true, Translate: {Web, ItemSaver},
    OffscreenSandbox: {addMessageListener(name, fn) { handlers.set(name, fn); }},
    Promise: {defer: () => ({promise: Promise.resolve()})},
    debug() {}, logError(error) { errors.push(error.message); }
  };
  const context = vm.createContext({
    Zotero, browser, setInterval() {},
    self: {clients: {matchAll: async () => [{url: 'offscreen/offscreen.html', postMessage() {}}]}}
  });
  vm.runInContext(translateSource, context);
  Zotero.OffscreenTranslate.init();
  async function restartManager() {
    vm.runInContext(managerSource, context);
    Zotero.OffscreenManager._messaging = {
      sendMessage(name, payload) { return handlers.get(name)(...payload); }
    };
    await Zotero.OffscreenManager.init();
    return Zotero.OffscreenManager;
  }
  return {Zotero, browser, errors, restartManager, translate: Zotero.OffscreenTranslate};
}

test('Translator cleanup preserves live Frames and reports only actual deletions', () => {
  const {translate} = offscreenEnvironment();
  const live = {0: {save: 'in progress'}, 7: {translation: 'in progress'}};
  translate.translateInstances = {11: live, 12: {0: {}}, 13: {0: {}}};
  const candidates = Object.keys(translate.translateInstances);
  const newTab = {0: {}};
  translate.translateInstances[14] = newTab;
  translate.onTabClosed('13');
  assert.deepEqual(Array.from(translate.onTranslateCleanup([11, '15'], candidates)), ['12']);
  assert.equal(translate.translateInstances[11], live);
  assert.equal(translate.translateInstances[14], newTab);
  assert.deepEqual(Array.from(translate.onTranslateCleanup(['11', 14], candidates)), []);
  translate.onTabClosed(11);
  assert.equal(translate.translateInstances[11], undefined);
});

test('Manager handles loading tabs, query races, restart and cleanup diagnostics', async () => {
  const env = offscreenEnvironment();
  const {translate, browser, errors} = env;
  const live = {0: {}, 8: {}};
  translate.translateInstances = {1: live, 2: {0: {}}, 3: {0: {}}};
  let manager = await env.restartManager();
  let releaseQuery, queryOptions, queryStarted;
  const querying = new Promise(resolve => { queryStarted = resolve; });
  browser.tabs.query = options => {
    queryOptions = options;
    queryStarted();
    return new Promise(resolve => { releaseQuery = resolve; });
  };
  const cleanup = manager.cleanup();
  await querying;
  const newTab = {0: {}};
  translate.translateInstances[4] = newTab;
  await browser.tabs.onRemoved.emit(3);
  releaseQuery([{id: 1, status: 'loading', windowType: 'popup'}, {id: 3}]);
  assert.deepEqual({...queryOptions}, {});
  assert.deepEqual(Array.from(await cleanup), ['2']);
  assert.equal(translate.translateInstances[1], live);
  assert.equal(translate.translateInstances[4], newTab);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /\["2"\]/);

  browser.tabs.query = async () => [{id: 1, status: 'loading'}, {id: 4}];
  manager = await env.restartManager(); // The offscreen instance survives a new worker manager.
  assert.deepEqual(Array.from(await manager.cleanup()), []);
  assert.equal(translate.translateInstances[1], live);
  assert.equal(translate.translateInstances[4], newTab);
  browser.tabs.query = async () => { throw new Error('Tabs query failed'); };
  await assert.rejects(manager.cleanup(), /Tabs query failed/);
  assert.deepEqual(Object.keys(translate.translateInstances), ['1', '4']);
  manager.getOffscreenPage = async () => undefined;
  assert.equal(await manager.cleanup(), false);
});

test('Background cleans both cache maps and preserves records created during the query', async () => {
  const live = {url: 'https://example.org/live'};
  const restored = {1: live, 2: {}, 3: {}};
  const injections = {1: {0: {}}, 5: {0: {}}};
  const removed = event();
  const browser = {
    tabs: {onRemoved: removed, onActivated: event()},
    action: {onClicked: event()},
    webNavigation: {onCommitted: event(), onDOMContentLoaded: event(), onHistoryStateUpdated: event()},
    management: {getSelf: async () => ({installType: 'development'})}
  };
  const Zotero = {
    isManifestV3: true,
    Utilities: {Connector: {createMV3PersistentObject: async () => restored}},
    Messaging: {addMessageListener() {}},
    initDeferred: {promise: Promise.resolve()}, initGlobal() {}, logError() {}
  };
  let periodicCleanup;
  const context = vm.createContext({
    browser, Zotero, lifecycleInjections: injections,
    setInterval(fn) { periodicCleanup = fn; }
  });
  // Seed the private map without introducing a production export or copying cleanup code.
  assert.ok(backgroundSource.includes('var _tabInjections = {};'));
  vm.runInContext(backgroundSource.replace('var _tabInjections = {};', 'var _tabInjections = lifecycleInjections;'), context);
  await Zotero.Connector_Browser.init();
  let releaseQuery;
  browser.tabs.query = options => {
    assert.deepEqual({...options}, {});
    return new Promise(resolve => { releaseQuery = resolve; });
  };
  const cleanup = periodicCleanup();
  const newInfo = Zotero.Connector_Browser.resetTabInfo(4);
  const newInjection = injections[4] = {0: {}};
  await removed.emit(3);
  releaseQuery([{id: 1, status: 'loading'}, {id: 3}]);
  await cleanup;
  assert.deepEqual(Object.keys(restored), ['1', '4']);
  assert.deepEqual(Object.keys(injections), ['1', '4']);
  assert.equal(restored[1], live);
  assert.equal(restored[4], newInfo);
  assert.equal(injections[4], newInjection);
  browser.tabs.query = async () => [{id: 1}, {id: 4}];
  await periodicCleanup();
  await removed.emit('1');
  assert.deepEqual(Object.keys(restored), ['4']);
  assert.deepEqual(Object.keys(injections), ['4']);
});
