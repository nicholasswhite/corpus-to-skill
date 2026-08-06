"""Focused offline tests for the domain-neutral claim framework core."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import claim_framework
from claim_framework.jsonio import (
    JsonContractError,
    RECORD_TYPES,
    UnsupportedSchemaVersion,
    canonical_dumps,
    dumps_jsonl,
    loads_jsonl,
    loads_record,
    sha256_text,
    stable_id,
    to_plain,
)
from claim_framework.provenance import (
    MissingSpanError,
    OrphanedReferenceError,
    ProvenanceChecksumMismatch,
    ProvenanceResolver,
)
from claim_framework.records import (
    CanonicalClaim,
    ClaimRelation,
    ClaimSemantics,
    ClassificationInfo,
    ContractError,
    CorpusManifest,
    DomainProfile,
    EVIDENCE_DIRECTIONS,
    EVIDENCE_TYPES,
    EvidenceRecord,
    ExtractionInfo,
    Locator,
    MetricDefinition,
    ScopeAnalysis,
    SourceClaim,
    SourceEntry,
    SourceRecord,
    SourceSpan,
    SynthesisArtifact,
    SynthesisAssertion,
)
from claim_framework.store import (
    ArtifactChecksumMismatch,
    ArtifactStore,
    UnsafeArtifactPath,
)
from claim_framework.synthesis import synthesize


EXTRACTED_TEXT = "Bounded queues prevent overload.\nRetries need limits.\n"
ASSERTION_TEXT = "Bounded queues prevent overload."


def _source_record() -> SourceRecord:
    return SourceRecord(
        id="source-1",
        corpus_id="corpus-1",
        title="Synthetic operations note",
        creators=("Example Author",),
        media_type="text/markdown",
        source_ref="sources/one.md",
        content_checksum=sha256_text(EXTRACTED_TEXT),
        parser_name="fixture-text",
        parser_version="1",
        ingestion_run_id="run-ingest",
        extracted_text_ref="extracted/source-1.txt",
        extracted_text_checksum=sha256_text(EXTRACTED_TEXT),
    )


def _span(checksum: str = None) -> SourceSpan:
    return SourceSpan(
        source_id="source-1",
        locator=Locator(start_offset=0, end_offset=len(ASSERTION_TEXT)),
        excerpt=ASSERTION_TEXT,
        excerpt_checksum=checksum or sha256_text(ASSERTION_TEXT),
    )


def _source_claim(span: SourceSpan = None) -> SourceClaim:
    return SourceClaim(
        id="source-claim-1",
        source_id="source-1",
        source_spans=(span or _span(),),
        original_assertion=ASSERTION_TEXT,
        proposition="bounded queues prevent overload",
        claim_type="descriptive",
        semantics=ClaimSemantics(
            subject="bounded queues",
            relation="prevent",
            object_or_value="overload",
            polarity="positive",
            definitions={"bounded queue": "a queue with a fixed capacity"},
        ),
        extraction=ExtractionInfo("run-extract", extraction_confidence=1.0),
    )


def _canonical_claim() -> CanonicalClaim:
    return CanonicalClaim(
        id="canonical-1",
        canonical_proposition="Bounded queues prevent overload.",
        claim_type="descriptive",
        semantics=_source_claim().semantics,
        member_source_claim_ids=("source-claim-1",),
        preserved_variants=(ASSERTION_TEXT,),
        normalization_rationale="Exact proposition and scope match.",
        normalization_run_id="run-normalize",
    )


def _assertion(**changes) -> SynthesisAssertion:
    values = {
        "id": "assertion-1",
        "text": "Use bounded queues to constrain overload.",
        "status": "consensus",
        "canonical_claim_ids": ("canonical-1",),
        "supporting_source_claim_ids": ("source-claim-1",),
        "opposing_source_claim_ids": (),
        "rationale": "The assertion is a direct rendering of the canonical claim.",
    }
    values.update(changes)
    return SynthesisAssertion(**values)


def test_nested_record_round_trip_and_deterministic_jsonl():
    claim = _source_claim()
    encoded = canonical_dumps(claim)

    assert loads_record(encoded, SourceClaim) == claim
    assert loads_record(encoded, "SourceClaim") == claim
    assert canonical_dumps({"z": 1, "a": {"y": 2, "b": 3}}) == canonical_dumps(
        {"a": {"b": 3, "y": 2}, "z": 1}
    )

    ledger = dumps_jsonl((claim, replace(claim, id="source-claim-2")))
    decoded = loads_jsonl(ledger, SourceClaim)
    assert tuple(item.id for item in decoded) == ("source-claim-1", "source-claim-2")
    assert ledger.endswith("\n")


def test_stable_ids_use_canonical_content_not_mapping_insertion_order():
    left = {"proposition": "bounded queues", "scope": {"b": 2, "a": 1}}
    right = {"scope": {"a": 1, "b": 2}, "proposition": "bounded queues"}

    assert stable_id("claim", left) == stable_id("claim", right)
    assert stable_id("claim", left).startswith("claim_")
    assert stable_id("claim", left) != stable_id("claim", {**left, "extra": True})


def test_strict_schema_and_unknown_field_rejection_is_nested():
    assertion = _assertion()
    artifact = SynthesisArtifact(
        id="synthesis-1",
        corpus_id="corpus-1",
        topic_clusters=(),
        assertions=(assertion,),
        disputes=(),
        unresolved_questions=(),
        coverage_notes=(),
        run_id="run-synthesis",
    )
    payload = to_plain(artifact)
    payload["assertions"][0]["schema_version"] = "2.0"

    with pytest.raises(UnsupportedSchemaVersion, match="assertions"):
        loads_record(json.dumps(payload), SynthesisArtifact)

    claim_payload = to_plain(_source_claim())
    claim_payload["unexpected"] = True
    with pytest.raises(JsonContractError, match="unknown field"):
        loads_record(json.dumps(claim_payload), SourceClaim)

    del claim_payload["unexpected"]
    del claim_payload["schema_version"]
    with pytest.raises(JsonContractError, match="schema_version is required"):
        loads_record(json.dumps(claim_payload), SourceClaim)

    with pytest.raises(JsonContractError, match="unregistered record type"):
        loads_record(canonical_dumps(Locator(page=1)), Locator)


def test_manifest_validation_remains_domain_neutral():
    manifest = CorpusManifest(
        id="corpus-1",
        name="Operations notes",
        source_entries=(
            SourceEntry("source-1", "sources/one.md"),
            SourceEntry("source-2", "sources/two.md"),
        ),
        configuration_ref="config-v1",
        created_at="2026-08-05T20:00:00Z",
    )
    assert loads_record(canonical_dumps(manifest), CorpusManifest) == manifest
    with pytest.raises(ContractError, match="at least two"):
        replace(manifest, source_entries=(manifest.source_entries[0],))


def test_extraction_safety_findings_are_separate_from_evidence_record_refs():
    claim = replace(
        _source_claim(),
        extraction=ExtractionInfo(
            "run-extract",
            review_status="rejected",
            extraction_confidence=0.0,
            safety_finding_ids=["prompt.ignore_previous"],
        ),
        evidence_refs=("evidence-1",),
    )

    assert claim.extraction.safety_finding_ids == ("prompt.ignore_previous",)
    assert claim.evidence_refs == ("evidence-1",)
    assert loads_record(canonical_dumps(claim), SourceClaim) == claim
    with pytest.raises(ContractError, match="safety_finding_ids must be unique"):
        replace(
            claim.extraction,
            safety_finding_ids=("prompt.ignore_previous", "prompt.ignore_previous"),
        )
    with pytest.raises(ContractError, match="must reference EvidenceRecord IDs"):
        replace(claim, evidence_refs=("safety:prompt.ignore_previous",))


def test_domain_profile_is_versioned_declarative_and_round_trips():
    profile = DomainProfile(
        id="incident-response",
        version="1.0.0",
        vocabulary_ref="profiles/incident-response/vocabulary-v1.json",
        condition_fields=("incident_phase", "asset_criticality"),
        metric_definitions=(
            MetricDefinition(
                id="containment_time",
                description="Elapsed time from confirmation to containment.",
                unit="minutes",
                metadata={"value_type": "number"},
            ),
        ),
        evidence_rubric_refs=("rubrics/operational-evidence@1.0",),
        predictive_eligibility_rules_ref="rules/predictive-eligibility@1.0",
        outcome_resolver_refs=("resolvers/incident-outcomes@1.0",),
    )

    assert loads_record(canonical_dumps(profile), DomainProfile) == profile
    with pytest.raises(TypeError):
        profile.metric_definitions[0].metadata["value_type"] = "mutated"


def test_evidence_record_is_versioned_traceable_immutable_and_registered():
    evidence = EvidenceRecord(
        id="evidence-1",
        source_claim_id="source-claim-1",
        evidence_type="experimental",
        direction="supports",
        description="A controlled synthetic fixture supports the assertion.",
        cited_source_refs=["fixture://experiment-1"],
        source_spans=[_span()],
        independence_group="fixture-study-1",
        limitations=["Synthetic fixture; no external validity is claimed."],
    )

    assert loads_record(canonical_dumps(evidence), EvidenceRecord) == evidence
    assert loads_record(canonical_dumps(evidence), "EvidenceRecord") == evidence
    assert RECORD_TYPES["EvidenceRecord"] is EvidenceRecord
    assert claim_framework.EvidenceRecord is EvidenceRecord
    assert claim_framework.EVIDENCE_TYPES == EVIDENCE_TYPES
    assert claim_framework.EVIDENCE_DIRECTIONS == EVIDENCE_DIRECTIONS
    assert isinstance(evidence.source_spans, tuple)
    assert isinstance(evidence.cited_source_refs, tuple)
    assert isinstance(evidence.limitations, tuple)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"evidence_type": "universal_truth"}, "evidence_type must be one of"),
        ({"direction": "proves"}, "direction must be one of"),
        ({"source_spans": ()}, "at least one source span"),
        ({"source_spans": (_span(), _span())}, "source_spans must be unique"),
        ({"description": ""}, "description must be a non-empty string"),
        (
            {"cited_source_refs": ("fixture://one", "fixture://one")},
            "cited_source_refs must be unique",
        ),
        (
            {"limitations": ("limited", "limited")},
            "limitations must be unique",
        ),
    ),
)
def test_evidence_record_rejects_invalid_or_untraceable_values(changes, message):
    values = {
        "id": "evidence-invalid",
        "source_claim_id": "source-claim-1",
        "evidence_type": "assertion",
        "direction": "contextualizes",
        "source_spans": (_span(),),
    }
    values.update(changes)

    with pytest.raises(ContractError, match=message):
        EvidenceRecord(**values)


def test_artifact_store_writes_atomically_and_round_trips_records(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    first_checksum = store.write_text("sources/note.txt", "first")
    second_checksum = store.write_text("sources/note.txt", "second")

    assert first_checksum == sha256_text("first")
    assert second_checksum == sha256_text("second")
    assert store.read_text("sources/note.txt", second_checksum) == "second"
    assert not list((tmp_path / "artifacts" / "sources").glob("*.tmp"))

    claim = _source_claim()
    store.write_json("claims/one.json", claim)
    store.write_jsonl("claims/ledger.jsonl", (claim, replace(claim, id="source-claim-2")))
    assert store.read_json("claims/one.json", SourceClaim) == claim
    assert [item.id for item in store.read_jsonl("claims/ledger.jsonl", SourceClaim)] == [
        "source-claim-1",
        "source-claim-2",
    ]

    with pytest.raises(ArtifactChecksumMismatch):
        store.read_text("sources/note.txt", sha256_text("not second"))
    with pytest.raises(UnsafeArtifactPath):
        store.write_text("../escape.txt", "no")


def test_provenance_resolves_and_verifies_offsets_and_checksums(tmp_path):
    store = ArtifactStore(tmp_path)
    source = _source_record()
    store.write_text(source.extracted_text_ref, EXTRACTED_TEXT)
    claim = _source_claim()
    canonical = _canonical_claim()
    resolver = ProvenanceResolver((source,), (claim,), (canonical,), store)

    trace = resolver.resolve_assertion(_assertion())

    assert trace.assertion_id == "assertion-1"
    assert trace.source_claim_ids == ("source-claim-1",)
    assert trace.spans[0].resolved_excerpt == ASSERTION_TEXT
    assert trace.run_ids == ("run-extract", "run-normalize")


def test_provenance_rejects_orphans_missing_spans_and_tampering(tmp_path):
    store = ArtifactStore(tmp_path)
    source = _source_record()
    store.write_text(source.extracted_text_ref, EXTRACTED_TEXT)
    claim = _source_claim()
    canonical = _canonical_claim()

    resolver = ProvenanceResolver((source,), (claim,), (canonical,), store)
    with pytest.raises(OrphanedReferenceError, match="missing canonical"):
        resolver.resolve_assertion(_assertion(canonical_claim_ids=("missing-canonical",)))

    with pytest.raises(ContractError, match="at least one source span"):
        replace(claim, source_spans=())

    page_only_claim = _source_claim(
        SourceSpan(source_id="source-1", locator=Locator(page=999999))
    )
    with pytest.raises(MissingSpanError, match="no verifiable"):
        ProvenanceResolver(
            (source,), (page_only_claim,), (canonical,), store
        ).resolve_assertion(_assertion())

    wrong_excerpt_claim = _source_claim(_span(sha256_text("different excerpt")))
    with pytest.raises(ProvenanceChecksumMismatch, match="excerpt checksum"):
        ProvenanceResolver((source,), (wrong_excerpt_claim,), (canonical,), store).resolve_assertion(
            _assertion()
        )

    store.write_text(source.extracted_text_ref, "tampered")
    with pytest.raises(ProvenanceChecksumMismatch, match="extracted text"):
        ProvenanceResolver((source,), (claim,), (canonical,), store).resolve_assertion(_assertion())


def test_synthesis_support_expansion_preserves_provenance_lineage(tmp_path):
    store = ArtifactStore(tmp_path)
    first_source = _source_record()
    second_source = replace(
        first_source,
        id="source-2",
        source_ref="sources/two.md",
        extracted_text_ref="extracted/source-2.txt",
    )
    store.write_text(first_source.extracted_text_ref, EXTRACTED_TEXT)
    store.write_text(second_source.extracted_text_ref, EXTRACTED_TEXT)

    first_claim = _source_claim()
    second_claim = replace(
        first_claim,
        id="source-claim-2",
        source_id=second_source.id,
        source_spans=(replace(_span(), source_id=second_source.id),),
    )
    first_canonical = _canonical_claim()
    second_canonical = replace(
        first_canonical,
        id="canonical-2",
        canonical_proposition=second_claim.proposition,
        member_source_claim_ids=(second_claim.id,),
        preserved_variants=(second_claim.original_assertion,),
    )
    agreement = ClaimRelation(
        id="relation-agreement",
        left_claim_id=first_canonical.id,
        right_claim_id=second_canonical.id,
        relation_type="agreement",
        directionality="symmetric",
        scope_analysis=ScopeAnalysis(),
        conflict_dimensions=(),
        rationale="The conclusions agree under the recorded scope.",
        supporting_source_claim_ids=(first_claim.id, second_claim.id),
        classification=ClassificationInfo("run-relate"),
    )

    artifact = synthesize(
        "corpus-1",
        (second_canonical, first_canonical),
        (second_claim, first_claim),
        (agreement,),
        "run-synthesis",
    )
    traces = ProvenanceResolver(
        (second_source, first_source),
        (second_claim, first_claim),
        (second_canonical, first_canonical),
        store,
    ).validate_synthesis(artifact)

    assert len(traces) == 2
    assert all(
        set(assertion.canonical_claim_ids)
        == {first_canonical.id, second_canonical.id}
        for assertion in artifact.assertions
    )
    assert all(
        set(trace.source_claim_ids) == {first_claim.id, second_claim.id}
        and all(span.resolved_excerpt == ASSERTION_TEXT for span in trace.spans)
        for trace in traces
    )


def test_json_shaped_contract_payloads_are_deeply_immutable():
    definitions = {"queue": {"properties": ["bounded", "fifo"]}}
    semantics = ClaimSemantics(
        subject="queues",
        relation="have",
        object_or_value={"capacity": [10, 20]},
        definitions=definitions,
        outcome={"metric": "rejections", "window": {"days": [1, 7]}},
    )
    definitions["queue"]["properties"].append("mutated-after-construction")

    assert semantics.definitions["queue"]["properties"] == ("bounded", "fifo")
    with pytest.raises(TypeError):
        semantics.definitions["new"] = "value"
    with pytest.raises(TypeError):
        semantics.outcome["window"]["days"] = (30,)

    claim = replace(_source_claim(), semantics=semantics)
    assert loads_record(canonical_dumps(claim), SourceClaim) == claim


def test_importing_framework_does_not_import_book_orchestration():
    script = """
import sys
import claim_framework
import claim_framework.jsonio
import claim_framework.ports
import claim_framework.provenance
import claim_framework.store
assert 'book_to_skill' not in sys.modules, sorted(
    name for name in sys.modules if name.startswith('book_to_skill')
)
assert 'claim_framework.evaluation' not in sys.modules
assert 'claim_framework.prediction' not in sys.modules
from claim_framework import evaluate
assert callable(evaluate)
assert 'claim_framework.evaluation' in sys.modules
assert 'claim_framework.prediction' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
