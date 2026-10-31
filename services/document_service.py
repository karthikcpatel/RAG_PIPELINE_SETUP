import logging

from services.errors import KnowledgeBaseError

logger = logging.getLogger(__name__)


def load_documents(folder):
    """Return {filename: text} for every non-empty .txt file in the folder."""
    if not folder.is_dir():
        logger.error("Knowledge folder not found: %s", folder)
        raise KnowledgeBaseError()
    documents = {}
    for path in sorted(folder.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            logger.warning("Could not read %s", path.name, exc_info=True)
            continue
        if text:
            documents[path.name] = text
    if not documents:
        logger.error("No readable .txt documents found in %s", folder)
        raise KnowledgeBaseError()
    return documents


def chunk_documents(documents):
    """Split each document into paragraph chunks, keeping the filename."""
    chunks = []
    for filename, text in documents.items():
        for paragraph in text.split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph:
                chunks.append({"source": filename, "text": paragraph})
    return chunks
