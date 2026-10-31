"""Receiver tests against a real in-process mock ITSM (integration, no network)."""

import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import ITSM_KEY, SECRET, make_settings, post_signed, signed_headers, valid_payload
from webhook_receiver import security
from webhook_receiver.app import create_app
from webhook_receiver.ticket_client import TicketClient


def incidents(itsm_http):
    return itsm_http.get("/api/incidents").json()


def test_health(receiver):
    assert receiver.get("/health").json() == {"status": "ok"}


def test_valid_event_creates_ticket_with_details(receiver, itsm_http):
    response = post_signed(receiver, valid_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created" and body["ticket_id"] == "INC0000001"

    ticket = itsm_http.get(f"/api/incidents/{body['ticket_id']}").json()
    assert ticket["short_description"] == "VPN disconnects every few minutes"
    assert ticket["priority"] == 2 and ticket["state"] == "New"
    assert ticket["external_id"] == "evt-0001-abcdef"
    assert ticket["caller_email"] == "alex.example@company.test"
    assert ticket["application"] == "Corporate VPN"
    assert "client_version: 5.2.1" in ticket["description"]


def test_duplicate_event_does_not_create_second_ticket(receiver, itsm_http):
    first = post_signed(receiver, valid_payload())
    second = post_signed(receiver, valid_payload())
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json() == {"status": "duplicate", "event_id": "evt-0001-abcdef", "ticket_id": first.json()["ticket_id"]}
    assert len(incidents(itsm_http)) == 1


def test_distinct_events_create_distinct_tickets(receiver, itsm_http):
    for i in range(3):
        assert post_signed(receiver, valid_payload(event_id=f"evt-multi-{i:04d}")).status_code == 201
    assert [t["number"] for t in incidents(itsm_http)] == ["INC0000003", "INC0000002", "INC0000001"]


# ---- authentication ---------------------------------------------------------

def test_missing_signature_rejected(receiver, itsm_http):
    response = receiver.post("/webhooks/issues", json=valid_payload())
    assert response.status_code == 401 and response.json()["code"] == "missing_signature"
    assert incidents(itsm_http) == []


def test_wrong_secret_rejected(receiver, itsm_http):
    response = post_signed(receiver, valid_payload(), secret="a-different-secret-0123456789")
    assert response.status_code == 401 and response.json()["code"] == "invalid_signature"
    assert incidents(itsm_http) == []


def test_tampered_body_rejected(receiver, itsm_http):
    body = json.dumps(valid_payload()).encode()
    headers = signed_headers(body)
    tampered = body.replace(b"high", b"low!")
    response = receiver.post("/webhooks/issues", content=tampered, headers=headers)
    assert response.status_code == 401
    assert incidents(itsm_http) == []


def test_replayed_old_request_rejected(receiver, itsm_http):
    response = post_signed(receiver, valid_payload(), timestamp=int(time.time()) - 3600)
    assert response.status_code == 401 and response.json()["code"] == "stale_timestamp"
    assert incidents(itsm_http) == []


# ---- validation -------------------------------------------------------------

def test_invalid_json_returns_400(receiver):
    response = post_signed(receiver, b"{not json")
    assert response.status_code == 400 and response.json()["code"] == "invalid_json"


def test_validation_errors_list_fields_without_echoing_input(receiver, itsm_http):
    payload = valid_payload(severity="urgent", title="bad", reporter={"name": "Sam", "email": "secret-not-an-email"})
    response = post_signed(receiver, payload)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert {e["field"] for e in body["errors"]} == {"severity", "title", "reporter.email"}
    assert "secret-not-an-email" not in response.text
    assert incidents(itsm_http) == []


def test_unknown_field_rejected(receiver):
    response = post_signed(receiver, valid_payload(surprise="x"))
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == "surprise"


def test_oversized_body_rejected(receiver):
    response = post_signed(receiver, valid_payload(description="x" * 70_000))
    assert response.status_code == 413


# ---- downstream failures ----------------------------------------------------

def receiver_with_transport(handler, attempts=3):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://itsm")
    client = TicketClient(http, attempts, sleep=lambda seconds: None)
    return TestClient(create_app(make_settings(), client))


def test_transient_downstream_failure_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(201, json={"number": "INC0000042"})

    response = post_signed(receiver_with_transport(handler), valid_payload())
    assert response.status_code == 201 and response.json()["ticket_id"] == "INC0000042"
    assert len(calls) == 3


def test_downstream_outage_returns_502_with_generic_message():
    def handler(request):
        raise httpx.ConnectError("connection refused at 192.0.2.5:9000 with key sk-secret")

    response = post_signed(receiver_with_transport(handler), valid_payload())
    assert response.status_code == 502
    assert response.json()["code"] == "ticket_service_unavailable"
    assert "192.0.2.5" not in response.text and "sk-secret" not in response.text


def test_downstream_rejecting_credentials_returns_502_without_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    response = post_signed(receiver_with_transport(handler), valid_payload())
    assert response.status_code == 502 and len(calls) == 1


def test_unexpected_error_returns_500_without_details():
    class Broken:
        def create_incident(self, payload):
            raise RuntimeError("database password is hunter2")

    client = TestClient(create_app(make_settings(), Broken()), raise_server_exceptions=False)
    response = post_signed(client, valid_payload())
    assert response.status_code == 500 and response.json()["code"] == "internal_error"
    assert "hunter2" not in response.text


def test_real_itsm_rejecting_wrong_api_key(itsm_http, tmp_path):
    http = TestClient(itsm_http.app, base_url="http://itsm", headers={"X-API-Key": "wrong"})
    client = TestClient(create_app(make_settings(), TicketClient(http, 3, sleep=lambda s: None)))
    assert post_signed(client, valid_payload()).status_code == 502


# ---- logging ----------------------------------------------------------------

def test_logs_are_useful_and_free_of_secrets_and_pii(receiver, caplog):
    caplog.set_level(logging.INFO)
    post_signed(receiver, valid_payload())
    receiver.post("/webhooks/issues", json=valid_payload(), headers={security.TIMESTAMP_HEADER: "1", security.SIGNATURE_HEADER: "sha256=deadbeef"})
    text = caplog.text
    assert "evt-0001-abcdef -> ticket INC0000001" in text
    assert "invalid_signature" in text or "stale_timestamp" in text
    for sensitive in (SECRET, ITSM_KEY, "alex.example@company.test", "deadbeef", "Alex Example"):
        assert sensitive not in text
