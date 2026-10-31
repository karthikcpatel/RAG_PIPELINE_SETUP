# IT Helpdesk Assistant (RAG)

A small Retrieval-Augmented Generation app. It answers IT policy questions using
only the `.txt` files in `data/golden/knowledge_base/`, and shows which files the answer came from.
If the knowledge base doesn't cover a question, it says so instead of inventing policy.

## Datasets
All datasets live in [`data/`](data/README.md), organised as **golden** (the knowledge base), **static** (fixed reference data), **scenario** (test and demo cases) and **integration** (the integration test scenario catalogue and its coverage status).

## Architecture

```
Browser -> Streamlit UI (app.py) -> services/ -> Chroma vector DB (chroma_db/)
                                              -> Claude Code CLI (claude -p)
```

| Path | Responsibility |
|---|---|
| `app.py` | Streamlit web UI (input, spinner, answer, sources, error display) |
| `cli.py` | Original command-line interface, same pipeline |
| `services/rag_service.py` | Orchestration: validate -> retrieve -> generate |
| `services/document_service.py` | Read `.txt` files, split into paragraph chunks |
| `services/vector_service.py` | Chroma index build/query (thread-safe, one client per process) |
| `services/llm_service.py` | Claude CLI call (tools disabled, prompt via stdin, concurrency-limited) |
| `services/errors.py` | Errors that carry user-safe messages |
| `config/settings.py` | All configuration from environment variables / `.env` |
| `utils/` | Input validation, logging setup |

Embeddings use Chroma's built-in local model (all-MiniLM-L6-v2, ~80 MB, downloaded
once to `~/.cache/chroma`). No embeddings API and no separate Anthropic API integration.

## Local setup

Requires Python 3.12+ and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) installed and logged in.

```bash
cd RAG_PIPELINE_SETUP
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # add requirements-dev.txt to run tests
cp .env.example .env                     # optional; all values have defaults
```

## Running

```bash
streamlit run app.py                     # web UI at http://localhost:8501
python cli.py "How do I reset my password?"   # command line
python -m pytest                         # tests (needs requirements-dev.txt)
```

## Configuration

All optional; defaults are in `.env.example`. Never put credentials in the repo.

| Variable | Default | Meaning |
|---|---|---|
| `KNOWLEDGE_DIR` | `data/golden/knowledge_base` | Folder of `.txt` documents |
| `CHROMA_PATH` | `chroma_db` | Where the vector DB is stored |
| `COLLECTION_NAME` | `helpdesk` | Chroma collection name |
| `TOP_K` / `MIN_SCORE` | `5` / `0.25` | Chunks retrieved / minimum cosine similarity |
| `CLAUDE_BIN` | `claude` | Claude CLI executable |
| `CLAUDE_TIMEOUT` | `120` | Seconds before a Claude call fails |
| `MAX_CONCURRENT_REQUESTS` | `3` | Simultaneous Claude calls |
| `MAX_QUESTION_CHARS` | `500` | Input length limit |
| `INDEX_REFRESH_SECONDS` | `300` | How often the web app re-checks the knowledge base |
| `LOG_LEVEL` | `INFO` | Server log verbosity |

The Claude CLI reads its own authentication (for example `ANTHROPIC_AUTH_TOKEN` /
`ANTHROPIC_BASE_URL`, or a `claude` login) from the process environment. The app never reads or logs them.

Editing files in `data/golden/knowledge_base/` is picked up automatically (within `INDEX_REFRESH_SECONDS`
for the web app, immediately for the CLI). Tip: name the topic inside each paragraph
(for example "The VPN support ticket should include...") because chunks are retrieved individually.

## Security notes

- The Claude CLI runs with **all tools disabled**, in an empty temp directory, with no session
  saved, and receives the prompt over stdin, so a malicious question cannot read server files or run commands.
- Browsers only see friendly messages; tracebacks and CLI errors go to the server log.
- The app has **no login**. Host it behind your company SSO / network controls.

## Deployment (not done; needs approval)

Recommended: **Azure Container Apps** or **App Service for Containers**, running one instance with a
persistent volume mounted at `CHROMA_PATH`, and SSO (Microsoft Entra ID) in front.

- A `Dockerfile` is provided but **has not been built or tested** (Docker wasn't available where it was written).
- **Decision needed before hosting:** the server must be able to authenticate the Claude CLI.
  Decide which credential it uses and supply it as a platform secret / environment variable, never in the image.
- Run a single instance. Multiple instances sharing one Chroma folder are not supported.
- Health check endpoint: `/_stcore/health`.

## Known limitations

- Retrieval matches paragraph by paragraph; a paragraph that doesn't name its topic can be missed.
- Each answer starts a Claude CLI process (a few seconds), capped by `MAX_CONCURRENT_REQUESTS`.
- Source list shows every retrieved file, not only the ones the answer used.

## Webhook integration: issue reports -> incidents

Separate from the helpdesk assistant. An external system posts a signed "issue reported"
event; the receiver validates it and creates an incident in the internal incident system.
Because Streamlit can't host webhook endpoints, this uses FastAPI.

```
Reporting system --(signed POST)--> webhook_receiver (port 8000) --(API key)--> incident system (port 9000)
```

- `webhook_receiver/` is the receiver (`app.py`, `security.py`, `models.py`, `ticket_client.py`, `settings.py`).
- `mock_itsm/` is a **mock** internal incident app (REST API + SQLite). Point `TICKET_API_URL`/`TICKET_API_KEY`
  at a real system later; the only code that talks to it is `webhook_receiver/ticket_client.py` and
  `IssueReport.to_incident()` in `models.py`.

### Run locally
Set the variables from `.env.example` (in `.env` or the environment), then in two terminals:
```bash
uvicorn mock_itsm.app:create_app --factory --port 9000
uvicorn webhook_receiver.app:create_app --factory --port 8000
python scripts/send_sample_webhook.py        # sends one signed sample event
```

### Request contract
`POST /webhooks/issues` with JSON body and headers:
- `X-Webhook-Timestamp`: current Unix time in seconds
- `X-Webhook-Signature`: `sha256=` + hex HMAC-SHA256 of `"<timestamp>.<raw body>"` using `WEBHOOK_SECRET`
  (see `security.sign`). Requests older or newer than 5 minutes are rejected (replay protection).

Body fields: `event_id` (8-100 chars, `A-Za-z0-9._:-`, unique per event), `event_type` (`issue.reported`),
`title` (5-160), `description` (10-5000), `severity` (`critical|high|medium|low` -> priority 1-4),
`reporter` (`name`, `email`), optional `category`, `application`, `reported_at` (ISO 8601),
`metadata` (up to 20 string key/value pairs). Unknown fields are rejected.

| Status | Meaning | Sender action |
|---|---|---|
| 201 | Ticket created (`ticket_id` returned) | Done |
| 200 | `event_id` already ticketed (`status: duplicate`) | Done (safe to resend) |
| 400 / 422 | Bad JSON / failed validation (field list returned) | Fix the payload |
| 401 | Missing, stale or invalid signature | Check secret and clock |
| 413 | Body over 64 KB | Shorten |
| 502 | Incident system unavailable (after 3 attempts with backoff) | **Retry later** with the same `event_id` |
| 500 | Unexpected error | Retry later |

Idempotency comes from `event_id`, which is stored as the incident's `external_id`. Resending is always safe.
Logs (stdout) contain event IDs, ticket numbers and rejection reasons, never secrets, signatures or reporter details.

### Tests
`python -m pytest` includes unit tests, in-process integration tests, and a live end-to-end test that
starts both servers on free ports (covering duplicates, concurrent deliveries, and an incident-system outage with recovery).

### Production notes
Run one receiver behind HTTPS (and ideally an API gateway with rate limiting). Store `WEBHOOK_SECRET` and the
ticket API key as platform secrets. Replace the mock with the real incident system's API (adjust `to_incident()`).

## Agentic mode: the assistant opens tickets itself

When the knowledge base cannot resolve a question, the assistant acts instead of just replying:

```
Question -> search KB -> answer?  yes -> answer with sources
                                  no  -> triage (Claude, JSON) -> real IT issue? yes -> signed webhook -> ticket INC...
                                                                                no  -> standard "contact the helpdesk" message
```
By default the agent **drafts** the ticket and asks the user to confirm it (**Yes, open the ticket** / **No thanks**)
before anything is created. Set `REQUIRE_TICKET_CONFIRMATION=false` for fully automatic ticket creation.

- Claude only **proposes** a triage decision as JSON. `services/agent_service.py` validates it and performs the one
  allow-listed action (`services/ticket_service.py` sends a signed `issue.reported` event to the webhook receiver).
  Claude is never given tools.
- Guardrails: asks the user to confirm before creating anything (the draft is kept server-side and consumed on first
  use, so a double click can't open two tickets) · needs the user's name and email (sidebar) · no ticket for non-IT questions · severity capped at `high`
  (never `critical`) · unusable triage output means no ticket · the same user and question on the same day
  maps to one ticket · failures degrade to the standard message and never show internal details.
- The UI shows an **Agent activity** trail of every step, including the ticket number.
- Needs the webhook receiver and incident app running (see above) and `WEBHOOK_SECRET` set; otherwise the feature is off.
  In production the user's identity should come from SSO rather than the sidebar fields.

### Demo script
1. Start the webhook receiver and incident app, then `streamlit run app.py`. Enter a name and email in the sidebar.
2. *"How do I reset my password?"* -> answered from the knowledge base with sources, no ticket.
3. *"I get permission denied on the shared finance drive and need it for month-end close"* -> the agent shows a draft ticket and asks for confirmation; click **Yes, open the ticket** -> ticket opened, activity trail shown.
4. Ask the same thing again and confirm -> "you already have ticket INC..." (no duplicate). Try **No thanks** on another issue -> no ticket.
5. *"What is the capital of France?"* -> no ticket (not an IT issue).
6. List tickets: `curl -H "X-API-Key: $MOCK_ITSM_API_KEY" http://localhost:9000/api/incidents`
