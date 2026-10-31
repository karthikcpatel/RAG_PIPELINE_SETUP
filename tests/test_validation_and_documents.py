import pytest

from services import document_service
from services.errors import InvalidQuestionError, KnowledgeBaseError
from utils.validation import validate_question


def test_validate_question_cleans_whitespace_and_control_chars():
    assert validate_question("  How do I\x00 reset \n my   password?  ") == "How do I reset my password?"


@pytest.mark.parametrize("bad", ["", "   \n\t", None, 123])
def test_validate_question_rejects_empty_or_non_string(bad):
    with pytest.raises(InvalidQuestionError):
        validate_question(bad)


def test_validate_question_rejects_too_long(monkeypatch):
    monkeypatch.setattr("config.settings.MAX_QUESTION_CHARS", 20)
    with pytest.raises(InvalidQuestionError, match="under 20"):
        validate_question("x" * 21)


def test_load_documents_and_chunking(tmp_path):
    (tmp_path / "a.txt").write_text("Para one.\n\nPara two.\n\n\nPara three.")
    (tmp_path / "empty.txt").write_text("   ")
    (tmp_path / "ignored.md").write_text("not a txt file")
    documents = document_service.load_documents(tmp_path)
    assert list(documents) == ["a.txt"]
    chunks = document_service.chunk_documents(documents)
    assert [c["text"] for c in chunks] == ["Para one.", "Para two.", "Para three."]
    assert {c["source"] for c in chunks} == {"a.txt"}


def test_load_documents_missing_folder(tmp_path):
    with pytest.raises(KnowledgeBaseError):
        document_service.load_documents(tmp_path / "nope")


def test_load_documents_no_usable_files(tmp_path):
    (tmp_path / "empty.txt").write_text("")
    with pytest.raises(KnowledgeBaseError):
        document_service.load_documents(tmp_path)
