"""Application settings, read from environment variables (or a local .env file)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

NO_INFO_MESSAGE = (
    "I don't have enough information in the knowledge base to answer that. "
    "Please contact the IT Helpdesk."
)


def _path(name, default):
    path = Path(os.environ.get(name, default))
    return path if path.is_absolute() else PROJECT_ROOT / path


def _number(name, default, cast):
    try:
        return cast(os.environ.get(name, default))
    except ValueError:
        raise SystemExit(f"Invalid value for environment variable {name}")


KNOWLEDGE_DIR = _path("KNOWLEDGE_DIR", "data/golden/knowledge_base")
CHROMA_PATH = _path("CHROMA_PATH", "chroma_db")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "helpdesk")

TOP_K = _number("TOP_K", 5, int)
MIN_SCORE = _number("MIN_SCORE", 0.25, float)  # cosine similarity threshold

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT = _number("CLAUDE_TIMEOUT", 120, int)  # seconds
MAX_CONCURRENT_REQUESTS = _number("MAX_CONCURRENT_REQUESTS", 3, int)

MAX_QUESTION_CHARS = _number("MAX_QUESTION_CHARS", 500, int)
INDEX_REFRESH_SECONDS = _number("INDEX_REFRESH_SECONDS", 300, int)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Agentic ticket creation: the assistant opens a Service Desk ticket (via the webhook
# receiver) when it cannot answer a genuine IT support question.
HELPDESK_WEBHOOK_URL = os.environ.get("HELPDESK_WEBHOOK_URL", "http://127.0.0.1:8000/webhooks/issues")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
TICKET_WEBHOOK_TIMEOUT = _number("TICKET_WEBHOOK_TIMEOUT", 15, int)  # seconds
AUTO_TICKET_ENABLED = (
    os.environ.get("AUTO_TICKET_ENABLED", "true").lower() == "true"
    and len(WEBHOOK_SECRET) >= 16
    and not WEBHOOK_SECRET.startswith("<")
)
# When true (default) the assistant drafts the ticket and waits for the user to confirm it.
REQUIRE_TICKET_CONFIRMATION = os.environ.get("REQUIRE_TICKET_CONFIRMATION", "true").lower() == "true"
