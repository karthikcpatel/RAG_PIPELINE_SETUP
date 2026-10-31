import subprocess

import pytest

from services import llm_service
from services.errors import LLMBusyError, LLMError, LLMUnavailableError

CHUNKS = [{"source": "a.txt", "text": "Reset passwords at the portal."}]


def completed(returncode=0, stdout="An answer", stderr=""):
    return subprocess.CompletedProcess(["claude"], returncode, stdout, stderr)


def test_ask_claude_success_uses_stdin_and_disables_tools(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert llm_service.ask_claude("How?", CHUNKS) == "An answer"
    assert captured["command"][-2:] == ["--tools", ""]
    assert "Reset passwords at the portal." in captured["input"]
    assert "How?" not in captured["command"]  # question must not appear in argv


def test_cli_not_installed(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(LLMUnavailableError):
        llm_service.ask_claude("How?", CHUNKS)


def test_cli_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(LLMError):
        llm_service.ask_claude("How?", CHUNKS)


def test_cli_failure_does_not_leak_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(1, "", "SECRET-TOKEN-123 traceback"))
    with pytest.raises(LLMError) as excinfo:
        llm_service.ask_claude("How?", CHUNKS)
    assert "SECRET" not in excinfo.value.user_message


def test_cli_empty_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(0, "  \n"))
    with pytest.raises(LLMError):
        llm_service.ask_claude("How?", CHUNKS)


def test_busy_when_no_slots(monkeypatch):
    import threading

    monkeypatch.setattr(llm_service, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr("config.settings.CLAUDE_TIMEOUT", 0.05)
    llm_service._slots.acquire()
    with pytest.raises(LLMBusyError):
        llm_service.ask_claude("How?", CHUNKS)


def test_slot_released_after_failure(monkeypatch):
    import threading

    monkeypatch.setattr(llm_service, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(1))
    for _ in range(3):  # would hang/raise Busy if the slot leaked
        with pytest.raises(LLMError):
            llm_service.ask_claude("How?", CHUNKS)
