"""The engine's HTTP surface: changes answer only the machine it runs on, and the UI route serves nothing outside ui/dist.
TestClient is used without its context manager, so the indexer thread and the feed hub never start."""
import pytest
from fastapi.testclient import TestClient

import server

client = TestClient(server.app)        # requests come from "testclient", not a loopback address


@pytest.fixture(autouse=True)
def no_override(monkeypatch):
    monkeypatch.delenv("GRADGATE_ALLOW_EDIT", raising=False)


def test_config_says_editing_is_refused_from_elsewhere():
    assert client.get("/api/config").json()["local_edit"] is False


@pytest.mark.parametrize("method,path", [
    ("post", "/api/strategies"), ("delete", "/api/strategies/early"), ("post", "/api/strategies/early/reset"),
    ("post", "/api/kill"), ("delete", "/api/kill"),
])
def test_changes_from_another_machine_are_refused(method, path):
    r = getattr(client, method)(path, **({"json": {"id": "early"}} if method == "post" and path == "/api/strategies" else {}))
    assert r.status_code == 403


def test_no_account_or_site_endpoints():
    for path in ("/api/me", "/api/strategies/mine", "/api/landing/tape", "/api/landing/records", "/api/radar", "/api/ledger"):
        assert client.get(path).status_code == 404, path


def test_ui_route_stays_inside_dist():
    assert client.get("/../engine/strategies.default.json").status_code in (200, 404)
    body = client.get("/%2e%2e/.env.example").text
    assert "RH_RPC" not in body
    assert client.get("/assets/does-not-exist.js").status_code == 404


def test_fastapi_docs_do_not_shadow_the_docs_page():
    r = client.get("/docs")
    assert "swagger" not in r.text.lower()
