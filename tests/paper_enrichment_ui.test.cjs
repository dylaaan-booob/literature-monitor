'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/literature_monitor/web/static/app.js'), 'utf8');
const block = source.slice(source.indexOf('  let enrichmentOperation = null;'), source.lastIndexOf('})();'));
async function flush() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function environment() {
  const handlers = {}, calls = [], replies = [];
  const message = {textContent: ''}, preview = {hidden: true}, candidateAbstract = {hidden: true, textContent: ''},
    candidateDate = {hidden: true, textContent: ''}, candidateAuthors = {hidden: true, textContent: ''}, abstractLabel = {hidden: true};
  const buttons = Object.fromEntries(['start', 'save', 'cancel'].map(action => [action, {dataset: {enrichmentAction: action}, disabled: false, hidden: false}]));
  const abstract = {textContent: 'Abstract unavailable'};
  const authors = {textContent: ''};
  const date = {textContent: 'Publication date unavailable'};
  const detail = {dataset: {activeView: 'inbox'}, querySelector: selector => selector.includes("authors") ? authors : date, querySelectorAll: () => [{textContent: 'Abstract', nextElementSibling: abstract}]};
  const panel = {dataset: {enrichmentPaperId: 'paper', csrf: 'csrf', missingAbstract: 'true', missingDate: 'false', missingAuthors: 'false'}, isConnected: true,
    querySelector(selector) { if (selector.includes('message')) return message; if (selector.includes('preview')) return preview;
      if (selector.includes('abstract-label')) return abstractLabel; if (selector.includes('abstract')) return candidateAbstract;
      if (selector.includes('date')) return candidateDate;
      if (selector.includes('authors')) return candidateAuthors;
      return buttons[selector.match(/="(\w+)"/)[1]]; },
    querySelectorAll: () => Object.values(buttons), closest: () => detail};
  const doc = {current: panel, querySelector() { return this.current; },
    addEventListener(name, fn) { handlers[name] = fn; }, body: {addEventListener(name, fn) { handlers[name] = fn; }}};
  const idle = {phase: 'Idle', connector_online: true, supported: true, message: ''};
  const context = {document: doc, window: {addEventListener(name, fn) { handlers[name] = fn; }},
    setInterval: () => 1, clearInterval() {}, fetch: async (url, options) => {
      calls.push({url, body: options && JSON.parse(options.body)});
      return {ok: true, json: async () => replies.length ? await replies.shift() : idle};
    }};
  vm.createContext(context); vm.runInContext(block + '\ndocument.addEventListener("click", handleEnrichmentClick); document.body.addEventListener("htmx:afterSwap", syncEnrichmentPanel);', context);
  return {context, panel, doc, calls, replies, buttons, message, preview, candidateAbstract, candidateDate, candidateAuthors, abstract, authors, date, handlers,
    async click(action) { await handlers.click({target: {closest: () => buttons[action]}}); }};
}
test('UI renders unescaped candidate only through textContent, Save is explicit', async () => {
  const env = environment(); await flush();
  env.replies.push({phase: 'Retrieving', request_id: 'request', connector_online: true, supported: true});
  await env.click('start');
  env.replies.push({phase: 'Preview', request_id: 'request', abstract: '<script>literal text</script>\nOriginal', connector_online: true, supported: true});
  await vm.runInContext('pollEnrichment()', env.context);
  assert.equal(env.candidateAbstract.textContent, '<script>literal text</script>\nOriginal');
  assert.equal(env.buttons.save.hidden, false);
  assert.equal(env.calls.some(call => call.url.endsWith('/save')), false);
  env.replies.push({phase: 'Completed', request_id: 'request', saved: {abstract: '<script>literal text</script>\nOriginal'}, missing_abstract: false, missing_date: false, message: 'Enrichment saved; filesystem sync failed', connector_online: true, supported: true});
  await env.click('save');
  const body = env.calls.find(call => call.url.endsWith('/save')).body;
  assert.deepEqual(body, {csrf_token: 'csrf', request_id: 'request'});
  assert.equal(env.abstract.textContent, '<script>literal text</script>\nOriginal');
  assert.match(env.message.textContent, /sync failed/);
  assert.equal(env.buttons.save.hidden, true);
});
test('UI Cancel and leaving Detail discard the current operation', async () => {
  const env = environment(); await flush();
  env.replies.push({phase: 'Retrieving', request_id: 'request', connector_online: true, supported: true}); await env.click('start');
  env.replies.push({accepted: true}); await env.click('cancel');
  assert.equal(env.preview.hidden, true);
  env.replies.push({phase: 'Retrieving', request_id: 'next', connector_online: true, supported: true}); await env.click('start');
  env.doc.current = null; env.panel.isConnected = false; env.handlers['htmx:afterSwap'](); await flush();
  assert.equal(env.calls.at(-1).url.endsWith('/cancel'), true);
  assert.equal(env.calls.at(-1).body.request_id, 'next');
});
test('UI readiness distinguishes offline and unsupported Connector', async () => {
  const env = environment(); await flush();
  env.replies.push({phase: 'Idle', connector_online: false, supported: true}); await vm.runInContext('pollEnrichment()', env.context);
  assert.equal(env.buttons.start.disabled, true); assert.match(env.message.textContent, /offline/);
  env.replies.push({phase: 'Idle', connector_online: true, supported: false}); await vm.runInContext('pollEnrichment()', env.context);
  assert.equal(env.buttons.start.disabled, true); assert.match(env.message.textContent, /Update/);
});

test('a late Preview poll cannot reopen an already saved candidate', async () => {
  const env = environment(); await flush();
  env.replies.push({phase: 'Retrieving', request_id: 'request', connector_online: true, supported: true}); await env.click('start');
  const preview = {phase: 'Preview', request_id: 'request', abstract: 'Original', connector_online: true, supported: true};
  env.replies.push({...preview}); await vm.runInContext('pollEnrichment()', env.context);
  let resolve;
  const delayed = new Promise(done => { resolve = done; });
  env.replies.push(delayed);
  const polling = vm.runInContext('pollEnrichment()', env.context); await flush();
  env.replies.push({phase: 'Completed', request_id: 'request', saved: {abstract: 'Original'}, missing_abstract: false, missing_date: false, message: 'Enrichment saved', connector_online: true, supported: true}); await env.click('save');
  resolve({...preview}); await polling;
  assert.equal(env.buttons.save.hidden, true);
  assert.equal(env.abstract.textContent, 'Original');
});

for (const fields of ['date', 'both']) {
  test(`UI previews ${fields} fields and permits a partial retry`, async () => {
    const env = environment(); env.panel.dataset.missingDate = 'true'; await flush();
    env.replies.push({phase: 'Retrieving', request_id: 'request', missing_abstract: true, missing_date: true, connector_online: true, supported: true});
    await env.click('start');
    const candidate = {publication_date: '2024-05', date_raw: 'May 2024'};
    if (fields === 'both') candidate.abstract = 'New Abstract';
    env.replies.push({phase: 'Preview', request_id: 'request', ...candidate, connector_online: true, supported: true});
    await vm.runInContext('pollEnrichment()', env.context);
    assert.equal(env.candidateDate.textContent, 'Publication date: May 2024 → 2024-05');
    assert.equal(env.candidateAbstract.hidden, fields === 'date');
    env.replies.push({phase: 'Completed', request_id: 'request', saved: candidate, missing_abstract: fields === 'date', missing_date: false, message: 'Enrichment saved', connector_online: true, supported: true});
    await env.click('save');
    assert.equal(env.date.textContent, '2024-05');
    assert.equal(env.abstract.textContent, fields === 'both' ? 'New Abstract' : 'Abstract unavailable');
    assert.equal(env.buttons.start.hidden, fields === 'both');
    if (fields === 'date') {
      env.replies.push({phase: 'Retrieving', request_id: 'next', missing_abstract: true, missing_date: false, connector_online: true, supported: true});
      await env.click('start');
      assert.equal(env.calls.at(-1).url.endsWith('/start'), true);
    }
  });
}

test('authors-only UI preserves candidate order and saves only the operation identifier', async () => {
  const env = environment();
  env.panel.dataset.missingAbstract = 'false'; env.panel.dataset.missingAuthors = 'true'; await flush();
  env.replies.push({phase: 'Retrieving', request_id: 'authors', connector_online: true, supported: true}); await env.click('start');
  const names = ['<script>literal author</script>', 'Research Consortium'];
  env.replies.push({phase: 'Preview', request_id: 'authors', authors: names, connector_online: true, supported: true});
  await vm.runInContext('pollEnrichment()', env.context);
  assert.equal(env.candidateAuthors.textContent, 'Authors: <script>literal author</script>; Research Consortium');
  assert.equal(env.candidateAuthors.hidden, false);
  assert.equal(env.calls.some(call => call.url.endsWith('/save')), false);
  env.replies.push({phase: 'Completed', request_id: 'authors', saved: {authors: names}, missing_abstract: false, missing_date: false, missing_authors: false, connector_online: true, supported: true, message: 'Enrichment saved'});
  await env.click('save');
  assert.equal(env.authors.textContent, '<script>literal author</script>, Research Consortium');
  assert.deepEqual(env.calls.at(-1).body, {csrf_token: 'csrf', request_id: 'authors'});
  assert.equal(env.buttons.start.hidden, true);
});
test('partial authors Save leaves Enrich available for the missing Abstract', async () => {
  const env = environment(); env.panel.dataset.missingAuthors = 'true'; await flush();
  env.replies.push({phase: 'Retrieving', request_id: 'partial', connector_online: true, supported: true}); await env.click('start');
  env.replies.push({phase: 'Completed', request_id: 'partial', saved: {authors: ['Ada Lovelace']}, missing_abstract: true, missing_date: false, missing_authors: false, connector_online: true, supported: true});
  await env.click('save');
  assert.equal(env.buttons.start.hidden, false); assert.equal(env.authors.textContent, 'Ada Lovelace');
});
test('failed Paper Save displays retained Author paths without marking Authors saved', async () => {
  const env = environment(); env.panel.dataset.missingAuthors = 'true'; await flush();
  env.replies.push({phase: 'Retrieving', request_id: 'failed', connector_online: true, supported: true}); await env.click('start');
  env.replies.push({phase: 'Failed', request_id: 'failed', missing_authors: true, connector_online: true, supported: true,
    message: 'Paper was not saved; created Author files retained: /fixture/Authors/unidentified-paper-01.md'});
  await env.click('save');
  assert.match(env.message.textContent, /Paper was not saved.*unidentified-paper-01.md/);
  assert.equal(env.authors.textContent, ''); assert.equal(env.buttons.start.hidden, false);
});
