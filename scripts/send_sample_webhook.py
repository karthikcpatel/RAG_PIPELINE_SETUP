"""Send one signed sample "issue reported" webhook.

Usage: python scripts/send_sample_webhook.py [url] [event_id]
Reads WEBHOOK_SECRET from the environment (or .env).
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.data_files import load_json  # noqa: E402
from webhook_receiver import security  # noqa: E402


def main():
    load_dotenv()
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        sys.exit("WEBHOOK_SECRET is not set")
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/webhooks/issues"
    event_id = sys.argv[2] if len(sys.argv) > 2 else f"evt-{uuid.uuid4().hex[:12]}"
    payload = {
        "event_id": event_id,
        "event_type": "issue.reported",
        **load_json("scenario", "webhook_payloads.json")["sample"],
        "reported_at": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    response = httpx.post(
        url,
        content=body,
        headers={
            "Content-Type": "application/json",
            security.TIMESTAMP_HEADER: timestamp,
            security.SIGNATURE_HEADER: security.sign(secret, timestamp, body),
        },
        timeout=15,
    )
    print(response.status_code, response.text)


if __name__ == "__main__":
    main()
