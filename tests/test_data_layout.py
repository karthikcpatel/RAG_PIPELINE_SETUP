"""Guards the structure and integrity of the datasets under data/."""

import json

from config import settings
from services import document_service
from utils.data_files import DATA_DIR, load_json
from utils.validation import validate_question
from webhook_receiver.models import IssueReport, Severity

GOLDEN_KB = DATA_DIR / "golden" / "knowledge_base"


def test_dataset_folders_exist():
    for kind in ("golden", "static", "scenario", "integration"):
        assert (DATA_DIR / kind).is_dir()


def test_golden_knowledge_base_contains_only_usable_text_policies():
    files = sorted(GOLDEN_KB.iterdir())
    assert files, "the golden knowledge base is empty"
    # Anything else here (notes, walkthroughs, JSON) would be indexed or confuse retrieval.
    assert all(f.suffix == ".txt" for f in files), [f.name for f in files if f.suffix != ".txt"]
    for f in files:
        assert len(f.read_text(encoding="utf-8").strip()) >= 50, f"{f.name} is empty or too short"


def test_app_reads_the_golden_knowledge_base_by_default(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)
    assert settings._path("KNOWLEDGE_DIR", "data/golden/knowledge_base") == GOLDEN_KB
    assert set(document_service.load_documents(GOLDEN_KB)) == {f.name for f in GOLDEN_KB.glob("*.txt")}


def test_static_severity_map_covers_every_severity_once():
    priorities = load_json("static", "severity_priority.json")
    assert set(priorities) == {s.value for s in Severity}
    assert sorted(priorities.values()) == [1, 2, 3, 4]


def test_static_categories_are_valid():
    categories = load_json("static", "ticket_categories.json")
    assert "general" in categories and len(categories) == len(set(categories))
    assert all(isinstance(c, str) and c == c.strip().lower() and c for c in categories)


def test_scenario_files_are_valid_json_and_consistent():
    for path in (DATA_DIR / "scenario").glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    IssueReport.model_validate(load_json("scenario", "webhook_payloads.json")["valid"])  # happy path stays valid


def test_demo_scenarios_are_well_formed():
    scenarios = load_json("scenario", "demo_scenarios.json")["scenarios"]
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids))
    for scenario in scenarios:
        assert {"question", "expected_outcome", "expected_ticket"} <= set(scenario)
        validate_question(scenario["question"])  # must be an acceptable input
