"""Artifact/security structure and in-memory worker tests; no real Chrome."""

import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'browser_companion'
MANIFEST = json.loads((ARTIFACT / 'manifest.json').read_text())
WORKER = (ARTIFACT / 'service_worker.js').read_text()
CONTENT = (ARTIFACT / 'handoff_content.js').read_text()
PROTOCOL = (ARTIFACT / 'handoff_protocol.js').read_text()
ACQUISITION = (ARTIFACT / 'acquisition_runtime.js').read_text()
ACQUISITION_PROTOCOL = (ARTIFACT / 'acquisition_protocol.js').read_text()
RESOLVER = (ARTIFACT / 'resolver_content.js').read_text()
EBSCO = (ARTIFACT / 'ebsco_record_content.js').read_text()
POPUP = (ARTIFACT / 'task_popup.js').read_text()
SCRIPTS = WORKER + CONTENT + PROTOCOL + ACQUISITION + ACQUISITION_PROTOCOL + RESOLVER + EBSCO + POPUP
README = (ARTIFACT / 'README.md').read_text()


def js_pattern(name):
    # Exercise the explicit regex literals, not a Python reimplementation of JS.
    literal = re.search(rf'const {name} = /(.+)/;', PROTOCOL).group(1)
    return re.compile(literal.replace(r'\/', '/'))


def test_standalone_plain_mv3_artifact():
    assert ARTIFACT.parent == ROOT
    assert not ARTIFACT.is_relative_to(ROOT / 'src')
    assert MANIFEST['manifest_version'] == 3
    assert MANIFEST['minimum_chrome_version'] == '102'
    assert MANIFEST['incognito'] == 'not_allowed'
    assert not (ARTIFACT / 'package.json').exists()
    assert not (ARTIFACT / 'node_modules').exists()


def test_only_required_permissions_and_entry_points():
    assert MANIFEST['permissions'] == ['storage', 'tabs', 'webNavigation', 'downloads']
    assert set(MANIFEST['host_permissions']) == {'http://localhost/*', 'http://127.0.0.1/*', 'https://resolver.ebsco.com/*', 'https://research.ebsco.com/*'}
    assert set(MANIFEST) == {
        'manifest_version', 'name', 'version', 'minimum_chrome_version',
        'description', 'incognito', 'permissions', 'host_permissions',
        'background', 'action', 'content_scripts',
    }
    assert set(MANIFEST['action']) == {'default_title', 'default_popup'}
    assert MANIFEST['action']['default_popup'] == 'task_popup.html'
    assert 'externally_connectable' not in MANIFEST
    assert 'web_accessible_resources' not in MANIFEST


def test_every_script_is_bundled_and_present():
    scripts = [MANIFEST['background']['service_worker']]
    for content in MANIFEST['content_scripts']:
        scripts.extend(content['js'])
    scripts.extend(re.findall(r'importScripts\("([^"]+)"\)', WORKER))
    assert set(scripts) == {'service_worker.js', 'handoff_content.js', 'handoff_protocol.js',
                           'acquisition_protocol.js', 'acquisition_runtime.js', 'resolver_content.js', 'ebsco_record_content.js'}
    for script in scripts:
        assert Path(script).name == script
        assert (ARTIFACT / script).is_file()
    assert not re.search(r'(?:importScripts|import)\(["\']https?://|\beval\s*\(|new Function\s*\(', SCRIPTS)


def test_content_match_and_glob_are_handoff_only():
    assert len(MANIFEST['content_scripts']) == 3
    entry = MANIFEST['content_scripts'][0]
    assert entry['matches'] == [
        'http://localhost/browser-handoff/*', 'http://127.0.0.1/browser-handoff/*',
    ]
    assert entry['include_globs'] == [
        'http://localhost*/browser-handoff/????????-????-????-????-????????????*',
        'http://127.0.0.1*/browser-handoff/????????-????-????-????-????????????*',
    ]
    assert entry['all_frames'] is False
    assert entry['js'] == ['handoff_protocol.js', 'handoff_content.js']
    assert 'window.top !== window' in CONTENT


@pytest.mark.parametrize('forbidden', [
    'localStorage', 'sessionStorage', 'chrome.storage.local', 'chrome.storage.sync',
    'indexedDB', 'chrome.cookies', 'document.cookie', 'chrome.history',
    'chrome.debugger', 'chrome.proxy', 'chrome.webRequest', 'chrome.downloads.removeFile',
    'chrome.downloads.erase', 'chrome.scripting', 'connectNative', 'sendNativeMessage',
    'console.', '<all_urls>', 'declarativeNetRequest',
])
def test_no_persistent_authority_or_unowned_browser_features(forbidden):
    assert forbidden not in SCRIPTS
    assert forbidden not in json.dumps(MANIFEST)


def test_complete_canonical_loopback_url_validation():
    assert 'BrowserHandoffProtocol.parseInitialUrl(initialUrl)' in CONTENT
    for check in (
        'url.href !== value', 'url.protocol !== "http:"',
        '["localhost", "127.0.0.1"].includes(url.hostname)',
        'url.username !== ""', 'url.password !== ""', 'url.search !== ""',
        'url.pathname.match(HANDOFF_PATH)', 'validTaskId(path[1])',
        'new URL(value).hash.slice(1)', 'if (!validCapability(capability)) return null',
    ):
        assert check in PROTOCOL
    assert 'value.length === 36' in PROTOCOL
    assert 'value.length === 43' in PROTOCOL
    assert 'return {origin: url.origin, taskId: path[1]}' in PROTOCOL


@pytest.mark.parametrize('value,valid', [
    ('22222222-2222-4222-8222-222222222222', True),
    ('AAAAAAAA-2222-4222-8222-222222222222', False),
    ('22222222-2222-4222-8222-22222222222', False),
    ('22222222-2222-4222-8222-222222222222/extra', False),
])
def test_canonical_task_pattern(value, valid):
    assert bool(js_pattern('UUID_PATTERN').fullmatch(value)) is valid
    assert bool(js_pattern('HANDOFF_PATH').fullmatch('/browser-handoff/' + value)) is valid


@pytest.mark.parametrize('value,valid', [
    ('a' * 43, True), ('_' * 42 + '-', True), ('a' * 42, False),
    ('a' * 44, False), ('a' * 42 + '=', False), ('a' * 42 + '/', False),
    ('a' * 43 + '\n', False), ('a' * 43 + '#extra', False),
])
def test_fragment_capability_pattern(value, valid):
    assert bool(js_pattern('CAPABILITY_PATTERN').fullmatch(value)) is valid


def test_binding_comes_only_from_chrome_sender():
    assert '{type: "claim_handoff", handoffUrl: initialUrl}' in CONTENT
    assert 'tabId' not in CONTENT and 'tab_binding' not in CONTENT
    for check in (
        'sender.id !== chrome.runtime.id', 'sender.frameId !== 0', '!sender.tab',
        'sender.tab.incognito === true', 'parsePageUrl(sender.url)',
        'tabId: sender.tab.id', 'tab_binding: BrowserHandoffProtocol.tabBinding(sender.tab.id)',
        'Object.keys(message).sort().join(",") !== "handoffUrl,type"',
    ):
        assert check in WORKER
    assert 'Number.isSafeInteger(tabId) && tabId >= 0' in PROTOCOL
    assert '`tab-${tabId}`' in PROTOCOL


def test_fixed_json_endpoints_and_no_ambient_network_authority():
    for check in (
        'const CLAIM_PATH = "/browser-handoff/claim"',
        'const EVENT_PATH = "/browser-handoff/events"',
        '!BrowserHandoffProtocol.validOrigin(origin)', '[CLAIM_PATH, EVENT_PATH].includes(path)',
        'fetch(origin + path,', 'method: "POST"', '"Content-Type": "application/json"',
        'body: JSON.stringify(payload)', 'credentials: "omit"', 'redirect: "error"',
        'referrerPolicy: "no-referrer"', 'cache: "no-store"',
        'postJson(handoff.origin, CLAIM_PATH,', 'owner.origin !== handoff.origin',
    ):
        assert check in WORKER
    assert WORKER.count('fetch(') == 2  # JSON POST and non-secret old-task status GET.
    assert 'url.origin === value' in PROTOCOL
    for destination in re.findall(r'fetch\(([^,]+),', WORKER):
        assert 'capability' not in destination


def test_exact_claim_response_and_secret_not_returned_to_content():
    for check in (
        'response.status !== 200', '!== "application/json"', 'JSON.parse(response.text)',
        'Object.keys(reply).length !== 1', 'Object.hasOwn(reply, "event_capability")',
        'Array.isArray(reply)', 'validCapability(reply.event_capability)',
        'reply.event_capability === handoff.capability',
    ):
        assert check in WORKER
    assert 'eventCapability' not in CONTENT and 'event_capability' not in CONTENT
    claim_listener = WORKER.split('busy = true;\n  (async () => {', 1)[1].split('const browserAcquisition', 1)[0]
    responses = re.findall(r'sendResponse\(([^;]+)\);', claim_listener)
    assert responses == ['{ok: claimed}']


def test_initial_secret_is_discarded_before_session_state():
    claim = WORKER.split('async function claimHandoff', 1)[1].split('chrome.runtime.onMessage', 1)[0]
    assert 'delete message.handoffUrl' in claim
    assert claim.index('handoff.capability = ""') < claim.index('const state =')
    state = re.search(r'const state = (\{.*?\});', claim, re.S).group(1)
    assert 'handoff.capability' not in state and 'handoffUrl' not in state
    assert 'eventCapability: reply.event_capability' in state
    assert 'payload.capability = ""' in WORKER
    assert 'handoff.capability = ""' in CONTENT


def test_session_restoration_is_minimal_and_worker_only():
    assert 'setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"})' in WORKER
    assert 'chrome.storage' not in CONTENT
    fields = re.search(r'const SESSION_FIELDS = (\[.*?\]);', WORKER).group(1)
    assert json.loads(fields) == ['eventCapability', 'origin', 'tabId', 'taskId']
    assert set(re.findall(r'chrome\.storage\.(\w+)', WORKER)) == {'session'}
    restoration = WORKER.split('async function loadState()', 1)[1].split('function trustedSender', 1)[0]
    for check in (
        'chrome.storage.session.get(STATE_KEY)', 'if (state === undefined) return null',
        'Object.keys(state).sort().join(",") !== SESSION_FIELDS.join(",")',
        'validTaskId(state.taskId)', 'validOrigin(state.origin)', 'tabBinding(state.tabId)',
        'validCapability(state.eventCapability)',
        'chrome.storage.session.remove(STATE_KEY)',
    ):
        assert check in restoration
    assert 'parseInitialUrl' not in restoration


def test_one_frozen_owner_and_no_silent_retarget_or_reclaim():
    assert 'state.tabId === owner.tabId && state.taskId === owner.taskId && state.origin === owner.origin' in WORKER
    assert 'if (matchesOwner(existing, owner)) return {claimed: true}' in WORKER
    assert '!state || !matchesOwner(state, owner)' in WORKER
    assert WORKER.count('postJson(handoff.origin, CLAIM_PATH,') == 1
    assert 'chrome.action.onClicked' not in SCRIPTS


def test_mismatched_owner_resolves_old_authority_before_new_claim():
    claim = WORKER.split('async function claimHandoff', 1)[1].split('chrome.runtime.onMessage', 1)[0]
    existing = claim.split('if (existing) {', 1)[1].split('let response;', 1)[0]
    assert existing.index('if (matchesOwner(existing, owner)) return') < existing.index('retireInactiveAuthority(existing)')
    assert 'if (!await retireInactiveAuthority(existing)) return null' in existing
    assert 'postJson(' not in existing and 'capability = ""' not in existing
    assert claim.index('retireInactiveAuthority(existing)') < claim.index('postJson(handoff.origin, CLAIM_PATH,')


def test_old_status_get_has_only_validated_old_identity_and_no_secrets():
    check = WORKER.split('async function authorityStatus(state)', 1)[1].split('async function retireCurrentAuthority', 1)[0]
    for expected in (
        '!BrowserHandoffProtocol.validOrigin(state.origin)',
        '!BrowserHandoffProtocol.validTaskId(state.taskId)',
        'fetch(state.origin + "/browser-handoff/" + state.taskId,',
        'method: "GET"', 'credentials: "omit"', 'redirect: "error"',
        'referrerPolicy: "no-referrer"', 'cache: "no-store"',
        'new AbortController()', 'controller.abort(), 10000',
        'signal: controller.signal', 'clearTimeout(timer)',
    ):
        assert expected in check
    assert check.index('validOrigin(state.origin)') < check.index('fetch(')
    assert check.index('validTaskId(state.taskId)') < check.index('fetch(')
    for forbidden in ('capability', 'Capability', 'handoff.', 'body:', 'headers:'):
        assert forbidden not in check
    destination = re.search(r'fetch\(([^,]+),', check).group(1)
    assert '?' not in destination and '#' not in destination


def test_active_or_unexpected_old_status_preserves_state_and_blocks_claim():
    check = WORKER.split('async function authorityStatus(state)', 1)[1].split('async function retireCurrentAuthority', 1)[0]
    assert 'return response.status === 200 || response.status === 404 ? response.status : null' in check
    assert 'chrome.storage' not in check and 'postJson(' not in check
    retire = WORKER.split('async function retireInactiveAuthority', 1)[1].split('async function postJson', 1)[0]
    assert 'await authorityStatus(state) === 404 && await retireCurrentAuthority(state)' in retire


def test_confirmed_old_404_retires_state_then_uses_fresh_claim_authority():
    check = WORKER.split('async function authorityStatus(state)', 1)[1].split('async function retireCurrentAuthority', 1)[0]
    retire = WORKER.split('async function retireInactiveAuthority', 1)[1].split('async function postJson', 1)[0]
    assert 'await authorityStatus(state) === 404 && await retireCurrentAuthority(state)' in retire
    discard = WORKER.split('async function retireCurrentAuthority', 1)[1].split('async function retireInactiveAuthority', 1)[0]
    assert discard.index('current.eventCapability !== state.eventCapability') < discard.index('chrome.storage.session.remove(STATE_KEY)')
    claim = WORKER.split('async function claimHandoff', 1)[1].split('chrome.runtime.onMessage', 1)[0]
    assert claim.index('await retireInactiveAuthority(existing)') < claim.index('postJson(handoff.origin, CLAIM_PATH,')
    assert 'task_id: handoff.taskId, capability: handoff.capability' in claim
    assert 'tab_binding: BrowserHandoffProtocol.tabBinding(sender.tab.id)' in claim
    assert 'eventCapability: reply.event_capability' in claim
    assert 'existing.eventCapability' not in claim
    assert claim.index('capability: handoff.capability') < claim.index('handoff.capability = ""')


def test_old_status_failure_keeps_state_and_new_fragment_retryable():
    check = WORKER.split('async function authorityStatus(state)', 1)[1].split('async function retireCurrentAuthority', 1)[0]
    failed = check.split('} catch {', 1)[1].split('} finally {', 1)[0]
    assert 'return null' in failed
    assert 'chrome.storage' not in failed and 'postJson(' not in failed
    assert 'if (!await retireInactiveAuthority(existing)) return null' in WORKER
    assert 'sendResponse({ok: claimed})' in WORKER
    # False acknowledgements retain the page's unconsumed initial URL for reload.
    assert CONTENT.index('result?.ok !== true) return') < CONTENT.index('cleanUrl.hash = ""')


def test_stale_retirement_does_not_depend_on_old_tab_lifecycle():
    check = WORKER.split('async function authorityStatus(state)', 1)[1].split('async function retireCurrentAuthority', 1)[0]
    assert set(re.findall(r'state\.(\w+)', check)) == {'origin', 'taskId'}
    assert 'tabId' not in check and 'parsePageUrl' not in check
    assert 'chrome.tabs' not in check and 'onRemoved' not in check
    assert check.count('fetch(') == 1


def test_documented_sequential_recovery_is_fail_closed():
    for term in ('terminal/cancel invalidation', 'GET `/browser-handoff/', '200', '404',
                 '旧 tab 已关闭', 'fail closed', '不发送新 claim', '不清除新页面 fragment'):
        assert term in README


def test_ready_is_authenticated_and_only_explicitly_retried():
    ready = WORKER.split('async function sendReady(state)', 1)[1].split('async function claimHandoff', 1)[0]
    for check in (
        'emitEvent(state, "tab_ready", {})', 'current.eventCapability !== state.eventCapability',
    ):
        assert check in ready
    assert 'readyDelivered' not in WORKER and 'freshState' not in WORKER
    assert 'activate_handoff' in CONTENT and 'activate_handoff' in WORKER
    emitter = WORKER.split('async function emitEvent', 1)[1].split('async function sendReady', 1)[0]
    for check in ('postJson(state.origin, EVENT_PATH,', 'capability: state.eventCapability',
                  'response.status === 403', 'retireCurrentAuthority(state)',
                  'reply = eventReply(response, state.taskId)'):
        assert check in emitter
    assert 'chrome.action.onClicked' not in SCRIPTS
    assert 'controller.abort(), 10000' in WORKER and 'clearTimeout(timer)' in WORKER
    assert '|| busy' in WORKER and 'busy = false' in WORKER
    assert not re.search(r'setInterval|\bwhile\s*\(|\bfor\s*\(', WORKER)


def test_consumed_fragment_is_scrubbed_even_if_session_save_fails():
    assert 'chrome.storage.session.remove(STATE_KEY).catch(() => {})' in WORKER
    assert 'return {claimed: true}' in WORKER
    assert 'result?.ok !== true' in CONTENT
    assert 'window.location.href !== initialUrl' in CONTENT
    assert 'cleanUrl.hash = ""' in CONTENT
    assert 'window.history.replaceState(null, "", cleanUrl.href)' in CONTENT
    assert not re.search(r'location\.(assign|replace)|location\.(href|hash)\s*=(?!=)|\bdocument\.', CONTENT)


def test_no_legacy_browser_or_external_zotero_integration():
    for forbidden in ('Zotero', 'Connector', 'Playwright', 'subprocess', 'window.open', '/api/links'):
        assert forbidden not in SCRIPTS


def test_installation_and_guidance_are_truthful():
    for term in ('chrome://extensions', 'Developer mode', 'Load unpacked', 'browser_companion',
                 '普通 Chrome', 'Chrome 102', 'Literature Monitor', 'Zotero Desktop',
                 'cookie', '密码', 'Zotero Connector', 'DOI-bound',
                 'https://doi.org/<normalized-doi>', 'PUBLISHER_EXHAUSTED',
                 'publisher 已明确 exhausted', 'recordEvidence', 'ebsco_pdf_action',
                 '10 秒', '120 秒', 'navigation epoch', 'stage_download', 'StagedPdf',
                 'v0.5.2 是最新已发布版本', 'v0.5.3 development',
                 'Python package metadata prepared identity 为 **0.5.3**',
                 '历史 v0.5.1 证据不建立 v0.5.2 live 验证'):
        assert term in README
    template = (ROOT / 'src/literature_monitor/web/templates/browser_handoff.html').read_text()
    for term in ('chrome://extensions', 'Developer mode', 'Load unpacked', 'browser_companion'):
        assert term in template
    for forbidden in ('<script', 'capability', 'csrf', 'fetch(', 'claim_handoff'):
        assert forbidden not in template


def test_a7_commands_bind_to_session_tab_not_a_supplied_tab():
    assert 'owner.taskId !== plan.task_id' in ACQUISITION
    assert 'tabId: owner.tabId' in ACQUISITION
    assert 'chrome.tabs.update(ctx.tabId, {url})' in ACQUISITION
    assert 'chrome.tabs.create' not in SCRIPTS
    writer = ACQUISITION.split('async function save', 1)[1].split('async function reconcileNavigation', 1)[0]
    assert 'event.tabId !== owner.tabId' in writer
    assert 'sender.tab?.id !== ctx.tabId' in ACQUISITION
    assert 'capability: state.eventCapability, event_type: eventType, payload' in WORKER
    emitter = WORKER.split('async function emitEvent', 1)[1].split('async function sendReady', 1)[0]
    assert 'if (busy) return false' in emitter
    assert '!matchesOwner(current, state)' in emitter
    assert 'current.eventCapability !== state.eventCapability' in emitter
    assert emitter.index('current.eventCapability !== state.eventCapability') < emitter.index('postJson(')


def test_a7_frozen_doi_route_and_explicit_exhaustion_fallback():
    assert 'navigationUrl: null' in ACQUISITION
    assert 'return navigate(ctx, plan.direct_url)' in ACQUISITION
    assert 'status !== "exhausted" || ctx.candidate' in ACQUISITION
    assert 'acquisition_class' not in ACQUISITION
    assert 'navigate(ctx, AcquisitionProtocol.resolverUrl(ctx.plan.doi),' in ACQUISITION
    assert 'value.direct_url !== doiUrl(value.doi)' in ACQUISITION_PROTOCOL
    assert 'target_version' not in ACQUISITION_PROTOCOL
    assert '"human_required"' in ACQUISITION
    assert 'if (message.human_required)' in ACQUISITION
    assert 'metadata' not in RESOLVER  # No generic publisher metadata/DOM classifier.


def test_a7_exact_resolver_context_and_visible_category_adapter():
    entry = MANIFEST['content_scripts'][1]
    assert entry['matches'] == ['https://resolver.ebsco.com/c/45yels/result*', 'https://resolver.ebsco.com/redirect?*']
    assert entry['all_frames'] is False
    assert entry['js'] == ['handoff_protocol.js', 'acquisition_protocol.js', 'resolver_content.js']
    for check in ('url.origin === expected.origin', '[expected.pathname, "/redirect"].includes(url.pathname)',
                  'url.searchParams.getAll(key).length === 1', 'url.searchParams.get(key) === v',
                  'customer: "s1215021", group: "main", profile: "ftf"'):
        assert check in ACQUISITION_PROTOCOL
    assert 'AcquisitionProtocol.resolverContext(pageUrl, context.doi)' in RESOLVER
    assert 'sender.id !== chrome.runtime.id || sender.frameId !== 0' in ACQUISITION
    assert 'getClientRects().length > 0' in RESOLVER
    assert 'if (label === "full text") return "FullText"' in RESOLVER
    assert 'return "SmartLinks"' in RESOLVER
    assert 'search\\s*engines|document\\s*delivery|\\bother\\b|research|help|tool' in RESOLVER
    assert 'if (heading && !groupCategory) continue' in RESOLVER
    assert RESOLVER.index('if (!eligible') < RESOLVER.index('safeUrl(anchor.href)')


def test_a7_choices_preserve_order_and_surface_ambiguity():
    assert 'for (const anchor of document.querySelectorAll' in RESOLVER
    assert 'choices.push({category: eligible, label, target})' in RESOLVER
    assert '.sort(' not in RESOLVER
    assert 'if (choices.length > 6) { report(pageUrl, [], false, "choice_overflow")' in RESOLVER
    assert 'new MutationObserver' in RESOLVER and 'inspect(true), 15000' in RESOLVER
    assert 'observer.disconnect()' in RESOLVER
    assert 'if (choices.length === 1)' in ACQUISITION and 'if (await choose(ctx.taskId, 0)) return {ok: true}' in ACQUISITION
    assert 'else if (choices.length > 1) await emitChoices(ctx)' in ACQUISITION
    assert '"resolver_choices"' in ACQUISITION
    assert 'choices.map((c, id) => ({id, category: c.category, label: c.label}))' in ACQUISITION
    assert 'ctx.taskId !== taskId || ctx.route !== "xmu"' in ACQUISITION


def test_a7_download_ownership_is_conservative_and_bound():
    for check in ('started - ctx.navigationTime <= ARM_WINDOW_MS',
                  '[item.url, item.finalUrl].includes(ctx.navigationUrl)',
                  '[ctx.navigationUrl, ctx.previousUrl].includes(item.referrer)',
                  'chrome.tabs.get(ctx.tabId)',
                  'next.candidate && next.candidate.id !== downloadChange.id',
                  'ambiguous_download_ownership',
                  'candidate.ownership === "extension_id"',
                  'item.byExtensionId !== chrome.runtime.id', 'ctx.candidate?.id !== id'):
        assert check in ACQUISITION
    assert 'navigationTime: Date.now()' in ACQUISITION
    assert 'tab.id !== ctx.tabId' in ACQUISITION
    assert 'chrome.downloads.search({id})' in ACQUISITION
    assert 'chrome.downloads.search({})' not in ACQUISITION
    assert 'overlappingDownload' not in ACQUISITION
    assert 'chrome.tabs.query({url:' not in ACQUISITION


def test_fix1_landing_download_requires_explicit_trusted_arm():
    arm = ACQUISITION.split('async function armUserDownload', 1)[1].split('async function navigationFailed', 1)[0]
    for check in ('tab.id !== ctx.tabId', 'tab.incognito', 'ctx.ambiguous || ctx.downloadReported || ctx.downloadOutcome || ctx.candidate',
                  'tab.url !== ctx.navigationUrl', 'chrome.tabs.get(ctx.tabId)',
                  'liveTab.id !== ctx.tabId', 'liveTab.url !== ctx.navigationUrl',
                  'navigationEpoch: ctx.navigationEpoch', 'navigationUrl: ctx.navigationUrl, armedAt: Date.now()'):
        assert check in arm
    assert 'ctx.navigationTime =' not in arm
    assert 'chrome.action.onClicked' not in ACQUISITION
    assert 'armUserDownload' not in ACQUISITION.split('chrome.runtime.onMessage.addListener', 1)[1]
    source = ACQUISITION.split('async function uniqueTaskSource', 1)[1].split('function downloadExpectation', 1)[0]
    # article -> file.pdf is eligible only through a matching, explicitly armed
    # referrer (SPA/root-referrer tolerance is limited to approved EBSCO blobs);
    # an unarmed referrer cannot satisfy the exact-source alternative.
    for check in ('const exactSource = !blob && !armedOnly && [item.url, item.finalUrl].includes(ctx.navigationUrl)',
                  'const armedSource = arm && arm.navigationEpoch === ctx.navigationEpoch && arm.navigationUrl === ctx.navigationUrl',
                  'item.referrer === arm.navigationUrl', 'started >= arm.armedAt',
                  'started - arm.armedAt <= ARM_WINDOW_MS', 'if (!exactSource && !armedSource) return null',
                  'useArm ? tab.url !== arm.navigationUrl',
                  'item.byExtensionId && item.byExtensionId !== chrome.runtime.id',
                  'AcquisitionProtocol.downloadTransport(item)'):
        assert check in source
    assert 'item.tabId' not in source
    assert not any(term in source for term in ('filename', 'mime', 'downloads.search'))


def test_fix1_arm_is_consumed_and_frozen_for_completion():
    context = ACQUISITION.split('async function context()', 1)[1].split('async function save', 1)[0]
    for check in ('value.ambiguous', 'value.userArm.navigationUrl !== value.navigationUrl',
                  'Date.now() - value.userArm.armedAt > ARM_WINDOW_MS', 'value.userArm = null', 'await save(value)'):
        assert check in context
    assert 'recordEvidence: null, providerAction: null, userArm: null' in ACQUISITION
    committed = ACQUISITION.split('if (commit !== null)', 1)[1].split('} else {', 1)[0]
    assert 'recordEvidence: null, providerAction: null, userArm: null' in committed
    observation = ACQUISITION.split('async function observeDownload', 1)[1].split('async function created', 1)[0]
    assert 'navigationTime: proof.navigationTime, expectation' in observation
    assert 'userArm: proof.userArm' in observation
    assert 'ctx.userArm = null' not in observation
    writer = ACQUISITION.split('async function save', 1)[1].split('async function reconcileNavigation', 1)[0]
    assert 'next.candidate = structuredClone(downloadChange.candidate)' in writer
    assert 'next.userArm = null' in writer
    complete = ACQUISITION.split('async function completed', 1)[1].split('async function reconcileDownloadOutcome', 1)[0]
    assert 'candidate.userArm && ctx.navigationUrl !== candidate.navigationUrl' in complete
    assert 'item, candidate.userArm, Boolean(candidate.userArm), candidate.providerAction' in complete
    assert 'navigation_time: candidate.navigationTime' in complete
    # Consumption/expiry of the live arm does not erase the one candidate's proof.
    assert 'item, ctx.userArm' not in complete
    assert 'userArm: useArm ? {...arm} : null' in ACQUISITION


def test_fix1_exact_anchor_labels_cannot_bypass_visible_result_structure():
    for check in ('anchor.closest(\'li, [role="listitem"], tr, [role="row"]\')',
                  'row?.closest(\'ul, ol, [role="list"], table, [role="table"], section, [role="group"]\')',
                  'const structure = collection || group',
                  'if (!redirectCategory && (!structure || !visible(structure) || (row && !visible(row)))) continue',
                  'if (heading && !groupCategory) continue'):
        assert check in RESOLVER
    # Applies equally to standalone Full Text and SmartLink anchors, before
    # either own-label or group-heading eligibility is considered.
    assert RESOLVER.index('if (!redirectCategory && (!structure') < RESOLVER.index('const ownCategory')
    assert RESOLVER.index('if (!redirectCategory && (!structure') < RESOLVER.index('safeUrl(anchor.href)')
    assert 'if (!row && !groupCategory && !redirectCategory) continue' in RESOLVER


def test_fix2_exact_labels_in_bare_or_unrelated_sections_are_rejected():
    choices = RESOLVER.split("for (const anchor of document.querySelectorAll('a[href]'))", 1)[1]
    # <a>Full Text</a> alone fails the existing visible-structure gate.
    assert 'if (!redirectCategory && (!structure || !visible(structure) || (row && !visible(row)))) continue' in choices
    # <section><a>Full Text</a></section> has no row or group category:
    # the own label must not bypass this rejection.
    gate = 'if (!row && !groupCategory && !redirectCategory) continue'
    assert gate in choices
    # <section><h2>Unrelated</h2><a>Full Text</a></section> is rejected
    # even though its anchor text independently names an eligible category.
    assert 'if (heading && !groupCategory) continue' in choices
    assert choices.index('if (heading && !groupCategory)') < choices.index('const eligible')
    assert choices.index(gate) < choices.index('safeUrl(anchor.href)')


def test_fix2_visible_rows_or_eligible_headed_groups_remain_supported():
    for check in ('anchor.closest(\'li, [role="listitem"], tr, [role="row"]\')',
                  'row?.closest(\'ul, ol, [role="list"], table, [role="table"], section, [role="group"]\')',
                  'const ownCategory = category(label)',
                  'heading && visible(heading)',
                  'heading.closest(\'section, [role="group"]\') === group',
                  'category(heading.textContent)', 'const eligible = redirectCategory || groupCategory || ownCategory'):
        assert check in RESOLVER
    # A visible ul/li or table/result row can supply an exact anchor label.
    # A provider anchor without a row can use its group's own eligible heading.
    assert 'if (!row && !groupCategory && !redirectCategory) continue' in RESOLVER
    assert 'if (!ownCategory && !row) continue' not in RESOLVER
    assert 'if (!row) continue' not in RESOLVER
    assert RESOLVER.index('if (!row && !groupCategory && !redirectCategory)') < RESOLVER.index('safeUrl(anchor.href)')


def test_a7_only_complete_candidates_and_no_user_download_mutation():
    for check in ('item.state === "interrupted"', 'item.exists === false',
                  'if (item.state !== "complete") return', 'Number.isSafeInteger(item.fileSize)',
                  'state: "complete"', 'eventType: downloadChange.type === "complete" ? "download_candidate" : "browser_path_failure"',
                  'AcquisitionProtocol.boundedPayload(payload)',
                  'chrome.downloads.download({url: ctx.navigationUrl, saveAs: true, conflictAction: "uniquify"})'):
        assert check in ACQUISITION
    for forbidden in ('removeFile', '.erase(', '.cancel(', 'chrome.cookies', 'headers:', 'password', 'api_key'):
        assert forbidden not in ACQUISITION
    for retired in ('version_labels', 'manifestation', 'target_version', 'versionEvidence'):
        assert retired not in ACQUISITION
    assert 'sanitizedIdentity(ctx.navigationUrl)' in ACQUISITION
    assert '{scheme: new URL(safe).protocol, host: new URL(safe).hostname}' in ACQUISITION_PROTOCOL


def test_a8_fixed_command_schema_has_no_tab_or_arbitrary_url_override():
    validator=ACQUISITION_PROTOCOL.split('function command(value, taskId)',1)[1].split('return Object.freeze',1)[0]
    for check in ('value.task_id !== taskId','fields === "plan,task_id,type"',
                  'const validated = plan(value.plan)','validated?.task_id === taskId',
                  'fields === "choice_id,task_id,type"','Number.isSafeInteger(value.choice_id)',
                  '["PUBLISHER_EXHAUSTED", "DOWNLOAD_CURRENT"]','fields === "task_id,type"'):
        assert check in validator
    assert 'tabId' not in validator and 'capability' not in validator
    execute=ACQUISITION.split('async function executeCommand',1)[1].split('async function guarded',1)[0]
    assert 'owner.taskId !== taskId' in execute
    assert 'acquisition_class' not in execute
    assert 'choose(taskId, value.choice_id)' in execute
    assert 'download(taskId, ctx.navigationUrl)' in execute
    assert 'JSON.stringify(ctx.plan) === JSON.stringify(value.plan)' in execute


def test_a8_reply_execution_rechecks_owner_after_releasing_network_mutex():
    emitter=WORKER.split('async function emitEvent',1)[1].split('async function sendReady',1)[0]
    assert emitter.count('current.eventCapability !== state.eventCapability')==2
    assert emitter.index('busy = false') < emitter.index('browserAcquisition.executeCommand')
    assert emitter.rindex('current.eventCapability !== state.eventCapability') < emitter.index('browserAcquisition.executeCommand')
    reply=WORKER.split('function eventReply',1)[1].split('async function emitEvent',1)[0]
    assert 'Object.keys(body).join(",") !== "command"' in reply
    assert 'AcquisitionProtocol.command(body.command, taskId)' in reply
    assert 'response.status !== 200' in reply
    assert 'path === CLAIM_PATH ? 1024 : 8192' in WORKER
    assert 'setInterval' not in SCRIPTS


def test_a8_popup_is_extension_owned_and_uses_only_real_claimed_tab():
    assert (ARTIFACT/MANIFEST['action']['default_popup']).is_file()
    for path in ('task_popup.js','task_popup.css'):
        assert path in (ARTIFACT/'task_popup.html').read_text() and (ARTIFACT/path).is_file()
    for check in ('sender.id !== chrome.runtime.id',
                  'sender.url !== chrome.runtime.getURL("task_popup.html")',
                  'chrome.tabs.query({active: true, currentWindow: true})',
                  'tab.id !== state.tabId', 'tab.incognito',
                  'fields !== "action,choice_id,type"'):
        assert check in WORKER
    for forbidden in ('eventCapability','capability','navigationUrl','localStorage','fetch(','.innerHTML'):
        assert forbidden not in POPUP
    for action in ('arm','download','fallback','choose'):
        assert '"'+action+'"' in POPUP
    user=ACQUISITION.split('async function userAction',1)[1].split('async function executeCommand',1)[0]
    assert 'live.url !== ctx.navigationUrl' in user
    assert 'acquisition_class' not in user
    for kind in ('publisher_fallback_request','resolver_choice_request','user_download_request'):
        assert kind in user
    assert 'armUserDownload(live)' in user


def test_returned_commands_require_app_liveness_and_fixed_failure_reporting():
    emitter=WORKER.split('async function emitEvent',1)[1].split('async function sendReady',1)[0]
    assert emitter.index('reply = eventReply') < emitter.index('await authorityStatus(state)') < emitter.index('browserAcquisition.executeCommand')
    assert 'if (status !== 200)' in emitter
    assert 'if (status === 404) await retireCurrentAuthority(state)' in emitter
    assert 'capability: state.eventCapability, event_type: "browser_path_failure"' in emitter
    assert '"download_unavailable" : "navigation_failed"' in emitter
    navigation=ACQUISITION.split('async function navigate',1)[1].split('async function start',1)[0]
    assert navigation.index('ctx.pendingNavigation = pending') < navigation.index('await save(ctx)') < navigation.index('chrome.tabs.update')
    assert 'clearPending(ctx, pending)' in navigation
    start = ACQUISITION.split('async function observeNavigationStart', 1)[1].split('async function observeNavigation', 1)[0]
    assert 'navigationStart:' in start
    assert 'emit(' not in start and 'ctx.navigationUrl =' not in start


def test_a8_shipped_worker_with_simulated_chrome_and_network():
    import importlib.util
    import shutil
    import subprocess
    node=shutil.which('node')
    if node is None:
        spec=importlib.util.find_spec('playwright')
        candidate=Path(spec.origin).parent/'driver'/'node' if spec is not None else None
        node=str(candidate) if candidate is not None and candidate.is_file() else None
    if node is None:
        pytest.skip('No existing JavaScript runtime; structural checks remain available.')
    for script in ARTIFACT.glob('*.js'):
        result=subprocess.run([node,'--check',str(script)],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
    result=subprocess.run([node,str(ROOT/'tests/browser_companion_runtime.cjs')],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'Shipped worker simulation:' in result.stdout
    assert 'Shipped content adapters:' in result.stdout
    assert 'Generic download observation:' in result.stdout
    assert 'Download outcome delivery:' in result.stdout
    assert 'No-arm provider action:' in result.stdout
    assert 'Task tab close:' in result.stdout
    assert 'Two-phase handoff:' in result.stdout
    assert 'Navigation sequence association:' in result.stdout


def test_live_fix_record_adapter_is_narrow_and_keeps_navigation_http_only():
    entry = MANIFEST['content_scripts'][2]
    assert entry['matches'] == ['https://research.ebsco.com/c/*/search/details/*']
    assert entry['js'] == ['acquisition_protocol.js', 'ebsco_record_content.js']
    assert entry['all_frames'] is False
    assert 'const embedded = new URL(blob.pathname)' in ACQUISITION_PROTOCOL
    safe_url = ACQUISITION_PROTOCOL.split('function safeUrl', 1)[1].split('function doiUrl', 1)[0]
    assert 'blob' not in safe_url
    for check in ('record-html-metadata', 'child.nodeType === 3', 'observed.fields.doi !== context.doi',
                  'event.isTrusted', 'bulk-download-modal-download-button', 'node.checked',
                  'observed.node.isConnected', 'text(observed.node) !== observed.text'):
        assert check in EBSCO
    for retired in ('document_type', 'processed-link__publication-authority', 'meta-data-publication-year'):
        assert retired not in EBSCO
    assert 'storeRecordEvidence(ctx, evidence, providerAction)' in ACQUISITION
    assert 'sender.tab?.id !== ctx.tabId' in ACQUISITION
    assert 'acquisition_class' not in ACQUISITION
    assert 'payload.provider_record_url = recordEvidence?.recordUrl || null' in ACQUISITION


def test_verified_provider_preparation_uses_separate_explicit_clocks():
    assert 'const ARM_WINDOW_MS = 10000' in ACQUISITION
    assert 'const PROVIDER_PREPARATION_WINDOW_MS = 120000' in ACQUISITION
    action = ACQUISITION.split('async function ebscoMessage', 1)[1].split('async function resolverMessage', 1)[0]
    assert 'ctx.userArm' not in action and 'armedAt' not in action
    assert 'message.navigation_epoch !== ctx.navigationEpoch' in action
    assert 'Date.now() - message.action_time > ACTION_MESSAGE_WINDOW_MS' in action
    assert 'storeRecordEvidence(ctx, evidence, providerAction)' in action
    source = ACQUISITION.split('async function uniqueTaskSource', 1)[1].split('function downloadExpectation', 1)[0]
    assert 'verifiedProviderAction(ctx, providerAction, evidence)' in source
    assert 'started - providerAction.actionTime > PROVIDER_PREPARATION_WINDOW_MS' in source
    assert 'started - arm.armedAt <= ARM_WINDOW_MS' in source
    assert 'payload.attribution = "ebsco_pdf_action"' in ACQUISITION
    assert 'arm_time' not in ACQUISITION
    assert 'provider_action' in ACQUISITION
    assert 'payload.action_time = candidate.providerAction.actionTime' in ACQUISITION
    assert 'payload.navigation_time = candidate.providerAction.navigationTime' in ACQUISITION


def test_a4_popup_removes_redundant_operations_and_keeps_content_human_wait():
    html = (ARTIFACT / 'task_popup.html').read_text()
    for removed in ('Check current download', 'Login or verification needed', 'selected version'):
        assert removed not in html + POPUP
    assert 'Capture next PDF download' in html
    assert 'checkDownload' not in ACQUISITION
    user = ACQUISITION.split('async function userAction', 1)[1].split('async function executeCommand', 1)[0]
    assert 'action === "check"' not in user and 'action === "human"' not in user
    assert '"check"' not in POPUP and '"human"' not in POPUP
    assert 'human_action_needed' in ACQUISITION
