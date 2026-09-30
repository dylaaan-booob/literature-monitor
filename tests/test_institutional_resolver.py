"""Sanitized fixtures grounded in the observed 2026-09-30 XMU browser protocol.

The real /api/links request has customer/openUrl fields; the response has facts,
links and diagnostics. Only fabricated candidate targets are used here. Unit
coverage does not constitute live institutional authentication evidence.
"""

from contextlib import contextmanager
from copy import deepcopy
import logging
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import pytest
from playwright.sync_api import TimeoutError as BrowserTimeout

from literature_monitor import institutional_resolver as resolver
from literature_monitor.institutional_resolver import ResolverOutcome as Outcome, XmuInstitutionalResolver

DOI = "10.1198/016214501753382273"
SECRET = "SENTINEL_COOKIE_AND_SIGNED_TARGET"
AUTHORITY = "https://resolver.ebsco.com/api/links"


def link(category="FullText", name="example", rank=1, **changes):
    value = {"category": category, "baseUrl": "https://publisher.example/" + quote(name, safe=""),
             "proxyUrl": "", "queryString": "?signature=" + SECRET,
             "rank": rank, "linkText": name}
    value.update(changes)
    return value


def payload(links=None):
    return {"diagnostics": [], "facts": [{"key": "doi", "value": DOI, "source": "OpenURL", "conflicts": []}],
            "links": [link()] if links is None else links, "openUrlFacts": [], "statInfo": []}


class FakeRequest:
    def __init__(self, method="POST", body=None, redirected_from=None):
        self.method = method
        self.post_data_json = body
        self.redirected_from = redirected_from


class FakeResponse:
    def __init__(self, data=None, *, url=AUTHORITY, status=200, method="POST", redirected=False):
        self.url = url
        self.status = status
        self.request = FakeRequest(method, redirected_from=object() if redirected else None)
        self.data = payload() if data is None else data
        self.json_error = None

    def json(self):
        if self.json_error:
            raise self.json_error
        return deepcopy(self.data)


class FakeExpectation:
    def __init__(self, page, predicate, timeout):
        self.page = page
        self.predicate = predicate
        self.timeout = timeout
        self.value = None

    def __enter__(self):
        self.page.waits.append(self.timeout)
        self.page.expectation = self
        return self

    def __exit__(self, kind, *args):
        if kind is None and self.value is None:
            raise BrowserTimeout(SECRET)


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.responses = [FakeResponse()]
        self.waits = []
        self.visits = []
        self.expectation = None
        self.human = False
        self.final_url = "https://resolver.ebsco.com/c/45yels/result"
        self.navigation_status = 200
        self.error = None
        self.body_changes = None

    def expect_response(self, predicate, timeout):
        return FakeExpectation(self, predicate, timeout)

    def goto(self, url, **options):
        self.visits.append((url, options))
        self.url = self.final_url
        if self.error:
            raise self.error
        query = parse_qs(urlsplit(url).query)
        body = {"customer": {"customerId": "s1215021", "groupId": "main", "profileId": "ftf", "isAuthenticated": True},
                "openUrl": "https://resolver.ebsco.com/openurl?" + urlencode({"rft_id": query["rft_id"][0], "x-opid": "45yels"}),
                "displayContext": ["FullText"], "factsToReturn": ["doi"], "loggingLevel": "Minimal"}
        if self.body_changes:
            self.body_changes(body)
        for response in self.responses:
            response.request.post_data_json = body
            if self.expectation.predicate(response):
                self.expectation.value = response
                break
        return SimpleNamespace(status=self.navigation_status)

    def locator(self, selector):
        assert "password" in selector or "captcha" in selector or "challenge" in selector
        assert "href" not in selector  # DOM anchors can never supply candidates.
        return SimpleNamespace(count=lambda: int(self.human))


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False
        self.close_error = None

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error

    def cookies(self):
        raise AssertionError("Do not copy browser cookies.")

    def storage_state(self, **kwargs):
        raise AssertionError("Do not export session state.")

    @property
    def request(self):
        raise AssertionError("The provider page issues its own request.")


@pytest.fixture
def scenario(monkeypatch, tmp_path):
    state = SimpleNamespace(page=FakePage(), launch_error=None, started=0, stopped=0, launches=[])
    state.context = FakeContext(state.page)
    state.app_data = tmp_path / "machine-data" / "Literature Monitor"
    state.platform_calls = []
    def user_data(name, **kwargs):
        state.platform_calls.append((name, kwargs))
        return state.app_data
    monkeypatch.setattr(resolver, "user_data_path", user_data)
    class Chromium:
        def launch_persistent_context(self, profile, **options):
            state.launches.append((profile, options))
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
    monkeypatch.setattr(resolver, "_browser_session", session)
    state.resolver = XmuInstitutionalResolver(project_dir=tmp_path / "repo", workspace_dir=tmp_path / "workspace",
                                            config_path=tmp_path / "configuration" / "monitor.yaml", timeout_seconds=5)
    return state


def assert_clean_result(result, caplog=None):
    public = repr(result) + result.message + (caplog.text if caplog else "")
    assert SECRET not in public
    assert "publisher.example" not in public
    assert "proxy.example" not in public


def test_profile_and_authenticated_resolution(scenario):
    result = scenario.resolver.resolve(" https://doi.org/" + DOI.upper() + " ")
    assert result.outcome is Outcome.RESOLVED
    assert len(result.candidates) == 1
    assert isinstance(result.candidates, tuple)
    assert scenario.platform_calls == [("Literature Monitor", {"appauthor": False})]
    profile, options = scenario.launches[0]
    assert Path(profile) == scenario.app_data / "institutional-browser"
    assert "0.4.7" not in profile and "Chrome" not in profile
    assert options == {"channel": "chrome", "headless": False, "accept_downloads": False, "timeout": 5000}
    assert Path(profile).is_dir()
    assert scenario.context.closed and scenario.stopped == 1
    entry, navigation = scenario.page.visits[0]
    assert entry.startswith("https://resolver.ebsco.com/c/45yels/result?")
    assert parse_qs(urlsplit(entry).query) == {"rft_id": ["info:doi/" + DOI]}
    assert navigation == {"wait_until": "domcontentloaded", "timeout": 5000}
    assert scenario.page.waits == [5000]
    assert_clean_result(result)


def test_profile_non_versioned_across_repeated_attempts(scenario):
    assert scenario.resolver.resolve(DOI).outcome is Outcome.RESOLVED
    assert scenario.resolver.resolve(DOI).outcome is Outcome.RESOLVED
    assert scenario.launches[0][0] == scenario.launches[1][0]
    assert scenario.started == scenario.stopped == 2


@pytest.mark.parametrize("location", ["project", "workspace", "configuration"])
def test_profile_rejects_protected_location(scenario, tmp_path, location):
    scenario.app_data = tmp_path / {"project": "repo", "workspace": "workspace", "configuration": "configuration"}[location] / "app"
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.BROWSER_UNAVAILABLE
    assert scenario.started == 0 and not scenario.app_data.exists()


def test_profile_symlink_into_workspace_rejected(scenario, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scenario.app_data.parent.mkdir()
    scenario.app_data.symlink_to(workspace, target_is_directory=True)
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.BROWSER_UNAVAILABLE
    assert not scenario.launches and list(workspace.iterdir()) == []


@pytest.mark.parametrize("error", ["executable unavailable", "SingletonLock profile already in use", SECRET])
def test_browser_unavailable_or_locked_no_fallback(scenario, error, caplog):
    scenario.launch_error = RuntimeError(error)
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.BROWSER_UNAVAILABLE
    assert len(scenario.launches) == 1 and scenario.started == scenario.stopped == 1
    assert Path(scenario.launches[0][0]).is_dir()  # Session directory is preserved.
    assert_clean_result(result, caplog)


@pytest.mark.parametrize("doi", [None, "", "   ", 123, []])
def test_invalid_doi_before_browser_launch(scenario, doi):
    result = scenario.resolver.resolve(doi)
    assert result.outcome is Outcome.INVALID_DOI
    assert not scenario.platform_calls and scenario.started == 0 and not scenario.launches


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
def test_wait_must_be_bounded(tmp_path, seconds):
    with pytest.raises(ValueError, match="finite and positive"):
        XmuInstitutionalResolver(project_dir=tmp_path, workspace_dir=tmp_path, config_path=tmp_path / "monitor.yaml",
                                 timeout_seconds=seconds)


@pytest.mark.parametrize("kind", ["institution", "password", "captcha", "navigation_401", "navigation_403"])
def test_human_authentication_required_and_bounded_cleanup(scenario, kind):
    scenario.page.responses = []
    if kind == "institution": scenario.page.final_url = "https://idp.xmu.edu.cn/login?state=" + SECRET
    elif kind in {"password", "captcha"}: scenario.page.human = True
    else: scenario.page.navigation_status = int(kind.split("_")[1])
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.AUTH_REQUIRED
    assert scenario.page.waits == [5000]
    assert scenario.context.closed and scenario.stopped == 1
    assert Path(scenario.launches[0][0]).exists()
    assert len(scenario.page.visits) == 1
    assert_clean_result(result)


@pytest.mark.parametrize("status", [401, 403])
def test_resolver_authorization_denial(scenario, status):
    scenario.page.responses = [FakeResponse(status=status)]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.AUTH_REQUIRED
    assert scenario.context.closed and scenario.stopped == 1


def test_request_unauthenticated_customer_never_returns_candidates(scenario):
    scenario.page.body_changes = lambda body: body["customer"].update(isAuthenticated=False)
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.AUTH_REQUIRED and result.candidates == ()


@pytest.mark.parametrize("field,value", [
    ("customerId", "other"), ("groupId", "other"), ("profileId", "other"), ("isAuthenticated", "true"),
])
def test_request_wrong_customer_or_authentication_schema_rejected(scenario, field, value):
    scenario.page.body_changes = lambda body: body["customer"].update({field: value})
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.INVALID_RESPONSE


@pytest.mark.parametrize("openurl", [
    "https://resolver.ebsco.com/openurl?rft_id=info:doi/other&x-opid=45yels",
    "https://resolver.ebsco.com/openurl?rft_id=info:doi/" + DOI + "&x-opid=other",
    "https://resolver.ebsco.com/openurl?rft_id=info:doi/" + DOI + "&rft_id=info:doi/other&x-opid=45yels",
    "https://resolver.ebsco.com/openurl?rft.title=title&x-opid=45yels", None,
])
def test_request_must_identify_exact_doi_and_opid(scenario, openurl):
    scenario.page.body_changes = lambda body: body.update(openUrl=openurl)
    assert scenario.resolver.resolve(DOI).outcome is Outcome.INVALID_RESPONSE


@pytest.mark.parametrize("url,method,redirected", [
    (AUTHORITY, "GET", False), (AUTHORITY + "/more", "POST", False),
    ("https://other.example/api/links", "POST", False),
    ("http://resolver.ebsco.com/api/links", "POST", False),
    ("https://resolver.ebsco.com:444/api/links", "POST", False),
    ("https://resolver.ebsco.com/api/get-research-tool-links", "POST", False),
    (AUTHORITY + "?endpoint=links", "POST", False), (AUTHORITY + "#secret", "POST", False),
    ("https://user:secret@resolver.ebsco.com/api/links", "POST", False), (AUTHORITY, "POST", True),
])
def test_only_exact_post_can_authorize_candidates(scenario, url, method, redirected):
    scenario.page.responses = [FakeResponse(url=url, method=method, redirected=redirected)]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.API_FAILURE and result.candidates == ()
    assert len(scenario.page.visits) == 1 and scenario.context.closed


def test_unrelated_responses_ignored_before_exact_post(scenario):
    scenario.page.responses = [FakeResponse(method="GET"), FakeResponse(url="https://resolver.ebsco.com/api/get-research-tool-links"),
                               FakeResponse()]
    assert scenario.resolver.resolve(DOI).outcome is Outcome.RESOLVED


@pytest.mark.parametrize("status", [302, 429, 500])
def test_resolver_http_failure(scenario, status):
    scenario.page.responses = [FakeResponse(status=status)]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.API_FAILURE
    assert scenario.context.closed and scenario.stopped == 1


@pytest.mark.parametrize("category,expected", [
    ("FullText", Outcome.RESOLVED), ("SmartLinks", Outcome.RESOLVED),
    ("SearchEngines", Outcome.NO_ELIGIBLE_CANDIDATES), ("Other", Outcome.NO_ELIGIBLE_CANDIDATES),
    ("DocumentDelivery", Outcome.NO_ELIGIBLE_CANDIDATES), ("Unknown", Outcome.NO_ELIGIBLE_CANDIDATES),
    ("fulltext", Outcome.NO_ELIGIBLE_CANDIDATES),
])
def test_exact_category_filter(scenario, category, expected):
    scenario.page.responses = [FakeResponse(payload([link(category)]))]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is expected
    assert_clean_result(result)


def test_provider_array_order_stable_without_label_or_category_preferences(scenario):
    links = [link("Other", "Other A", 0), link("FullText", "Z B", 1),
             link("SmartLinks", "EBSCOhost SmartLinks", 1), link("FullText", "A D", 8),
             link("SmartLinks", "Z E", 8), link("DocumentDelivery", "F", 9)]
    scenario.page.responses = [FakeResponse(payload(links))]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.RESOLVED
    assert [c.category for c in result.candidates] == ["FullText", "SmartLinks", "FullText", "SmartLinks"]
    assert [c.target for c in result.candidates] == [links[i]["baseUrl"] + links[i]["queryString"] for i in [1, 2, 3, 4]]
    assert len(scenario.page.visits) == 1  # No candidate destination is visited.


def test_proxy_url_is_complete_target_not_recombined_with_query(scenario):
    proxy = "https://proxy.example/login?url=https%3A%2F%2Fpublisher.example%2F&signature=" + SECRET
    scenario.page.responses = [FakeResponse(payload([link(proxyUrl=proxy)]))]
    result = scenario.resolver.resolve(DOI)
    assert result.candidates[0].target == proxy
    assert_clean_result(result)


def test_empty_query_uses_base_and_provider_entity_encoding(scenario):
    scenario.page.responses = [FakeResponse(payload([link(queryString="", baseUrl="https://publisher.example/?a=%26amp%3B&lt;x&gt;&amp;")]))]
    result = scenario.resolver.resolve(DOI)
    assert result.candidates[0].target == "https://publisher.example/?a=%26%3Cx%3E%26"


def test_zero_links_explicit_no_candidate_without_fallback(scenario):
    scenario.page.responses = [FakeResponse(payload([]))]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.NO_ELIGIBLE_CANDIDATES and result.candidates == ()
    assert len(scenario.page.visits) == 1


@pytest.mark.parametrize("changes", [
    {"category": None}, {"baseUrl": None}, {"baseUrl": ""}, {"proxyUrl": None}, {"queryString": None},
    {"rank": "1"}, {"rank": True}, {"rank": -1},
    {"baseUrl": "javascript:alert(1)", "queryString": ""},
    {"baseUrl": "file:///tmp/file.pdf", "queryString": ""},
    {"baseUrl": "https://user:password@publisher.example/"},
    {"baseUrl": "https://publisher.example:bad/"},
    {"proxyUrl": "https://proxy.example/\n"},
])
def test_malformed_candidate_never_accepts_partial_list(scenario, changes):
    scenario.page.responses = [FakeResponse(payload([link(), link(**changes)]))]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.INVALID_RESPONSE and result.candidates == ()
    assert_clean_result(result)


@pytest.mark.parametrize("data", [
    [], "HTML " + SECRET, {}, {"diagnostics": [], "facts": [], "links": []},
    {"diagnostics": [], "facts": [{"key": "doi", "value": "other"}], "links": []},
    {"diagnostics": [], "facts": [{"key": "doi", "value": DOI}], "links": {}},
    {"diagnostics": ["unexpected " + SECRET], "facts": [{"key": "doi", "value": DOI}], "links": []},
    {"diagnostics": [], "facts": [{"key": "doi", "value": DOI}], "links": [42]},
])
def test_malformed_resolver_payload(scenario, data):
    scenario.page.responses = [FakeResponse(data)]
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.INVALID_RESPONSE
    assert scenario.context.closed and scenario.stopped == 1
    assert_clean_result(result)


def test_invalid_json_sanitized(scenario, caplog):
    scenario.page.responses[0].json_error = ValueError(SECRET)
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.INVALID_RESPONSE
    assert_clean_result(result, caplog)


@pytest.mark.parametrize("where", ["navigation", "close"])
def test_browser_session_exceptions_sanitized_with_cleanup(scenario, caplog, where):
    caplog.set_level(logging.DEBUG)
    if where == "navigation": scenario.page.error = RuntimeError(SECRET)
    else: scenario.context.close_error = RuntimeError(SECRET)
    result = scenario.resolver.resolve(DOI)
    assert result.outcome is Outcome.API_FAILURE
    assert scenario.context.closed and scenario.stopped == 1
    assert_clean_result(result, caplog)


def test_caller_cancellation_closes_context_and_driver(scenario):
    scenario.page.error = KeyboardInterrupt(SECRET)
    with pytest.raises(KeyboardInterrupt):
        scenario.resolver.resolve(DOI)
    assert scenario.context.closed and scenario.stopped == 1


def test_browser_driver_debug_environment_not_inherited(monkeypatch):
    stopped = []
    monkeypatch.setenv("DEBUG", "pw:api")
    monkeypatch.setenv("PWDEBUG", "1")
    class Manager:
        def start(self):
            assert "DEBUG" not in resolver.os.environ and "PWDEBUG" not in resolver.os.environ
            return SimpleNamespace(stop=lambda: stopped.append(True))
    monkeypatch.setattr(resolver, "sync_playwright", Manager)
    with resolver._browser_session():
        assert resolver.os.environ["DEBUG"] == "pw:api"
        assert resolver.os.environ["PWDEBUG"] == "1"
    assert stopped == [True]


def test_results_immutable_and_targets_hidden(scenario, caplog):
    from dataclasses import FrozenInstanceError
    caplog.set_level(logging.INFO)
    result = scenario.resolver.resolve(DOI)
    with pytest.raises(FrozenInstanceError): result.candidates[0].target = "changed"
    logging.getLogger(__name__).info("Resolver result: %r", result)
    assert "Resolver result:" in caplog.text
    assert_clean_result(result, caplog)
    assert not hasattr(result, "raw_data")
