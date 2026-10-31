"""HTTP client for the internal incident system, with retries for transient failures."""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TicketServiceError(Exception):
    """The incident system could not create the ticket (details are only for logs)."""


class DuplicateTicket(Exception):
    def __init__(self, ticket_id):
        super().__init__(ticket_id)
        self.ticket_id = ticket_id


class TicketClient:
    def __init__(self, http_client, max_attempts=3, backoff_seconds=0.5, sleep=time.sleep):
        self._http = http_client
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._sleep = sleep

    def create_incident(self, payload):
        """Return the created incident dict. Raises DuplicateTicket or TicketServiceError."""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.post("/api/incidents", json=payload)
            except httpx.TransportError as exc:  # includes timeouts and connection errors
                logger.warning("Ticket system unreachable (attempt %d/%d): %s", attempt, self._max_attempts, type(exc).__name__)
            else:
                if response.status_code == 201:
                    return response.json()
                if response.status_code == 409:
                    raise DuplicateTicket(response.json().get("ticket_id"))
                if response.status_code not in RETRYABLE_STATUS:
                    # 4xx means our request or credentials are wrong; retrying cannot help.
                    logger.error("Ticket system rejected the request with HTTP %d", response.status_code)
                    raise TicketServiceError(f"rejected with HTTP {response.status_code}")
                logger.warning("Ticket system returned HTTP %d (attempt %d/%d)", response.status_code, attempt, self._max_attempts)
            if attempt < self._max_attempts:
                self._sleep(self._backoff * 2 ** (attempt - 1))
        raise TicketServiceError(f"unavailable after {self._max_attempts} attempts")
