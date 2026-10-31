"""True end-to-end test: two real uvicorn servers (receiver + mock ITSM) talking over HTTP."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from conftest import ITSM_KEY, SECRET, signed_headers, valid_payload

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(module, port, env):
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", f"{module}:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return process
        except httpx.TransportError:
            time.sleep(0.1)
    process.kill()
    raise RuntimeError(f"{module} did not start:\n{process.stdout.read()}")


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    db = tmp_path_factory.mktemp("itsm") / "itsm.db"
    itsm_port, hook_port = free_port(), free_port()
    itsm_env = {"MOCK_ITSM_API_KEY": ITSM_KEY, "MOCK_ITSM_DB": str(db)}
    hook_env = {
        "WEBHOOK_SECRET": SECRET, "TICKET_API_KEY": ITSM_KEY,
        "TICKET_API_URL": f"http://127.0.0.1:{itsm_port}", "TICKET_MAX_ATTEMPTS": "2", "TICKET_TIMEOUT_SECONDS": "2",
    }
    itsm = start("mock_itsm.app", itsm_port, itsm_env)
    hook = start("webhook_receiver.app", hook_port, hook_env)
    state = {"itsm": itsm, "hook": hook, "hook_url": f"http://127.0.0.1:{hook_port}", "itsm_url": f"http://127.0.0.1:{itsm_port}",
             "itsm_port": itsm_port, "itsm_env": itsm_env}
    yield state
    stop(state["hook"])
    stop(state["itsm"])


def send(stack, payload, **kwargs):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return httpx.post(f"{stack['hook_url']}/webhooks/issues", content=body, headers=signed_headers(body, **kwargs), timeout=20)


def tickets(stack):
    return httpx.get(f"{stack['itsm_url']}/api/incidents", headers={"X-API-Key": ITSM_KEY}).json()


def test_end_to_end_flow(stack):
    # 1. a reported issue becomes a ticket with all details
    response = send(stack, valid_payload(event_id="live-evt-0001"))
    assert response.status_code == 201
    ticket = httpx.get(f"{stack['itsm_url']}/api/incidents/{response.json()['ticket_id']}", headers={"X-API-Key": ITSM_KEY}).json()
    assert ticket["external_id"] == "live-evt-0001" and ticket["priority"] == 2
    assert "Home office" in ticket["description"]

    # 2. re-delivery is idempotent
    again = send(stack, valid_payload(event_id="live-evt-0001"))
    assert again.status_code == 200 and again.json()["ticket_id"] == ticket["number"]

    # 3. bad signature and invalid payload create nothing
    assert send(stack, valid_payload(event_id="live-evt-0002"), secret="not-the-right-secret-123456").status_code == 401
    assert send(stack, valid_payload(event_id="live-evt-0003", severity="nope")).status_code == 422
    assert [t["external_id"] for t in tickets(stack)] == ["live-evt-0001"]


def test_concurrent_deliveries(stack):
    results = []

    def deliver(event_id):
        results.append(send(stack, valid_payload(event_id=event_id)).status_code)

    # 5 threads race on the same event, 5 more send distinct events
    threads = [threading.Thread(target=deliver, args=("live-race-0001",)) for _ in range(5)]
    threads += [threading.Thread(target=deliver, args=(f"live-many-{i:04d}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(set(results)) == [200, 201]
    ids = [t["external_id"] for t in tickets(stack)]
    assert ids.count("live-race-0001") == 1
    assert sum(i.startswith("live-many-") for i in ids) == 5


def test_outage_then_recovery_without_data_loss(stack):
    before = len(tickets(stack))
    stop(stack["itsm"])
    outage = send(stack, valid_payload(event_id="live-outage-0001"))
    assert outage.status_code == 502 and outage.json()["code"] == "ticket_service_unavailable"

    # restart the ticket system on the same port and database
    stack["itsm"] = start("mock_itsm.app", stack["itsm_port"], stack["itsm_env"])
    assert len(tickets(stack)) == before  # nothing lost or corrupted by the restart
    retry = send(stack, valid_payload(event_id="live-outage-0001"))  # sender retries after the 502
    assert retry.status_code == 201
    assert len(tickets(stack)) == before + 1
