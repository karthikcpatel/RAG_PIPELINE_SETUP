"""The agent's one action: open a Service Desk ticket by sending a signed webhook event."""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from config import settings
from services.errors import HelpdeskError
from webhook_receiver import security

logger = logging.getLogger(__name__)


class TicketCreationError(HelpdeskError):
    user_message = "I couldn't open a ticket automatically. Please contact the IT Helpdesk directly."


def event_id_for(email, question, day=None):
    """Stable per user + question + day, so repeating a question never opens a second ticket."""
    day = day or datetime.now(timezone.utc).date().isoformat()
    digest = hashlib.sha256(f"{email.lower()}|{question.lower()}|{day}".encode()).hexdigest()[:24]
    return f"hd-{digest}"


def create_ticket(reporter, triage, question, kb_note, http_client=None):
    """Send the event. Returns (ticket_id, is_duplicate); raises TicketCreationError."""
    payload = {
        "event_id": event_id_for(reporter["email"], question),
        "event_type": "issue.reported",
        "title": triage.title,
        "description": f"{triage.summary}\n\nOriginal question from the user: {question}",
        "severity": triage.severity,
        "category": triage.category,
        "reporter": {"name": reporter["name"], "email": reporter["email"]},
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"created_by": "helpdesk-assistant", "knowledge_base": kb_note},
    }
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        security.TIMESTAMP_HEADER: timestamp,
        security.SIGNATURE_HEADER: security.sign(settings.WEBHOOK_SECRET, timestamp, body),
    }
    try:
        if http_client is not None:
            response = http_client.post(settings.HELPDESK_WEBHOOK_URL, content=body, headers=headers)
        else:
            response = httpx.post(
                settings.HELPDESK_WEBHOOK_URL, content=body, headers=headers,
                timeout=settings.TICKET_WEBHOOK_TIMEOUT,
            )
    except httpx.TransportError as exc:
        logger.error("Ticket webhook unreachable: %s", type(exc).__name__)
        raise TicketCreationError() from exc
    if response.status_code not in (200, 201):
        logger.error("Ticket webhook returned HTTP %d", response.status_code)
        raise TicketCreationError()
    result = response.json()
    logger.info("Ticket %s (%s) for event %s", result["ticket_id"], result["status"], payload["event_id"])
    return result["ticket_id"], response.status_code == 200
