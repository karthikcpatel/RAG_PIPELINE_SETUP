"""Shared fixtures for the webhook tests."""

import copy
import json
import time

import pytest
from fastapi.testclient import TestClient

from mock_itsm.app import create_app as create_itsm_app
from utils.data_files import load_json
from webhook_receiver import security
from webhook_receiver.app import create_app as create_receiver_app
from webhook_receiver.settings import Settings
from webhook_receiver.ticket_client import TicketClient

SECRET = "test-secret-0123456789abcdef"
ITSM_KEY = "itsm-test-key"


def expand(value):
    """Expand {"$repeat": [text, n]} markers used in the scenario JSON files."""
    if isinstance(value, dict):
        if set(value) == {"$repeat"}:
            text, count = value["$repeat"]
            return text * count
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


def valid_payload(**overrides):
    payload = copy.deepcopy(load_json("scenario", "webhook_payloads.json")["valid"])
    payload.update(overrides)
    return payload


def invalid_payload_cases():
    """(case name, field overrides) pairs from data/scenario/webhook_payloads.json."""
    cases = load_json("scenario", "webhook_payloads.json")["invalid_cases"]
    return [pytest.param(expand(c["overrides"]), id=c["case"]) for c in cases]


def signed_headers(body, secret=SECRET, timestamp=None):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "Content-Type": "application/json",
        security.TIMESTAMP_HEADER: timestamp,
        security.SIGNATURE_HEADER: security.sign(secret, timestamp, body),
    }


def post_signed(client, payload, **kwargs):
    """POST a payload (dict, or raw bytes) with a valid signature."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return client.post("/webhooks/issues", content=body, headers=signed_headers(body, **kwargs))


def make_settings(**overrides):
    values = dict(webhook_secret=SECRET, ticket_api_url="http://itsm", ticket_api_key=ITSM_KEY)
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def itsm_http(tmp_path):
    """HTTP client wired in-process to a real mock ITSM app (no network)."""
    app = create_itsm_app(api_key=ITSM_KEY, db_path=tmp_path / "itsm.db")
    return TestClient(app, base_url="http://itsm", headers={"X-API-Key": ITSM_KEY})


@pytest.fixture
def receiver(itsm_http):
    ticket_client = TicketClient(itsm_http, max_attempts=3, sleep=lambda seconds: None)
    return TestClient(create_receiver_app(make_settings(), ticket_client))
