// Execute the shipped visible-DOM adapters; synthetic DOM only, no browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'browser_companion');
const DOI = '10.5555/test';
const TASK = '22222222-2222-4222-8222-222222222222';
const RECORD = 'https://research.ebsco.com/c/context7/search/details/record8';
const REDIRECT = 'https://resolver.ebsco.com/redirect?' + new URLSearchParams({
  rft_id: 'info:doi/' + DOI, 'x-opid': '45yels', customer: 's1215021', group: 'main', profile: 'ftf'});

function executeHandoff(url, exchange = (message, reply) => reply({ok: true}), options = {}) {
  const messages = [], events = [];
  const window = {location: {href: url}};
  window.top = options.subframe ? {} : window;
  window.history = {replaceState: (state, title, cleanUrl) => {
    assert.equal(state, null); assert.equal(title, '');
    events.push({type: 'scrub', url: cleanUrl});
    window.location.href = cleanUrl;
    options.scrub?.(cleanUrl);
  }};
  const runtime = {lastError: undefined, sendMessage: (message, reply) => {
    const snapshot = structuredClone(message);
    messages.push(snapshot); events.push({type: 'message', message: snapshot});
    exchange(message, reply, window);
  }};
  const context = vm.createContext({window, chrome: {runtime}, URL});
  for (const script of ['handoff_protocol.js', 'handoff_content.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, script), 'utf8'), context, {filename: script});
  }
  return {window, runtime, messages, events};
}

class Element {
  constructor(tag, own = '', attrs = {}, children = []) {
    this.tag = tag; this.own = own; this.attrs = attrs; this.children = children;
    this.style = {display: 'block', visibility: 'visible', opacity: '1'};
    this.hasRects = true;
    this.checked = true; this.disabled = false; this.labels = []; this.isConnected = true;
    for (const child of children) child.parentElement = this;
  }
  get id() { return this.attrs.id; }
  get href() { return this.attrs.href; }
  get textContent() { return this.own + this.children.map(c => c.textContent).join(''); }
  get innerText() {
    if ('hidden' in this.attrs || 'inert' in this.attrs || this.attrs['aria-hidden'] === 'true' ||
        this.style.display === 'none' || this.style.visibility !== 'visible') return '';
    return this.own + this.children.map(c => c.innerText).join('');
  }
  get childNodes() { return [{nodeType: 3, textContent: this.own}, ...this.children]; }
  get nextElementSibling() {
    const siblings = this.parentElement?.children || [];
    return siblings[siblings.indexOf(this) + 1];
  }
  matches(selector) {
    return selector.split(',').some(raw => {
      const s = raw.trim();
      if (s.includes(' > ')) {
        const [parent, child] = s.split(' > ');
        return this.matches(child) && Boolean(this.parentElement?.matches(parent));
      }
      const tag = s.match(/^[a-z][a-z0-9]*/);
      if (tag && tag[0] !== this.tag) return false;
      const id = s.match(/#([\w-]+)/);
      if (id && id[1] !== this.id) return false;
      const cls = s.match(/\.([\w-]+)/);
      if (cls && !(this.attrs.class || '').split(' ').includes(cls[1])) return false;
      for (const [, name, value] of s.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
        if (!(name in this.attrs) || (value !== undefined && this.attrs[name] !== value)) return false;
      }
      return true;
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  querySelectorAll(selector) {
    return this.children.flatMap(c => [...(c.matches(selector) ? [c] : []), ...c.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getClientRects() { return !this.hasRects || this.closest('[hidden]') || this.style.display === 'none' ? [] : [{}]; }
}
const e = (...args) => new Element(...args);
function execute(script, url, body, authority = {task_id: TASK, doi: DOI, navigation_epoch: 1}, runtimeSend = null) {
  const messages = [], timers = [], handlers = {}, observers = [];
  const cancelledTimers = new Set(), frames = [];
  let now = Date.now(), contextRequests = 0;
  class ClockDate extends Date { static now() { return now; } }
  const document = e('html', '', {}, [body]);
  document.documentElement = document;
  document.addEventListener = (name, fn) => { handlers[name] = fn; };
  const window = {location: {href: url}}; window.top = window;
  const context = vm.createContext({URL, URLSearchParams, TextEncoder, structuredClone, Date: ClockDate,
    window, document, getComputedStyle: n => n.style,
    MutationObserver: class {
      constructor(fn) { this.fn = fn; this.active = false; observers.push(this); }
      observe() { this.active = true; }
      disconnect() { this.active = false; }
    },
    setTimeout: (fn, delay) => {
      const id = timers.length + 1;
      const fire = () => {
        if (fire.fired || cancelledTimers.has(id)) return;
        fire.fired = true; now = Math.max(now, fire.due); fn();
      };
      fire.delay = delay; fire.due = now + delay;
      timers.push(fire); return id;
    }, clearTimeout: id => cancelledTimers.add(id),
    requestAnimationFrame: callback => {
      frames.push({callback, cancelled: false, fired: false}); return frames.length;
    },
    cancelAnimationFrame: id => { frames[id - 1].cancelled = true; },
    chrome: {runtime: {sendMessage: (message, reply) => {
      messages.push(message);
      if (runtimeSend) { runtimeSend(message, reply); return; }
      if (message.type.endsWith('_context')) {
        const response = Array.isArray(authority) ? authority[Math.min(contextRequests, authority.length - 1)] : authority;
        contextRequests++; if (reply) reply(response);
      } else if (reply) reply({ok: true});
    }}}});
  vm.runInContext(fs.readFileSync(path.join(root, 'acquisition_protocol.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, script), 'utf8'), context);
  return {messages, timers, window, document, frames,
    pendingFrames: () => frames.filter(f => !f.cancelled && !f.fired).length,
    renderFrame: () => {
      now += 16;
      const pending = frames.filter(f => !f.cancelled && !f.fired);
      pending.forEach(f => { f.fired = true; f.callback(now); });
    },
    activeObservers: () => observers.filter(o => o.active).length,
    elapse: ms => { now += ms; },
    nextTimer: () => {
      const timer = timers.filter((fn, i) => !fn.fired && !cancelledTimers.has(i + 1))
        .sort((a, b) => a.due - b.due)[0];
      if (!timer) return false; timer(); return true;
    },
    mutate: () => observers.filter(o => o.active).forEach(o => o.fn([])),
    finalSnapshot: () => timers.filter(fn => fn.delay === 15000).forEach(fn => fn()),
    click: (target, isTrusted = true) => handlers.click?.({target, isTrusted})};
}
function recordFixture() {
  const doi = e('li', DOI, {id: 'DOI'}, [e('a', '10.9999/injected', {href: 'https://unrelated.example/'})]);
  const type = e('li', 'Article');
  const source = e('li', 'Summer2026, Vol. 8 Issue 3, p248-265. 18p.', {}, [
    e('a', 'Test Journal', {'data-auto': 'processed-link__publication-authority'})]);
  const article = e('article', '', {lang: 'en'}, [e('h3', 'DOI'), e('ul', '', {}, [doi]),
    e('h3', '文献类型', {id: 'TypDoc'}), e('ul', '', {}, [type]),
    e('h3', '来源', {id: 'Src'}), e('ul', '', {}, [source])]);
  const entry = e('button', 'Download', {'data-auto': 'card-call-to-action-download-button', value: 'download'});
  const radio = e('input', '', {'data-auto': 'bulk-download-formats-group-input', name: 'fullText', value: 'pdf', type: 'radio'});
  const label = e('label', 'PDF（推荐台式计算机使用）'); radio.labels = [label];
  const year = e('p', '2026', {class: 'nuc-modal-header-with-metadata__meta-data-publication-year'});
  const button = e('button', '下载', {'data-auto': 'bulk-download-modal-download-button'});
  const body = e('body', '', {}, [e('div', '', {'data-auto': 'record-html-metadata'}, [article]), entry, year, radio, label, button]);
  return {body, article, doi, type, source, entry, radio, label, year, button};
}
function runContentCases() {
  let cases = 0;
  const clean = 'http://localhost:8765/browser-handoff/' + TASK;
  const initial = clean + '#' + 'i'.repeat(43);
  {
    const h = executeHandoff(initial);
    assert.equal(h.window.location.href, clean);
    assert.deepEqual(h.events.map(event => event.type), ['message', 'scrub', 'message']);
    assert.deepEqual(h.messages, [{type: 'claim_handoff', handoffUrl: initial}, {type: 'activate_handoff'}]);
    cases++;
  }
  {
    const h = executeHandoff(clean);
    assert.deepEqual(h.messages, [{type: 'activate_handoff'}]);
    assert.equal(h.events.length, 1); cases++;
  }
  for (const mode of ['denied', 'last_error', 'navigated']) {
    let callback;
    const h = executeHandoff(initial, (message, reply) => { callback = reply; });
    if (mode === 'last_error') h.runtime.lastError = {message: 'Unavailable'};
    if (mode === 'navigated') h.window.location.href = 'https://publisher.example/';
    callback({ok: mode !== 'denied'});
    assert.equal(h.messages.length, 1); assert.equal(h.events.length, 1); cases++;
  }
  for (const [url, options] of [[initial, {subframe: true}],
    ['https://publisher.example/browser-handoff/' + TASK, {}], [clean + '#bad', {}],
    [clean + '#', {}], [clean + '?query=1', {}]]) {
    assert.equal(executeHandoff(url, undefined, options).messages.length, 0); cases++;
  }
  for (const mode of ['valid', 'wrong_doi', 'missing_doi', 'aam', 'preprint', 'missing_journal',
    'missing_issue', 'missing_volume', 'missing_source_year', 'missing_publication_metadata', 'missing_modal_year', 'online_first',
    'abstract_preprint', 'abstract_aam', 'other_type', 'wrong_year', 'html_selected', 'hidden_pdf_label', 'untrusted', 'unclaimed',
    'unrelated_path', 'other_origin', 'record_changed', 'hidden_record', 'modal_hides_record', 'styled_radio', 'disabled_pdf', 'disabled_button', 'hidden_button', 'multiple_pdf', 'no_epoch', 'untrusted_entry']) {
    const f = recordFixture();
    if (mode === 'wrong_doi') f.doi.own = '10.9999/wrong';
    if (mode === 'missing_doi') f.doi.own = '';
    if (mode === 'aam') f.type.own = 'Accepted author manuscript';
    if (mode === 'preprint') f.type.own = 'Preprint';
    if (mode === 'online_first') f.type.own = 'Online first';
    if (mode === 'other_type') f.type.own = 'Book';
    if (mode === 'missing_journal') f.source.children[0].own = '';
    if (mode === 'missing_issue') f.source.own = 'Summer2026, Vol. 8';
    if (mode === 'missing_volume') f.source.own = 'Summer2026, Issue 3';
    if (mode === 'missing_source_year') f.source.own = 'Vol. 8 Issue 3';
    if (mode === 'missing_publication_metadata') f.article.children = f.article.children.slice(0, 2);
    if (mode === 'missing_modal_year') f.body.children = f.body.children.filter(node => node !== f.year);
    if (['abstract_preprint', 'abstract_aam'].includes(mode)) {
      const abstract = e('p', mode === 'abstract_preprint' ? 'we compare against an earlier preprint'
        : 'accepted manuscript policies are discussed');
      abstract.parentElement = f.article; f.article.children.push(abstract);
    }
    if (mode === 'wrong_year') f.year.own = '2025';
    if (mode === 'html_selected') f.radio.checked = false;
    if (mode === 'disabled_pdf') f.radio.disabled = true;
    if (mode === 'disabled_button') f.button.disabled = true;
    if (mode === 'hidden_button') f.button.attrs.hidden = '';
    if (mode === 'multiple_pdf') {
      const other = e('input', '', {...f.radio.attrs}); other.labels = [f.label];
      other.parentElement = f.body; f.body.children.push(other);
    }
    if (mode === 'hidden_pdf_label') f.label.attrs.hidden = '';
    if (mode === 'hidden_record') f.article.attrs['aria-hidden'] = 'true';
    if (mode === 'styled_radio') f.radio.style.opacity = '0';
    const url = mode === 'unrelated_path' ? RECORD.replace('details', 'results')
      : mode === 'other_origin' ? RECORD.replace('research.ebsco.com', 'publisher.example') : RECORD;
    const h = execute('ebsco_record_content.js', url, f.body, mode === 'unclaimed' ? {ok: false} : mode === 'no_epoch' ? {task_id: TASK, doi: DOI} : undefined);
    h.click(f.entry, mode !== 'untrusted_entry');
    if (mode === 'record_changed') f.doi.own = '10.9999/changed';
    if (mode === 'modal_hides_record') f.article.attrs['aria-hidden'] = 'true';
    h.click(f.button, mode !== 'untrusted');
    const evidence = h.messages.filter(m => m.type === 'ebsco_pdf_action');
    const valid = ['valid', 'modal_hides_record', 'styled_radio', 'abstract_preprint', 'abstract_aam',
      'aam', 'preprint', 'online_first', 'other_type', 'missing_journal', 'missing_issue',
      'missing_volume', 'missing_source_year', 'missing_publication_metadata', 'missing_modal_year', 'wrong_year'].includes(mode);
    assert.equal(evidence.length, valid ? 1 : 0, mode);
    if (valid) {
      assert.equal(evidence[0].record.doi, DOI); // injected descendant DOI ignored
      assert.deepEqual(Object.keys(evidence[0].record), ['doi']);
      assert.equal(evidence[0].pageUrl, RECORD);
      assert.equal(evidence[0].navigation_epoch, 1);
      assert.equal(evidence[0].manifestation, undefined);
    }
    cases++;
  }
  const base = RECORD + '?request-context=plink&db=bth';
  const modal = base + '&modal=details-bulk-download';
  const transitions = {
    modal, query_before_entry: modal,
    other_record: modal.replace('record8', 'record9'),
    other_context: modal.replace('context7', 'context8'),
    other_origin: modal.replace('research.ebsco.com', 'publisher.example'),
    results: modal.replace('/details/record8', '/results'),
    fragment: modal + '#modal', empty_fragment: modal + '#',
    userinfo: modal.replace('https://', 'https://user@'),
    insecure: modal.replace('https://', 'http://'),
    unrelated: 'https://research.ebsco.com/help?modal=details-bulk-download',
    normalized_path: modal.replace('/record8?', '/record9/../record8?'),
    changed_metadata: modal, detached_metadata: modal, no_entry: modal
  };
  for (const [mode, target] of Object.entries(transitions)) {
    const f = recordFixture(), h = execute('ebsco_record_content.js', base, f.body);
    if (mode === 'query_before_entry') h.window.location.href = modal;
    if (mode !== 'no_entry') h.click(f.entry);
    // Same window, document, script and captured record; no reinjection.
    h.window.location.href = target;
    if (mode === 'changed_metadata') f.source.children[0].own = 'Changed Journal';
    if (mode === 'detached_metadata') f.article.isConnected = false;
    h.click(f.button);
    const actions = h.messages.filter(m => m.type === 'ebsco_pdf_action');
    const valid = ['modal', 'query_before_entry'].includes(mode);
    assert.equal(actions.length, valid ? 1 : 0, 'SPA ' + mode);
    if (valid) {
      assert.equal(actions[0].pageUrl, base);
      assert.equal(actions[0].record.doi, DOI);
    }
    cases++;
  }
  for (const mode of ['valid', 'wrong_doi', 'wrong_profile', 'wrong_opid', 'wrong_customer', 'wrong_group', 'duplicate_query', 'extra_query',
    'wrong_path', 'other_origin', 'full_text', 'hidden', 'hidden_sentence', 'arbitrary', 'multiple', 'standalone', 'bare_section',
    'unrelated_heading', 'list', 'eligible_group']) {
    const link = e('a', 'EBSCOhost SmartLinks', {href: RECORD});
    let body = e('body', '', {}, [e('p', 'Find this article in full text from ', {}, [link])]);
    if (mode === 'full_text') link.own = 'EBSCOhost Full Text';
    if (mode === 'hidden') link.attrs['aria-hidden'] = 'true';
    if (mode === 'hidden_sentence') body = e('body', '', {}, [e('p', '', {}, [
      e('span', 'Find this article in full text from ', {hidden: ''}), link])]);
    if (mode === 'arbitrary') body.children[0].own = 'An arbitrary link: ';
    if (mode === 'multiple') {
      const second = e('a', 'EBSCOhost SmartLinks', {href: RECORD.replace('record8', 'record9')});
      const para = e('p', 'Find this article in full text from ', {}, [second]);
      para.parentElement = body; body.children.push(para);
    }
    let url = REDIRECT;
    if (mode === 'wrong_doi') url = REDIRECT.replace(encodeURIComponent(DOI), '10.9999%2Fwrong');
    if (mode === 'wrong_opid') url = REDIRECT.replace('x-opid=45yels', 'x-opid=other');
    if (mode === 'wrong_customer') url = REDIRECT.replace('customer=s1215021', 'customer=other');
    if (mode === 'wrong_group') url = REDIRECT.replace('group=main', 'group=other');
    if (mode === 'wrong_profile') url = REDIRECT.replace('profile=ftf', 'profile=other');
    if (mode === 'duplicate_query') url += '&profile=ftf';
    if (mode === 'extra_query') url += '&target=https%3A%2F%2Farbitrary.example';
    if (mode === 'wrong_path') url = REDIRECT.replace('/redirect?', '/redirect/unrelated?');
    if (mode === 'other_origin') url = REDIRECT.replace('resolver.ebsco.com', 'other.example');
    if (['standalone', 'bare_section', 'unrelated_heading', 'list', 'eligible_group'].includes(mode)) {
      url = REDIRECT.replace('/redirect?', '/c/45yels/result?');
      const own = e('a', 'Full Text', {href: RECORD});
      const nodes = mode === 'standalone' ? [own] : mode === 'bare_section' ? [e('section', '', {}, [own])]
        : mode === 'unrelated_heading' ? [e('section', '', {}, [e('h2', 'Unrelated'), own])]
        : mode === 'list' ? [e('ul', '', {}, [e('li', '', {}, [own])])]
        : [e('section', '', {}, [e('h2', 'SmartLinks'), e('a', 'Provider', {href: RECORD})])];
      body = e('body', '', {}, nodes);
    }
    const h = execute('resolver_content.js', url, body);
    const early = h.messages.filter(m => m.type === 'resolver_observation');
    const expectedEarly = mode === 'multiple' ? 2 : ['valid', 'full_text'].includes(mode) ? 1 : 0;
    assert.equal(early.length, expectedEarly ? 1 : 0, 'initial inspection ' + mode);
    if (expectedEarly) assert.equal(early[0].choices.length, expectedEarly, mode);
    h.finalSnapshot();
    const result = h.messages.find(m => m.type === 'resolver_observation');
    assert(h.messages.filter(m => m.type === 'resolver_observation').length <= 1, mode);
    const expected = mode === 'multiple' ? 2 : ['valid', 'full_text', 'list', 'eligible_group'].includes(mode) ? 1 : 0;
    assert.equal(result?.choices.length || 0, expected, mode);
    if (mode === 'multiple') assert.deepEqual(Array.from(result.choices, c => c.target), [RECORD, RECORD.replace('record8', 'record9')]);
    if (['valid', 'multiple', 'eligible_group'].includes(mode)) assert.equal(result.choices[0].category, 'SmartLinks');
    cases++;
  }
  for (const mode of ['mutation_smartlinks', 'mutation_full_text', 'mutation_multiple', 'ordinary_result',
    'ordinary_sentence', 'redirect_list', 'no_choice_timeout', 'overflow', 'busy_redirect']) {
    const ordinary = ['ordinary_result', 'ordinary_sentence'].includes(mode);
    const body = e('body'), url = ordinary ? REDIRECT.replace('/redirect?', '/c/45yels/result?') : REDIRECT;
    const h = execute('resolver_content.js', url, body);
    const reports = () => h.messages.filter(m => m.type === 'resolver_observation');
    assert.equal(reports().length, 0, mode);
    assert.equal(h.timers.length, 1); assert.equal(h.timers[0].delay, 15000);
    const add = node => { node.parentElement = body; body.children.push(node); };
    const provider = (name, href) => e('p', 'Find this article in full text from ', {}, [
      e('a', 'EBSCOhost ' + name, {href})]);
    if (['ordinary_result', 'redirect_list'].includes(mode)) {
      add(e('ul', '', {}, [e('li', '', {}, [e('a', 'Full Text', {href: RECORD})])]));
    } else if (mode === 'overflow') {
      for (let n = 0; n < 7; n++) add(provider('SmartLinks', RECORD + '?provider=' + n));
    } else if (mode !== 'no_choice_timeout') {
      add(provider(mode === 'mutation_full_text' ? 'Full Text' : 'SmartLinks', RECORD));
      if (mode === 'mutation_multiple') add(provider('Full Text', RECORD.replace('record8', 'record9')));
      if (mode === 'busy_redirect') body.attrs['aria-busy'] = 'true';
    }
    h.mutate();
    const early = mode.startsWith('mutation_') || mode === 'overflow';
    assert.equal(reports().length, early ? 1 : 0, 'mutation non-final ' + mode);
    if (mode === 'mutation_multiple') {
      assert.deepEqual(Array.from(reports()[0].choices, c => c.target), [RECORD, RECORD.replace('record8', 'record9')]);
      assert.deepEqual(Array.from(reports()[0].choices, c => c.category), ['SmartLinks', 'FullText']);
    }
    if (mode === 'overflow') {
      assert.equal(reports()[0].unavailable, 'choice_overflow'); assert.equal(reports()[0].choices.length, 0);
    }
    if (mode === 'busy_redirect') {
      delete body.attrs['aria-busy']; h.mutate(); assert.equal(reports().length, 1);
    }
    h.finalSnapshot(); h.mutate();
    assert.equal(reports().length, 1, 'one report only ' + mode);
    const expected = ['ordinary_sentence', 'no_choice_timeout', 'overflow'].includes(mode) ? 0
      : mode === 'mutation_multiple' ? 2 : 1;
    assert.equal(reports()[0].choices.length, expected, mode);
    cases++;
  }
  const resultUrl = REDIRECT.replace('/redirect?', '/c/45yels/result?');
  const provider = () => e('p', '', {}, [e('a', 'Find this article in full text from EBSCOhost SmartLinks', {href: RECORD})]);
  for (const mode of ['second_attempt', 'fifth_attempt', 'all_fail', 'leave_origin', 'wrong_path',
    'wrong_institution', 'wrong_worker_doi', 'late_reply_budget', 'not_top_level', 'retry_on_redirect']) {
    const responses = mode === 'second_attempt' || mode === 'retry_on_redirect' ? [{ok: false}, {task_id: TASK, doi: DOI}]
      : mode === 'fifth_attempt' ? [{ok: false}, undefined, {}, {ok: false}, {task_id: TASK, doi: DOI}]
      : mode === 'wrong_worker_doi' ? [{ok: false}, {task_id: TASK, doi: '10.9999/wrong'}] : [{ok: false}];
    const h = execute('resolver_content.js', resultUrl, e('body', '', {}, [provider()]), responses);
    const requests = () => h.messages.filter(m => m.type === 'resolver_context');
    const reports = () => h.messages.filter(m => m.type === 'resolver_observation');
    assert.equal(requests().length, 1); assert.equal(h.activeObservers(), 0);
    if (mode === 'leave_origin') h.window.location.href = RECORD;
    if (mode === 'wrong_path') h.window.location.href = resultUrl.replace('/result?', '/unrelated?');
    if (mode === 'wrong_institution') h.window.location.href = REDIRECT.replace('profile=ftf', 'profile=other');
    if (mode === 'late_reply_budget') h.elapse(1001);
    if (mode === 'not_top_level') h.window.top = {};
    if (mode === 'retry_on_redirect') h.window.location.href = REDIRECT;
    for (let n = 0; n < 5; n++) { if (!h.nextTimer()) break; if (h.activeObservers()) break; }
    const success = ['second_attempt', 'fifth_attempt', 'retry_on_redirect'].includes(mode);
    assert.equal(h.activeObservers(), mode === 'retry_on_redirect' ? 0 : success ? 1 : 0, mode);
    assert.equal(requests().length, mode === 'fifth_attempt' || mode === 'all_fail' ? 5
      : ['second_attempt', 'wrong_worker_doi', 'retry_on_redirect'].includes(mode) ? 2 : 1, mode);
    const retryTimers = h.timers.filter(fn => fn.delay !== 15000);
    assert(retryTimers.length <= 4); assert(retryTimers.every(fn => fn.delay === 150));
    assert(retryTimers.reduce((sum, fn) => sum + fn.delay, 0) <= 1000);
    assert.equal(reports().length, mode === 'retry_on_redirect' ? 1 : 0, mode);
    if (success && mode !== 'retry_on_redirect') {
      h.window.location.href = REDIRECT; h.mutate();
      assert.equal(reports().length, 1, mode); assert.equal(reports()[0].pageUrl, REDIRECT);
    }
    h.finalSnapshot(); h.mutate();
    assert.equal(reports().length, success ? 1 : 0, mode);
    assert.equal(h.activeObservers(), 0); assert.equal(h.nextTimer(), false);
    cases++;
  }
  for (const mode of ['redirect', 'unrelated_path', 'changed_doi', 'changed_opid', 'changed_customer',
    'changed_group', 'changed_profile', 'arbitrary_page', 'other_origin']) {
    const body = e('body'), h = execute('resolver_content.js', resultUrl, body);
    const originalWindow = h.window, originalDocument = h.document;
    assert.equal(h.activeObservers(), 1);
    assert.equal(h.messages.filter(m => m.type === 'resolver_observation').length, 0);
    let target = REDIRECT;
    if (mode === 'unrelated_path') target = REDIRECT.replace('/redirect?', '/redirect/unrelated?');
    if (mode === 'changed_doi') target = REDIRECT.replace(encodeURIComponent(DOI), '10.9999%2Fwrong');
    if (mode === 'changed_opid') target = REDIRECT.replace('x-opid=45yels', 'x-opid=other');
    if (mode === 'changed_customer') target = REDIRECT.replace('customer=s1215021', 'customer=other');
    if (mode === 'changed_group') target = REDIRECT.replace('group=main', 'group=other');
    if (mode === 'changed_profile') target = REDIRECT.replace('profile=ftf', 'profile=other');
    if (mode === 'arbitrary_page') target = 'https://resolver.ebsco.com/help';
    if (mode === 'other_origin') target = RECORD;
    h.window.location.href = target;
    const node = provider(); node.parentElement = body; body.children.push(node);
    h.mutate();
    const reports = () => h.messages.filter(m => m.type === 'resolver_observation');
    assert.equal(reports().length, mode === 'redirect' ? 1 : 0, mode);
    if (mode === 'redirect') {
      assert.equal(reports()[0].pageUrl, REDIRECT); assert.equal(reports()[0].choices[0].category, 'SmartLinks');
    }
    assert.equal(h.window, originalWindow); assert.equal(h.document, originalDocument);
    assert.equal(h.activeObservers(), 0);
    h.window.location.href = REDIRECT; h.mutate(); h.finalSnapshot();
    assert.equal(reports().length, mode === 'redirect' ? 1 : 0, 'terminal ' + mode);
    assert.equal(h.nextTimer(), false);
    cases++;
  }
  for (const mode of ['settles', 'stays_invisible', 'arbitrary_anchor', 'wrong_sentence', 'wrong_doi',
    'wrong_profile', 'unrelated_path', 'other_origin', 'back_to_result', 'repeated_mutations',
    'reported_before_settle', 'human_required', 'final_snapshot', 'final_not_ready', 'ordinary_result']) {
    const body = e('body'), h = execute('resolver_content.js', resultUrl, body);
    h.window.location.href = mode === 'ordinary_result' ? resultUrl : REDIRECT;
    const anchor = e('a', mode === 'arbitrary_anchor' ? 'EBSCOhost SmartLinks'
      : mode === 'wrong_sentence' ? 'Find unrelated full text from EBSCOhost SmartLinks'
      : 'Find this article in full text from EBSCOhost SmartLinks', {href: RECORD});
    anchor.hasRects = false; anchor.style.display = 'none'; anchor.parentElement = body; body.children.push(anchor);
    h.mutate();
    const reports = () => h.messages.filter(m => m.type === 'resolver_observation');
    assert.equal(reports().length, 0, 'initial hidden DOM ' + mode);
    const eligible = !['arbitrary_anchor', 'wrong_sentence', 'ordinary_result'].includes(mode);
    assert.equal(h.pendingFrames(), eligible ? 1 : 0, mode);
    for (let n = 0; n < 20; n++) h.mutate();
    assert.equal(h.frames.length, eligible ? 1 : 0, 'coalesced mutations ' + mode);
    // Rendering changes without a second MutationObserver callback.
    if (mode !== 'stays_invisible') { anchor.hasRects = true; anchor.style.display = 'inline'; }
    if (mode === 'wrong_doi') h.window.location.href = REDIRECT.replace(encodeURIComponent(DOI), '10.9999%2Fwrong');
    if (mode === 'wrong_profile') h.window.location.href = REDIRECT.replace('profile=ftf', 'profile=other');
    if (mode === 'unrelated_path') h.window.location.href = REDIRECT.replace('/redirect?', '/unrelated?');
    if (mode === 'other_origin') h.window.location.href = RECORD;
    if (mode === 'back_to_result') h.window.location.href = resultUrl;
    if (mode === 'reported_before_settle') h.mutate();
    if (mode === 'human_required') {
      const password = e('input', '', {type: 'password'}); password.parentElement = body; body.children.push(password); h.mutate();
    }
    if (mode === 'final_snapshot' || mode === 'final_not_ready') {
      if (mode === 'final_not_ready') body.attrs['aria-busy'] = 'true';
      h.finalSnapshot();
    }
    h.renderFrame();
    assert(h.pendingFrames() <= 1);
    h.renderFrame();
    const early = ['settles', 'repeated_mutations', 'reported_before_settle', 'final_snapshot'].includes(mode);
    const terminal = early || ['human_required', 'final_not_ready'].includes(mode);
    assert.equal(reports().length, terminal ? 1 : 0, 'after bounded settle ' + mode);
    if (early) {
      assert.equal(reports()[0].pageUrl, REDIRECT); assert.equal(reports()[0].choices[0].category, 'SmartLinks');
    }
    if (mode === 'human_required') { assert.equal(reports()[0].human_required, true); assert.equal(reports()[0].choices.length, 0); }
    if (mode === 'final_not_ready') assert.equal(reports()[0].unavailable, 'not_ready');
    assert.equal(h.pendingFrames(), 0);
    // Even forced stale callbacks cannot report a second observation.
    if (terminal) h.frames.forEach(frame => frame.callback());
    for (let n = 0; n < 20; n++) { h.mutate(); h.renderFrame(); }
    assert(h.frames.length <= 2, 'finite frames ' + mode);
    assert.equal(reports().length, terminal ? 1 : 0, 'no recurring settle ' + mode);
    h.finalSnapshot();
    assert(reports().length <= 1, 'one final observation ' + mode);
    if (mode === 'ordinary_result') {
      assert.equal(h.frames.length, 0); assert.equal(h.timers[0].delay, 15000);
    }
    cases++;
  }
  return cases;
}
module.exports = {runContentCases, recordFixture, RECORD, REDIRECT, execute, executeHandoff, e};
if (require.main === module) process.stdout.write(`Shipped content adapters: ${runContentCases()} cases passed. Synthetic DOM only.\n`);
