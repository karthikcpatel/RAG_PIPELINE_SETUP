"""IT Helpdesk Assistant: Streamlit web UI. Run with `streamlit run app.py`."""

import logging

import streamlit as st

from config import settings
from pydantic import ValidationError

from services import rag_service
from services.errors import HelpdeskError, InvalidQuestionError
from utils.logging_setup import configure_logging
from webhook_receiver.models import Reporter

configure_logging()
logger = logging.getLogger(__name__)

GENERIC_ERROR = "Unable to process the request. Please verify your input and try again."

st.set_page_config(page_title="IT Helpdesk Assistant", page_icon="💬")


@st.cache_resource(ttl=settings.INDEX_REFRESH_SECONDS)
def prepare_index():
    """Sync the vector index with the knowledge base (cheap if nothing changed)."""
    rag_service.refresh_index()
    return True


def clear():
    st.session_state.question = ""
    st.session_state.result = None
    st.session_state.error = None


def confirm_ticket():
    st.session_state.error = None
    try:
        with st.spinner("Opening your ticket..."):
            st.session_state.result = rag_service.confirm_ticket(st.session_state.result)
    except Exception:
        logger.exception("Unexpected error while opening a ticket")
        st.session_state.error = ("error", GENERIC_ERROR)


def decline_ticket():
    st.session_state.error = None
    st.session_state.result = rag_service.decline_ticket(st.session_state.result)


def current_reporter():
    """The user's details from the sidebar, None if blank. Raises ValidationError if invalid."""
    name = st.session_state.get("reporter_name", "").strip()
    email = st.session_state.get("reporter_email", "").strip()
    if not name and not email:
        return None
    return Reporter(name=name, email=email).model_dump()


def submit():
    st.session_state.result = None
    st.session_state.error = None
    try:
        reporter = current_reporter()
    except ValidationError:
        st.session_state.error = ("warning", "Please enter your name and a valid email address, or leave both blank.")
        return
    try:
        with st.spinner("Working on it..."):
            st.session_state.result = rag_service.answer_question(st.session_state.question, reporter)
    except InvalidQuestionError as exc:
        st.session_state.error = ("warning", exc.user_message)
    except HelpdeskError as exc:
        st.session_state.error = ("error", exc.user_message)
    except Exception:
        logger.exception("Unexpected error while answering a question")
        st.session_state.error = ("error", GENERIC_ERROR)


st.title("IT Helpdesk Assistant")
st.write(
    "Ask a question about company IT policies. Answers come only from the "
    "internal knowledge base, and the source documents are shown with each answer."
)

try:
    prepare_index()
except HelpdeskError as exc:
    st.error(exc.user_message)
    st.stop()
except Exception:
    logger.exception("Unexpected error while preparing the index")
    st.error(GENERIC_ERROR)
    st.stop()

with st.sidebar:
    st.subheader("Your details")
    st.text_input("Name", key="reporter_name", max_chars=100)
    st.text_input("Email", key="reporter_email", max_chars=254)
    if settings.AUTO_TICKET_ENABLED:
        st.caption("If I can't resolve your issue, I'll open a Service Desk ticket for you using these details.")
    else:
        st.caption("Automatic ticket creation is not configured.")

st.session_state.setdefault("question", "")
st.session_state.setdefault("result", None)
st.session_state.setdefault("error", None)

with st.form("ask"):
    st.text_area(
        "Your question",
        key="question",
        max_chars=settings.MAX_QUESTION_CHARS,
        placeholder="For example: How do I reset my password?",
    )
    ask_col, clear_col, _ = st.columns([1, 1, 4])
    ask_col.form_submit_button("Ask", type="primary", on_click=submit)
    clear_col.form_submit_button("Clear", on_click=clear)

if st.session_state.error:
    level, message = st.session_state.error
    (st.warning if level == "warning" else st.error)(message)

result = st.session_state.result
if result is not None:
    st.subheader("Answer")
    if result.answered:
        st.markdown(result.text)
        st.caption("Sources: " + ", ".join(result.sources))
        with st.expander("Show retrieved excerpts"):
            for excerpt in result.excerpts:
                st.markdown(f"**{excerpt['source']}**")
                st.text(excerpt["text"])
    elif result.ticket_status == "pending":
        st.info(result.text)
        proposal = result.proposal
        with st.container(border=True):
            st.markdown(f"**{proposal.title}**")
            st.write(proposal.summary)
            st.caption(
                f"Severity: {proposal.severity} · Category: {proposal.category} · "
                f"Reporter: {proposal.reporter['name']} ({proposal.reporter['email']})"
            )
        yes_col, no_col, _ = st.columns([2, 2, 3])
        yes_col.button("Yes, open the ticket", type="primary", key="confirm_ticket", on_click=confirm_ticket)
        no_col.button("No thanks", key="decline_ticket", on_click=decline_ticket)
    elif result.ticket_status in ("created", "duplicate"):
        st.success(result.text)
    else:
        st.info(result.text)
        if settings.AUTO_TICKET_ENABLED and not (
            st.session_state.reporter_name or st.session_state.reporter_email
        ):
            st.caption("Tip: enter your name and email in the sidebar and I can open a ticket for you automatically.")

    ICONS = {"done": "✅", "skipped": "➖", "failed": "⚠️"}
    with st.expander("Agent activity", expanded=result.ticket_status is not None):
        for step in result.steps:
            st.markdown(f"{ICONS.get(step.status, '•')} {step.text}")
