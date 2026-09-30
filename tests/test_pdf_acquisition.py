"""Network-independent generic acquisition tests; all targets and bytes fabricated."""

from contextlib import contextmanager
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as BrowserTimeout

from literature_monitor import pdf_acquisition as acquisition
from literature_monitor.institutional_resolver import ResolverCandidate
from literature_monitor.pdf_acquisition import PdfAcquisitionOutcome as Outcome, PdfSourceKind as Source, GenericPdfAcquirer

SECRET = 'SENTINEL_SIGNED_TARGET_COOKIE'
FIRST = 'https://publisher.example/landing?signature=' + SECRET
SECOND = 'https://other.example/landing?session=' + SECRET
PDF = b'%PDF-1.7\n fabricated test fixture\n%%EOF'
HTML = b'<html>not a PDF</html>'


def candidate(target=FIRST, category='FullText'):
    return ResolverCandidate(category, target)


def route(data=HTML, *, status=200, content_type='text/html', metadata=None, links=None, downloads=None,
          human=False, final=None, error=None):
    return SimpleNamespace(data=data, status=status, content_type=content_type, metadata=metadata or [],
                           links=links or [], downloads=downloads or [], human=human, final=final, error=error)


class FakeDownload:
    def __init__(self, data=PDF, error=None):
        self.data, self.error = data, error
        self.saved = []
    @property
    def suggested_filename(self):
        raise AssertionError('Never trust a suggested filename.')
    @property
    def url(self):
        raise AssertionError('Do not expose a download target.')
    def save_as(self, name):
        path = Path(name)
        self.saved.append(path)
        path.write_bytes(self.data)
        if self.error:
            raise self.error


class FakeResponse:
    def __init__(self, item, page):
        self.status = item.status
        self.url = page.url
        self.headers = {'content-type': item.content_type}
        self.item = item
        self.request = SimpleNamespace(is_navigation_request=lambda: True, frame=page.main_frame)
    def body(self):
        if isinstance(self.item.data, BaseException):
            raise self.item.data
        return self.item.data


class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector
    def count(self):
        assert 'password' in self.selector
        return int(self.page.current.human)
    def evaluate_all(self, expression):
        if self.selector.startswith('meta'):
            self.page.reads.append('metadata')
            return self.page.current.metadata
        assert self.selector == 'a[href], link[href]'
        self.page.reads.append('links')
        if self.page.links_error:
            raise RuntimeError(SECRET)
        return self.page.current.links


class FakePage:
    def __init__(self, state):
        self.state = state
        self.url = 'about:blank'
        self.main_frame = object()
        self.current = route()
        self.handlers = {'response': [], 'download': []}
        self.visits, self.waits, self.reads = [], [], []
        self.links_error = False
        self.human_clears_at = None
        self.after_human = None
        self.late_downloads = []
        self.download_waits = []
    def on(self, event, callback):
        self.handlers[event].append(callback)
    def remove_listener(self, event, callback):
        self.handlers[event].remove(callback)
    def goto(self, target, **kwargs):
        self.visits.append((target, kwargs))
        item = self.state.routes[target]
        self.current = item
        self.url = item.final or target
        response = FakeResponse(item, self)
        for callback in self.handlers['response']:
            callback(response)
        for download in item.downloads:
            for callback in self.handlers['download']:
                callback(download)
        if item.error:
            raise item.error
        return response
    def wait_for_timeout(self, ms):
        self.waits.append(ms)
        self.state.now += ms / 1000
        if self.human_clears_at is not None and self.state.now >= self.human_clears_at:
            self.current = self.after_human or route()
            self.human_clears_at = None
            for callback in self.handlers['response']:
                callback(FakeResponse(self.current, self))
    def locator(self, selector):
        return FakeLocator(self, selector)
    def wait_for_event(self, event, timeout):
        assert event == 'download'
        self.download_waits.append(timeout)
        if not self.late_downloads:
            raise BrowserTimeout(SECRET)
        download = self.late_downloads.pop(0)
        for callback in self.handlers['download']:
            callback(download)
        return download


class FakeContext:
    def __init__(self, page):
        self.page, self.closed = page, False
        self.close_error = None
        self.default_timeout = None
    def new_page(self):
        return self.page
    def set_default_timeout(self, ms):
        self.default_timeout = ms
    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error
    def cookies(self):
        raise AssertionError('Cookies must remain in the browser.')
    def storage_state(self, **kwargs):
        raise AssertionError('No storage-state export.')
    @property
    def request(self):
        raise AssertionError('This implementation uses browser navigation only.')


@pytest.fixture
def scenario(monkeypatch, tmp_path):
    state = SimpleNamespace(now=10.0, routes={FIRST: route()}, started=0, stopped=0, launches=[], launch_error=None)
    state.page = FakePage(state)
    state.context = FakeContext(state.page)
    state.app_data = tmp_path / 'machine-state' / 'Literature Monitor'
    state.temp = tmp_path / 'system-temp'
    state.temp.mkdir()
    state.platform_calls = []
    def user_data(name, **kwargs):
        state.platform_calls.append((name, kwargs))
        return state.app_data
    monkeypatch.setattr(acquisition, 'user_data_path', user_data)
    monkeypatch.setattr(acquisition.tempfile, 'gettempdir', lambda: str(state.temp))
    monkeypatch.setattr(acquisition.time, 'monotonic', lambda: state.now)
    class Chromium:
        def launch_persistent_context(self, profile, **kwargs):
            state.launches.append((profile, kwargs))
            if state.launch_error:
                raise state.launch_error
            return state.context
    @contextmanager
    def session():
        state.started += 1
        try:
            yield SimpleNamespace(chromium=Chromium())
        finally:
            state.stopped += 1
    monkeypatch.setattr(acquisition, '_browser_session', session)
    state.acquirer = GenericPdfAcquirer(project_dir=tmp_path / 'repo', workspace_dir=tmp_path / 'workspace',
                                       config_path=tmp_path / 'config' / 'monitor.yaml', timeout_seconds=2)
    return state


def acquire(state, candidates=None):
    return state.acquirer.acquire((candidate(),) if candidates is None else candidates)


def assert_private(result, caplog=None):
    public = repr(result) + result.message + (caplog.text if caplog else '')
    assert SECRET not in public
    assert 'publisher.example' not in public and 'other.example' not in public and 'redirect.example' not in public
    assert not hasattr(result, 'target')
    if result.pdf:
        assert not hasattr(result.pdf, 'target')


def test_direct_pdf_and_attempt_ownership(scenario, caplog):
    scenario.routes[FIRST] = route(PDF, content_type='text/html', final='https://redirect.example/file?token=' + SECRET)
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED
    pdf = result.pdf
    assert pdf.path.read_bytes() == PDF
    assert pdf.source_kind is Source.DIRECT_RESPONSE and pdf.candidate_position == 1
    assert pdf.category == 'FullText' and pdf.byte_count == len(PDF)
    assert pdf.path.is_relative_to(scenario.temp)
    profile, options = scenario.launches[0]
    assert Path(profile) == scenario.app_data / 'institutional-browser'
    assert scenario.platform_calls == [('Literature Monitor', {'appauthor': False})]
    assert options['headless'] is False and options['accept_downloads'] is True and options['channel'] == 'chrome'
    assert Path(options['downloads_path']) == pdf.path.parent
    assert scenario.context.closed and scenario.started == scenario.stopped == 1
    assert all(not handlers for handlers in scenario.page.handlers.values())
    caplog.set_level(logging.INFO)
    logging.getLogger(__name__).info('Acquisition result: %r', result)
    assert 'Acquisition result:' in caplog.text
    assert_private(result, caplog)
    pdf.cleanup()
    pdf.cleanup()  # Cleanup is idempotent.
    assert not pdf.path.exists() and not list(scenario.temp.iterdir())
    assert Path(profile).is_dir()


@pytest.mark.parametrize('data', [b'', HTML, b'PDF-1.7', b' %PDF-1.7'])
def test_byte_validator_rejects_non_pdf(scenario, data):
    scenario.routes[FIRST] = route(data, content_type='application/pdf', final='https://publisher.example/file.pdf')
    result = acquire(scenario)
    assert result.outcome is Outcome.NO_VALID_PDF and result.pdf is None
    assert not list(scenario.temp.iterdir())


@pytest.mark.parametrize('content_type', ['application/pdf', 'text/html', 'application/octet-stream'])
def test_signature_is_authority_not_content_type(scenario, content_type):
    scenario.routes[FIRST] = route(PDF, content_type=content_type)
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED
    with result.pdf as pdf:
        assert pdf.path.exists()
    assert not list(scenario.temp.iterdir())


def test_first_valid_wins_and_candidate_order(scenario):
    third = 'https://familiar.example/later'
    scenario.routes.update({FIRST: route(HTML, content_type='application/pdf'), SECOND: route(PDF), third: route(PDF)})
    result = acquire(scenario, (candidate(), candidate(SECOND, 'SmartLinks'), candidate(third)))
    assert result.outcome is Outcome.ACQUIRED and result.pdf.candidate_position == 2
    assert result.pdf.category == 'SmartLinks'
    assert [url for url, _ in scenario.page.visits] == [FIRST, SECOND]
    result.pdf.cleanup()


def test_direct_response_has_priority_over_all_lower_methods(scenario):
    download = FakeDownload()
    scenario.routes[FIRST] = route(PDF, downloads=[download], metadata=['/citation.pdf'], links=[{'href': '/explicit.pdf', 'type': None}])
    result = acquire(scenario)
    assert result.pdf.source_kind is Source.DIRECT_RESPONSE
    assert download.saved == [] and scenario.page.reads == []
    assert len(scenario.page.visits) == 1
    result.pdf.cleanup()


def test_download_has_priority_over_metadata_and_links(scenario):
    download = FakeDownload()
    scenario.routes[FIRST] = route(downloads=[download], metadata=['/citation.pdf'], links=[{'href': '/explicit.pdf', 'type': None}])
    result = acquire(scenario)
    assert result.pdf.source_kind is Source.DOWNLOAD
    assert scenario.page.reads == [] and len(scenario.page.visits) == 1
    assert download.saved[0] == result.pdf.path
    result.pdf.cleanup()


def test_download_navigation_abort_still_succeeds(scenario):
    scenario.routes[FIRST] = route(RuntimeError(SECRET), downloads=[FakeDownload()], error=RuntimeError(SECRET))
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED and result.pdf.source_kind is Source.DOWNLOAD
    result.pdf.cleanup()


@pytest.mark.parametrize('download', [FakeDownload(HTML), FakeDownload(b''), FakeDownload(HTML, RuntimeError(SECRET))])
def test_invalid_or_partial_download_cleaned_then_metadata(scenario, download):
    scenario.routes[FIRST] = route(downloads=[download], metadata=['/citation.pdf'])
    scenario.routes['https://publisher.example/citation.pdf'] = route(PDF)
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED and result.pdf.source_kind is Source.CITATION_PDF_URL
    assert all(not path.exists() for path in download.saved)
    assert list(result.pdf.path.parent.iterdir()) == [result.pdf.path]
    result.pdf.cleanup()


def test_citation_relative_url_priority_and_final_document_base(scenario):
    scenario.routes[FIRST] = route(final='https://redirect.example/path/article?token=' + SECRET,
                                  metadata=['../citation.pdf'], links=[{'href': '/explicit.pdf', 'type': 'application/pdf'}])
    target = 'https://redirect.example/citation.pdf'
    scenario.routes[target] = route(PDF)
    result = acquire(scenario)
    assert result.pdf.source_kind is Source.CITATION_PDF_URL
    assert [url for url, _ in scenario.page.visits] == [FIRST, target]
    result.pdf.cleanup()


@pytest.mark.parametrize('metadata', [['javascript:bad'], ['https://bad.example/file.pdf'], [None]])
def test_citation_invalid_url_or_bytes_then_explicit(scenario, metadata):
    scenario.routes[FIRST] = route(metadata=metadata, links=[{'href': '/download', 'type': 'application/pdf'}])
    scenario.routes['https://bad.example/file.pdf'] = route(HTML, content_type='application/pdf')
    scenario.routes['https://publisher.example/download'] = route(PDF)
    result = acquire(scenario)
    assert result.pdf.source_kind is Source.EXPLICIT_PDF_LINK
    result.pdf.cleanup()


@pytest.mark.parametrize('link', [
    {'href': '/file.pdf?token=' + SECRET, 'type': None},
    {'href': '/file.PDF', 'type': None},
    {'href': '/file', 'type': 'APPLICATION/PDF'},
    {'href': '/file', 'type': 'application/pdf; charset=binary'},
])
def test_explicit_generic_pdf_signals(scenario, link):
    scenario.routes[FIRST] = route(links=[link])
    target = acquisition._target(link['href'], FIRST)
    scenario.routes[target] = route(PDF)
    result = acquire(scenario)
    assert result.pdf.source_kind is Source.EXPLICIT_PDF_LINK
    result.pdf.cleanup()


def test_unrelated_and_link_text_only_anchors_never_visited(scenario):
    scenario.routes[FIRST] = route(links=[
        {'href': '/download', 'type': None, 'text': 'Download PDF Full Text'},
        {'href': '/search?file=paper.pdf', 'type': None},
        {'href': 'javascript:download()', 'type': 'application/pdf'},
        {'href': '/paper', 'type': 'text/html', 'class': 'pdf-download'}])
    result = acquire(scenario)
    assert result.outcome is Outcome.NO_VALID_PDF and len(scenario.page.visits) == 1


def test_broken_explicit_link_then_next_candidate(scenario):
    scenario.routes[FIRST] = route(links=[{'href': '/broken.pdf', 'type': None}])
    scenario.routes['https://publisher.example/broken.pdf'] = route(status=500)
    scenario.routes[SECOND] = route(PDF)
    result = acquire(scenario, (candidate(), candidate(SECOND)))
    assert [url for url, _ in scenario.page.visits] == [FIRST, 'https://publisher.example/broken.pdf', SECOND]
    assert result.pdf.candidate_position == 2
    result.pdf.cleanup()


@pytest.mark.parametrize('first', [route(status=500), route(data=RuntimeError(SECRET)), route(error=RuntimeError(SECRET)),
                                 route(metadata=[42], links=[42])])
def test_candidate_failure_does_not_discard_later_route(scenario, first):
    scenario.routes.update({FIRST: first, SECOND: route(PDF)})
    result = acquire(scenario, (candidate(), candidate(SECOND)))
    assert result.outcome is Outcome.ACQUIRED and result.pdf.candidate_position == 2
    result.pdf.cleanup()


def test_lower_priority_dom_failure_does_not_discard_citation(scenario):
    scenario.routes[FIRST] = route(metadata=['/citation.pdf'])
    scenario.routes['https://publisher.example/citation.pdf'] = route(PDF)
    scenario.page.links_error = True
    result = acquire(scenario)
    assert result.pdf.source_kind is Source.CITATION_PDF_URL
    result.pdf.cleanup()


@pytest.mark.parametrize('candidates', [None, [], (object(),), (candidate('file:///tmp/a.pdf'),), (candidate('javascript:alert(1)'),),
                                      (candidate('https://user:password@host.example/file'),), (candidate('https://host.example:bad/file'),),
                                      (candidate('https://host.example/\n'),), (candidate(None),), (candidate(category='Other'),)])
def test_invalid_input_rejected_before_launch(scenario, candidates):
    result = scenario.acquirer.acquire(candidates)
    assert result.outcome is Outcome.INVALID_RESPONSE
    assert not scenario.launches
    assert_private(result)


def test_empty_candidates_no_launch(scenario):
    assert acquire(scenario, ()).outcome is Outcome.NO_VALID_PDF
    assert not scenario.launches and not list(scenario.temp.iterdir())


@pytest.mark.parametrize('reason', ['missing executable', 'SingletonLock profile in use', SECRET])
def test_browser_launch_failure_sanitized_no_alternate_profile(scenario, reason, caplog):
    scenario.launch_error = RuntimeError(reason)
    result = acquire(scenario)
    assert result.outcome is Outcome.BROWSER_UNAVAILABLE
    assert len(scenario.launches) == 1 and scenario.started == scenario.stopped == 1
    assert not list(scenario.temp.iterdir())
    assert Path(scenario.launches[0][0]).is_dir()
    assert_private(result, caplog)


@pytest.mark.parametrize('protected', ['repo', 'workspace', 'config'])
def test_temp_location_rejects_protected_paths(scenario, tmp_path, monkeypatch, protected):
    parent = tmp_path / protected
    parent.mkdir()
    monkeypatch.setattr(acquisition.tempfile, 'gettempdir', lambda: str(parent))
    result = acquire(scenario)
    assert result.outcome is Outcome.BROWSER_UNAVAILABLE
    assert not scenario.launches and list(parent.iterdir()) == []


def test_temp_location_rejects_browser_profile(scenario, monkeypatch):
    profile = scenario.app_data / 'institutional-browser'
    profile.mkdir(parents=True)
    monkeypatch.setattr(acquisition.tempfile, 'gettempdir', lambda: str(profile))
    result = acquire(scenario)
    assert result.outcome is Outcome.BROWSER_UNAVAILABLE and list(profile.iterdir()) == []


@pytest.mark.parametrize('response', [route(human=True), route(status=401), route(status=403)])
def test_human_auth_wait_bounded_stops_candidate_traversal(scenario, response):
    scenario.routes.update({FIRST: response, SECOND: route(PDF)})
    result = acquire(scenario, (candidate(), candidate(SECOND)))
    assert result.outcome is Outcome.AUTH_REQUIRED
    assert scenario.now == pytest.approx(12)
    assert max(scenario.page.waits) <= 250
    assert len(scenario.page.visits) == 1
    assert scenario.context.closed and not list(scenario.temp.iterdir())
    assert_private(result)


def test_manual_human_flow_can_complete_in_current_attempt(scenario):
    scenario.routes[FIRST] = route(human=True)
    scenario.page.human_clears_at = 10.5
    scenario.page.after_human = route(PDF)
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED and scenario.now == 10.5
    assert len(scenario.page.visits) == 1  # No automatic password submission/navigation bypass.
    result.pdf.cleanup()


@pytest.mark.parametrize('seconds', [0, -1, float('nan'), float('inf')])
def test_timeout_finite_positive(tmp_path, seconds):
    with pytest.raises(ValueError, match='finite and positive'):
        GenericPdfAcquirer(project_dir=tmp_path, workspace_dir=tmp_path, config_path=tmp_path/'monitor.yaml', timeout_seconds=seconds)


@pytest.mark.parametrize('error', [RuntimeError(SECRET), KeyboardInterrupt(SECRET)])
def test_cleanup_when_context_close_fails_after_pdf(scenario, error, caplog):
    scenario.routes[FIRST] = route(PDF)
    scenario.context.close_error = error
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            acquire(scenario)
    else:
        result = acquire(scenario)
        assert result.outcome is Outcome.API_FAILURE and result.pdf is None
        assert_private(result, caplog)
    assert scenario.context.closed and scenario.stopped == 1 and not list(scenario.temp.iterdir())


def test_caller_cancellation_removes_partial_owned_files(scenario):
    scenario.routes[FIRST] = route(downloads=[FakeDownload(HTML, KeyboardInterrupt(SECRET))])
    with pytest.raises(KeyboardInterrupt):
        acquire(scenario)
    assert not list(scenario.temp.iterdir()) and scenario.context.closed and scenario.stopped == 1


@pytest.mark.parametrize('category', [None, [], 42])
def test_malformed_category_is_typed_failure(scenario, category):
    result = acquire(scenario, (candidate(category=category),))
    assert result.outcome is Outcome.INVALID_RESPONSE and not scenario.launches


@pytest.mark.parametrize('data,downloads,source', [(PDF, [], Source.DIRECT_RESPONSE), (HTML, [FakeDownload()], Source.DOWNLOAD)])
def test_received_pdf_precedes_stale_human_dom(scenario, data, downloads, source):
    scenario.routes[FIRST] = route(data, human=True, downloads=downloads)
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED and result.pdf.source_kind is source
    assert scenario.now == 10.0
    result.pdf.cleanup()


def test_malformed_lower_priority_links_do_not_preempt_citation(scenario):
    scenario.routes[FIRST] = route(metadata=['/citation.pdf'], links=[42])
    scenario.routes['https://publisher.example/citation.pdf'] = route(PDF)
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED and result.pdf.source_kind is Source.CITATION_PDF_URL
    result.pdf.cleanup()


def test_failed_navigation_does_not_reuse_previous_candidate_dom(scenario):
    scenario.routes[FIRST] = route(metadata=['/bad.pdf'])
    scenario.routes['https://publisher.example/bad.pdf'] = route(HTML)
    scenario.routes[SECOND] = route(error=RuntimeError(SECRET), metadata=['/never.pdf'])
    result = acquire(scenario, (candidate(), candidate(SECOND)))
    assert result.outcome is Outcome.NO_VALID_PDF
    assert all('/never.pdf' not in url for url, _ in scenario.page.visits)


def test_failed_download_does_not_leave_artifacts_with_success(scenario):
    bad, good = FakeDownload(b'%PDF-partial', RuntimeError(SECRET)), FakeDownload(PDF)
    scenario.routes[FIRST] = route(downloads=[bad, good])
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED
    assert all(not path.exists() for path in bad.saved)
    assert list(result.pdf.path.parent.iterdir()) == [result.pdf.path]
    result.pdf.cleanup()


def test_delayed_download_precedes_metadata_and_link_navigation(scenario):
    scenario.routes[FIRST] = route(metadata=['/citation.pdf'], links=[{'href': '/explicit.pdf', 'type': None}])
    scenario.page.late_downloads = [FakeDownload(PDF)]
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED and result.pdf.source_kind is Source.DOWNLOAD
    assert scenario.page.download_waits == [1000]
    assert scenario.page.reads == [] and len(scenario.page.visits) == 1
    result.pdf.cleanup()


def test_invalid_delayed_download_cleans_up_then_uses_metadata(scenario):
    download = FakeDownload(HTML)
    scenario.page.late_downloads = [download]
    scenario.routes[FIRST] = route(metadata=['/citation.pdf'])
    scenario.routes['https://publisher.example/citation.pdf'] = route(PDF)
    result = acquire(scenario)
    assert result.outcome is Outcome.ACQUIRED and result.pdf.source_kind is Source.CITATION_PDF_URL
    assert all(not path.exists() for path in download.saved)
    result.pdf.cleanup()
