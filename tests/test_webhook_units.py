import threading

import httpx
import pytest
from pydantic import ValidationError

from conftest import SECRET, invalid_payload_cases, valid_payload
from mock_itsm.store import IncidentStore
from webhook_receiver import security
from webhook_receiver.models import IssueReport
from webhook_receiver.settings import ConfigError, Settings
from webhook_receiver.ticket_client import DuplicateTicket, TicketClient, TicketServiceError


# ---- signatures -------------------------------------------------------------

def test_signature_roundtrip():
    sig = security.sign(SECRET, "1000", b"{}")
    security.verify(SECRET, "1000", sig, b"{}", tolerance_seconds=300, now=1000)


@pytest.mark.parametrize(
    "ts, sig, body, now, code",
    [
        (None, None, b"{}", 1000, "missing_signature"),
        ("abc", "sha256=x", b"{}", 1000, "invalid_timestamp"),
        ("1000", "sha256=bad", b"{}", 1000, "invalid_signature"),
        ("1000", "WILL_BE_REPLACED", b'{"tampered":1}', 1000, "invalid_signature"),
        ("1000", "WILL_BE_REPLACED", b"{}", 2000, "stale_timestamp"),
    ],
)
def test_signature_failures(ts, sig, body, now, code):
    if sig == "WILL_BE_REPLACED":
        sig = security.sign(SECRET, ts, b"{}")
    with pytest.raises(security.SignatureError) as excinfo:
        security.verify(SECRET, ts, sig, body, tolerance_seconds=300, now=now)
    assert excinfo.value.code == code


def test_signature_depends_on_secret():
    sig = security.sign("another-secret-0123456789", "1000", b"{}")
    with pytest.raises(security.SignatureError):
        security.verify(SECRET, "1000", sig, b"{}", 300, now=1000)


# ---- payload model ----------------------------------------------------------

def test_valid_payload_maps_to_incident_with_all_details():
    incident = IssueReport.model_validate(valid_payload()).to_incident()
    assert incident["external_id"] == "evt-0001-abcdef"
    assert incident["priority"] == 2
    assert incident["short_description"] == "VPN disconnects every few minutes"
    for detail in ("Corporate VPN", "network", "Alex Example", "alex.example@company.test", "client_version: 5.2.1", "Home office"):
        assert detail in incident["description"]


@pytest.mark.parametrize("severity, priority", [("critical", 1), ("high", 2), ("medium", 3), ("low", 4)])
def test_severity_to_priority(severity, priority):
    assert IssueReport.model_validate(valid_payload(severity=severity)).to_incident()["priority"] == priority


def test_optional_fields_default():
    payload = valid_payload()
    for key in ("category", "application", "reported_at", "metadata"):
        payload.pop(key)
    incident = IssueReport.model_validate(payload).to_incident()
    assert incident["category"] == "general" and incident["opened_at"] is None


@pytest.mark.parametrize("overrides", invalid_payload_cases())
def test_invalid_payloads_rejected(overrides):
    with pytest.raises(ValidationError):
        IssueReport.model_validate(valid_payload(**overrides))


def test_required_fields():
    for field in ("event_id", "event_type", "title", "description", "severity", "reporter"):
        payload = valid_payload()
        payload.pop(field)
        with pytest.raises(ValidationError):
            IssueReport.model_validate(payload)


# ---- ticket client ----------------------------------------------------------

def client_with(handler, attempts=3):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://itsm")
    sleeps = []
    return TicketClient(http, attempts, backoff_seconds=1, sleep=sleeps.append), sleeps


def test_retries_transient_errors_then_succeeds():
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(201, json={"number": "INC1"})])
    client, sleeps = client_with(lambda request: next(responses))
    assert client.create_incident({})["number"] == "INC1"
    assert sleeps == [1, 2]  # exponential backoff


def test_retries_connection_errors_then_gives_up():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("down")

    client, _ = client_with(handler)
    with pytest.raises(TicketServiceError):
        client.create_incident({})
    assert len(calls) == 3


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    client, _ = client_with(handler)
    with pytest.raises(TicketServiceError):
        client.create_incident({})
    assert len(calls) == 1


def test_conflict_raises_duplicate():
    client, _ = client_with(lambda request: httpx.Response(409, json={"ticket_id": "INC0000007"}))
    with pytest.raises(DuplicateTicket) as excinfo:
        client.create_incident({})
    assert excinfo.value.ticket_id == "INC0000007"


# ---- settings ---------------------------------------------------------------

def test_settings_require_variables(monkeypatch):
    monkeypatch.setattr("webhook_receiver.settings.load_dotenv", lambda: None)
    for name in ("WEBHOOK_SECRET", "TICKET_API_URL", "TICKET_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match="WEBHOOK_SECRET, TICKET_API_URL, TICKET_API_KEY"):
        Settings.from_env()


def test_settings_reject_unedited_placeholders(monkeypatch):
    monkeypatch.setattr("webhook_receiver.settings.load_dotenv", lambda: None)
    monkeypatch.setenv("WEBHOOK_SECRET", "<generate-a-random-string-of-at-least-16-characters>")
    monkeypatch.setenv("TICKET_API_URL", "http://itsm")
    monkeypatch.setenv("TICKET_API_KEY", "real-key")
    with pytest.raises(ConfigError, match="placeholders"):
        Settings.from_env()


def test_settings_reject_weak_secret(monkeypatch):
    monkeypatch.setattr("webhook_receiver.settings.load_dotenv", lambda: None)
    monkeypatch.setenv("WEBHOOK_SECRET", "short")
    monkeypatch.setenv("TICKET_API_URL", "http://itsm")
    monkeypatch.setenv("TICKET_API_KEY", "k")
    with pytest.raises(ConfigError, match="at least"):
        Settings.from_env()


# ---- incident store ---------------------------------------------------------

def test_store_creates_unique_numbers_and_survives_reopen(tmp_path):
    path = tmp_path / "s.db"
    base = {"short_description": "s", "description": "d", "priority": 3}
    store = IncidentStore(path)
    first, created = store.create({**base, "external_id": "a"})
    second, _ = store.create({**base, "external_id": "b"})
    assert created and first["number"] == "INC0000001" and second["number"] == "INC0000002"
    reopened = IncidentStore(path)
    assert reopened.get("INC0000001")["external_id"] == "a"
    assert len(reopened.list()) == 2


def test_store_concurrent_duplicates_create_one_incident(tmp_path):
    store = IncidentStore(tmp_path / "s.db")
    data = {"external_id": "same", "short_description": "s", "description": "d", "priority": 3}
    results = []
    threads = [threading.Thread(target=lambda: results.append(store.create(data))) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(created for _, created in results) == 1
    assert len(store.list()) == 1
