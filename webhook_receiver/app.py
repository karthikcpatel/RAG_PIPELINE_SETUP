"""Webhook receiver: turns signed "issue reported" events into incidents.

Run with: uvicorn webhook_receiver.app:create_app --factory --port 8000
Environment: WEBHOOK_SECRET, TICKET_API_URL, TICKET_API_KEY (see .env.example)

Responses: 201 created, 200 duplicate (already ticketed), 400 bad JSON, 401 bad signature,
413 too large, 422 invalid payload, 502 incident system unavailable (sender should retry),
500 unexpected error. Error bodies never contain internal details.
"""

import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from webhook_receiver import security
from webhook_receiver.models import IssueReport
from webhook_receiver.settings import Settings
from webhook_receiver.ticket_client import DuplicateTicket, TicketClient, TicketServiceError

logger = logging.getLogger("webhook_receiver")


def _error(status, code, message, **extra):
    return JSONResponse(status_code=status, content={"status": "error", "code": code, "message": message, **extra})


def create_app(settings=None, ticket_client=None):
    settings = settings or Settings.from_env()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if ticket_client is None:
        http_client = httpx.Client(
            base_url=settings.ticket_api_url,
            headers={"X-API-Key": settings.ticket_api_key},
            timeout=settings.ticket_timeout_seconds,
        )
        ticket_client = TicketClient(http_client, settings.ticket_max_attempts)

    app = FastAPI(title="Issue webhook receiver", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/webhooks/issues")
    async def receive_issue(request: Request):
        client_ip = request.client.host if request.client else "unknown"

        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_body_bytes:
            logger.warning("Rejected oversized request from %s", client_ip)
            return _error(413, "payload_too_large", "Request body is too large.")
        body = await request.body()
        if len(body) > settings.max_body_bytes:
            logger.warning("Rejected oversized request from %s", client_ip)
            return _error(413, "payload_too_large", "Request body is too large.")

        try:
            security.verify(
                settings.webhook_secret,
                request.headers.get(security.TIMESTAMP_HEADER),
                request.headers.get(security.SIGNATURE_HEADER),
                body,
                settings.signature_tolerance_seconds,
            )
        except security.SignatureError as exc:
            logger.warning("Rejected request from %s: %s", client_ip, exc.code)
            return _error(401, exc.code, exc.message)

        try:
            report = IssueReport.model_validate_json(body)
        except ValidationError as exc:
            problems = exc.errors()
            if any(p["type"] == "json_invalid" for p in problems):
                logger.warning("Rejected request from %s: invalid JSON", client_ip)
                return _error(400, "invalid_json", "Request body is not valid JSON.")
            fields = [{"field": ".".join(str(part) for part in p["loc"]), "message": p["msg"]} for p in problems]
            logger.warning("Rejected request from %s: invalid fields %s", client_ip, [f["field"] for f in fields])
            return _error(422, "validation_error", "The payload failed validation.", errors=fields)

        try:
            incident = await run_in_threadpool(ticket_client.create_incident, report.to_incident())
        except DuplicateTicket as exc:
            logger.info("Event %s already ticketed as %s", report.event_id, exc.ticket_id)
            return JSONResponse(
                status_code=200,
                content={"status": "duplicate", "event_id": report.event_id, "ticket_id": exc.ticket_id},
            )
        except TicketServiceError as exc:
            logger.error("Could not create ticket for event %s: %s", report.event_id, exc)
            return _error(502, "ticket_service_unavailable", "Could not create the ticket right now. Please retry later.")
        except Exception:
            logger.exception("Unexpected error handling event %s", report.event_id)
            return _error(500, "internal_error", "An internal error occurred.")

        number = incident["number"]
        logger.info("Event %s -> ticket %s (severity=%s)", report.event_id, number, report.severity.value)
        return JSONResponse(
            status_code=201,
            content={"status": "created", "event_id": report.event_id, "ticket_id": number},
        )

    return app
