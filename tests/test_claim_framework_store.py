"""Offline contract tests for the immutable logical-record store."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import pytest

import claim_framework.store as store_module
from claim_framework.jsonio import canonical_dumps, sha256_text
from claim_framework.records import (
    EligibilityResult,
    EvidenceRecord,
    Locator,
    SourceSpan,
)
from claim_framework.store import (
    ArtifactStore,
    ClaimStore,
    InvalidRecordId,
    RecordIdCollision,
    RecordNotFound,
    RecordQueryError,
    RecordStoreCorruption,
    UnsupportedRecordType,
)


def _evidence(
    record_id="evidence-1",
    *,
    source_claim_id="source-claim-1",
    direction="supports",
    description="Synthetic evidence fixture.",
):
    excerpt = f"Evidence excerpt for {record_id}."
    return EvidenceRecord(
        id=record_id,
        source_claim_id=source_claim_id,
        evidence_type="example",
        direction=direction,
        description=description,
        cited_source_refs=(f"fixture://{record_id}",),
        source_spans=(
            SourceSpan(
                source_id="source-1",
                locator=Locator(start_offset=0, end_offset=len(excerpt)),
                excerpt=excerpt,
                excerpt_checksum=sha256_text(excerpt),
            ),
        ),
        limitations=("Synthetic fixture only.",),
    )


def test_claim_store_saves_gets_idempotently_and_uses_only_hashed_ids(tmp_path):
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    store = ClaimStore(artifact_store)
    record = _evidence("../../unsafe/id-with-unicode-α")

    first_checksum = store.save(record)
    second_checksum = store.save(record)
    stored_path = store.record_path(EvidenceRecord, record.id)
    digest = sha256_text(record.id)

    assert first_checksum == second_checksum
    assert first_checksum == sha256_text(canonical_dumps(record) + "\n")
    assert store.root == artifact_store.root
    assert stored_path.is_file()
    assert stored_path.name == f"{digest}.json"
    assert stored_path.parent.name == digest[:2]
    assert record.id not in stored_path.as_posix()
    assert stored_path.is_relative_to(store.root)
    assert store.exists(EvidenceRecord, record.id)
    assert store.exists("EvidenceRecord", record.id)
    assert store.get(EvidenceRecord, record.id) == record
    assert store.get("EvidenceRecord", record.id) == record
    assert not (tmp_path / "unsafe").exists()


def test_claim_store_query_is_exact_typed_and_deterministically_ordered(tmp_path):
    store = ClaimStore(tmp_path / "records")
    records = (
        _evidence("evidence-z", source_claim_id="claim-shared"),
        _evidence(
            "evidence-a",
            source_claim_id="claim-shared",
            direction="challenges",
        ),
        _evidence("evidence-m", source_claim_id="claim-other"),
    )
    for record in records:
        store.save(record)

    assert tuple(record.id for record in store.query(EvidenceRecord)) == (
        "evidence-a",
        "evidence-m",
        "evidence-z",
    )
    assert tuple(
        record.id
        for record in store.query(
            "EvidenceRecord", source_claim_id="claim-shared"
        )
    ) == ("evidence-a", "evidence-z")
    assert store.query(EvidenceRecord, direction="unknown") == ()
    with pytest.raises(RecordQueryError, match="unknown query field"):
        store.query(EvidenceRecord, universal_truth=True)


def test_claim_store_rejects_changed_content_without_overwriting(tmp_path):
    store = ClaimStore(tmp_path / "records")
    original = _evidence("immutable-id", description="Original content.")
    changed = replace(original, description="Changed content.")
    store.save(original)
    stored_path = store.record_path(EvidenceRecord, original.id)
    before = stored_path.read_bytes()

    with pytest.raises(RecordIdCollision, match="immutable record collision"):
        store.save(changed)

    assert stored_path.read_bytes() == before
    assert store.get(EvidenceRecord, original.id) == original


def test_claim_store_detects_hashed_id_collision_without_overwriting(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        store_module, "_record_id_digest", lambda record_id: "0" * 64
    )
    store = ClaimStore(tmp_path / "records")
    first = _evidence("first-id")
    second = _evidence("second-id")
    store.save(first)
    stored_path = store.record_path(EvidenceRecord, first.id)
    before = stored_path.read_bytes()

    with pytest.raises(RecordIdCollision, match="distinct record IDs"):
        store.save(second)

    assert stored_path.read_bytes() == before
    assert store.get(EvidenceRecord, first.id) == first


def test_claim_store_concurrent_instances_cannot_overwrite_one_identity(tmp_path):
    root = tmp_path / "records"
    first_store = ClaimStore(root)
    second_store = ClaimStore(root)
    first = _evidence("raced-id", description="First candidate.")
    second = _evidence("raced-id", description="Second candidate.")
    barrier = threading.Barrier(2)

    def attempt(store, record):
        barrier.wait(timeout=5)
        try:
            store.save(record)
        except RecordIdCollision:
            return "collision"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(attempt, first_store, first),
            executor.submit(attempt, second_store, second),
        )
        outcomes = tuple(future.result(timeout=10) for future in futures)

    assert sorted(outcomes) == ["collision", "saved"]
    stored = first_store.get(EvidenceRecord, "raced-id")
    assert stored in (first, second)
    assert not list(root.rglob("*.tmp"))


def test_claim_store_reports_missing_unsupported_and_invalid_requests(tmp_path):
    store = ClaimStore(tmp_path / "records")

    with pytest.raises(RecordNotFound, match="record not found"):
        store.get(EvidenceRecord, "missing")
    with pytest.raises(InvalidRecordId, match="non-empty"):
        store.get(EvidenceRecord, " ")
    with pytest.raises(UnsupportedRecordType, match="no immutable id"):
        store.save(EligibilityResult("claim-1", "eligible"))

    @dataclass(frozen=True)
    class UnregisteredRecord:
        id: str

    with pytest.raises(UnsupportedRecordType, match="unregistered record type"):
        store.save(UnregisteredRecord("record-1"))
    with pytest.raises(UnsupportedRecordType, match="unregistered record type"):
        store.get("MadeUpRecord", "record-1")


def test_claim_store_detects_tampered_identity_and_noncanonical_content(tmp_path):
    store = ClaimStore(tmp_path / "records")
    record = _evidence("record-1")
    store.save(record)
    stored_path = store.record_path(EvidenceRecord, record.id)

    stored_path.write_text(
        canonical_dumps(replace(record, id="different-id")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RecordStoreCorruption, match="requested hashed identity"):
        store.get(EvidenceRecord, record.id)

    stored_path.write_text(
        "  " + canonical_dumps(record) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RecordStoreCorruption, match="canonical deterministic JSON"):
        store.get(EvidenceRecord, record.id)

    stored_path.write_bytes(b"\xff")
    with pytest.raises(RecordStoreCorruption, match="cannot be read safely"):
        store.get(EvidenceRecord, record.id)
