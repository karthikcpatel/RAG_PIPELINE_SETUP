"""Orchestrates the RAG pipeline and agentic escalation; shared by the web UI and the CLI."""

import logging

from config import settings
from services import agent_service, document_service, llm_service, vector_service
from services.models import Answer, Step
from utils.validation import validate_question

logger = logging.getLogger(__name__)


def refresh_index():
    """Load the knowledge base and make sure the vector index matches it."""
    chunks = document_service.chunk_documents(
        document_service.load_documents(settings.KNOWLEDGE_DIR)
    )
    vector_service.build_index(chunks)


def answer_question(question, reporter=None):
    """Answer from the knowledge base; if that fails and `reporter` is given, open a ticket.

    `reporter` is {"name": ..., "email": ...} for the signed-in user, or None.
    """
    question = validate_question(question)
    retrieved = vector_service.retrieve(question)
    logger.info("Question (%d chars): %d relevant chunks", len(question), len(retrieved))
    steps = [Step(f"Searched the knowledge base: {len(retrieved)} relevant passage(s) found")]

    if retrieved:
        text = llm_service.ask_claude(question, retrieved)
        if settings.NO_INFO_MESSAGE not in text:
            steps.append(Step("Wrote an answer using only the retrieved passages"))
            return Answer(
                text=text,
                sources=sorted({c["source"] for c in retrieved}),
                excerpts=retrieved,
                steps=steps,
            )
        steps.append(Step("Checked the passages: they do not answer the question", "skipped"))
        kb_note = "relevant passages were found but did not answer the question"
    else:
        kb_note = "no relevant content found"

    answer = Answer(settings.NO_INFO_MESSAGE, answered=False, steps=steps)
    if reporter is None or not settings.AUTO_TICKET_ENABLED:
        return answer
    return agent_service.escalate(question, reporter, kb_note, answer)


def confirm_ticket(answer):
    """The user approved the drafted ticket: open it."""
    answer.steps.append(Step("You confirmed the ticket"))
    return agent_service.open_ticket(answer)


def decline_ticket(answer):
    """The user rejected the drafted ticket."""
    return agent_service.decline_ticket(answer)
