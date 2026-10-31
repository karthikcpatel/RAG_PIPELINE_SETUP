"""Validates the integration scenario catalogue so it stays accurate and traceable."""

import importlib.util
import json
import re
from collections import Counter

import pytest

from utils.data_files import DATA_DIR

ROOT = DATA_DIR.parent
SOURCE = DATA_DIR / "integration" / "integration_scenarios.json"
README = DATA_DIR / "integration" / "README.md"

DOC = json.loads(SOURCE.read_text(encoding="utf-8"))
SCENARIOS = DOC["scenarios"]
REQUIRED = {"id", "area", "title", "priority", "type", "integration_points", "given", "steps",
            "expected", "automation", "automated_by", "test_data"}


def test_catalogue_is_not_empty_and_ids_are_unique():
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) >= 50 and len(ids) == len(set(ids))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_is_well_formed(scenario):
    assert REQUIRED <= set(scenario), REQUIRED - set(scenario)
    assert re.fullmatch(r"INT-[A-Z0-9]+-\d{2}", scenario["id"])
    assert scenario["id"].split("-")[1] == scenario["area"] and scenario["area"] in DOC["areas"]
    assert scenario["priority"] in DOC["priority_scale"]
    assert scenario["automation"] in DOC["automation_scale"]
    known_points = {p["id"] for p in DOC["integration_points"]}
    assert scenario["integration_points"] and set(scenario["integration_points"]) <= known_points
    assert scenario["steps"] and scenario["expected"] and scenario["given"].strip()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_automation_status_matches_linked_tests(scenario):
    linked = scenario["automated_by"]
    if scenario["automation"] in ("automated", "partial"):
        assert linked, "automated/partial scenarios must name the tests that cover them"
    if scenario["automation"] == "gap":
        assert not linked, "a gap has no covering tests"
    if scenario["automation"] in ("partial", "gap", "manual") and not linked:
        assert scenario.get("notes") or scenario.get("manual_evidence"), "explain what is missing or how it was verified"
    if scenario["automation"] == "manual":
        assert scenario.get("manual_evidence"), "manual scenarios must record evidence"


def test_every_linked_test_exists():
    missing = []
    for scenario in SCENARIOS:
        for ref in scenario["automated_by"]:
            path, _, name = ref.partition("::")
            file = ROOT / path
            if not file.is_file() or f"def {name}(" not in file.read_text(encoding="utf-8"):
                missing.append((scenario["id"], ref))
    assert not missing, missing


def test_test_data_references_exist():
    scenario_ids = {s["id"] for s in json.loads((DATA_DIR / "scenario" / "demo_scenarios.json").read_text())["scenarios"]}
    bad = []
    for scenario in SCENARIOS:
        for ref in scenario["test_data"]:
            file, _, anchor = ref.partition("#")
            if not (ROOT / file).is_file():
                bad.append((scenario["id"], ref))
            elif file.endswith("demo_scenarios.json") and anchor and anchor not in scenario_ids:
                bad.append((scenario["id"], ref))
            elif anchor and not file.endswith("demo_scenarios.json") and anchor not in json.loads((ROOT / file).read_text()):
                bad.append((scenario["id"], ref))
    assert not bad, bad


def test_priorities_stay_meaningful():
    """P1 must remain a minority, otherwise the priority carries no information."""
    counts = Counter(s["priority"] for s in SCENARIOS)
    assert counts["P1"] / len(SCENARIOS) <= 0.5


def test_readme_is_up_to_date():
    spec = importlib.util.spec_from_file_location("render_integration_docs", ROOT / "scripts" / "render_integration_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert README.read_text(encoding="utf-8") == module.render(DOC), \
        "README is stale: run python scripts/render_integration_docs.py"
