"""UI tests using Streamlit's AppTest runner (no browser or server needed)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config import settings
from services import rag_service
from services.errors import LLMError

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def stub_index(monkeypatch):
    monkeypatch.setattr(rag_service, "refresh_index", lambda: None)


def run_with_question(question):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_area[0].set_value(question)
    at.button[0].click().run()  # "Ask"
    return at


def test_page_loads():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value == "IT Helpdesk Assistant"
    assert [b.label for b in at.button] == ["Ask", "Clear"]


def test_valid_question_shows_answer_and_sources(monkeypatch):
    monkeypatch.setattr(
        rag_service, "answer_question",
        lambda q, reporter=None: rag_service.Answer("Use the portal.", ["password_policy.txt"], [{"source": "password_policy.txt", "text": "excerpt"}]),
    )
    at = run_with_question("How do I reset my password?")
    assert not at.exception
    assert any("Use the portal." in m.value for m in at.markdown)
    assert any("password_policy.txt" in c.value for c in at.caption)


def test_empty_question_shows_validation_message():
    at = run_with_question("   ")
    assert at.warning[0].value == "Please enter a question."


def test_internal_errors_are_not_exposed(monkeypatch):
    def boom(q, reporter=None):
        raise RuntimeError("secret-connection-string://user:pw@host Traceback")

    monkeypatch.setattr(rag_service, "answer_question", boom)
    at = run_with_question("How do I reset my password?")
    assert at.error[0].value == "Unable to process the request. Please verify your input and try again."
    assert not at.exception
    assert "secret" not in str([e.value for e in at.error])


def test_known_service_error_shows_friendly_message(monkeypatch):
    def fail(q, reporter=None):
        raise LLMError()

    monkeypatch.setattr(rag_service, "answer_question", fail)
    at = run_with_question("How do I reset my password?")
    assert at.error[0].value == LLMError.user_message


def test_no_info_answer_is_shown_as_info(monkeypatch):
    monkeypatch.setattr(rag_service, "answer_question", lambda q, reporter=None: rag_service.Answer(settings.NO_INFO_MESSAGE, answered=False))
    at = run_with_question("What is the holiday policy?")
    assert at.info[0].value == settings.NO_INFO_MESSAGE


def test_clear_resets_question_and_result(monkeypatch):
    monkeypatch.setattr(rag_service, "answer_question", lambda q, reporter=None: rag_service.Answer("Answer text", ["a.txt"], []))
    at = run_with_question("A question")
    at.button[1].click().run()  # "Clear"
    assert at.text_area[0].value == ""
    assert not any("Answer text" in m.value for m in at.markdown)


def test_agent_ticket_is_shown_with_activity_trail(monkeypatch):
    from services.models import Step

    seen = {}

    def fake(q, reporter=None):
        seen["reporter"] = reporter
        return rag_service.Answer(
            "I've opened ticket INC0000005 for you.", answered=False, ticket_id="INC0000005", ticket_status="created",
            steps=[Step("Searched the knowledge base: 0 relevant passage(s) found"), Step("Opened Service Desk ticket INC0000005")],
        )

    monkeypatch.setattr(rag_service, "answer_question", fake)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input(key="reporter_name").set_value("Alex Example")
    at.text_input(key="reporter_email").set_value("alex@company.test")
    at.text_area[0].set_value("My laptop keeps crashing")
    at.button[0].click().run()
    assert seen["reporter"] == {"name": "Alex Example", "email": "alex@company.test"}
    assert at.success[0].value == "I've opened ticket INC0000005 for you."
    assert any("Opened Service Desk ticket INC0000005" in m.value for m in at.markdown)


def test_invalid_reporter_email_is_rejected_before_calling_the_agent(monkeypatch):
    called = []
    monkeypatch.setattr(rag_service, "answer_question", lambda q, reporter=None: called.append(1))
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input(key="reporter_name").set_value("Alex")
    at.text_input(key="reporter_email").set_value("not-an-email")
    at.text_area[0].set_value("My laptop keeps crashing")
    at.button[0].click().run()
    assert "valid email" in at.warning[0].value and called == []


def pending_answer():
    from services.models import Step, TicketProposal

    reporter = {"name": "Alex Example", "email": "alex@company.test"}
    return rag_service.Answer(
        "I can open a ticket. Please confirm.", answered=False, ticket_status="pending",
        proposal=TicketProposal("Laptop keeps crashing", "The laptop crashes repeatedly.", "medium", "hardware", "q", "none", reporter),
        steps=[Step("Drafted a ticket and am waiting for your confirmation")],
    )


def ask_with_pending(monkeypatch):
    monkeypatch.setattr(rag_service, "answer_question", lambda q, reporter=None: pending_answer())
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input(key="reporter_name").set_value("Alex Example")
    at.text_input(key="reporter_email").set_value("alex@company.test")
    at.text_area[0].set_value("My laptop keeps crashing")
    at.button[0].click().run()
    return at


def test_pending_ticket_is_shown_for_review(monkeypatch):
    at = ask_with_pending(monkeypatch)
    assert not at.exception
    assert any("Laptop keeps crashing" in m.value for m in at.markdown)
    assert at.button(key="confirm_ticket").label == "Yes, open the ticket"
    assert at.button(key="decline_ticket").label == "No thanks"


def test_confirm_button_opens_the_ticket(monkeypatch):
    at = ask_with_pending(monkeypatch)
    opened = []

    def fake_confirm(answer):
        opened.append(answer.proposal.title)
        answer.ticket_status, answer.ticket_id, answer.proposal = "created", "INC0000007", None
        answer.text = "I've opened ticket INC0000007 for you."
        return answer

    monkeypatch.setattr(rag_service, "confirm_ticket", fake_confirm)
    at.button(key="confirm_ticket").click().run()
    assert opened == ["Laptop keeps crashing"]
    assert at.success[0].value == "I've opened ticket INC0000007 for you."
    assert not at.exception


def test_decline_button_shows_standard_message(monkeypatch):
    at = ask_with_pending(monkeypatch)
    at.button(key="decline_ticket").click().run()
    assert at.info[0].value == rag_service.settings.NO_INFO_MESSAGE
    assert not at.exception
