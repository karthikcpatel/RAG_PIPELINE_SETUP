import pytest
from fastapi.testclient import TestClient

from conftest import ITSM_KEY
from mock_itsm.app import create_app

INCIDENT = {
    "external_id": "ext-1", "short_description": "Printer jammed", "description": "Paper jam on floor 3",
    "priority": 3, "caller_email": "a@b.test",
}


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(api_key=ITSM_KEY, db_path=tmp_path / "itsm.db"))


def auth():
    return {"X-API-Key": ITSM_KEY}


def test_requires_api_key(api):
    assert api.post("/api/incidents", json=INCIDENT).status_code == 401
    assert api.post("/api/incidents", json=INCIDENT, headers={"X-API-Key": "nope"}).status_code == 401
    assert api.get("/api/incidents").status_code == 401
    assert api.get("/health").status_code == 200  # health needs no key


def test_create_get_list(api):
    created = api.post("/api/incidents", json=INCIDENT, headers=auth())
    assert created.status_code == 201 and created.json()["number"] == "INC0000001"
    assert api.get("/api/incidents/INC0000001", headers=auth()).json()["short_description"] == "Printer jammed"
    assert len(api.get("/api/incidents", headers=auth()).json()) == 1
    assert api.get("/api/incidents/INC9999999", headers=auth()).status_code == 404


def test_duplicate_external_id_conflicts(api):
    api.post("/api/incidents", json=INCIDENT, headers=auth())
    response = api.post("/api/incidents", json=INCIDENT, headers=auth())
    assert response.status_code == 409 and response.json()["ticket_id"] == "INC0000001"


@pytest.mark.parametrize("change", [{"priority": 0}, {"priority": 5}, {"short_description": ""}, {"extra": 1}])
def test_validation(api, change):
    assert api.post("/api/incidents", json={**INCIDENT, **change}, headers=auth()).status_code == 422


def test_refuses_to_start_without_api_key(monkeypatch):
    monkeypatch.delenv("MOCK_ITSM_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
