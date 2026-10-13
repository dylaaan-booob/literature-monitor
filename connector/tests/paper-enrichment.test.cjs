'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const patch = fs.readFileSync(path.join(__dirname, '../patches/0003-literature-monitor-paper-enrichment.patch'), 'utf8');
function additions(file) {
  const section = patch.split(`--- a/${file}\n`)[1].split('\n--- a/')[0];
  return section.split('\n').filter(line => line.startsWith('+') && !line.startsWith('+++')).map(line => line.slice(1)).join('\n');
}
const sandboxSource = additions('src/browserExt/offscreen/offscreenTranslate.js');
const readOnlySource = sandboxSource.slice(sandboxSource.indexOf('async function literatureMonitorTranslateReadOnly'));
const pageSource = additions('src/common/inject/pageSaving.js');
function environment() {
  let nativeCalls = 0;
  const forbidden = () => { nativeCalls++; throw new Error('Native save must never be called'); };
  function Translator(metadata) { Object.assign(this, metadata); }
  Translator.RUN_MODE_IN_BROWSER = 1;
  Translator.TRANSLATOR_PASSING_PROPERTIES = [];
  const Zotero = {Date: {strToDate: () => ({order: 'dmy', year: '2024', month: 4, day: 17})}, Translator, isManifestV3: true, Translate: {Base: {prototype: {
    _translateTranslatorLoaded() { return [{DOI: '10.5555/paper', abstractNote: 'Full abstract', itemType: 'journalArticle', date: '17 May 2024', creators: [{creatorType: 'author', firstName: 'Ada', lastName: 'Lovelace'}]}]; }
  }}}, Connector: {callMethod: forbidden}, API: {createItem: forbidden}, ItemSaver: forbidden, PageSaving: {}};
  const context = {Zotero, Date, window: {}}; context.window.top = context.window;
  vm.createContext(context); vm.runInContext(readOnlySource, context); vm.runInContext(pageSource, context);
  const registration = sandboxSource.slice(sandboxSource.indexOf("this.addMessageListener('Translate.literatureMonitorEnrich'"), sandboxSource.indexOf("\n\t\t});") + 7);
  context.register = {addMessageListener(name, handler) { context.enrichmentHandler = handler; }};
  vm.runInContext(`(function(){${registration}}).call(register)`, context);
  const original = forbidden;
  const translate = {_translateTranslatorLoaded: original,
    async setTranslator(translator) { this.translator = [translator]; },
    setHandler(name, handler) { this.select = handler; },
    async translate(options) { this.options = options; return this._translateTranslatorLoaded(); }};
  return {context, Zotero, translate, original, nativeCalls: () => nativeCalls};
}
test('actual offscreen patch runs browser-only translation without saving', async () => {
  const env = environment();
  const items = await env.context.literatureMonitorTranslateReadOnly(env.translate, {runMode: 1});
  assert.equal(items[0].abstractNote, 'Full abstract');
  assert.deepEqual({...env.translate.options}, {libraryID: false, saveAttachments: false});
  assert.equal(env.translate._translateTranslatorLoaded, env.original);
  assert.equal(env.nativeCalls(), 0);
});
for (const runMode of [2, 4, undefined]) {
  test(`offscreen rejects remote or unknown mode ${runMode}`, async () => {
    const env = environment();
    await assert.rejects(env.context.literatureMonitorTranslateReadOnly(env.translate, {runMode}), /browser Translator/);
    assert.equal(env.nativeCalls(), 0);
  });
}
test('mode changing after translator load cannot enter RPC branch', async () => {
  const env = environment();
  env.translate.translate = async function() { this.translator[0].runMode = 2; return this._translateTranslatorLoaded(); };
  await assert.rejects(env.context.literatureMonitorTranslateReadOnly(env.translate, {runMode: 1}), /Remote translation/);
  assert.equal(env.translate._translateTranslatorLoaded, env.original);
  assert.equal(env.nativeCalls(), 0);
});
test('item selection fails without opening selection or save UI', async () => {
  const env = environment();
  env.translate.translate = async function() { this.select(); };
  await assert.rejects(env.context.literatureMonitorTranslateReadOnly(env.translate, {runMode: 1}), /Multiple Items/);
  assert.equal(env.nativeCalls(), 0);
});
test('actual page entrypoint returns only complete item fields without saving or sessions', async () => {
  const env = environment(); const page = env.Zotero.PageSaving;
  const metadata = {translatorID: 'translator', itemType: 'journalArticle', runMode: 1, serialize() { return this; }};
  page.translators = [metadata];
  page._initTranslate = async () => ({sendMessage: async (message, args) => {
    assert.equal(message, 'Translate.literatureMonitorEnrich');
    return env.context.enrichmentHandler(env.translate, args);
  }});
  page.onTranslate = page.translateAndSave = () => assert.fail('Save entrypoint called');
  const result = await page.enrich({translatorID: 'translator', expiresAt: Date.now() + 75000});
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {items: [{item_type: 'journalArticle', doi: '10.5555/paper', abstract: 'Full abstract', creators: [{creatorType: 'author', firstName: 'Ada', lastName: 'Lovelace'}], date: '17 May 2024', date_parts: {year: 2024, month: 5, day: 17}}]});
  assert.equal(page.sessionDetails, undefined);
  assert.equal(env.nativeCalls(), 0);
});
for (const itemType of ['multiple', 'webpage', 'attachment', 'note']) {
  test(`page entrypoint rejects ${itemType}`, async () => {
    const env = environment();
    env.Zotero.PageSaving.translators = [{translatorID: 'translator', itemType}];
    await assert.rejects(env.Zotero.PageSaving.enrich({translatorID: 'translator', expiresAt: Date.now() + 75000}), /Single literature/);
    assert.equal(env.nativeCalls(), 0);
  });
}
test('page entrypoint refuses expired invocation and subframes', async () => {
  const env = environment();
  await assert.rejects(env.Zotero.PageSaving.enrich({expiresAt: 0}), /expired/);
  env.context.window.top = {};
  await assert.rejects(env.Zotero.PageSaving.enrich({}), /top document/);
});

for (const [raw, parsed, expected] of [
  ['2024', {order: 'y', year: '2024'}, {year: 2024}],
  ['2024-05', {order: 'ym', year: '2024', month: 4}, {year: 2024, month: 5}],
  ['May 2024', {order: 'my', year: '2024', month: 4}, {year: 2024, month: 5}],
  ['17 May 2024', {order: 'dmy', year: '2024', month: 4, day: 17}, {year: 2024, month: 5, day: 17}],
  ['2024-13-05', {order: 'ydm', year: '2024', month: 4, day: 13}, null],
  ['2024-00-17', {order: 'ymd', year: '2024', day: 17}, null],
  ['2024-05-00', {order: 'ymd', year: '2024', month: 4, day: 0}, null],
  ['Spring 2024', {order: 'y', year: '2024', part: 'Spring'}, null],
  ['2024-2025', {order: 'y', year: '2024', part: '2025'}, null],
  ['May–June 2024', {order: 'my', year: '2024', month: 5, part: 'May'}, null],
  ['May', {order: 'm', month: 4}, null],
]) {
  test(`date projection preserves missing parts and rejects ambiguity: ${raw}`, () => {
    const env = environment();
    env.Zotero.Date.strToDate = value => { assert.equal(value, raw); return parsed; };
    const result = env.context.literatureMonitorDateParts(raw);
    assert.deepEqual(result && JSON.parse(JSON.stringify(result)), expected);
  });
}
test('date parser failure leaves the valid abstract available without native saving', async () => {
  const env = environment();
  env.Zotero.Date.strToDate = () => { throw new Error('bad date'); };
  const items = await env.context.enrichmentHandler(env.translate, [{runMode: 1}]);
  assert.equal(items[0].abstract, 'Full abstract');
  assert.equal(items[0].date_parts, null);
  assert.equal(env.nativeCalls(), 0);
});
test('date-only Item survives the read-only offscreen projection', async () => {
  const env = environment();
  env.Zotero.Translate.Base.prototype._translateTranslatorLoaded = () => [{itemType: 'journalArticle', DOI: '10.5555/paper', date: '17 May 2024'}];
  const items = await env.context.enrichmentHandler(env.translate, [{runMode: 1}]);
  assert.equal(items[0].abstract, undefined);
  assert.deepEqual({...items[0].date_parts}, {year: 2024, month: 5, day: 17});
  assert.equal(env.nativeCalls(), 0);
});

test('offscreen forwards the original creators array without invoking native save', async () => {
  const env = environment();
  const creators = [{creatorType: 'editor', firstName: 'Ed', lastName: 'Editor'},
    {creatorType: 'author', fieldMode: 1, lastName: 'Research Consortium'},
    {creatorType: 'author', firstName: 'Ada', lastName: 'Lovelace'}];
  env.Zotero.Translate.Base.prototype._translateTranslatorLoaded = () => [{itemType: 'journalArticle', DOI: '10.5555/paper', creators}];
  const items = await env.context.enrichmentHandler(env.translate, [{runMode: 1}]);
  assert.equal(items[0].creators, creators);
  assert.equal(env.nativeCalls(), 0);
});
