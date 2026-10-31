# Integration test scenarios

<!-- Generated from integration_scenarios.json by scripts/render_integration_docs.py. Do not edit by hand. -->

**101 scenarios** across 8 areas and 8 integration points. The full detail of every scenario (given, steps, expected results, test data, linked automated tests, notes and manual evidence) is in [`integration_scenarios.json`](integration_scenarios.json).

## Scope and approach

Integration testing checks that the parts work **together** across their boundaries: browser and UI, knowledge base files, vector database, Claude CLI, webhook receiver, incident system and its database, and configuration. Unit-level logic is covered separately by the unit tests.

- **Risk-based priority.** **P1**: Core flow or security control; a failure blocks release. **P2**: Important behaviour; fix before release or accept the risk explicitly. **P3**: Edge case; schedule when convenient.
- **Honest coverage status.** **automated**: Covered by an automated test that asserts the expected result. **partial**: Some of the expected result is automated; the note says what is missing. **manual**: Verified by hand only (evidence recorded). **gap**: Not tested yet.
- **Traceability.** Every `automated` or `partial` scenario lists the tests that cover it; a test checks those tests exist.

## Test environments

| ID | Name | Description |
|---|---|---|
| ENV-A | In-process automated | pytest; real Chroma and embedding model in a temp folder, real receiver and incident app connected in memory, Claude CLI stubbed. |
| ENV-B | Live automated | pytest starts the receiver and the incident system as real uvicorn servers on free ports and calls them over HTTP; Claude is not involved. |
| ENV-C | Manual / full stack | All three services running with the real Claude CLI, exercised in a real browser. |

## Integration points

| ID | From | To | Protocol |
|---|---|---|---|
| IP-01 | Browser | Streamlit UI | HTTP / WebSocket |
| IP-02 | App | Golden knowledge base files | File system |
| IP-03 | App | Chroma vector DB and local embedding model | In-process library, local disk |
| IP-04 | App | Claude CLI (answering and triage) | Subprocess, prompt on stdin |
| IP-05 | App or external sender | Webhook receiver | HTTP POST, HMAC-signed |
| IP-06 | Webhook receiver | Incident system | HTTP, API key, retries |
| IP-07 | Incident system | SQLite database | In-process library, local disk |
| IP-08 | Environment / .env | All components | Environment variables |

## Coverage summary

| Area | automated | partial | manual | gap | Total |
|---|---|---|---|---|---|
| Knowledge base and indexing | 5 | 3 | 0 | 3 | 11 |
| Retrieval and answer generation | 11 | 3 | 1 | 1 | 16 |
| Agent escalation and ticket confirmation | 15 | 5 | 0 | 3 | 23 |
| Webhook receiver | 17 | 2 | 0 | 2 | 21 |
| Incident system | 7 | 0 | 0 | 0 | 7 |
| Configuration and startup | 3 | 1 | 1 | 3 | 8 |
| End-to-end journeys | 1 | 3 | 2 | 3 | 9 |
| Security and privacy (cross-cutting) | 1 | 2 | 1 | 2 | 6 |
| **Total** | 60 | 19 | 5 | 17 | 101 |

| Priority | automated | partial | manual | gap | Total |
|---|---|---|---|---|---|
| P1 | 25 | 6 | 1 | 7 | 39 |
| P2 | 33 | 11 | 4 | 6 | 54 |
| P3 | 2 | 2 | 0 | 4 | 8 |

## Highest-priority gaps

P1 scenarios that are not fully automated, in the order they should be closed:

| ID | Scenario | Status | Why it matters / what is missing |
|---|---|---|---|
| INT-CFG-06 | Automatic tickets turn off when WEBHOOK_SECRET is missing, short or a placeholder | gap | Evaluated at import time, so it needs a subprocess or reload test. |
| INT-E2E-06 | Two users with pending drafts at the same time stay isolated | gap | Highest-value missing multi-user test: session state is the isolation mechanism. |
| INT-E2E-09 | Agent to receiver to incident system over real HTTP between separate processes | gap | Agent tests use an in-process receiver and live tests do not use the agent. This automatable test closes that join. |
| INT-KB-04 | Editing an existing paragraph replaces the old content | gap | Only file addition is covered today. |
| INT-KB-05 | Removing a policy file removes its content from answers | gap | Stale content surviving a deletion would be a policy-accuracy defect. |
| INT-RAG-04 | The paragraph that holds the answer ranks inside TOP_K | gap | Today this is only checked by hand. A retrieval-only test on the golden files needs no Claude call. |
| INT-SEC-06 | Anonymous users are blocked when the app is hosted centrally | gap | The app has no login of its own; this can only be tested in the target environment. |
| INT-AGT-09 | Automatic tickets are disabled when the webhook secret is missing or a placeholder | partial | The test sets the flag directly; deriving the flag from the environment is not tested (see INT-CFG-06). |
| INT-AGT-13 | The agent can never raise a critical incident | partial | The clamp is unit tested; the priority on the created ticket is not asserted end to end. |
| INT-E2E-04 | Browser journey: incident system fails at confirmation, then recovers | partial | Webhook-level recovery is tested live; the browser-level journey is not. |
| INT-KB-01 | First start builds the index from the golden knowledge base | partial | Answer and source are asserted; the exact vector count (20) is not. |
| INT-RAG-12 | A question that tries to make Claude read files or run commands is refused | partial | Automated test checks the CLI is launched with tools disabled and the prompt on stdin; actual refusal is manual. |
| INT-SEC-02 | Secrets never appear in served pages, API responses or logs | partial | Receiver logs are tested; the Streamlit page and agent-path logs were only checked by hand. |
| INT-E2E-01 | Browser journey: unresolved issue, review, confirm, ticket visible in the incident system | manual | No automated browser test; the logic is covered by in-process tests. |

## Entry and exit criteria

- **Entry:** unit tests pass; the golden knowledge base and static data validate (`tests/test_data_layout.py`); services start with the documented configuration.
- **Exit (release):** every P1 scenario is `automated`, or `manual` with recorded evidence from the release candidate; no open P1 `gap`; P2 gaps are either closed or accepted in writing; no secret, stack trace or personal data in any response or log.

## Scenarios by area

### Knowledge base and indexing

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-KB-01 | First start builds the index from the golden knowledge base | P1 | functional | IP-02, IP-03 | partial |
| INT-KB-02 | Restart reuses an unchanged index without re-embedding | P2 | resilience | IP-02, IP-03 | automated |
| INT-KB-03 | Adding a policy file makes its content answerable | P1 | functional | IP-02, IP-03 | automated |
| INT-KB-04 | Editing an existing paragraph replaces the old content | P1 | functional | IP-02, IP-03 | gap |
| INT-KB-05 | Removing a policy file removes its content from answers | P1 | functional | IP-02, IP-03 | gap |
| INT-KB-06 | Missing knowledge-base folder gives a friendly error | P2 | negative | IP-02, IP-01 | partial |
| INT-KB-07 | Folder with no usable .txt files gives a friendly error | P2 | negative | IP-02 | partial |
| INT-KB-08 | Non-.txt and empty files are ignored | P3 | functional | IP-02 | automated |
| INT-KB-09 | Each blank-line-separated paragraph becomes one retrievable chunk | P2 | functional | IP-02, IP-03 | automated |
| INT-KB-10 | An unreadable file is skipped with a warning, others still load | P3 | negative | IP-02 | gap |
| INT-KB-11 | The golden folder only ever contains .txt policy files | P2 | data | IP-02 | automated |

### Retrieval and answer generation

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-RAG-01 | A policy question is answered from the knowledge base with sources | P1 | functional | IP-01, IP-03, IP-04 | automated |
| INT-RAG-02 | A question outside the knowledge base returns the standard message without calling Claude | P1 | functional | IP-03, IP-04 | automated |
| INT-RAG-03 | Passages retrieved but Claude declines to answer | P1 | functional | IP-03, IP-04 | automated |
| INT-RAG-04 | The paragraph that holds the answer ranks inside TOP_K | P1 | functional | IP-03 | gap |
| INT-RAG-05 | Empty or whitespace question is rejected with a clear message | P2 | negative | IP-01 | automated |
| INT-RAG-06 | Question over the length limit is rejected | P2 | negative | IP-01 | partial |
| INT-RAG-07 | Control characters and extra whitespace are normalised | P3 | negative | IP-01 | automated |
| INT-RAG-08 | Claude CLI not installed | P2 | negative | IP-04, IP-01 | automated |
| INT-RAG-09 | Claude CLI times out | P2 | resilience | IP-04 | automated |
| INT-RAG-10 | Claude CLI exits with an error; its stderr is not shown | P2 | security | IP-04 | automated |
| INT-RAG-11 | Claude CLI returns empty output | P2 | negative | IP-04 | automated |
| INT-RAG-12 | A question that tries to make Claude read files or run commands is refused | P1 | security | IP-04 | partial |
| INT-RAG-13 | Concurrent Claude calls are capped and the slot is always released | P2 | concurrency | IP-04 | automated |
| INT-RAG-14 | Concurrent questions during an index rebuild do not error | P2 | concurrency | IP-03 | automated |
| INT-RAG-15 | The installed Claude CLI accepts the options the app relies on | P2 | functional | IP-04 | manual |
| INT-RAG-16 | Missing or unreadable index gives a friendly error | P3 | negative | IP-03 | partial |

### Agent escalation and ticket confirmation

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-AGT-01 | An unresolved IT issue produces a ticket proposal and creates nothing yet | P1 | functional | IP-01, IP-04, IP-03 | automated |
| INT-AGT-02 | Confirming creates the ticket exactly as drafted | P1 | functional | IP-01, IP-05, IP-06, IP-07 | automated |
| INT-AGT-03 | Declining creates no ticket | P2 | functional | IP-01, IP-06 | automated |
| INT-AGT-04 | Double confirmation (double click) opens one ticket | P1 | functional | IP-01, IP-05 | automated |
| INT-AGT-05 | Confirming after declining does nothing | P2 | negative | IP-01 | automated |
| INT-AGT-06 | A non-IT question never produces a proposal | P1 | functional | IP-04 | automated |
| INT-AGT-07 | Without name and email the agent does not escalate and shows a tip | P2 | functional | IP-01 | partial |
| INT-AGT-08 | An invalid email in the sidebar is rejected before the agent runs | P2 | negative | IP-01 | automated |
| INT-AGT-09 | Automatic tickets are disabled when the webhook secret is missing or a placeholder | P1 | configuration | IP-08 | partial |
| INT-AGT-10 | Related passages that do not answer the question still escalate | P2 | functional | IP-03, IP-04, IP-05 | automated |
| INT-AGT-11 | A failing triage call falls back to the standard message | P2 | resilience | IP-04 | automated |
| INT-AGT-12 | Untrustworthy triage output never creates a ticket | P1 | security | IP-04 | automated |
| INT-AGT-13 | The agent can never raise a critical incident | P1 | security | IP-04, IP-05 | partial |
| INT-AGT-14 | An unknown triage category falls back to 'general' | P3 | functional | IP-04, IP-05 | partial |
| INT-AGT-15 | Same user, same question, same day returns the existing ticket | P1 | functional | IP-05, IP-06 | automated |
| INT-AGT-16 | The same question on a later day opens a new ticket | P2 | functional | IP-05, IP-06 | gap |
| INT-AGT-17 | Two different users with the same question get separate tickets | P2 | functional | IP-05, IP-06 | gap |
| INT-AGT-18 | The receiver is unreachable when the user confirms | P2 | resilience | IP-05 | partial |
| INT-AGT-19 | The receiver rejects the event (401, 422, 500, 502) | P2 | negative | IP-05 | automated |
| INT-AGT-20 | The agent's event is correctly signed and accepted by the receiver | P1 | security | IP-05 | automated |
| INT-AGT-21 | With confirmation switched off, the ticket opens immediately | P2 | configuration | IP-05, IP-06 | automated |
| INT-AGT-22 | The activity trail accurately lists every step taken | P2 | functional | IP-01 | automated |
| INT-AGT-23 | Markdown or HTML in a model-written title or summary renders safely | P2 | security | IP-01, IP-04 | gap |

### Webhook receiver

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-WH-01 | A valid signed event creates a ticket with every reported detail | P1 | functional | IP-05, IP-06, IP-07 | automated |
| INT-WH-02 | Re-delivery of the same event is idempotent | P1 | functional | IP-05, IP-06 | automated |
| INT-WH-03 | Distinct events create distinct, sequential tickets | P2 | functional | IP-05, IP-06, IP-07 | automated |
| INT-WH-04 | Missing signature headers are rejected | P1 | security | IP-05 | automated |
| INT-WH-05 | A signature made with the wrong secret is rejected | P1 | security | IP-05 | automated |
| INT-WH-06 | A body altered after signing is rejected | P1 | security | IP-05 | automated |
| INT-WH-07 | A replayed old request is rejected | P1 | security | IP-05 | automated |
| INT-WH-08 | A malformed timestamp header is rejected | P2 | security | IP-05 | partial |
| INT-WH-09 | Invalid JSON returns 400 | P2 | negative | IP-05 | automated |
| INT-WH-10 | Invalid fields return 422 with a field list and never echo the input | P1 | negative | IP-05 | automated |
| INT-WH-11 | Unknown extra fields are rejected | P2 | negative | IP-05 | automated |
| INT-WH-12 | An oversized body is rejected | P2 | negative | IP-05 | automated |
| INT-WH-13 | Transient incident-system errors are retried with backoff | P2 | resilience | IP-06 | automated |
| INT-WH-14 | Incident system down: 502 with a generic message, then recovery with no data loss | P1 | resilience | IP-05, IP-06, IP-07 | automated |
| INT-WH-15 | Incident system rejects the API key: 502 without retries | P2 | negative | IP-06 | automated |
| INT-WH-16 | An unexpected internal error returns a generic 500 | P2 | negative | IP-05 | automated |
| INT-WH-17 | Logs are useful and free of secrets and personal data | P1 | security | IP-05 | automated |
| INT-WH-18 | Health endpoints respond | P2 | functional | IP-05, IP-06 | automated |
| INT-WH-19 | A slow incident system (timeout) is retried and then reported as 502 | P2 | resilience | IP-06 | partial |
| INT-WH-20 | A large body sent without a Content-Length header | P3 | security | IP-05 | gap |
| INT-WH-21 | A burst of events from one sender | P3 | resilience | IP-05 | gap |

### Incident system

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-ITSM-01 | Every incident API call requires the API key | P1 | security | IP-06 | automated |
| INT-ITSM-02 | Create, retrieve and list incidents; unknown number returns 404 | P1 | functional | IP-06, IP-07 | automated |
| INT-ITSM-03 | A repeated external_id returns 409 with the existing ticket | P2 | functional | IP-06, IP-07 | automated |
| INT-ITSM-04 | Invalid incident bodies are rejected | P2 | negative | IP-06 | automated |
| INT-ITSM-05 | Tickets survive a restart and numbering continues | P1 | resilience | IP-07 | automated |
| INT-ITSM-06 | Concurrent creates with the same external_id produce one ticket | P1 | concurrency | IP-06, IP-07 | automated |
| INT-ITSM-07 | The incident system refuses to start without an API key | P2 | configuration | IP-08 | automated |

### Configuration and startup

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-CFG-01 | The receiver refuses to start without required settings | P2 | configuration | IP-08 | automated |
| INT-CFG-02 | Unedited .env.example placeholders are rejected | P2 | security | IP-08 | automated |
| INT-CFG-03 | A weak (short) secret is rejected | P2 | security | IP-08 | automated |
| INT-CFG-04 | A non-numeric value for a numeric setting fails clearly | P3 | configuration | IP-08 | gap |
| INT-CFG-05 | KNOWLEDGE_DIR defaults to the golden folder and can be overridden | P2 | configuration | IP-02, IP-08 | partial |
| INT-CFG-06 | Automatic tickets turn off when WEBHOOK_SECRET is missing, short or a placeholder | P1 | configuration | IP-08 | gap |
| INT-CFG-07 | The app and receiver use different secrets | P2 | configuration | IP-05, IP-08 | gap |
| INT-CFG-08 | Services bind to localhost by default | P2 | security | IP-01, IP-05, IP-06 | manual |

### End-to-end journeys

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-E2E-01 | Browser journey: unresolved issue, review, confirm, ticket visible in the incident system | P1 | functional | IP-01, IP-03, IP-04, IP-05, IP-06, IP-07 | manual |
| INT-E2E-02 | Browser journey: a policy question is answered with sources | P2 | functional | IP-01, IP-03, IP-04 | manual |
| INT-E2E-03 | Browser journey: decline the ticket | P2 | functional | IP-01 | partial |
| INT-E2E-04 | Browser journey: incident system fails at confirmation, then recovers | P1 | resilience | IP-01, IP-05, IP-06 | partial |
| INT-E2E-05 | The app restarts while a draft is pending | P2 | resilience | IP-01 | gap |
| INT-E2E-06 | Two users with pending drafts at the same time stay isolated | P1 | concurrency | IP-01, IP-05, IP-06 | gap |
| INT-E2E-07 | Ten simultaneous questions | P2 | concurrency | IP-01, IP-03, IP-04 | partial |
| INT-E2E-08 | An external sender posts an event with the sample script and gets a ticket | P2 | functional | IP-05, IP-06, IP-07 | automated |
| INT-E2E-09 | Agent to receiver to incident system over real HTTP between separate processes | P1 | functional | IP-05, IP-06, IP-07 | gap |

### Security and privacy (cross-cutting)

| ID | Scenario | Pri | Type | Points | Status |
|---|---|---|---|---|---|
| INT-SEC-01 | No stack trace or internal detail ever reaches the browser | P1 | security | IP-01 | automated |
| INT-SEC-02 | Secrets never appear in served pages, API responses or logs | P1 | security | IP-01, IP-05, IP-06 | partial |
| INT-SEC-03 | Prompt injection in a question cannot force a critical or unrelated ticket | P2 | security | IP-04, IP-05 | partial |
| INT-SEC-04 | Signature and API-key comparisons are timing-safe | P2 | security | IP-05, IP-06 | manual |
| INT-SEC-05 | Agent-path logs contain no names, emails or question text | P2 | security | IP-04, IP-05 | gap |
| INT-SEC-06 | Anonymous users are blocked when the app is hosted centrally | P1 | security | IP-01 | gap |

## Maintaining this catalogue

1. Add or change scenarios in `integration_scenarios.json` (keep IDs stable; new IDs continue the area's numbering).
2. When you add an automated test, list it in `automated_by` as `tests/<file>.py::<test_name>` and set the status.
3. Run `python scripts/render_integration_docs.py` to refresh this page.
4. Run `python -m pytest tests/test_integration_scenarios.py` to validate structure, links to tests and README freshness.
