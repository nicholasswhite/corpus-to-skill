"""Trace synthesis assertions back to checksum-verified source spans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from claim_framework.jsonio import sha256_text
from claim_framework.records import (
    CanonicalClaim,
    SourceClaim,
    SourceRecord,
    SourceSpan,
    SynthesisArtifact,
    SynthesisAssertion,
)
from claim_framework.store import ArtifactStore, StoreError


class ProvenanceError(ValueError):
    """Base error for incomplete or invalid provenance."""


class OrphanedReferenceError(ProvenanceError):
    """Raised when a derived artifact references a record that does not exist."""


class MissingSpanError(ProvenanceError):
    """Raised when a source claim has no resolvable source span."""


class ProvenanceChecksumMismatch(ProvenanceError):
    """Raised when persisted source text or an excerpt fails verification."""


class InvalidSpanError(ProvenanceError):
    """Raised when a locator falls outside the verified extracted text."""


@dataclass(frozen=True)
class ResolvedSpan:
    source_record_id: str
    source_claim_id: str
    span: SourceSpan
    resolved_excerpt: Optional[str]


@dataclass(frozen=True)
class ProvenanceTrace:
    assertion_id: str
    canonical_claim_ids: Tuple[str, ...]
    source_claim_ids: Tuple[str, ...]
    spans: Tuple[ResolvedSpan, ...]
    run_ids: Tuple[str, ...]


def _unique_index(records: Iterable[object], label: str) -> Dict[str, object]:
    index: Dict[str, object] = {}
    for record in records:
        record_id = getattr(record, "id", None)
        if not isinstance(record_id, str) or not record_id:
            raise ProvenanceError(f"{label} contains a record without an id")
        if record_id in index:
            raise ProvenanceError(f"duplicate {label} id: {record_id}")
        index[record_id] = record
    return index


class ProvenanceResolver:
    """Resolve and verify an assertion's canonical/source-claim lineage."""

    def __init__(
        self,
        source_records: Iterable[SourceRecord],
        source_claims: Iterable[SourceClaim],
        canonical_claims: Iterable[CanonicalClaim],
        store: ArtifactStore,
    ) -> None:
        self._source_records = _unique_index(source_records, "source record")
        self._source_claims = _unique_index(source_claims, "source claim")
        self._canonical_claims = _unique_index(canonical_claims, "canonical claim")
        self._store = store

    def _source_record(self, source_id: str) -> SourceRecord:
        try:
            record = self._source_records[source_id]
        except KeyError as exc:
            raise OrphanedReferenceError(f"missing source record: {source_id}") from exc
        if not isinstance(record, SourceRecord):
            raise ProvenanceError(f"invalid source record index entry: {source_id}")
        return record

    def _source_claim(self, claim_id: str) -> SourceClaim:
        try:
            claim = self._source_claims[claim_id]
        except KeyError as exc:
            raise OrphanedReferenceError(f"missing source claim: {claim_id}") from exc
        if not isinstance(claim, SourceClaim):
            raise ProvenanceError(f"invalid source claim index entry: {claim_id}")
        return claim

    def _canonical_claim(self, claim_id: str) -> CanonicalClaim:
        try:
            claim = self._canonical_claims[claim_id]
        except KeyError as exc:
            raise OrphanedReferenceError(f"missing canonical claim: {claim_id}") from exc
        if not isinstance(claim, CanonicalClaim):
            raise ProvenanceError(f"invalid canonical claim index entry: {claim_id}")
        return claim

    def _verified_text(self, source: SourceRecord) -> str:
        try:
            text = self._store.read_text(
                source.extracted_text_ref,
                expected_checksum=source.extracted_text_checksum,
            )
        except StoreError as exc:
            raise ProvenanceChecksumMismatch(
                f"cannot verify extracted text for source {source.id}: {exc}"
            ) from exc
        return text

    def _resolve_span(self, claim: SourceClaim, span: SourceSpan) -> ResolvedSpan:
        if span.source_id != claim.source_id:
            raise OrphanedReferenceError(
                f"span source {span.source_id} does not match claim {claim.id} source {claim.source_id}"
            )
        source = self._source_record(span.source_id)
        text = self._verified_text(source)
        locator = span.locator
        resolved_excerpt: Optional[str] = None

        if locator.start_offset is not None and locator.end_offset is not None:
            if locator.start_offset < 0 or locator.end_offset > len(text):
                raise InvalidSpanError(
                    f"span for claim {claim.id} falls outside source {source.id}: "
                    f"{locator.start_offset}:{locator.end_offset} of {len(text)} characters"
                )
            resolved_excerpt = text[locator.start_offset : locator.end_offset]
        elif span.excerpt is not None:
            if span.excerpt not in text:
                raise InvalidSpanError(
                    f"excerpt for claim {claim.id} is absent from extracted source {source.id}"
                )
            resolved_excerpt = span.excerpt
        else:
            raise MissingSpanError(
                f"claim {claim.id} has locator metadata but no verifiable "
                "character offsets or excerpt"
            )

        if span.excerpt is not None and resolved_excerpt != span.excerpt:
            raise ProvenanceChecksumMismatch(
                f"stored excerpt does not match resolved text for claim {claim.id}"
            )
        if span.excerpt_checksum is not None:
            if resolved_excerpt is None:
                raise MissingSpanError(
                    f"claim {claim.id} has an excerpt checksum but no resolvable excerpt"
                )
            actual = sha256_text(resolved_excerpt)
            if actual != span.excerpt_checksum.lower():
                raise ProvenanceChecksumMismatch(
                    f"excerpt checksum mismatch for claim {claim.id}: "
                    f"expected {span.excerpt_checksum}, got {actual}"
                )

        return ResolvedSpan(source.id, claim.id, span, resolved_excerpt)

    def resolve_assertion(self, assertion: SynthesisAssertion) -> ProvenanceTrace:
        canonical_claims = tuple(
            self._canonical_claim(claim_id) for claim_id in assertion.canonical_claim_ids
        )
        allowed_source_claim_ids = {
            member_id
            for canonical in canonical_claims
            for member_id in canonical.member_source_claim_ids
        }

        resolved_claims = []
        resolved_spans = []
        run_ids = {canonical.normalization_run_id for canonical in canonical_claims}
        for claim_id in assertion.supporting_source_claim_ids:
            if claim_id not in allowed_source_claim_ids:
                raise OrphanedReferenceError(
                    f"assertion {assertion.id} supporting claim {claim_id} is not a member "
                    "of any referenced canonical claim"
                )
            claim = self._source_claim(claim_id)
            if not claim.source_spans:
                raise MissingSpanError(f"source claim {claim.id} has no source spans")
            resolved_claims.append(claim)
            run_ids.add(claim.extraction.run_id)
            resolved_spans.extend(self._resolve_span(claim, span) for span in claim.source_spans)

        if not resolved_spans:
            raise MissingSpanError(f"assertion {assertion.id} resolves to no source spans")
        return ProvenanceTrace(
            assertion_id=assertion.id,
            canonical_claim_ids=tuple(assertion.canonical_claim_ids),
            source_claim_ids=tuple(claim.id for claim in resolved_claims),
            spans=tuple(resolved_spans),
            run_ids=tuple(sorted(run_ids)),
        )

    trace = resolve_assertion

    def validate_synthesis(self, artifact: SynthesisArtifact) -> Tuple[ProvenanceTrace, ...]:
        return tuple(self.resolve_assertion(assertion) for assertion in artifact.assertions)


__all__ = [
    "InvalidSpanError",
    "MissingSpanError",
    "OrphanedReferenceError",
    "ProvenanceChecksumMismatch",
    "ProvenanceError",
    "ProvenanceResolver",
    "ProvenanceTrace",
    "ResolvedSpan",
]
