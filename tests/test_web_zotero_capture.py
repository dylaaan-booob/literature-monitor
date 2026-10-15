"""Connector bridge still rejects unreserved native capture commands."""
from fastapi.testclient import TestClient

from literature_monitor.web.app import create_app


def test_bridge_has_no_unreserved_legacy_capture_bypass(tmp_path):
    app=create_app(tmp_path/'missing.yaml')
    with TestClient(app,base_url='http://localhost') as client:
        assert client.post('/api/connector/heartbeat',json={'version':'test','zotero_reachable':True}).status_code==200
        assert client.post('/api/connector/claim',json={}).json()=={'command':None}
        assert client.post('/api/connector/dispatch',json={'request_id':'old','invocation_id':'old','doi_url':'https://doi.org/10.1000/example','tab_id':100,'session_id':'session'}).status_code==409
