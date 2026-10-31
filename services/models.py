from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Step:
    """One entry in the agent's activity trail shown to the user."""

    text: str
    status: str = "done"  # done | skipped | failed


@dataclass
class TicketProposal:
    """A ticket the agent has drafted and, by default, is waiting for the user to confirm."""

    title: str
    summary: str
    severity: str
    category: str
    question: str
    kb_note: str
    reporter: dict  # {"name": ..., "email": ...}


@dataclass
class Answer:
    text: str
    sources: list = field(default_factory=list)
    excerpts: list = field(default_factory=list)  # retrieved chunks: {"source", "text"}
    answered: bool = True
    steps: list = field(default_factory=list)
    ticket_id: Optional[str] = None
    ticket_status: Optional[str] = None  # pending | created | duplicate | declined | failed
    proposal: Optional[TicketProposal] = None
