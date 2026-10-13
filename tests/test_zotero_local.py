"""Current read-only Zotero Desktop health, without Library access."""
import pytest


@pytest.mark.parametrize('server,status,verified', [
    ('status-instance', 200, True), (None, 200, False), (' ', 200, False),
    ('status-instance', 403, False), ('status-instance', 412, False),
    ('status-instance', 302, False), ('status-instance', 503, False),
    ('status-instance', None, False),
])
def test_read_only_current_instance_status(server, status, verified):
    import httpx
    from literature_monitor.zotero_local import ZoteroLocalClient, ZoteroReadOutcome
    requests = []
    def respond(request):
        requests.append(request)
        assert request.method == 'GET' and request.url.path == '/api/'
        assert 'Zotero-API-Key' not in request.headers
        if status is None:
            raise httpx.ConnectError('STATUS_SENTINEL_SECRET', request=request)
        headers = {'Zotero-Server-ID': server} if server is not None else {}
        return httpx.Response(status, headers=headers, json={'private': 'STATUS_SENTINEL_SECRET'})
    with ZoteroLocalClient(transport=httpx.MockTransport(respond)) as client:
        result = client.current_instance()
    assert (result.outcome is ZoteroReadOutcome.VERIFIED) is verified
    assert result.server_id == (server if verified else None)
    assert len(requests) == 1 and 'STATUS_SENTINEL_SECRET' not in repr(result)
