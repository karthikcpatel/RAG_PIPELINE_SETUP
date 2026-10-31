"""HMAC-SHA256 request signing.

The sender signs "<unix-timestamp>.<raw body>" with the shared secret and sends:
    X-Webhook-Timestamp: <unix-timestamp>
    X-Webhook-Signature: sha256=<hex digest>
The timestamp is part of the signed data, so a captured request can't be replayed later.
"""

import hashlib
import hmac
import time

TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SIGNATURE_HEADER = "X-Webhook-Signature"


class SignatureError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def sign(secret, timestamp, body):
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(secret, timestamp_header, signature_header, body, tolerance_seconds, now=None):
    """Raise SignatureError unless the request is fresh and correctly signed."""
    if not timestamp_header or not signature_header:
        raise SignatureError("missing_signature", "Missing signature headers.")
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        raise SignatureError("invalid_timestamp", "Invalid timestamp header.") from None
    now = time.time() if now is None else now
    if abs(now - timestamp) > tolerance_seconds:
        raise SignatureError("stale_timestamp", "Request timestamp is outside the allowed window.")
    if not hmac.compare_digest(sign(secret, timestamp_header, body), signature_header):
        raise SignatureError("invalid_signature", "Signature verification failed.")
