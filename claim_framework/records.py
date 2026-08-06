"""Versioned, domain-neutral records for claim and corpus processing.

The records intentionally use only the Python standard library.  They describe
provenance and reasoning artifacts; none of the fields represents a universal
truth score.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "1.0"


class ContractError(ValueError):
    """Raised when a persisted record violates its declared contract."""


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")


def _require_schema(value: str) -> None:
    if value != SCHEMA_VERSION:
        raise ContractError(
            f"unsupported schema_version {value!r}; supported: {SCHEMA_VERSION!r}"
        )


def _require_choice(name: str, value: str, choices: Sequence[str]) -> None:
    if value not in choices:
        raise ContractError(
            f"{name} must be one of {', '.join(choices)}; received {value!r}"
        )


def _require_timestamp(name: str, value: Optional[str]) -> None:
    if value is None:
        return
    _require_text(name, value)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} must be an ISO-8601 timestamp") from exc


def _require_sha256(name: str, value: Optional[str]) -> None:
    if value is None:
        return
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise ContractError(f"{name} must be a 64-character SHA-256 hex digest")


def _as_tuple(value: Sequence[Any]) -> Tuple[Any, ...]:
    return value if isinstance(value, tuple) else tuple(value)


def _freeze_json_value(name: str, value: Any) -> Any:
    """Validate and recursively freeze a JSON-shaped contract value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{name} must not contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{name} mapping keys must be strings")
            frozen[key] = _freeze_json_value(f"{name}.{key}", item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json_value(f"{name}[{index}]", item)
            for index, item in enumerate(value)
        )
    raise ContractError(
        f"{name} must contain only JSON scalar, mapping, or sequence values"
    )


@dataclass(frozen=True)
class Locator:
    page: Optional[int] = None
    chapter: Optional[str] = None
    section: Optional[str] = None
    paragraph: Optional[int] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None

    def __post_init__(self) -> None:
        for name in ("page", "paragraph", "start_offset", "end_offset"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ContractError(f"locator.{name} must be a non-negative integer")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ContractError("locator offsets must be supplied together")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ContractError("locator.end_offset must be greater than start_offset")
        if all(
            value is None
            for value in (
                self.page,
                self.chapter,
                self.section,
                self.paragraph,
                self.start_offset,
            )
        ):
            raise ContractError("a locator must identify at least one source position")


@dataclass(frozen=True)
class SourceSpan:
    source_id: str
    locator: Locator
    excerpt: Optional[str] = None
    excerpt_checksum: Optional[str] = None

    def __post_init__(self) -> None:
        _require_text("source_span.source_id", self.source_id)
        if not isinstance(self.locator, Locator):
            raise ContractError("source_span.locator must be a Locator")
        _require_sha256("source_span.excerpt_checksum", self.excerpt_checksum)
        if self.excerpt is not None and not self.excerpt:
            raise ContractError("source_span.excerpt cannot be empty")
        if self.excerpt is not None and self.excerpt_checksum is None:
            raise ContractError("an excerpt requires excerpt_checksum")


@dataclass(frozen=True)
class Condition:
    field: str
    operator: str
    value: Any

    def __post_init__(self) -> None:
        _require_text("condition.field", self.field)
        _require_text("condition.operator", self.operator)
        object.__setattr__(
            self,
            "value",
            _freeze_json_value("condition.value", self.value),
        )


@dataclass(frozen=True)
class ClaimSemantics:
    subject: Optional[str] = None
    relation: Optional[str] = None
    object_or_value: Any = None
    polarity: str = "neutral"
    quantifier: Optional[str] = None
    modality: Optional[str] = None
    population: Optional[str] = None
    geography: Optional[str] = None
    temporal_scope: Optional[str] = None
    definitions: Mapping[str, Any] = None  # type: ignore[assignment]
    assumptions: Tuple[str, ...] = ()
    conditions: Tuple[Condition, ...] = ()
    objective: Optional[str] = None
    outcome: Mapping[str, Any] = None  # type: ignore[assignment]
    time_horizon: Optional[str] = None

    def __post_init__(self) -> None:
        _require_choice("claim_semantics.polarity", self.polarity, ("positive", "negative", "neutral"))
        object.__setattr__(
            self,
            "object_or_value",
            _freeze_json_value(
                "claim_semantics.object_or_value", self.object_or_value
            ),
        )
        object.__setattr__(
            self,
            "definitions",
            _freeze_json_value(
                "claim_semantics.definitions", self.definitions or {}
            ),
        )
        object.__setattr__(
            self,
            "outcome",
            _freeze_json_value("claim_semantics.outcome", self.outcome or {}),
        )
        object.__setattr__(self, "assumptions", _as_tuple(self.assumptions))
        object.__setattr__(self, "conditions", _as_tuple(self.conditions))
        if any(not isinstance(item, Condition) for item in self.conditions):
            raise ContractError("claim_semantics.conditions must contain Condition records")


@dataclass(frozen=True)
class ExtractionInfo:
    run_id: str
    review_status: str = "unreviewed"
    extraction_confidence: Optional[float] = None
    safety_finding_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("extraction.run_id", self.run_id)
        _require_choice(
            "extraction.review_status",
            self.review_status,
            ("unreviewed", "accepted", "corrected", "rejected"),
        )
        if self.extraction_confidence is not None and not 0 <= self.extraction_confidence <= 1:
            raise ContractError("extraction_confidence must be between 0 and 1")
        object.__setattr__(
            self, "safety_finding_ids", _as_tuple(self.safety_finding_ids)
        )
        for finding_id in self.safety_finding_ids:
            _require_text("extraction.safety_finding_id", finding_id)
        if len(set(self.safety_finding_ids)) != len(self.safety_finding_ids):
            raise ContractError("extraction.safety_finding_ids must be unique")


@dataclass(frozen=True)
class SourceEntry:
    source_id: str
    input_ref: str
    media_type: Optional[str] = None
    metadata_overrides: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        _require_text("source_entry.source_id", self.source_id)
        _require_text("source_entry.input_ref", self.input_ref)
        object.__setattr__(
            self,
            "metadata_overrides",
            _freeze_json_value(
                "source_entry.metadata_overrides", self.metadata_overrides or {}
            ),
        )


@dataclass(frozen=True)
class CorpusManifest:
    id: str
    name: str
    source_entries: Tuple[SourceEntry, ...]
    configuration_ref: str
    created_at: str
    domain_profile: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("corpus_manifest.id", self.id)
        _require_text("corpus_manifest.name", self.name)
        _require_text("corpus_manifest.configuration_ref", self.configuration_ref)
        _require_timestamp("corpus_manifest.created_at", self.created_at)
        object.__setattr__(self, "source_entries", _as_tuple(self.source_entries))
        if len(self.source_entries) < 2:
            raise ContractError("a corpus manifest requires at least two source entries")
        if any(not isinstance(item, SourceEntry) for item in self.source_entries):
            raise ContractError("source_entries must contain SourceEntry records")
        source_ids = [entry.source_id for entry in self.source_entries]
        if len(set(source_ids)) != len(source_ids):
            raise ContractError("source_entry.source_id values must be unique")


@dataclass(frozen=True)
class SourceRecord:
    id: str
    corpus_id: str
    title: str
    creators: Tuple[str, ...]
    media_type: str
    source_ref: str
    content_checksum: str
    parser_name: str
    parser_version: str
    ingestion_run_id: str
    extracted_text_ref: str
    extracted_text_checksum: str
    edition: Optional[str] = None
    publication_date: Optional[str] = None
    rights_or_access_notes: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in (
            "id", "corpus_id", "title", "media_type", "source_ref", "parser_name",
            "parser_version", "ingestion_run_id", "extracted_text_ref",
        ):
            _require_text(f"source_record.{name}", getattr(self, name))
        _require_sha256("source_record.content_checksum", self.content_checksum)
        _require_sha256("source_record.extracted_text_checksum", self.extracted_text_checksum)
        object.__setattr__(self, "creators", _as_tuple(self.creators))


@dataclass(frozen=True)
class RunRecord:
    id: str
    stage: str
    implementation_version: str
    schema_versions: Mapping[str, str]
    configuration_hash: str
    input_artifact_ids: Tuple[str, ...]
    started_at: str
    status: str
    completed_at: Optional[str] = None
    model_or_tool: Optional[str] = None
    prompt_or_ruleset_version: Optional[str] = None
    limitations: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("run.id", self.id)
        _require_choice(
            "run.stage",
            self.stage,
            ("ingest", "extract", "normalize", "relate", "synthesize", "evaluate", "score", "compile"),
        )
        _require_text("run.implementation_version", self.implementation_version)
        _require_sha256("run.configuration_hash", self.configuration_hash)
        _require_timestamp("run.started_at", self.started_at)
        _require_timestamp("run.completed_at", self.completed_at)
        _require_choice("run.status", self.status, ("running", "completed", "partial", "failed"))
        object.__setattr__(
            self,
            "schema_versions",
            _freeze_json_value("run.schema_versions", self.schema_versions),
        )
        object.__setattr__(self, "input_artifact_ids", _as_tuple(self.input_artifact_ids))
        object.__setattr__(self, "limitations", _as_tuple(self.limitations))


CLAIM_TYPES = (
    "descriptive", "definitional", "causal", "normative", "procedural",
    "comparative", "predictive", "historical_observation",
)


@dataclass(frozen=True)
class SourceClaim:
    id: str
    source_id: str
    source_spans: Tuple[SourceSpan, ...]
    original_assertion: str
    proposition: str
    claim_type: str
    semantics: ClaimSemantics
    extraction: ExtractionInfo
    evidence_refs: Tuple[str, ...] = ()
    author_confidence: Any = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "source_id", "original_assertion", "proposition"):
            _require_text(f"source_claim.{name}", getattr(self, name))
        _require_choice("source_claim.claim_type", self.claim_type, CLAIM_TYPES)
        object.__setattr__(self, "source_spans", _as_tuple(self.source_spans))
        object.__setattr__(self, "evidence_refs", _as_tuple(self.evidence_refs))
        for evidence_ref in self.evidence_refs:
            _require_text("source_claim.evidence_ref", evidence_ref)
            if evidence_ref.startswith("safety:"):
                raise ContractError(
                    "source_claim.evidence_refs must reference EvidenceRecord IDs; "
                    "use extraction.safety_finding_ids for safety findings"
                )
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ContractError("source_claim.evidence_refs must be unique")
        object.__setattr__(
            self,
            "author_confidence",
            _freeze_json_value(
                "source_claim.author_confidence", self.author_confidence
            ),
        )
        if not self.source_spans:
            raise ContractError("a source claim requires at least one source span")
        if any(span.source_id != self.source_id for span in self.source_spans):
            raise ContractError("every source span must reference the source claim's source_id")


@dataclass(frozen=True)
class CanonicalClaim:
    id: str
    canonical_proposition: str
    claim_type: str
    semantics: ClaimSemantics
    member_source_claim_ids: Tuple[str, ...]
    preserved_variants: Tuple[str, ...]
    normalization_rationale: str
    normalization_run_id: str
    status: str = "active"
    review_status: str = "unreviewed"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "canonical_proposition", "normalization_rationale", "normalization_run_id"):
            _require_text(f"canonical_claim.{name}", getattr(self, name))
        _require_choice("canonical_claim.claim_type", self.claim_type, CLAIM_TYPES)
        _require_choice("canonical_claim.status", self.status, ("active", "disputed", "unresolved", "deprecated"))
        _require_choice("canonical_claim.review_status", self.review_status, ("unreviewed", "accepted", "corrected", "rejected"))
        object.__setattr__(self, "member_source_claim_ids", _as_tuple(self.member_source_claim_ids))
        object.__setattr__(self, "preserved_variants", _as_tuple(self.preserved_variants))
        if not self.member_source_claim_ids:
            raise ContractError("a canonical claim requires at least one source claim")


EVIDENCE_TYPES = (
    "assertion",
    "anecdote",
    "example",
    "case_study",
    "observational",
    "experimental",
    "synthesis",
    "formal_argument",
    "cited_external_source",
    "historical_data",
    "unknown",
)
EVIDENCE_DIRECTIONS = ("supports", "challenges", "contextualizes", "unknown")


@dataclass(frozen=True)
class EvidenceRecord:
    """Source-linked evidence classification without a universal quality rank."""

    id: str
    source_claim_id: str
    evidence_type: str
    direction: str
    description: Optional[str] = None
    cited_source_refs: Tuple[str, ...] = ()
    source_spans: Tuple[SourceSpan, ...] = ()
    independence_group: Optional[str] = None
    limitations: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("evidence_record.id", self.id)
        _require_text("evidence_record.source_claim_id", self.source_claim_id)
        _require_choice(
            "evidence_record.evidence_type", self.evidence_type, EVIDENCE_TYPES
        )
        _require_choice(
            "evidence_record.direction", self.direction, EVIDENCE_DIRECTIONS
        )
        if self.description is not None:
            _require_text("evidence_record.description", self.description)
        if self.independence_group is not None:
            _require_text(
                "evidence_record.independence_group", self.independence_group
            )
        object.__setattr__(
            self, "cited_source_refs", _as_tuple(self.cited_source_refs)
        )
        object.__setattr__(self, "source_spans", _as_tuple(self.source_spans))
        object.__setattr__(self, "limitations", _as_tuple(self.limitations))
        if not self.source_spans:
            raise ContractError("an evidence record requires at least one source span")
        if any(not isinstance(span, SourceSpan) for span in self.source_spans):
            raise ContractError(
                "evidence_record.source_spans must contain SourceSpan records"
            )
        if len(set(self.source_spans)) != len(self.source_spans):
            raise ContractError("evidence_record.source_spans must be unique")
        for field_name in ("cited_source_refs", "limitations"):
            values = getattr(self, field_name)
            for value in values:
                _require_text(f"evidence_record.{field_name}", value)
            if len(set(values)) != len(values):
                raise ContractError(f"evidence_record.{field_name} must be unique")


ALIGNMENT_VALUES = ("aligned", "divergent", "unknown")
OVERLAP_VALUES = ("same", "partial", "disjoint", "unknown")


@dataclass(frozen=True)
class ScopeAnalysis:
    term_definition_alignment: str = "unknown"
    population_overlap: str = "unknown"
    temporal_overlap: str = "unknown"
    condition_overlap: str = "unknown"
    objective_alignment: str = "unknown"
    shared_conditions: Tuple[Condition, ...] = ()
    left_only_conditions: Tuple[Condition, ...] = ()
    right_only_conditions: Tuple[Condition, ...] = ()

    def __post_init__(self) -> None:
        _require_choice("scope.term_definition_alignment", self.term_definition_alignment, ALIGNMENT_VALUES)
        _require_choice("scope.objective_alignment", self.objective_alignment, ALIGNMENT_VALUES)
        for name in ("population_overlap", "temporal_overlap", "condition_overlap"):
            _require_choice(f"scope.{name}", getattr(self, name), OVERLAP_VALUES)
        for name in ("shared_conditions", "left_only_conditions", "right_only_conditions"):
            object.__setattr__(self, name, _as_tuple(getattr(self, name)))


@dataclass(frozen=True)
class ClassificationInfo:
    run_id: str
    review_status: str = "unreviewed"
    classifier_confidence: Optional[float] = None

    def __post_init__(self) -> None:
        _require_text("classification.run_id", self.run_id)
        _require_choice("classification.review_status", self.review_status, ("unreviewed", "accepted", "corrected", "rejected"))
        if self.classifier_confidence is not None and not 0 <= self.classifier_confidence <= 1:
            raise ContractError("classifier_confidence must be between 0 and 1")


@dataclass(frozen=True)
class HumanOverride:
    """Audited human correction of one classified relation field."""

    field: str
    prior_value: Any
    new_value: Any
    reviewer: str
    timestamp: str
    reason: str
    prior_record_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("field", "reviewer", "reason"):
            _require_text(f"human_override.{name}", getattr(self, name))
        _require_timestamp("human_override.timestamp", self.timestamp)
        if self.prior_record_id is not None:
            _require_text(
                "human_override.prior_record_id", self.prior_record_id
            )
        object.__setattr__(
            self,
            "prior_value",
            _freeze_json_value("human_override.prior_value", self.prior_value),
        )
        object.__setattr__(
            self,
            "new_value",
            _freeze_json_value("human_override.new_value", self.new_value),
        )
        if self.prior_value == self.new_value:
            raise ContractError("a human override must change the recorded value")


RELATION_TYPES = (
    "equivalent", "agreement", "support", "refinement", "qualification",
    "contradiction", "conditional_disagreement", "tension", "alternative",
    "supersedes", "orthogonal", "insufficient_information",
)


@dataclass(frozen=True)
class ClaimRelation:
    id: str
    left_claim_id: str
    right_claim_id: str
    relation_type: str
    directionality: str
    scope_analysis: ScopeAnalysis
    conflict_dimensions: Tuple[str, ...]
    rationale: str
    supporting_source_claim_ids: Tuple[str, ...]
    classification: ClassificationInfo
    human_override: Optional[HumanOverride] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "left_claim_id", "right_claim_id", "rationale"):
            _require_text(f"claim_relation.{name}", getattr(self, name))
        if self.left_claim_id == self.right_claim_id:
            raise ContractError("a claim relation requires two distinct claims")
        _require_choice("claim_relation.relation_type", self.relation_type, RELATION_TYPES)
        _require_choice("claim_relation.directionality", self.directionality, ("symmetric", "left_to_right", "right_to_left"))
        object.__setattr__(self, "conflict_dimensions", _as_tuple(self.conflict_dimensions))
        object.__setattr__(self, "supporting_source_claim_ids", _as_tuple(self.supporting_source_claim_ids))
        if self.human_override is not None and not isinstance(
            self.human_override, HumanOverride
        ):
            raise ContractError(
                "claim_relation.human_override must be a HumanOverride"
            )


@dataclass(frozen=True)
class ClaimGraphSnapshot:
    id: str
    corpus_id: str
    canonical_claim_ids: Tuple[str, ...]
    relation_ids: Tuple[str, ...]
    run_id: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "corpus_id", "run_id"):
            _require_text(f"claim_graph.{name}", getattr(self, name))
        object.__setattr__(self, "canonical_claim_ids", _as_tuple(self.canonical_claim_ids))
        object.__setattr__(self, "relation_ids", _as_tuple(self.relation_ids))


@dataclass(frozen=True)
class SynthesisAssertion:
    id: str
    text: str
    status: str
    canonical_claim_ids: Tuple[str, ...]
    supporting_source_claim_ids: Tuple[str, ...]
    opposing_source_claim_ids: Tuple[str, ...]
    rationale: str
    condition_summary: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("synthesis_assertion.id", self.id)
        _require_text("synthesis_assertion.text", self.text)
        _require_text("synthesis_assertion.rationale", self.rationale)
        _require_choice("synthesis_assertion.status", self.status, ("consensus", "contested", "conditional", "minority_view", "unresolved"))
        for name in ("canonical_claim_ids", "supporting_source_claim_ids", "opposing_source_claim_ids"):
            object.__setattr__(self, name, _as_tuple(getattr(self, name)))
        if not self.canonical_claim_ids or not self.supporting_source_claim_ids:
            raise ContractError("a synthesis assertion requires canonical and supporting source claims")


@dataclass(frozen=True)
class DisputeRecord:
    id: str
    topic: str
    position_claim_ids: Tuple[str, ...]
    conflict_type: str
    shared_assumptions: Tuple[str, ...]
    differing_assumptions: Tuple[str, ...]
    key_variables: Tuple[str, ...]
    status: str
    reconciliation: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("dispute.id", self.id)
        _require_text("dispute.topic", self.topic)
        _require_choice(
            "dispute.conflict_type", self.conflict_type,
            ("direct", "definition_mismatch", "scope_mismatch", "population_mismatch", "objective_mismatch", "temporal_mismatch", "evidence_disagreement", "conditional_disagreement", "unresolved"),
        )
        _require_choice("dispute.status", self.status, ("reconciled_conditionally", "unresolved", "needs_review"))
        for name in ("position_claim_ids", "shared_assumptions", "differing_assumptions", "key_variables"):
            object.__setattr__(self, name, _as_tuple(getattr(self, name)))
        if len(self.position_claim_ids) < 2:
            raise ContractError("a dispute requires at least two positions")


@dataclass(frozen=True)
class SynthesisGap:
    id: str
    text: str
    related_claim_ids: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("synthesis_gap.id", self.id)
        _require_text("synthesis_gap.text", self.text)
        object.__setattr__(self, "related_claim_ids", _as_tuple(self.related_claim_ids))


@dataclass(frozen=True)
class TopicCluster:
    id: str
    topic: str
    canonical_claim_ids: Tuple[str, ...]
    assertion_ids: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("topic_cluster.id", self.id)
        _require_text("topic_cluster.topic", self.topic)
        object.__setattr__(self, "canonical_claim_ids", _as_tuple(self.canonical_claim_ids))
        object.__setattr__(self, "assertion_ids", _as_tuple(self.assertion_ids))


@dataclass(frozen=True)
class SynthesisArtifact:
    id: str
    corpus_id: str
    topic_clusters: Tuple[TopicCluster, ...]
    assertions: Tuple[SynthesisAssertion, ...]
    disputes: Tuple[DisputeRecord, ...]
    unresolved_questions: Tuple[SynthesisGap, ...]
    coverage_notes: Tuple[str, ...]
    run_id: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "corpus_id", "run_id"):
            _require_text(f"synthesis.{name}", getattr(self, name))
        for name in ("topic_clusters", "assertions", "disputes", "unresolved_questions", "coverage_notes"):
            object.__setattr__(self, name, _as_tuple(getattr(self, name)))


@dataclass(frozen=True)
class SkillBuildManifest:
    id: str
    corpus_id: str
    source_record_ids: Tuple[str, ...]
    synthesis_artifact_id: str
    included_claim_ids: Tuple[str, ...]
    unresolved_dispute_ids: Tuple[str, ...]
    compiler_version: str
    configuration_hash: str
    generated_at: str
    output_checksums: Mapping[str, str]
    domain_profile_id_and_version: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "corpus_id", "synthesis_artifact_id", "compiler_version"):
            _require_text(f"skill_build_manifest.{name}", getattr(self, name))
        _require_sha256("skill_build_manifest.configuration_hash", self.configuration_hash)
        _require_timestamp("skill_build_manifest.generated_at", self.generated_at)
        for name in ("source_record_ids", "included_claim_ids", "unresolved_dispute_ids"):
            object.__setattr__(self, name, _as_tuple(getattr(self, name)))
        checksums = dict(self.output_checksums)
        for path, checksum in checksums.items():
            _require_text("skill_build_manifest.output_checksums path", path)
            _require_sha256(f"output checksum for {path}", checksum)
        object.__setattr__(
            self,
            "output_checksums",
            _freeze_json_value(
                "skill_build_manifest.output_checksums", checksums
            ),
        )


# Domain profile records ---------------------------------------------------

@dataclass(frozen=True)
class MetricDefinition:
    """Domain-supplied meaning for a metric without embedding domain logic."""

    id: str
    description: str
    unit: Optional[str] = None
    metadata: Mapping[str, Any] = None  # type: ignore[assignment]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("metric_definition.id", self.id)
        _require_text("metric_definition.description", self.description)
        if self.unit is not None:
            _require_text("metric_definition.unit", self.unit)
        object.__setattr__(
            self,
            "metadata",
            _freeze_json_value(
                "metric_definition.metadata", self.metadata or {}
            ),
        )


@dataclass(frozen=True)
class DomainProfile:
    """Versioned declarative inputs supplied by an optional domain adapter."""

    id: str
    version: str
    condition_fields: Tuple[str, ...] = ()
    metric_definitions: Tuple[MetricDefinition, ...] = ()
    evidence_rubric_refs: Tuple[str, ...] = ()
    outcome_resolver_refs: Tuple[str, ...] = ()
    vocabulary_ref: Optional[str] = None
    ontology_ref: Optional[str] = None
    predictive_eligibility_rules_ref: Optional[str] = None
    skill_template_ref: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("domain_profile.id", self.id)
        _require_text("domain_profile.version", self.version)
        for name in (
            "condition_fields",
            "metric_definitions",
            "evidence_rubric_refs",
            "outcome_resolver_refs",
        ):
            object.__setattr__(self, name, _as_tuple(getattr(self, name)))
        if any(
            not isinstance(item, MetricDefinition)
            for item in self.metric_definitions
        ):
            raise ContractError(
                "domain_profile.metric_definitions must contain MetricDefinition records"
            )
        metric_ids = [item.id for item in self.metric_definitions]
        if len(set(metric_ids)) != len(metric_ids):
            raise ContractError("domain profile metric definition ids must be unique")
        for name in (
            "condition_fields",
            "evidence_rubric_refs",
            "outcome_resolver_refs",
        ):
            values = getattr(self, name)
            for value in values:
                _require_text(f"domain_profile.{name}", value)
            if len(set(values)) != len(values):
                raise ContractError(f"domain_profile.{name} must be unique")
        for name in (
            "vocabulary_ref",
            "ontology_ref",
            "predictive_eligibility_rules_ref",
            "skill_template_ref",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_text(f"domain_profile.{name}", value)


# Evaluation records -------------------------------------------------------

EVALUATION_TARGET_TYPES = (
    "source_claim",
    "canonical_claim",
    "evidence",
    "synthesis",
    "skill",
)
MISSING_POLICIES = ("unknown", "not_applicable", "fail")
ASSESSMENT_STATUSES = ("assessed", "unknown", "not_applicable")


def _finite_number(name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ContractError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class EvaluationDimension:
    """One named question and its explicit numeric scoring scale."""

    id: str
    question: str
    scale: Mapping[str, Any]
    weight: Optional[float] = None
    missing_policy: str = "unknown"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("evaluation_dimension.id", self.id)
        _require_text("evaluation_dimension.question", self.question)
        if not isinstance(self.scale, Mapping):
            raise ContractError("evaluation_dimension.scale must be a mapping")
        scale = dict(self.scale)
        if not scale:
            raise ContractError(
                "evaluation_dimension.scale must explicitly define a scale"
            )
        has_minimum = "minimum" in scale
        has_maximum = "maximum" in scale
        if has_minimum != has_maximum:
            raise ContractError(
                "evaluation_dimension.scale numeric bounds must include both "
                "minimum and maximum"
            )
        if has_minimum:
            minimum = _finite_number(
                "evaluation_dimension.scale.minimum", scale["minimum"]
            )
            maximum = _finite_number(
                "evaluation_dimension.scale.maximum", scale["maximum"]
            )
            if maximum <= minimum:
                raise ContractError(
                    "evaluation_dimension.scale.maximum must be greater than minimum"
                )
            scale["minimum"] = minimum
            scale["maximum"] = maximum
        object.__setattr__(
            self,
            "scale",
            _freeze_json_value("evaluation_dimension.scale", scale),
        )
        if self.weight is not None:
            weight = _finite_number("evaluation_dimension.weight", self.weight)
            if weight < 0:
                raise ContractError("evaluation_dimension.weight cannot be negative")
            object.__setattr__(self, "weight", weight)
        _require_choice(
            "evaluation_dimension.missing_policy",
            self.missing_policy,
            MISSING_POLICIES,
        )


@dataclass(frozen=True)
class EvaluationRubric:
    """A named, versioned set of dimensions for one kind of target."""

    id: str
    version: str
    target_type: str
    dimensions: Tuple[EvaluationDimension, ...]
    aggregation_method: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("evaluation_rubric.id", self.id)
        _require_text("evaluation_rubric.version", self.version)
        _require_choice(
            "evaluation_rubric.target_type",
            self.target_type,
            EVALUATION_TARGET_TYPES,
        )
        object.__setattr__(self, "dimensions", _as_tuple(self.dimensions))
        if not self.dimensions:
            raise ContractError("an evaluation rubric requires at least one dimension")
        if any(
            not isinstance(dimension, EvaluationDimension)
            for dimension in self.dimensions
        ):
            raise ContractError(
                "evaluation_rubric.dimensions must contain EvaluationDimension records"
            )
        dimension_ids = [dimension.id for dimension in self.dimensions]
        if len(set(dimension_ids)) != len(dimension_ids):
            raise ContractError("evaluation dimension ids must be unique within a rubric")
        if self.aggregation_method is not None:
            _require_text(
                "evaluation_rubric.aggregation_method", self.aggregation_method
            )


@dataclass(frozen=True)
class DimensionAssessment:
    """The inspectable result for a single rubric dimension."""

    dimension_id: str
    status: str
    rationale: str
    score: Optional[float] = None
    evidence_refs: Tuple[str, ...] = ()
    uncertainty: Any = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("dimension_assessment.dimension_id", self.dimension_id)
        _require_choice(
            "dimension_assessment.status", self.status, ASSESSMENT_STATUSES
        )
        _require_text("dimension_assessment.rationale", self.rationale)
        object.__setattr__(self, "evidence_refs", _as_tuple(self.evidence_refs))
        object.__setattr__(
            self,
            "uncertainty",
            _freeze_json_value(
                "dimension_assessment.uncertainty", self.uncertainty
            ),
        )
        for evidence_ref in self.evidence_refs:
            _require_text("dimension_assessment.evidence_ref", evidence_ref)
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ContractError("dimension_assessment.evidence_refs must be unique")
        if self.status == "assessed" and self.score is not None:
            object.__setattr__(
                self,
                "score",
                _finite_number("dimension_assessment.score", self.score),
            )
        elif self.score is not None:
            raise ContractError(
                "unknown and not_applicable assessments cannot carry a score"
            )


@dataclass(frozen=True)
class EvaluationRecord:
    """A reproducible rubric application with transparent aggregation inputs."""

    id: str
    target_ref: str
    rubric_id: str
    rubric_version: str
    dimensions: Tuple[DimensionAssessment, ...]
    run_id: str
    aggregate_score: Optional[float] = None
    reviewer: Optional[str] = None
    aggregation_method: Optional[str] = None
    component_weights: Mapping[str, float] = None  # type: ignore[assignment]
    aggregate_component_ids: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "target_ref", "rubric_id", "rubric_version", "run_id"):
            _require_text(f"evaluation_record.{name}", getattr(self, name))
        if self.reviewer is not None:
            _require_text("evaluation_record.reviewer", self.reviewer)
        object.__setattr__(self, "dimensions", _as_tuple(self.dimensions))
        if not self.dimensions:
            raise ContractError("an evaluation record requires dimension assessments")
        if any(
            not isinstance(assessment, DimensionAssessment)
            for assessment in self.dimensions
        ):
            raise ContractError(
                "evaluation_record.dimensions must contain DimensionAssessment records"
            )
        dimension_ids = [assessment.dimension_id for assessment in self.dimensions]
        if len(set(dimension_ids)) != len(dimension_ids):
            raise ContractError(
                "evaluation dimension assessments must have unique dimension ids"
            )

        weights = dict(self.component_weights or {})
        unknown_weight_ids = sorted(set(weights) - set(dimension_ids))
        if unknown_weight_ids:
            raise ContractError(
                "component_weights references unknown dimension ids: "
                + ", ".join(unknown_weight_ids)
            )
        for dimension_id, value in weights.items():
            weight = _finite_number(
                f"evaluation_record.component_weights.{dimension_id}", value
            )
            if weight < 0:
                raise ContractError("evaluation component weights cannot be negative")
            weights[dimension_id] = weight
        object.__setattr__(
            self,
            "component_weights",
            _freeze_json_value("evaluation_record.component_weights", weights),
        )

        object.__setattr__(
            self,
            "aggregate_component_ids",
            _as_tuple(self.aggregate_component_ids),
        )
        if len(set(self.aggregate_component_ids)) != len(
            self.aggregate_component_ids
        ):
            raise ContractError("aggregate_component_ids must be unique")
        assessment_by_id = {
            assessment.dimension_id: assessment for assessment in self.dimensions
        }
        for dimension_id in self.aggregate_component_ids:
            if dimension_id not in assessment_by_id:
                raise ContractError(
                    f"aggregate component {dimension_id!r} has no assessment"
                )
            assessment = assessment_by_id[dimension_id]
            if assessment.status != "assessed" or assessment.score is None:
                raise ContractError(
                    "aggregate components must be assessed numeric dimensions"
                )
            if dimension_id not in weights:
                raise ContractError(
                    f"aggregate component {dimension_id!r} has no recorded weight"
                )

        if self.aggregation_method is not None:
            _require_text(
                "evaluation_record.aggregation_method", self.aggregation_method
            )
        if self.aggregate_score is not None:
            if self.aggregation_method is None:
                raise ContractError(
                    "an aggregate score requires a recorded aggregation method"
                )
            if not self.aggregate_component_ids:
                raise ContractError(
                    "an aggregate score requires at least one aggregate component"
                )
            object.__setattr__(
                self,
                "aggregate_score",
                _finite_number("evaluation_record.aggregate_score", self.aggregate_score),
            )


# Predictive records -------------------------------------------------------

PREDICTION_STATUSES = (
    "draft",
    "frozen",
    "awaiting_outcome",
    "resolved",
    "unresolvable",
)
SCORING_RULES = (
    "brier",
    "log_loss",
    "absolute_error",
    "squared_error",
    "accuracy",
    "custom",
)
PREDICTIVE_SCORE_STATUSES = ("resolved", "awaiting_outcome", "unresolvable")


def _require_scalar(name: str, value: Any, *, optional: bool = False) -> None:
    """Require an immutable JSON scalar suitable for frozen predictive content."""
    if value is None and optional:
        return
    if isinstance(value, str):
        _require_text(name, value)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return
    raise ContractError(f"{name} must be a finite number, boolean, or non-empty string")


def _parsed_timestamp(name: str, value: str) -> datetime:
    _require_timestamp(name, value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_earlier(
    earlier_name: str,
    earlier: str,
    later_name: str,
    later: str,
) -> None:
    earlier_value = _parsed_timestamp(earlier_name, earlier)
    later_value = _parsed_timestamp(later_name, later)
    try:
        ordered = earlier_value < later_value
    except TypeError as exc:
        raise ContractError(
            f"{earlier_name} and {later_name} must use compatible timezone notation"
        ) from exc
    if not ordered:
        raise ContractError(f"{earlier_name} must be earlier than {later_name}")


def _require_not_later(
    earlier_name: str,
    earlier: str,
    later_name: str,
    later: str,
) -> None:
    earlier_value = _parsed_timestamp(earlier_name, earlier)
    later_value = _parsed_timestamp(later_name, later)
    try:
        ordered = earlier_value <= later_value
    except TypeError as exc:
        raise ContractError(
            f"{earlier_name} and {later_name} must use compatible timezone notation"
        ) from exc
    if not ordered:
        raise ContractError(f"{earlier_name} must be at or before {later_name}")


@dataclass(frozen=True)
class EligibilityResult:
    """Whether a canonical claim can be turned into a prospective prediction."""

    canonical_claim_id: str
    status: str
    reasons: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_text("eligibility_result.canonical_claim_id", self.canonical_claim_id)
        _require_choice(
            "eligibility_result.status", self.status, ("eligible", "ineligible")
        )
        object.__setattr__(self, "reasons", _as_tuple(self.reasons))
        for reason in self.reasons:
            _require_text("eligibility_result.reason", reason)
        if len(set(self.reasons)) != len(self.reasons):
            raise ContractError("eligibility_result.reasons must be unique")
        if self.status == "ineligible" and not self.reasons:
            raise ContractError("an ineligible claim requires at least one reason")

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"


@dataclass(frozen=True)
class EvaluationWindow:
    """The pre-registered interval in which an outcome may be observed."""

    start: str
    end: str

    def __post_init__(self) -> None:
        _require_earlier(
            "evaluation_window.start",
            self.start,
            "evaluation_window.end",
            self.end,
        )


@dataclass(frozen=True)
class PredictionSpec:
    """A versioned prospective forecast whose frozen content is hash-verified."""

    id: str
    canonical_claim_id: str
    target_metric: str
    probability_or_forecast: Any
    issue_or_information_cutoff_time: str
    evaluation_window: EvaluationWindow
    outcome_data_source: str
    resolution_rule: str
    scoring_rule: str
    status: str = "draft"
    population: Optional[str] = None
    direction: Optional[str] = None
    threshold: Any = None
    unit: Optional[str] = None
    benchmark_or_base_rate: Any = None
    frozen_content_hash: Optional[str] = None
    registered_at: Optional[str] = None
    version: int = 1
    supersedes_prediction_spec_id: Optional[str] = None
    status_reason: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in (
            "id",
            "canonical_claim_id",
            "target_metric",
            "outcome_data_source",
            "resolution_rule",
        ):
            _require_text(f"prediction_spec.{name}", getattr(self, name))
        _require_choice("prediction_spec.status", self.status, PREDICTION_STATUSES)
        _require_choice("prediction_spec.scoring_rule", self.scoring_rule, SCORING_RULES)
        if not isinstance(self.evaluation_window, EvaluationWindow):
            raise ContractError(
                "prediction_spec.evaluation_window must be an EvaluationWindow"
            )
        _require_earlier(
            "prediction_spec.issue_or_information_cutoff_time",
            self.issue_or_information_cutoff_time,
            "prediction_spec.evaluation_window.start",
            self.evaluation_window.start,
        )
        _require_scalar(
            "prediction_spec.probability_or_forecast",
            self.probability_or_forecast,
        )
        _require_scalar("prediction_spec.threshold", self.threshold, optional=True)
        _require_scalar(
            "prediction_spec.benchmark_or_base_rate",
            self.benchmark_or_base_rate,
            optional=True,
        )
        for name in ("population", "direction", "unit", "status_reason"):
            value = getattr(self, name)
            if value is not None:
                _require_text(f"prediction_spec.{name}", value)
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ContractError("prediction_spec.version must be a positive integer")
        if self.supersedes_prediction_spec_id is not None:
            _require_text(
                "prediction_spec.supersedes_prediction_spec_id",
                self.supersedes_prediction_spec_id,
            )
            if self.supersedes_prediction_spec_id == self.id:
                raise ContractError("a prediction version cannot supersede itself")
        if self.version == 1 and self.supersedes_prediction_spec_id is not None:
            raise ContractError("prediction_spec version 1 cannot supersede another spec")
        if self.version > 1 and self.supersedes_prediction_spec_id is None:
            raise ContractError("later prediction versions must identify the superseded spec")
        if self.status == "unresolvable" and self.status_reason is None:
            raise ContractError("an unresolvable prediction requires a status_reason")
        if self.status != "unresolvable" and self.status_reason is not None:
            raise ContractError("status_reason is only valid for an unresolvable prediction")
        if self.status == "draft":
            if self.registered_at is not None:
                raise ContractError("a draft prediction cannot carry registered_at")
            if self.frozen_content_hash is not None:
                raise ContractError("a draft prediction cannot carry a frozen content hash")
        else:
            if self.registered_at is None:
                raise ContractError("a frozen prediction requires registered_at")
            _require_not_later(
                "prediction_spec.registered_at",
                self.registered_at,
                "prediction_spec.issue_or_information_cutoff_time",
                self.issue_or_information_cutoff_time,
            )
            _require_sha256(
                "prediction_spec.frozen_content_hash", self.frozen_content_hash
            )
            if self.frozen_content_hash != prediction_spec_content_hash(self):
                raise ContractError(
                    "frozen prediction content does not match frozen_content_hash; "
                    "create a new prediction version for edits"
                )


def prediction_spec_content_hash(
    spec: PredictionSpec,
    *,
    registered_at: Optional[str] = None,
) -> str:
    """Hash only registered content, excluding lifecycle status and its reason."""
    bound_registered_at = (
        spec.registered_at if registered_at is None else registered_at
    )
    payload = {
        "schema_version": spec.schema_version,
        "id": spec.id,
        "canonical_claim_id": spec.canonical_claim_id,
        "version": spec.version,
        "supersedes_prediction_spec_id": spec.supersedes_prediction_spec_id,
        "target_metric": spec.target_metric,
        "population": spec.population,
        "direction": spec.direction,
        "threshold": spec.threshold,
        "unit": spec.unit,
        "probability_or_forecast": spec.probability_or_forecast,
        "issue_or_information_cutoff_time": spec.issue_or_information_cutoff_time,
        "evaluation_window": {
            "start": spec.evaluation_window.start,
            "end": spec.evaluation_window.end,
        },
        "outcome_data_source": spec.outcome_data_source,
        "resolution_rule": spec.resolution_rule,
        "scoring_rule": spec.scoring_rule,
        "benchmark_or_base_rate": spec.benchmark_or_base_rate,
        "registered_at": bound_registered_at,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class OutcomeObservation:
    """A provenance-bearing observation linked to exactly one prediction spec."""

    id: str
    prediction_spec_id: str
    observed_at: str
    value: Any
    source_ref: str
    collection_run_id: str
    source_checksum: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in ("id", "prediction_spec_id", "source_ref", "collection_run_id"):
            _require_text(f"outcome_observation.{name}", getattr(self, name))
        _require_timestamp("outcome_observation.observed_at", self.observed_at)
        _require_scalar("outcome_observation.value", self.value)
        _require_sha256(
            "outcome_observation.source_checksum", self.source_checksum
        )


@dataclass(frozen=True)
class PredictiveScore:
    """A resolved score or an explicit non-numeric pending/unresolvable result."""

    id: str
    prediction_spec_id: str
    outcome_observation_ids: Tuple[str, ...]
    metric: str
    value: Optional[float]
    benchmark_value: Optional[float]
    sample_size: int
    uncertainty: Optional[float]
    scoring_method_version: str
    scored_at: str
    run_id: str
    status: str = "resolved"
    reason: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        for name in (
            "id",
            "prediction_spec_id",
            "scoring_method_version",
            "run_id",
        ):
            _require_text(f"predictive_score.{name}", getattr(self, name))
        _require_choice("predictive_score.metric", self.metric, SCORING_RULES)
        _require_choice(
            "predictive_score.status", self.status, PREDICTIVE_SCORE_STATUSES
        )
        _require_timestamp("predictive_score.scored_at", self.scored_at)
        object.__setattr__(
            self,
            "outcome_observation_ids",
            _as_tuple(self.outcome_observation_ids),
        )
        for observation_id in self.outcome_observation_ids:
            _require_text("predictive_score.outcome_observation_id", observation_id)
        if len(set(self.outcome_observation_ids)) != len(
            self.outcome_observation_ids
        ):
            raise ContractError(
                "predictive_score.outcome_observation_ids must be unique"
            )
        if (
            not isinstance(self.sample_size, int)
            or isinstance(self.sample_size, bool)
            or self.sample_size < 0
        ):
            raise ContractError("predictive_score.sample_size must be non-negative")
        if self.sample_size != len(self.outcome_observation_ids):
            raise ContractError(
                "predictive_score.sample_size must match outcome observation ids"
            )
        for name in ("value", "benchmark_value", "uncertainty"):
            item = getattr(self, name)
            if item is not None:
                number = _finite_number(f"predictive_score.{name}", item)
                if name == "uncertainty" and number < 0:
                    raise ContractError("predictive_score.uncertainty cannot be negative")
                object.__setattr__(self, name, number)
        if self.status == "resolved":
            if self.value is None or self.sample_size < 1:
                raise ContractError(
                    "a resolved predictive score requires a value and observations"
                )
            if self.reason is not None:
                raise ContractError("a resolved predictive score cannot carry a reason")
            numeric_scores = tuple(
                item
                for item in (self.value, self.benchmark_value)
                if item is not None
            )
            if self.metric in ("brier", "accuracy") and any(
                not 0 <= item <= 1 for item in numeric_scores
            ):
                raise ContractError(
                    f"predictive_score.{self.metric} values must be between 0 and 1"
                )
            if self.metric in (
                "log_loss",
                "absolute_error",
                "squared_error",
            ) and any(item < 0 for item in numeric_scores):
                raise ContractError(
                    f"predictive_score.{self.metric} values cannot be negative"
                )
        else:
            if any(
                item is not None
                for item in (self.value, self.benchmark_value, self.uncertainty)
            ):
                raise ContractError(
                    "pending and unresolvable results cannot carry numeric scores"
                )
            if self.sample_size != 0:
                raise ContractError(
                    "pending and unresolvable results cannot carry observations"
                )
            if self.reason is None:
                raise ContractError(
                    "pending and unresolvable results require an explanatory reason"
                )
        if self.reason is not None:
            _require_text("predictive_score.reason", self.reason)


PERSISTED_RECORD_TYPES = (
    CorpusManifest,
    SourceRecord,
    RunRecord,
    SourceClaim,
    CanonicalClaim,
    EvidenceRecord,
    ClaimRelation,
    ClaimGraphSnapshot,
    SynthesisAssertion,
    DisputeRecord,
    SynthesisGap,
    TopicCluster,
    SynthesisArtifact,
    SkillBuildManifest,
    MetricDefinition,
    DomainProfile,
    EvaluationDimension,
    EvaluationRubric,
    DimensionAssessment,
    EvaluationRecord,
    EligibilityResult,
    PredictionSpec,
    OutcomeObservation,
    PredictiveScore,
)
