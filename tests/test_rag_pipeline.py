"""Integration tests: real Chroma store (in a temp dir) and the local embedding model.
Only the Claude CLI call is replaced by a fake."""

import threading

import pytest

from config import settings
from services import llm_service, rag_service, vector_service
from services.errors import InvalidQuestionError, VectorStoreError


@pytest.fixture
def kb(tmp_path, monkeypatch):
    knowledge = tmp_path / "kb"
    knowledge.mkdir()
    (knowledge / "password.txt").write_text(
        "Employees must reset their company passwords through the official company password portal.\n\n"
        "Employees must never share their passwords with colleagues."
    )
    (knowledge / "vpn.txt").write_text(
        "If the VPN connection fails, employees must raise an IT Helpdesk ticket "
        "with the error message and the time of the failure."
    )
    monkeypatch.setattr(settings, "KNOWLEDGE_DIR", knowledge)
    monkeypatch.setattr(settings, "CHROMA_PATH", tmp_path / "chroma")
    monkeypatch.setattr(vector_service, "_client", None)
    monkeypatch.setattr(
        llm_service, "ask_claude", lambda q, retrieved: "Fake answer from: " + ", ".join(c["source"] for c in retrieved)
    )
    return knowledge


def test_relevant_question_returns_sources(kb):
    rag_service.refresh_index()
    answer = rag_service.answer_question("How do I reset my password?")
    assert answer.answered
    assert answer.sources[0] == "password.txt"
    assert answer.excerpts


def test_unrelated_question_returns_no_info(kb):
    rag_service.refresh_index()
    answer = rag_service.answer_question("What is the capital of France?")
    assert not answer.answered
    assert answer.text == settings.NO_INFO_MESSAGE
    assert answer.sources == []


def test_llm_refusal_is_reported_as_unanswered(kb, monkeypatch):
    monkeypatch.setattr(llm_service, "ask_claude", lambda q, r: settings.NO_INFO_MESSAGE)
    rag_service.refresh_index()
    answer = rag_service.answer_question("How do I reset my password?")
    assert not answer.answered and answer.sources == []


def test_invalid_question_is_rejected(kb):
    rag_service.refresh_index()
    with pytest.raises(InvalidQuestionError):
        rag_service.answer_question("   ")


def test_index_survives_restart_and_picks_up_changes(kb, monkeypatch):
    rag_service.refresh_index()
    # Simulate a restart: new client against the same on-disk database.
    monkeypatch.setattr(vector_service, "_client", None)
    rag_service.refresh_index()
    assert rag_service.answer_question("How do I reset my password?").answered

    (kb / "new.txt").write_text("Visitors must sign in at the front desk and wear a badge.")
    rag_service.refresh_index()
    answer = rag_service.answer_question("Where do visitors sign in?")
    assert answer.answered and "new.txt" in answer.sources


def test_query_before_index_exists_raises_clean_error(kb):
    with pytest.raises(VectorStoreError):
        vector_service.retrieve("anything")


def test_concurrent_requests_and_rebuilds(kb):
    rag_service.refresh_index()
    errors = []

    def ask():
        try:
            assert rag_service.answer_question("How do I reset my password?").answered
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def rebuild():
        try:
            rag_service.refresh_index()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    (kb / "extra.txt").write_text("Laptops must be locked when left unattended.")
    threads = [threading.Thread(target=ask) for _ in range(8)] + [threading.Thread(target=rebuild) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
