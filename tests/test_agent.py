"""Tests for agentic escalation: unresolved question -> triage -> ticket via the webhook."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, make_settings
from config import settings
from services import agent_service, llm_service, rag_service, ticket_service, vector_service
from services.errors import LLMError
from utils.data_files import load_json
from webhook_receiver.app import create_app as create_receiver_app
from webhook_receiver.ticket_client import TicketClient

REPORTER = {"name": "Alex Example", "email": "alex.example@company.test"}
TRIAGE = load_json("scenario", "triage_replies.json")
ISSUE = json.dumps(TRIAGE["issue"])
NOT_ISSUE = json.dumps(TRIAGE["not_issue"])


# ---- triage parsing and guardrails -------------------------------------------

def test_parse_plain_json():
    triage = agent_service.parse_triage(ISSUE)
    assert triage.is_support_issue and triage.severity == "medium" and triage.category == "hardware"


def test_parse_tolerates_code_fences_and_prose():
    assert agent_service.parse_triage(f"Here you go:\n```json\n{ISSUE}\n```").is_support_issue


@pytest.mark.parametrize("raw", TRIAGE["garbage_replies"])
def test_parse_rejects_garbage(raw):
    assert agent_service.parse_triage(raw) is None


def test_parse_rejects_support_issue_without_usable_title():
    assert agent_service.parse_triage(json.dumps(TRIAGE["issue_without_usable_title"])) is None


def test_critical_severity_is_clamped_to_high():
    assert agent_service.parse_triage(json.dumps(TRIAGE["critical_severity"])).severity == "high"


def test_unknown_category_falls_back_to_general():
    assert agent_service.parse_triage(json.dumps(TRIAGE["unknown_category"])).category == "general"


def test_event_id_is_stable_per_user_question_day():
    a = ticket_service.event_id_for("A@x.test", "Why?", "2026-01-01")
    assert a == ticket_service.event_id_for("a@x.test", "why?", "2026-01-01")
    assert a != ticket_service.event_id_for("a@x.test", "why?", "2026-01-02")
    assert a != ticket_service.event_id_for("b@x.test", "why?", "2026-01-01")


# ---- full agent flow against the real receiver + mock ITSM (in-process) ------

@pytest.fixture
def agent_env(tmp_path, itsm_http, monkeypatch):
    receiver_client = TicketClient(itsm_http, 3, sleep=lambda s: None)
    receiver = TestClient(create_receiver_app(make_settings(), receiver_client), base_url="http://hook")
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "AUTO_TICKET_ENABLED", True)
    monkeypatch.setattr(settings, "HELPDESK_WEBHOOK_URL", "http://hook/webhooks/issues")
    real_create = ticket_service.create_ticket
    monkeypatch.setattr(
        ticket_service, "create_ticket",
        lambda reporter, triage, question, kb_note, http_client=None: real_create(reporter, triage, question, kb_note, http_client=receiver),
    )
    # knowledge base + index
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "password.txt").write_text("Employees must reset their company passwords through the official company password portal.")
    monkeypatch.setattr(settings, "KNOWLEDGE_DIR", kb)
    monkeypatch.setattr(settings, "CHROMA_PATH", tmp_path / "chroma")
    monkeypatch.setattr(vector_service, "_client", None)
    monkeypatch.setattr(llm_service, "ask_claude", lambda q, r: "Use the portal.")
    monkeypatch.setattr(llm_service, "triage_issue", lambda q: ISSUE)
    rag_service.refresh_index()
    return itsm_http


def ask_and_confirm(question, reporter=REPORTER):
    answer = rag_service.answer_question(question, reporter)
    return rag_service.confirm_ticket(answer) if answer.ticket_status == "pending" else answer


def tickets(itsm_http):
    return itsm_http.get("/api/incidents").json()


def test_unresolved_issue_opens_ticket_with_details(agent_env):
    answer = ask_and_confirm("My laptop screen flickers and Teams keeps crashing")
    assert answer.ticket_status == "created" and answer.ticket_id == "INC0000001"
    assert answer.ticket_id in answer.text and REPORTER["email"] in answer.text
    assert [s.status for s in answer.steps].count("failed") == 0
    assert any("Opened Service Desk ticket INC0000001" in s.text for s in answer.steps)

    [ticket] = tickets(agent_env)
    assert ticket["short_description"] == "Laptop screen flickers and Teams crashes"
    assert ticket["priority"] == 3 and ticket["category"] == "hardware"
    assert ticket["caller_email"] == REPORTER["email"]
    assert "My laptop screen flickers and Teams keeps crashing" in ticket["description"]
    assert "created_by: helpdesk-assistant" in ticket["description"]
    assert "no relevant content found" in ticket["description"]


def test_repeating_the_question_does_not_open_a_second_ticket(agent_env):
    question = "My laptop screen flickers and Teams keeps crashing"
    first = ask_and_confirm(question)
    second = ask_and_confirm(question)
    assert (first.ticket_status, second.ticket_status) == ("created", "duplicate")
    assert second.ticket_id == first.ticket_id and len(tickets(agent_env)) == 1


def test_resolvable_question_is_answered_without_ticket(agent_env):
    answer = rag_service.answer_question("How do I reset my password?", REPORTER)
    assert answer.answered and answer.ticket_status is None and tickets(agent_env) == []
    assert answer.sources == ["password.txt"]


def test_non_support_question_opens_no_ticket(agent_env, monkeypatch):
    monkeypatch.setattr(llm_service, "triage_issue", lambda q: NOT_ISSUE)
    answer = rag_service.answer_question("What is the capital of France?", REPORTER)
    assert not answer.answered and answer.ticket_status is None
    assert answer.text == settings.NO_INFO_MESSAGE
    assert any(s.status == "skipped" and "no ticket" in s.text for s in answer.steps)
    assert tickets(agent_env) == []


def test_no_ticket_without_reporter_details(agent_env):
    answer = rag_service.answer_question("My laptop screen flickers and Teams keeps crashing")
    assert answer.text == settings.NO_INFO_MESSAGE and answer.ticket_status is None
    assert tickets(agent_env) == []


def test_no_ticket_when_feature_disabled(agent_env, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TICKET_ENABLED", False)
    answer = rag_service.answer_question("My laptop screen flickers and Teams keeps crashing", REPORTER)
    assert answer.ticket_status is None and tickets(agent_env) == []


def test_relevant_passages_that_do_not_answer_still_escalate(agent_env, monkeypatch):
    monkeypatch.setattr(llm_service, "ask_claude", lambda q, r: settings.NO_INFO_MESSAGE)
    answer = ask_and_confirm("I can't reset my password, the portal shows an error")
    assert answer.ticket_status == "created"
    assert "did not answer the question" in tickets(agent_env)[0]["description"]


# ---- confirmation before acting ----------------------------------------------

QUESTION = "My laptop screen flickers and Teams keeps crashing"


def test_agent_asks_for_confirmation_and_creates_nothing_yet(agent_env):
    answer = rag_service.answer_question(QUESTION, REPORTER)
    assert answer.ticket_status == "pending" and answer.ticket_id is None
    assert answer.proposal.title == "Laptop screen flickers and Teams crashes"
    assert answer.proposal.reporter == REPORTER
    assert "confirm" in answer.text.lower()
    assert any("waiting for your confirmation" in s.text for s in answer.steps)
    assert tickets(agent_env) == []


def test_confirming_creates_the_ticket_exactly_as_drafted(agent_env):
    answer = rag_service.confirm_ticket(rag_service.answer_question(QUESTION, REPORTER))
    assert answer.ticket_status == "created" and answer.proposal is None
    [ticket] = tickets(agent_env)
    assert ticket["short_description"] == "Laptop screen flickers and Teams crashes"
    assert any(s.text == "You confirmed the ticket" for s in answer.steps)


def test_declining_creates_no_ticket(agent_env):
    answer = rag_service.decline_ticket(rag_service.answer_question(QUESTION, REPORTER))
    assert answer.ticket_status == "declined" and answer.proposal is None
    assert answer.text == settings.NO_INFO_MESSAGE
    assert any(s.status == "skipped" and "chose not to" in s.text for s in answer.steps)
    assert tickets(agent_env) == []


def test_double_confirmation_cannot_open_two_tickets(agent_env):
    answer = rag_service.answer_question(QUESTION, REPORTER)
    rag_service.confirm_ticket(answer)
    rag_service.confirm_ticket(answer)  # e.g. a double click
    assert len(tickets(agent_env)) == 1


def test_confirming_after_declining_does_nothing(agent_env):
    answer = rag_service.answer_question(QUESTION, REPORTER)
    rag_service.decline_ticket(answer)
    rag_service.confirm_ticket(answer)
    assert answer.ticket_status == "declined" and tickets(agent_env) == []


def test_confirming_an_existing_issue_reports_the_duplicate(agent_env):
    ask_and_confirm(QUESTION)
    again = rag_service.confirm_ticket(rag_service.answer_question(QUESTION, REPORTER))
    assert again.ticket_status == "duplicate" and len(tickets(agent_env)) == 1


def test_autonomous_mode_opens_ticket_without_asking(agent_env, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_TICKET_CONFIRMATION", False)
    answer = rag_service.answer_question(QUESTION, REPORTER)
    assert answer.ticket_status == "created" and len(tickets(agent_env)) == 1


# ---- failure handling: the agent never crashes or leaks details --------------

def test_triage_failure_falls_back_to_standard_message(agent_env, monkeypatch):
    def broken(q):
        raise LLMError()

    monkeypatch.setattr(llm_service, "triage_issue", broken)
    answer = rag_service.answer_question("My laptop screen flickers and Teams keeps crashing", REPORTER)
    assert answer.text == settings.NO_INFO_MESSAGE and answer.ticket_status is None
    assert answer.steps[-1].status == "failed"


def test_untrustworthy_triage_output_opens_no_ticket(agent_env, monkeypatch):
    monkeypatch.setattr(llm_service, "triage_issue", lambda q: TRIAGE["chatty_reply_without_json"])
    answer = rag_service.answer_question("My laptop screen flickers and Teams keeps crashing", REPORTER)
    assert answer.ticket_status is None and tickets(agent_env) == []


def test_webhook_outage_is_reported_gracefully(agent_env, monkeypatch):
    def down(reporter, triage, question, kb_note, http_client=None):
        raise ticket_service.TicketCreationError()  # what create_ticket raises when the webhook is unreachable

    monkeypatch.setattr(ticket_service, "create_ticket", down)
    answer = ask_and_confirm("My laptop screen flickers and Teams keeps crashing")
    assert answer.ticket_status == "failed"
    assert "contact the helpdesk" in answer.text
    assert any(s.status == "failed" and "ticket" in s.text for s in answer.steps)


def test_unreachable_webhook_raises_clean_error(monkeypatch):
    def down(*args, **kwargs):
        raise httpx.ConnectError("connection refused 192.0.2.9")

    monkeypatch.setattr(httpx, "post", down)
    with pytest.raises(ticket_service.TicketCreationError) as excinfo:
        ticket_service.create_ticket(REPORTER, agent_service.parse_triage(ISSUE), "Q?", "n/a")
    assert "192.0.2.9" not in excinfo.value.user_message


# ---- ticket_service on the wire ----------------------------------------------

def test_ticket_service_sends_a_correctly_signed_event(monkeypatch):
    from webhook_receiver import security

    monkeypatch.setattr(settings, "WEBHOOK_SECRET", SECRET)
    captured = {}

    def handler(request):
        captured.update(headers=request.headers, body=request.content)
        return httpx.Response(201, json={"status": "created", "event_id": "x", "ticket_id": "INC0000099"})

    triage = agent_service.parse_triage(ISSUE)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ticket_id, duplicate = ticket_service.create_ticket(REPORTER, triage, "Q?", "no relevant content found", http_client=client)
    assert (ticket_id, duplicate) == ("INC0000099", False)
    security.verify(
        SECRET, captured["headers"][security.TIMESTAMP_HEADER], captured["headers"][security.SIGNATURE_HEADER],
        captured["body"], tolerance_seconds=300,
    )
    assert json.loads(captured["body"])["reporter"] == REPORTER


@pytest.mark.parametrize("status", [401, 422, 502, 500])
def test_ticket_service_maps_http_errors(status):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"code": "secret-internal"})))
    with pytest.raises(ticket_service.TicketCreationError) as excinfo:
        ticket_service.create_ticket(REPORTER, agent_service.parse_triage(ISSUE), "Q?", "n/a", http_client=client)
    assert "secret-internal" not in excinfo.value.user_message
