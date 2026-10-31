import re

from config import settings
from services.errors import InvalidQuestionError

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def validate_question(question):
    """Return a cleaned question, or raise InvalidQuestionError with a user-safe message."""
    if not isinstance(question, str):
        raise InvalidQuestionError("Please enter a question.")
    cleaned = " ".join(_CONTROL_CHARS.sub(" ", question).split())
    if not cleaned:
        raise InvalidQuestionError("Please enter a question.")
    if len(cleaned) > settings.MAX_QUESTION_CHARS:
        raise InvalidQuestionError(
            f"Please keep your question under {settings.MAX_QUESTION_CHARS} characters."
        )
    return cleaned
