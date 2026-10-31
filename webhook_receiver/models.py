"""Webhook payload schema and its mapping to an incident."""

import re
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from utils.data_files import load_json

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_METADATA_ITEMS = 20


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


# data/static/severity_priority.json: {"critical": 1, ..., "low": 4}
PRIORITY = {Severity(name): priority for name, priority in load_json("static", "severity_priority.json").items()}


def _single_line(value):
    if value is not None and _CONTROL_CHARS.search(value):
        raise ValueError("must not contain control characters")
    return value


class Reporter(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254)

    _name_single_line = field_validator("name")(_single_line)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not _EMAIL.match(value):
            raise ValueError("must be a valid email address")
        return value


class IssueReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,100}$")
    event_type: Literal["issue.reported"]
    title: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=10, max_length=5000)
    severity: Severity
    category: str = Field(default="general", max_length=50)
    application: Optional[str] = Field(default=None, max_length=100)
    reporter: Reporter
    reported_at: Optional[datetime] = None
    metadata: dict[str, str] = Field(default_factory=dict)

    _title_single_line = field_validator("title", "category", "application")(_single_line)

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError(f"at most {MAX_METADATA_ITEMS} entries are allowed")
        for key, item in value.items():
            if not key or len(key) > 50 or len(item) > 200:
                raise ValueError("keys must be 1-50 characters and values at most 200 characters")
        return value

    def to_incident(self):
        """Build the incident payload, keeping every reported detail."""
        lines = [
            self.description,
            "",
            "--- Reported via webhook ---",
            f"Severity: {self.severity.value}",
            f"Category: {self.category}",
            f"Application: {self.application or 'n/a'}",
            f"Reporter: {self.reporter.name} <{self.reporter.email}>",
            f"Reported at: {self.reported_at.isoformat() if self.reported_at else 'n/a'}",
            f"Source event: {self.event_id}",
        ]
        if self.metadata:
            lines.append("Additional details:")
            lines.extend(f"  {key}: {value}" for key, value in sorted(self.metadata.items()))
        return {
            "external_id": self.event_id,
            "short_description": self.title,
            "description": "\n".join(lines),
            "priority": PRIORITY[self.severity],
            "category": self.category,
            "application": self.application,
            "caller_name": self.reporter.name,
            "caller_email": self.reporter.email,
            "opened_at": self.reported_at.isoformat() if self.reported_at else None,
        }
