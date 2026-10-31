"""Application errors. Each carries a message that is safe to show to end users."""


class HelpdeskError(Exception):
    user_message = "Unable to process the request. Please verify your input and try again."

    def __init__(self, user_message=None):
        super().__init__(user_message or self.user_message)
        if user_message:
            self.user_message = user_message


class InvalidQuestionError(HelpdeskError):
    """The user's question failed validation (message is specific and safe)."""


class KnowledgeBaseError(HelpdeskError):
    user_message = "The knowledge base is currently unavailable. Please contact the IT Helpdesk."


class VectorStoreError(HelpdeskError):
    user_message = "The search index is currently unavailable. Please try again later."


class LLMUnavailableError(HelpdeskError):
    user_message = "The answer service is currently unavailable. Please try again later."


class LLMBusyError(HelpdeskError):
    user_message = "The service is busy right now. Please try again in a moment."


class LLMError(HelpdeskError):
    user_message = "Unable to generate an answer right now. Please try again later."
