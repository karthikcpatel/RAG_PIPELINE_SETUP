"""Command-line entry point (the original `python app.py` behaviour)."""

import logging
import sys

from services import rag_service
from services.errors import HelpdeskError
from utils.logging_setup import configure_logging


def main():
    configure_logging()
    logging.getLogger().setLevel(logging.WARNING)  # keep CLI output clean
    question = " ".join(sys.argv[1:]).strip() or input("Question: ")
    try:
        rag_service.refresh_index()
        answer = rag_service.answer_question(question)
    except HelpdeskError as exc:
        sys.exit(f"Error: {exc.user_message}")
    if not answer.answered:
        print(answer.text)
        return
    print(f"\nAnswer:\n{answer.text}\n\nSources: {', '.join(answer.sources)}")


if __name__ == "__main__":
    main()
