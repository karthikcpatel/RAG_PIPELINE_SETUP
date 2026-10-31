"""Agentic escalation: when the knowledge base can't help, triage the request and act.

Claude only *proposes* (a JSON triage decision); this code validates it and performs the
single allow-listed action (opening a ticket). Claude is never given tools.
"""

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from config import settings
from services import llm_service, ticket_service
from services.errors import HelpdeskError
from services.models import Step, TicketProposal
from utils.data_files import load_json

logger = logging.getLogger(__name__)

CATEGORIES = set(load_json("static", "ticket_categories.json"))  # data/static/ticket_categories.json


class Triage(BaseModel):
    is_support_issue: bool
    title: str = ""
    summary: str = ""
    severity: str = "medium"
    category: str = "general"


def parse_triage(raw):
    """Turn Claude's reply into a validated Triage, or None if it can't be trusted."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate prose or code fences around the JSON
    if not match:
        return None
    try:
        triage = Triage.model_validate(json.loads(match.group(0)))
    except (ValueError, ValidationError):
        return None
    triage.title = " ".join(triage.title.split())[:160]
    triage.summary = " ".join(triage.summary.split())[:1000]
    triage.category = triage.category if triage.category in CATEGORIES else "general"
    # Guardrail: the agent may never raise a critical incident; humans escalate those.
    triage.severity = triage.severity if triage.severity in {"low", "medium", "high"} else (
        "high" if triage.severity == "critical" else "medium"
    )
    if triage.is_support_issue and (len(triage.title) < 5 or len(triage.summary) < 10):
        return None
    return triage


def escalate(question, reporter, kb_note, answer):
    """Triage an unresolved question and open a ticket if it is a real support issue.

    Updates and returns `answer`. Never raises: any failure leaves the standard
    "contact the IT Helpdesk" message in place and is recorded in the steps.
    """
    try:
        triage = parse_triage(llm_service.triage_issue(question))
    except HelpdeskError:
        logger.exception("Triage call failed")
        triage = None
    if triage is None:
        answer.steps.append(Step("Assessed whether this needs a ticket", "failed"))
        return answer

    if not triage.is_support_issue:
        answer.steps.append(Step("Assessed the request: not an IT support issue, so no ticket was opened", "skipped"))
        return answer
    answer.steps.append(
        Step(f"Assessed the request: IT support issue (severity {triage.severity}, category {triage.category})")
    )

    answer.proposal = TicketProposal(
        title=triage.title, summary=triage.summary, severity=triage.severity, category=triage.category,
        question=question, kb_note=kb_note, reporter=reporter,
    )
    if settings.REQUIRE_TICKET_CONFIRMATION:
        answer.ticket_status = "pending"
        answer.steps.append(Step("Drafted a ticket and am waiting for your confirmation"))
        answer.text = (
            "I couldn't find an answer to this in the knowledge base. "
            "I can open a Service Desk ticket for you. Please review it below and confirm."
        )
        return answer
    return open_ticket(answer)


def open_ticket(answer):
    """Create the drafted ticket. Safe to call twice: the draft is consumed on first use."""
    proposal = answer.proposal
    if proposal is None:
        return answer
    answer.proposal = None
    try:
        ticket_id, duplicate = ticket_service.create_ticket(
            proposal.reporter, proposal, proposal.question, proposal.kb_note
        )
    except HelpdeskError:
        answer.steps.append(Step("Opened a Service Desk ticket", "failed"))
        answer.ticket_status = "failed"
        answer.text = (
            f"{settings.NO_INFO_MESSAGE} I tried to open a ticket for you but couldn't. "
            "Please contact the helpdesk directly."
        )
        return answer

    answer.ticket_id = ticket_id
    email = proposal.reporter["email"]
    if duplicate:
        answer.ticket_status = "duplicate"
        answer.steps.append(Step(f"Found your existing ticket {ticket_id} for this issue, so no new ticket was opened"))
        answer.text = (
            f"You already have an open request for this today: ticket {ticket_id}. "
            f"The IT Helpdesk will follow up at {email}."
        )
    else:
        answer.ticket_status = "created"
        answer.steps.append(Step(f"Opened Service Desk ticket {ticket_id}"))
        answer.text = (
            "I couldn't find an answer to this in the knowledge base, so I've opened "
            f"ticket {ticket_id} for you. The IT Helpdesk will follow up at {email}."
        )
    return answer


def decline_ticket(answer):
    """The user chose not to open the drafted ticket."""
    if answer.proposal is None:
        return answer
    answer.proposal = None
    answer.ticket_status = "declined"
    answer.steps.append(Step("You chose not to open a ticket", "skipped"))
    answer.text = settings.NO_INFO_MESSAGE
    return answer
