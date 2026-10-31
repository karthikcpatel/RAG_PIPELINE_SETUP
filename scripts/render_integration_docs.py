"""Render data/integration/README.md from data/integration/integration_scenarios.json.

Usage: python scripts/render_integration_docs.py
The JSON is the source of truth; edit it, then rerun this script. A test fails if the README is stale.
"""

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "integration" / "integration_scenarios.json"
TARGET = ROOT / "data" / "integration" / "README.md"
STATUSES = ["automated", "partial", "manual", "gap"]


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def render(doc):
    scenarios = doc["scenarios"]
    by_status = Counter(s["automation"] for s in scenarios)
    out = [
        "# Integration test scenarios",
        "",
        "<!-- Generated from integration_scenarios.json by scripts/render_integration_docs.py. Do not edit by hand. -->",
        "",
        f"**{len(scenarios)} scenarios** across {len(doc['areas'])} areas and {len(doc['integration_points'])} integration points. "
        "The full detail of every scenario (given, steps, expected results, test data, linked automated tests, notes and "
        "manual evidence) is in [`integration_scenarios.json`](integration_scenarios.json).",
        "",
        "## Scope and approach",
        "",
        "Integration testing checks that the parts work **together** across their boundaries: browser and UI, knowledge base "
        "files, vector database, Claude CLI, webhook receiver, incident system and its database, and configuration. "
        "Unit-level logic is covered separately by the unit tests.",
        "",
        "- **Risk-based priority.** " + " ".join(f"**{k}**: {v}" for k, v in doc["priority_scale"].items()),
        "- **Honest coverage status.** " + " ".join(f"**{k}**: {v}" for k, v in doc["automation_scale"].items()),
        "- **Traceability.** Every `automated` or `partial` scenario lists the tests that cover it; a test checks those tests exist.",
        "",
        "## Test environments",
        "",
        table(["ID", "Name", "Description"], [[e["id"], e["name"], e["description"]] for e in doc["environments"]]),
        "",
        "## Integration points",
        "",
        table(["ID", "From", "To", "Protocol"], [[p["id"], p["from"], p["to"], p["protocol"]] for p in doc["integration_points"]]),
        "",
        "## Coverage summary",
        "",
    ]

    rows = []
    for key, name in doc["areas"].items():
        group = [s for s in scenarios if s["area"] == key]
        counts = Counter(s["automation"] for s in group)
        rows.append([name] + [counts.get(st, 0) for st in STATUSES] + [len(group)])
    rows.append(["**Total**"] + [by_status.get(st, 0) for st in STATUSES] + [len(scenarios)])
    out += [table(["Area"] + STATUSES + ["Total"], rows), ""]

    prio_rows = []
    for pri in doc["priority_scale"]:
        group = [s for s in scenarios if s["priority"] == pri]
        counts = Counter(s["automation"] for s in group)
        prio_rows.append([pri] + [counts.get(st, 0) for st in STATUSES] + [len(group)])
    out += [table(["Priority"] + STATUSES + ["Total"], prio_rows), ""]

    out += ["## Highest-priority gaps", "", "P1 scenarios that are not fully automated, in the order they should be closed:", ""]
    open_p1 = [s for s in scenarios if s["priority"] == "P1" and s["automation"] != "automated"]
    order = {"gap": 0, "partial": 1, "manual": 2}
    open_p1.sort(key=lambda s: (order[s["automation"]], s["id"]))
    out += [table(["ID", "Scenario", "Status", "Why it matters / what is missing"],
                  [[s["id"], s["title"], s["automation"], s.get("notes", "").replace("|", "/")] for s in open_p1]), ""]

    out += [
        "## Entry and exit criteria",
        "",
        "- **Entry:** unit tests pass; the golden knowledge base and static data validate (`tests/test_data_layout.py`); services start with the documented configuration.",
        "- **Exit (release):** every P1 scenario is `automated`, or `manual` with recorded evidence from the release candidate; no open P1 `gap`; "
        "P2 gaps are either closed or accepted in writing; no secret, stack trace or personal data in any response or log.",
        "",
        "## Scenarios by area",
        "",
    ]
    for key, name in doc["areas"].items():
        group = [s for s in scenarios if s["area"] == key]
        out += [f"### {name}", "", table(["ID", "Scenario", "Pri", "Type", "Points", "Status"],
                [[s["id"], s["title"], s["priority"], s["type"], ", ".join(s["integration_points"]), s["automation"]] for s in group]), ""]

    out += [
        "## Maintaining this catalogue",
        "",
        "1. Add or change scenarios in `integration_scenarios.json` (keep IDs stable; new IDs continue the area's numbering).",
        "2. When you add an automated test, list it in `automated_by` as `tests/<file>.py::<test_name>` and set the status.",
        "3. Run `python scripts/render_integration_docs.py` to refresh this page.",
        "4. Run `python -m pytest tests/test_integration_scenarios.py` to validate structure, links to tests and README freshness.",
        "",
    ]
    return "\n".join(out)


def main():
    doc = json.loads(SOURCE.read_text(encoding="utf-8"))
    TARGET.write_text(render(doc), encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)} ({len(doc['scenarios'])} scenarios)")


if __name__ == "__main__":
    main()
