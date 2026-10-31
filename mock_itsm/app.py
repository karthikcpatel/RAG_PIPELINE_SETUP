"""Mock internal incident-management application (stands in for a real ITSM tool).

Run with: uvicorn mock_itsm.app:create_app --factory --port 9000
Environment: MOCK_ITSM_API_KEY (required), MOCK_ITSM_DB (default mock_itsm.db)
"""

import hmac
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mock_itsm.store import IncidentStore

logger = logging.getLogger("mock_itsm")


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_id: str = Field(min_length=1, max_length=100)
    short_description: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=12000)
    priority: int = Field(ge=1, le=4)  # 1 = critical ... 4 = low
    category: Optional[str] = Field(default=None, max_length=50)
    application: Optional[str] = Field(default=None, max_length=100)
    caller_name: Optional[str] = Field(default=None, max_length=100)
    caller_email: Optional[str] = Field(default=None, max_length=254)
    opened_at: Optional[str] = Field(default=None, max_length=40)


def create_app(api_key=None, db_path=None):
    api_key = api_key or os.environ.get("MOCK_ITSM_API_KEY")
    if not api_key:
        raise RuntimeError("MOCK_ITSM_API_KEY is not set")
    store = IncidentStore(db_path or os.environ.get("MOCK_ITSM_DB", "mock_itsm.db"))
    app = FastAPI(title="Mock ITSM", docs_url=None, redoc_url=None, openapi_url=None)

    def require_api_key(x_api_key: Optional[str] = Header(default=None)):
        if not x_api_key or not hmac.compare_digest(x_api_key.encode(), api_key.encode()):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/incidents", status_code=201, dependencies=[Depends(require_api_key)])
    def create_incident(incident: IncidentCreate):
        record, created = store.create(incident.model_dump())
        if not created:
            logger.info("Duplicate external_id %s -> %s", record["external_id"], record["number"])
            return JSONResponse(
                status_code=409,
                content={"code": "duplicate", "ticket_id": record["number"]},
            )
        logger.info("Created %s (priority %s)", record["number"], record["priority"])
        return record

    @app.get("/api/incidents/{number}", dependencies=[Depends(require_api_key)])
    def get_incident(number: str):
        record = store.get(number)
        if record is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return record

    @app.get("/api/incidents", dependencies=[Depends(require_api_key)])
    def list_incidents(limit: int = Query(default=50, ge=1, le=200)):
        return store.list(limit)

    return app
