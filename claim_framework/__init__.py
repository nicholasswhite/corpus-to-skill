"""Domain-neutral, versioned claim, evaluation, and prediction framework.

Evaluation and prediction engines are loaded lazily so the claim core and the
evaluation package remain independently usable without importing predictive
scoring code.
"""

from importlib import import_module

from claim_framework.normalize import normalize, normalize_claims
from claim_framework.ports import ClaimExtractor, DomainAdapter, PredictivePowerPort
from claim_framework.provenance import ProvenanceResolver
from claim_framework.records import (
    ASSESSMENT_STATUSES,
    CLAIM_TYPES,
    EVIDENCE_DIRECTIONS,
    EVIDENCE_TYPES,
    EVALUATION_TARGET_TYPES,
    MISSING_POLICIES,
    PREDICTION_STATUSES,
    PREDICTIVE_SCORE_STATUSES,
    RELATION_TYPES,
    SCHEMA_VERSION,
    SCORING_RULES,
    CanonicalClaim,
    ClaimGraphSnapshot,
    ClaimRelation,
    ClaimSemantics,
    ClassificationInfo,
    Condition,
    ContractError,
    CorpusManifest,
    DimensionAssessment,
    DisputeRecord,
    DomainProfile,
    EvidenceRecord,
    EligibilityResult,
    EvaluationDimension,
    EvaluationRecord,
    EvaluationRubric,
    EvaluationWindow,
    ExtractionInfo,
    HumanOverride,
    Locator,
    MetricDefinition,
    OutcomeObservation,
    PredictionSpec,
    PredictiveScore,
    RunRecord,
    ScopeAnalysis,
    SkillBuildManifest,
    SourceClaim,
    SourceEntry,
    SourceRecord,
    SourceSpan,
    SynthesisArtifact,
    SynthesisAssertion,
    SynthesisGap,
    TopicCluster,
    prediction_spec_content_hash,
)
from claim_framework.relationships import (
    apply_human_override,
    classify_relation,
    classify_relations,
    retrieve_candidates,
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
from claim_framework.synthesis import synthesize, synthesize_claims


_LAZY_EXPORTS = {
    # Evaluation engine: deliberately does not import prediction.
    "DEFAULT_PROVENANCE_STRUCTURE_RUBRIC": (
        "claim_framework.evaluation",
        "DEFAULT_PROVENANCE_STRUCTURE_RUBRIC",
    ),
    "WEIGHTED_MEAN_V1": ("claim_framework.evaluation", "WEIGHTED_MEAN_V1"),
    "EvaluationEngine": ("claim_framework.evaluation", "EvaluationEngine"),
    "EvaluationError": ("claim_framework.evaluation", "EvaluationError"),
    "MissingAssessmentError": (
        "claim_framework.evaluation",
        "MissingAssessmentError",
    ),
    "UnsupportedAggregationError": (
        "claim_framework.evaluation",
        "UnsupportedAggregationError",
    ),
    "evaluate": ("claim_framework.evaluation", "evaluate"),
    "evaluate_synthesis_artifact": (
        "claim_framework.evaluation",
        "evaluate_synthesis_artifact",
    ),
    "provenance_structure_rubric": (
        "claim_framework.evaluation",
        "provenance_structure_rubric",
    ),
    # Experimental predictive foundation.
    "DEFAULT_MINIMUM_RESOLVED": (
        "claim_framework.prediction",
        "DEFAULT_MINIMUM_RESOLVED",
    ),
    "SCORING_METHOD_VERSION": (
        "claim_framework.prediction",
        "SCORING_METHOD_VERSION",
    ),
    "PredictiveAggregate": (
        "claim_framework.prediction",
        "PredictiveAggregate",
    ),
    "aggregate_scores": ("claim_framework.prediction", "aggregate_scores"),
    "check_eligibility": ("claim_framework.prediction", "check_eligibility"),
    "export_prediction_candidates": (
        "claim_framework.prediction",
        "export_prediction_candidates",
    ),
    "freeze_prediction": ("claim_framework.prediction", "freeze_prediction"),
    "register_and_freeze": (
        "claim_framework.prediction",
        "register_and_freeze",
    ),
    "new_prediction_version": (
        "claim_framework.prediction",
        "new_prediction_version",
    ),
    "mark_awaiting_outcome": (
        "claim_framework.prediction",
        "mark_awaiting_outcome",
    ),
    "mark_resolved": ("claim_framework.prediction", "mark_resolved"),
    "mark_unresolvable": (
        "claim_framework.prediction",
        "mark_unresolvable",
    ),
    "record_outcome": ("claim_framework.prediction", "record_outcome"),
    "score_prediction": ("claim_framework.prediction", "score_prediction"),
}


def __getattr__(name):
    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    "SCHEMA_VERSION",
    "CLAIM_TYPES",
    "EVIDENCE_TYPES",
    "EVIDENCE_DIRECTIONS",
    "RELATION_TYPES",
    "EVALUATION_TARGET_TYPES",
    "MISSING_POLICIES",
    "ASSESSMENT_STATUSES",
    "PREDICTION_STATUSES",
    "SCORING_RULES",
    "PREDICTIVE_SCORE_STATUSES",
    "CanonicalClaim",
    "ClaimGraphSnapshot",
    "ClaimRelation",
    "ClaimSemantics",
    "ClassificationInfo",
    "Condition",
    "ContractError",
    "CorpusManifest",
    "DimensionAssessment",
    "DisputeRecord",
    "DomainProfile",
    "EvidenceRecord",
    "EligibilityResult",
    "EvaluationDimension",
    "EvaluationRecord",
    "EvaluationRubric",
    "EvaluationWindow",
    "ExtractionInfo",
    "HumanOverride",
    "Locator",
    "MetricDefinition",
    "OutcomeObservation",
    "PredictionSpec",
    "PredictiveScore",
    "RunRecord",
    "ScopeAnalysis",
    "SkillBuildManifest",
    "SourceClaim",
    "SourceEntry",
    "SourceRecord",
    "SourceSpan",
    "SynthesisArtifact",
    "SynthesisAssertion",
    "SynthesisGap",
    "TopicCluster",
    "ArtifactStore",
    "ClaimStore",
    "InvalidRecordId",
    "RecordIdCollision",
    "RecordNotFound",
    "RecordQueryError",
    "RecordStoreCorruption",
    "UnsupportedRecordType",
    "ClaimExtractor",
    "DomainAdapter",
    "PredictivePowerPort",
    "ProvenanceResolver",
    "apply_human_override",
    "classify_relation",
    "classify_relations",
    "normalize",
    "normalize_claims",
    "prediction_spec_content_hash",
    "retrieve_candidates",
    "synthesize",
    "synthesize_claims",
    *_LAZY_EXPORTS,
]
