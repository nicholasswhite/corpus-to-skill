"""Deterministic, domain-neutral evaluation under versioned rubrics.

The engine contains no scoring heuristics.  Callers supply one assessment per
dimension (or a callable that produces them); this module validates those
results, applies the rubric's missing-data policy, and optionally calculates a
transparent weighted mean.
"""

from __future__ import annotations

import math
from collections.abc import Mapping as MappingABC
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple, Union

from claim_framework.jsonio import stable_id
from claim_framework.records import (
    CanonicalClaim,
    ContractError,
    DimensionAssessment,
    EvaluationDimension,
    EvaluationRecord,
    EvaluationRubric,
    SkillBuildManifest,
    SourceClaim,
    SynthesisArtifact,
)


WEIGHTED_MEAN_V1 = "weighted_mean:v1"


class EvaluationError(ContractError):
    """Raised when a rubric cannot be applied to supplied assessment results."""


class MissingAssessmentError(EvaluationError):
    """Raised when a required dimension has no assessable result."""


class UnsupportedAggregationError(EvaluationError):
    """Raised when the engine does not implement a rubric's aggregation method."""


AssessmentCallable = Callable[
    [Any, EvaluationDimension], Optional[DimensionAssessment]
]
AssessmentValue = Optional[Union[DimensionAssessment, Mapping[str, Any]]]
AssessmentInput = Union[
    Mapping[str, AssessmentValue],
    Iterable[DimensionAssessment],
    AssessmentCallable,
]


def provenance_structure_rubric() -> EvaluationRubric:
    """Return a small example rubric for a provenance-bearing synthesis."""

    return EvaluationRubric(
        id="provenance-structure",
        version="1.0.0",
        target_type="synthesis",
        dimensions=(
            EvaluationDimension(
                id="provenance_completeness",
                question=(
                    "What proportion of material assertions resolve to explicit "
                    "source references?"
                ),
                scale={
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "anchors": {
                        "0": "no material assertions resolve to sources",
                        "1": "all material assertions resolve to sources",
                    },
                },
                weight=0.6,
                missing_policy="unknown",
            ),
            EvaluationDimension(
                id="structural_completeness",
                question=(
                    "How completely does the target populate its required "
                    "structural elements?"
                ),
                scale={
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "anchors": {
                        "0": "required structure is absent",
                        "1": "required structure is complete",
                    },
                },
                weight=0.4,
                missing_policy="unknown",
            ),
        ),
        aggregation_method=WEIGHTED_MEAN_V1,
    )


DEFAULT_PROVENANCE_STRUCTURE_RUBRIC = provenance_structure_rubric()


def _target_ref(target: Any) -> str:
    if isinstance(target, str):
        reference = target
    elif isinstance(target, MappingABC):
        reference = target.get("id")
    else:
        reference = getattr(target, "id", None)
    if not isinstance(reference, str) or not reference.strip():
        raise EvaluationError(
            "evaluation target must be a non-empty reference or expose a non-empty id"
        )
    return reference


def _declared_target_type(target: Any) -> Optional[str]:
    known_types = (
        (SourceClaim, "source_claim"),
        (CanonicalClaim, "canonical_claim"),
        (SynthesisArtifact, "synthesis"),
        (SkillBuildManifest, "skill"),
    )
    for record_type, target_type in known_types:
        if isinstance(target, record_type):
            return target_type
    if isinstance(target, MappingABC):
        value = target.get("target_type")
        return value if isinstance(value, str) else None
    value = getattr(target, "target_type", None)
    return value if isinstance(value, str) else None


def _assessment_from_mapping(
    dimension_id: str, payload: Mapping[str, Any]
) -> DimensionAssessment:
    values = dict(payload)
    supplied_id = values.setdefault("dimension_id", dimension_id)
    if supplied_id != dimension_id:
        raise EvaluationError(
            f"assessment key {dimension_id!r} does not match dimension_id "
            f"{supplied_id!r}"
        )
    try:
        return DimensionAssessment(**values)
    except (TypeError, ValueError) as exc:
        raise EvaluationError(
            f"invalid assessment for dimension {dimension_id!r}: {exc}"
        ) from exc


def _provided_assessments(
    target: Any,
    rubric: EvaluationRubric,
    supplied: Optional[AssessmentInput],
) -> Dict[str, DimensionAssessment]:
    if supplied is None:
        return {}

    provided: Dict[str, DimensionAssessment] = {}
    if callable(supplied):
        candidates = []
        for dimension in rubric.dimensions:
            assessment = supplied(target, dimension)
            if assessment is not None:
                candidates.append(assessment)
    elif isinstance(supplied, MappingABC):
        candidates = []
        for dimension_id, value in supplied.items():
            if not isinstance(dimension_id, str):
                raise EvaluationError("assessment mapping keys must be dimension ids")
            if value is None:
                continue
            if isinstance(value, MappingABC):
                value = _assessment_from_mapping(dimension_id, value)
            if not isinstance(value, DimensionAssessment):
                raise EvaluationError(
                    f"assessment for dimension {dimension_id!r} must be a "
                    "DimensionAssessment, mapping, or None"
                )
            if value.dimension_id != dimension_id:
                raise EvaluationError(
                    f"assessment key {dimension_id!r} does not match dimension_id "
                    f"{value.dimension_id!r}"
                )
            candidates.append(value)
    else:
        if isinstance(supplied, (str, bytes)):
            raise EvaluationError("assessments must not be supplied as text")
        try:
            candidates = list(supplied)
        except TypeError as exc:
            raise EvaluationError(
                "assessments must be a mapping, iterable, callable, or None"
            ) from exc

    for assessment in candidates:
        if not isinstance(assessment, DimensionAssessment):
            raise EvaluationError(
                "assessment iterables and callables must yield "
                "DimensionAssessment records or None"
            )
        if assessment.dimension_id in provided:
            raise EvaluationError(
                f"duplicate assessment for dimension {assessment.dimension_id!r}"
            )
        provided[assessment.dimension_id] = assessment

    rubric_ids = {dimension.id for dimension in rubric.dimensions}
    extra_ids = sorted(set(provided) - rubric_ids)
    if extra_ids:
        raise EvaluationError(
            "assessments reference dimensions absent from the rubric: "
            + ", ".join(extra_ids)
        )
    return provided


def _missing_assessment(dimension: EvaluationDimension) -> DimensionAssessment:
    if dimension.missing_policy == "fail":
        raise MissingAssessmentError(
            f"dimension {dimension.id!r} requires an assessment"
        )
    return DimensionAssessment(
        dimension_id=dimension.id,
        status=dimension.missing_policy,
        rationale=(
            "No assessment result was supplied; applied rubric missing_policy "
            f"{dimension.missing_policy!r}."
        ),
    )


def _validate_score(
    dimension: EvaluationDimension, assessment: DimensionAssessment
) -> None:
    if assessment.status == "unknown" and dimension.missing_policy == "fail":
        raise MissingAssessmentError(
            f"dimension {dimension.id!r} does not permit an unknown result"
        )
    if assessment.status != "assessed" or assessment.score is None:
        return
    score = assessment.score
    if "minimum" in dimension.scale and "maximum" in dimension.scale:
        minimum = float(dimension.scale["minimum"])
        maximum = float(dimension.scale["maximum"])
        if score < minimum or score > maximum:
            raise EvaluationError(
                f"score for dimension {dimension.id!r} must be between "
                f"{minimum} and {maximum}"
            )


def _aggregate(
    rubric: EvaluationRubric,
    assessments: Tuple[DimensionAssessment, ...],
    weights: Mapping[str, float],
) -> Tuple[Optional[float], Tuple[str, ...]]:
    method = rubric.aggregation_method
    if method is None:
        return None, ()
    if method != WEIGHTED_MEAN_V1:
        raise UnsupportedAggregationError(
            f"unsupported aggregation method {method!r}"
        )

    components = tuple(
        assessment.dimension_id
        for assessment in assessments
        if assessment.status == "assessed" and assessment.score is not None
    )
    total_weight = math.fsum(weights[dimension_id] for dimension_id in components)
    if total_weight == 0:
        return None, components
    weighted_total = math.fsum(
        assessment.score * weights[assessment.dimension_id]
        for assessment in assessments
        if assessment.dimension_id in components and assessment.score is not None
    )
    return weighted_total / total_weight, components


class EvaluationEngine:
    """Apply supplied dimension assessments under a validated rubric."""

    def __init__(self, assessor: Optional[AssessmentCallable] = None) -> None:
        if assessor is not None and not callable(assessor):
            raise TypeError("assessor must be callable")
        self._assessor = assessor

    def evaluate(
        self,
        target: Any,
        rubric: EvaluationRubric,
        assessments: Optional[AssessmentInput] = None,
        run_id: Optional[str] = None,
        reviewer: Optional[str] = None,
        *,
        assessor_results: Optional[AssessmentInput] = None,
    ) -> EvaluationRecord:
        if not isinstance(rubric, EvaluationRubric):
            raise TypeError("rubric must be an EvaluationRubric")
        if assessments is not None and assessor_results is not None:
            raise EvaluationError(
                "supply either assessments or assessor_results, not both"
            )
        supplied = assessments if assessments is not None else assessor_results
        if supplied is None:
            supplied = self._assessor
        reference = _target_ref(target)
        actual_target_type = _declared_target_type(target)
        if (
            actual_target_type is not None
            and actual_target_type != rubric.target_type
        ):
            raise EvaluationError(
                f"rubric target_type {rubric.target_type!r} cannot evaluate "
                f"target type {actual_target_type!r}"
            )
        if not isinstance(run_id, str) or not run_id.strip():
            raise EvaluationError("run_id must be a non-empty string")

        provided = _provided_assessments(target, rubric, supplied)
        resolved = []
        for dimension in rubric.dimensions:
            assessment = provided.get(dimension.id)
            if assessment is None:
                assessment = _missing_assessment(dimension)
            _validate_score(dimension, assessment)
            resolved.append(assessment)
        ordered_assessments = tuple(resolved)

        weights = {
            dimension.id: (
                dimension.weight if dimension.weight is not None else 1.0
            )
            for dimension in rubric.dimensions
        }
        aggregate_score, aggregate_component_ids = _aggregate(
            rubric, ordered_assessments, weights
        )
        identity = {
            "target_ref": reference,
            "rubric_id": rubric.id,
            "rubric_version": rubric.version,
            "dimensions": ordered_assessments,
            "run_id": run_id,
            "aggregate_score": aggregate_score,
            "reviewer": reviewer,
            "aggregation_method": rubric.aggregation_method,
            "component_weights": weights,
            "aggregate_component_ids": aggregate_component_ids,
        }
        return EvaluationRecord(
            id=stable_id("evaluation", identity),
            target_ref=reference,
            rubric_id=rubric.id,
            rubric_version=rubric.version,
            dimensions=ordered_assessments,
            aggregate_score=aggregate_score,
            run_id=run_id,
            reviewer=reviewer,
            aggregation_method=rubric.aggregation_method,
            component_weights=weights,
            aggregate_component_ids=aggregate_component_ids,
        )


def evaluate(
    target: Any,
    rubric: EvaluationRubric,
    assessments: Optional[AssessmentInput] = None,
    run_id: Optional[str] = None,
    reviewer: Optional[str] = None,
    *,
    assessor_results: Optional[AssessmentInput] = None,
) -> EvaluationRecord:
    """Apply ``rubric`` without constructing an engine explicitly."""

    return EvaluationEngine().evaluate(
        target,
        rubric,
        assessments,
        run_id,
        reviewer,
        assessor_results=assessor_results,
    )


def evaluate_synthesis_artifact(
    synthesis: SynthesisArtifact,
    resolved_assertion_ids: Iterable[str],
    run_id: str,
    reviewer: Optional[str] = None,
) -> EvaluationRecord:
    """Evaluate synthesis provenance and structure with deterministic rules.

    Provenance completeness is the fraction of material synthesis assertions
    reported as successfully resolved to sources.  Structural completeness is
    the equally weighted fraction of five explicit checks: assertions exist,
    topic clusters exist, assertion claim links are populated, every assertion
    appears in a topic cluster, and coverage notes exist.
    """

    if not isinstance(synthesis, SynthesisArtifact):
        raise TypeError("synthesis must be a SynthesisArtifact")
    try:
        resolved_ids = tuple(resolved_assertion_ids)
    except TypeError as exc:
        raise EvaluationError("resolved_assertion_ids must be iterable") from exc
    for assertion_id in resolved_ids:
        if not isinstance(assertion_id, str) or not assertion_id.strip():
            raise EvaluationError(
                "resolved_assertion_ids must contain non-empty strings"
            )
    if len(set(resolved_ids)) != len(resolved_ids):
        raise EvaluationError("resolved_assertion_ids must be unique")

    assertion_ids = {assertion.id for assertion in synthesis.assertions}
    unknown_ids = sorted(set(resolved_ids) - assertion_ids)
    if unknown_ids:
        raise EvaluationError(
            "resolved_assertion_ids are absent from the synthesis: "
            + ", ".join(unknown_ids)
        )

    if synthesis.assertions:
        provenance = DimensionAssessment(
            dimension_id="provenance_completeness",
            status="assessed",
            score=len(resolved_ids) / len(synthesis.assertions),
            rationale=(
                f"Resolved {len(resolved_ids)} of {len(synthesis.assertions)} "
                "material synthesis assertions to sources."
            ),
            evidence_refs=tuple(sorted(resolved_ids)),
        )
    else:
        provenance = DimensionAssessment(
            dimension_id="provenance_completeness",
            status="not_applicable",
            rationale=(
                "The synthesis contains no material assertions, so a provenance "
                "completeness ratio is not applicable."
            ),
        )

    clustered_assertion_ids = {
        assertion_id
        for cluster in synthesis.topic_clusters
        for assertion_id in cluster.assertion_ids
    }
    structural_checks = (
        ("assertions_present", bool(synthesis.assertions)),
        ("topic_clusters_present", bool(synthesis.topic_clusters)),
        (
            "assertion_claim_links_complete",
            bool(synthesis.assertions)
            and all(
                assertion.canonical_claim_ids
                and assertion.supporting_source_claim_ids
                for assertion in synthesis.assertions
            ),
        ),
        (
            "all_assertions_clustered",
            bool(synthesis.assertions)
            and assertion_ids <= clustered_assertion_ids,
        ),
        ("coverage_notes_present", bool(synthesis.coverage_notes)),
    )
    passed_checks = tuple(name for name, passed in structural_checks if passed)
    failed_checks = tuple(name for name, passed in structural_checks if not passed)
    structure = DimensionAssessment(
        dimension_id="structural_completeness",
        status="assessed",
        score=len(passed_checks) / len(structural_checks),
        rationale=(
            f"Passed {len(passed_checks)} of {len(structural_checks)} structural "
            f"checks; passed: {', '.join(passed_checks) or 'none'}; "
            f"missing: {', '.join(failed_checks) or 'none'}."
        ),
        evidence_refs=(synthesis.id,),
    )

    return evaluate(
        synthesis,
        DEFAULT_PROVENANCE_STRUCTURE_RUBRIC,
        (provenance, structure),
        run_id,
        reviewer,
    )


__all__ = [
    "DEFAULT_PROVENANCE_STRUCTURE_RUBRIC",
    "WEIGHTED_MEAN_V1",
    "EvaluationEngine",
    "EvaluationError",
    "MissingAssessmentError",
    "UnsupportedAggregationError",
    "evaluate",
    "evaluate_synthesis_artifact",
    "provenance_structure_rubric",
]
