"""Locate and load the JSON files under data/ (static reference data, scenario data)."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(kind, name):
    """Load data/<kind>/<name>, where kind is "static" or "scenario"."""
    with open(DATA_DIR / kind / name, encoding="utf-8") as handle:
        return json.load(handle)
