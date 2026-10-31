import os
from dataclasses import dataclass

from dotenv import load_dotenv

MIN_SECRET_LENGTH = 16


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing or unsafe."""


@dataclass(frozen=True)
class Settings:
    webhook_secret: str
    ticket_api_url: str
    ticket_api_key: str
    signature_tolerance_seconds: int = 300
    max_body_bytes: int = 64 * 1024
    ticket_timeout_seconds: float = 5.0
    ticket_max_attempts: int = 3

    @classmethod
    def from_env(cls):
        load_dotenv()
        required = ("WEBHOOK_SECRET", "TICKET_API_URL", "TICKET_API_KEY")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
        secret = os.environ["WEBHOOK_SECRET"]
        if secret.startswith("<") or os.environ["TICKET_API_KEY"].startswith("<"):
            raise ConfigError("WEBHOOK_SECRET and TICKET_API_KEY are still the .env.example placeholders")
        if len(secret) < MIN_SECRET_LENGTH:
            raise ConfigError(f"WEBHOOK_SECRET must be at least {MIN_SECRET_LENGTH} characters")
        try:
            return cls(
                webhook_secret=secret,
                ticket_api_url=os.environ["TICKET_API_URL"].rstrip("/"),
                ticket_api_key=os.environ["TICKET_API_KEY"],
                signature_tolerance_seconds=int(os.environ.get("SIGNATURE_TOLERANCE_SECONDS", 300)),
                max_body_bytes=int(os.environ.get("MAX_BODY_BYTES", 64 * 1024)),
                ticket_timeout_seconds=float(os.environ.get("TICKET_TIMEOUT_SECONDS", 5)),
                ticket_max_attempts=int(os.environ.get("TICKET_MAX_ATTEMPTS", 3)),
            )
        except ValueError as exc:
            raise ConfigError("A numeric environment variable has an invalid value") from exc
