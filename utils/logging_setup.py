import logging

from config import settings

_configured = False


def configure_logging():
    """Configure server-side logging once per process."""
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _configured = True
