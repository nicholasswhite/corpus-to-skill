"""Golden compatibility checks for every persisted claim-framework record."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claim_framework.jsonio import canonical_dumps, loads_record, to_plain
from claim_framework.records import PERSISTED_RECORD_TYPES, SCHEMA_VERSION


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "schema_v1" / "records.json"
)


def _golden_records():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_v1_golden_fixture_type_set_exactly_matches_persisted_registry():
    fixtures = _golden_records()
    registered = {record_type.__name__ for record_type in PERSISTED_RECORD_TYPES}

    assert set(fixtures) == registered


@pytest.mark.parametrize(
    "record_type",
    PERSISTED_RECORD_TYPES,
    ids=lambda record_type: record_type.__name__,
)
def test_v1_golden_record_loads_and_canonical_round_trips(record_type):
    payload = _golden_records()[record_type.__name__]

    assert payload["schema_version"] == SCHEMA_VERSION == "1.0"
    record = loads_record(canonical_dumps(payload), record_type)

    assert to_plain(record) == payload
    assert canonical_dumps(record) == canonical_dumps(payload)
    assert loads_record(canonical_dumps(record), record_type) == record
