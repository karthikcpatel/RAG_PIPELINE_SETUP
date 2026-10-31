# Datasets

| Folder | Type | Meaning | Change control |
|---|---|---|---|
| `golden/` | Golden | Authoritative source of truth the assistant answers from | Reviewed by the content owner before any edit |
| `static/` | Static | Fixed reference data and assets that do not change at run time | Change only with a code review |
| `scenario/` | Scenario | Data built to exercise one specific situation (happy path, edge case, failure) | Add freely; keep each case small and named |
| `integration/` | Scenario (integration) | The catalogue of integration test scenarios, with coverage status and traceability to automated tests | QA owns it; regenerate the README after edits |

## golden/
| Path | Used by |
|---|---|
| `golden/knowledge_base/application_compliance.txt` | Application access policy |
| `golden/knowledge_base/internet_connectivity.txt` | VPN troubleshooting policy (despite the file name) |
| `golden/knowledge_base/password_policy.txt` | Password reset policy |

The app reads this folder (`KNOWLEDGE_DIR`, default `data/golden/knowledge_base`) and indexes every `.txt` file in it.
**Only `.txt` policy files belong here**; anything else would be indexed or confuse retrieval (a test enforces this).
Write each paragraph so it names its topic, because paragraphs are retrieved individually.
Owner / version / review date: *to be assigned*.

## static/
| Path | Used by |
|---|---|
| `static/severity_priority.json` | `webhook_receiver/models.py`: severity to incident priority (critical=1 ... low=4) |
| `static/ticket_categories.json` | `services/agent_service.py`: allowed ticket categories |
| `static/readme.txt` | Training walkthrough; not used by the app |

Not stored here but also static: the embedding model (`~/.cache/chroma/onnx_models/all-MiniLM-L6-v2`),
behaviour constants in `config/settings.py` (`TOP_K`, `MIN_SCORE`, the "no information" message),
and the fake test credentials in `tests/conftest.py`.

## scenario/
| Path | Used by |
|---|---|
| `scenario/webhook_payloads.json` | Valid event, 14 invalid cases, and the sample event (`tests/`, `scripts/send_sample_webhook.py`) |
| `scenario/triage_replies.json` | Well-formed and untrustworthy triage replies (`tests/test_agent.py`) |
| `scenario/demo_scenarios.json` | Demo and manual-test questions with expected outcomes (documentation; not loaded by code) |

Other scenario data stays inside the tests because it is tied to the code under test:
signature cases, downstream failure responses, per-test temporary knowledge bases.

## integration/
| Path | Purpose |
|---|---|
| `integration/integration_scenarios.json` | Source of truth: 101 scenarios (given, steps, expected results, test data, linked tests, notes, manual evidence) |
| `integration/README.md` | Generated QA view: scope, environments, integration points, coverage summary, P1 gaps, scenario tables |

Edit the JSON, then run `python scripts/render_integration_docs.py`. `tests/test_integration_scenarios.py` fails if a linked
test no longer exists, a status contradicts its links, or the README is stale.

## Generated data (not datasets, not stored here)
`chroma_db/` (vector index, rebuilt from `golden/`), `mock_itsm.db` (demo tickets), `.env` (secrets). All are gitignored.
