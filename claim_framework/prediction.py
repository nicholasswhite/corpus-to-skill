"""Prospective, domain-neutral prediction registration and scoring.

This module operates only on pre-registered synthetic or externally supplied
records.  It fetches no live data and makes no claim about historical accuracy.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence, Tuple

from claim_framework.jsonio import stable_id
from claim_framework.records import (
    CanonicalClaim,
    ContractError,
    EligibilityResult,
    OutcomeObservation,
    PredictionSpec,
    PredictiveScore,
    prediction_spec_content_hash,
)


SCORING_METHOD_VERSION = "domain-neutral-v1"
DEFAULT_MINIMUM_RESOLVED = 30
_INELIGIBLE_CLAIM_TYPES = {"normative", "definitional", "procedural"}
_SUPPORTED_SCORING_RULES = {
    "brier",
    "absolute_error",
    "squared_error",
    "accuracy",
}
_DIRECTION_OPERATORS = {
    "above": "gt",
    "greater_than": "gt",
    "exceeds": "gt",
    "more_than": "gt",
    "at_least": "ge",
    "at_or_above": "ge",
    "greater_than_or_equal": "ge",
    "greater_than_or_equal_to": "ge",
    "below": "lt",
    "less_than": "lt",
    "under": "lt",
    "at_most": "le",
    "at_or_below": "le",
    "less_than_or_equal": "le",
    "less_than_or_equal_to": "le",
    "equal": "eq",
    "equal_to": "eq",
    "equals": "eq",
    "not_equal": "ne",
    "not_equal_to": "ne",
}
_MATERIAL_SPEC_FIELDS = {
    "target_metric",
    "population",
    "direction",
    "threshold",
    "unit",
    "probability_or_forecast",
    "issue_or_information_cutoff_time",
    "evaluation_window",
    "outcome_data_source",
    "resolution_rule",
    "scoring_rule",
    "benchmark_or_base_rate",
}


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_eligibility(claim: CanonicalClaim) -> EligibilityResult:
    """Conservatively identify claims that can support a prospective spec.

    Eligibility does not construct a prediction or infer missing registration
    fields.  A metric and an explicit temporal boundary must already exist in
    the canonical claim's structured semantics.
    """
    if not isinstance(claim, CanonicalClaim):
        raise TypeError("predictive eligibility expects a CanonicalClaim")

    reasons = []
    if claim.claim_type in _INELIGIBLE_CLAIM_TYPES:
        reasons.append(
            f"{claim.claim_type} claims are not prospective falsifiable outcomes"
        )
    elif claim.claim_type == "historical_observation":
        reasons.append(
            "historical observations are already outcome-aware, not prospective predictions"
        )

    if claim.status == "deprecated":
        reasons.append("deprecated claims are not eligible for new predictions")
    if claim.review_status == "rejected":
        reasons.append("rejected claims are not eligible for new predictions")

    outcome = claim.semantics.outcome
    metric = outcome.get("metric") or outcome.get("target_metric")
    if not _is_nonempty_text(metric):
        reasons.append(
            "claim is non-falsifiable as recorded because no measurable outcome metric is identified"
        )

    explicit_time = claim.semantics.time_horizon or claim.semantics.temporal_scope
    if not explicit_time:
        for key in (
            "deadline",
            "evaluation_window",
            "evaluation_window_end",
            "window",
            "window_end",
            "time_horizon",
            "temporal_scope",
        ):
            value = outcome.get(key)
            if value is not None and value != "" and value != {}:
                explicit_time = value
                break
    if not explicit_time:
        reasons.append(
            "claim is non-falsifiable as recorded because no outcome time boundary is identified"
        )

    return EligibilityResult(
        canonical_claim_id=claim.id,
        status="ineligible" if reasons else "eligible",
        reasons=tuple(reasons),
    )


def export_prediction_candidates(
    claims: Iterable[CanonicalClaim],
) -> Tuple[EligibilityResult, ...]:
    """Export deterministic eligibility records without inventing draft fields.

    A complete draft needs a cutoff, exact window, forecast, resolution rule,
    and authorized outcome source.  Canonical claims do not guarantee those
    fields, so this seam deliberately exports eligibility only.
    """
    by_id = {}
    for claim in claims:
        if not isinstance(claim, CanonicalClaim):
            raise TypeError("prediction export expects CanonicalClaim records")
        existing = by_id.get(claim.id)
        if existing is not None and existing != claim:
            raise ContractError(
                f"canonical claim id {claim.id!r} appears with conflicting records"
            )
        by_id[claim.id] = claim
    return tuple(check_eligibility(by_id[claim_id]) for claim_id in sorted(by_id))


def _number(name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ContractError(f"{name} must be a finite number")
    return float(value)


def _probability(name: str, value: Any) -> float:
    probability = _number(name, value)
    if not 0.0 <= probability <= 1.0:
        raise ContractError(f"{name} must be between 0 and 1")
    return probability


def _normalized_direction(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _validate_scoring_configuration(spec: PredictionSpec) -> None:
    if spec.scoring_rule == "log_loss":
        raise ContractError(
            "log_loss is not implemented by the built-in scorer; no implicit "
            "probability clipping policy is applied"
        )
    if spec.scoring_rule == "custom":
        raise ContractError(
            "custom scoring requires an explicitly versioned external scorer"
        )
    if spec.scoring_rule in ("brier", "accuracy"):
        if (spec.direction is None) != (spec.threshold is None):
            raise ContractError(
                "event scoring requires both direction and threshold when either is set"
            )
        if spec.direction is not None:
            direction = _normalized_direction(spec.direction)
            if direction not in _DIRECTION_OPERATORS:
                raise ContractError(f"unsupported event direction {spec.direction!r}")
            _number("prediction_spec.threshold", spec.threshold)
    if spec.scoring_rule == "brier":
        _probability(
            "prediction_spec.probability_or_forecast",
            spec.probability_or_forecast,
        )
        if spec.benchmark_or_base_rate is not None:
            _probability(
                "prediction_spec.benchmark_or_base_rate",
                spec.benchmark_or_base_rate,
            )
    elif spec.scoring_rule in ("absolute_error", "squared_error"):
        _number(
            "prediction_spec.probability_or_forecast",
            spec.probability_or_forecast,
        )
        if spec.benchmark_or_base_rate is not None:
            _number(
                "prediction_spec.benchmark_or_base_rate",
                spec.benchmark_or_base_rate,
            )
    elif spec.scoring_rule == "accuracy" and (
        spec.direction is not None or spec.threshold is not None
    ):
        _probability(
            "prediction_spec.probability_or_forecast",
            spec.probability_or_forecast,
        )
        if spec.benchmark_or_base_rate is not None:
            _probability(
                "prediction_spec.benchmark_or_base_rate",
                spec.benchmark_or_base_rate,
            )


def freeze_prediction(
    spec: PredictionSpec,
    claim: CanonicalClaim,
    *,
    registered_at: str,
) -> PredictionSpec:
    """Recheck the actual claim, then freeze content and registration time."""
    if not isinstance(spec, PredictionSpec):
        raise TypeError("freeze_prediction expects a PredictionSpec")
    if not isinstance(claim, CanonicalClaim):
        raise TypeError("freeze_prediction expects a CanonicalClaim")
    if spec.status != "draft":
        raise ContractError("only a draft prediction can be frozen")
    if claim.id != spec.canonical_claim_id:
        raise ContractError("canonical claim does not match the prediction spec")
    eligibility = check_eligibility(claim)
    if not eligibility.eligible:
        raise ContractError(
            "an ineligible claim cannot be frozen: " + "; ".join(eligibility.reasons)
        )
    claim_metric = claim.semantics.outcome.get("metric") or claim.semantics.outcome.get(
        "target_metric"
    )
    if spec.target_metric != claim_metric:
        raise ContractError(
            "prediction target_metric does not match the canonical claim outcome metric"
        )
    _validate_scoring_configuration(spec)
    return replace(
        spec,
        status="frozen",
        registered_at=registered_at,
        frozen_content_hash=prediction_spec_content_hash(
            spec, registered_at=registered_at
        ),
    )


register_and_freeze = freeze_prediction


def new_prediction_version(
    spec: PredictionSpec,
    new_id: str,
    **changes: Any,
) -> PredictionSpec:
    """Create an editable successor; frozen material is never edited in place."""
    if not isinstance(spec, PredictionSpec):
        raise TypeError("new_prediction_version expects a PredictionSpec")
    if spec.status == "draft":
        raise ContractError("a draft does not need a successor version")
    if new_id == spec.id:
        raise ContractError("a new prediction version requires a new id")
    unknown = sorted(set(changes) - _MATERIAL_SPEC_FIELDS)
    if unknown:
        raise ContractError(
            "unsupported prediction revision field(s): " + ", ".join(unknown)
        )
    if not changes:
        raise ContractError("a new prediction version requires a material change")
    return replace(
        spec,
        id=new_id,
        version=spec.version + 1,
        supersedes_prediction_spec_id=spec.id,
        status="draft",
        status_reason=None,
        registered_at=None,
        frozen_content_hash=None,
        **changes,
    )


def mark_awaiting_outcome(spec: PredictionSpec) -> PredictionSpec:
    if spec.status != "frozen":
        raise ContractError("only a frozen prediction can become awaiting_outcome")
    return replace(spec, status="awaiting_outcome")


def mark_unresolvable(spec: PredictionSpec, reason: str) -> PredictionSpec:
    if spec.status not in ("frozen", "awaiting_outcome"):
        raise ContractError(
            "only a frozen or awaiting prediction can become unresolvable"
        )
    return replace(spec, status="unresolvable", status_reason=reason)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _between_inclusive(value: str, start: str, end: str) -> bool:
    try:
        return _timestamp(start) <= _timestamp(value) <= _timestamp(end)
    except TypeError as exc:
        raise ContractError(
            "prediction and observation timestamps must use compatible timezone notation"
        ) from exc


def _not_before(value: str, earliest: str) -> bool:
    try:
        return _timestamp(value) >= _timestamp(earliest)
    except TypeError as exc:
        raise ContractError("timestamps must use compatible timezone notation") from exc


def record_outcome(
    spec: PredictionSpec,
    observation: OutcomeObservation,
) -> OutcomeObservation:
    """Validate one authorized observation against its frozen registration."""
    if not isinstance(spec, PredictionSpec):
        raise TypeError("record_outcome expects a PredictionSpec")
    if not isinstance(observation, OutcomeObservation):
        raise TypeError("record_outcome expects an OutcomeObservation")
    if spec.status == "draft":
        raise ContractError("outcomes cannot be attached to a draft prediction")
    if spec.status == "unresolvable":
        raise ContractError("outcomes cannot be attached after unresolvable status")
    if observation.prediction_spec_id != spec.id:
        raise ContractError("outcome observation references a different prediction spec")
    if observation.source_ref != spec.outcome_data_source:
        raise ContractError("outcome source does not match the registered data source")
    if not _not_before(
        observation.observed_at, spec.issue_or_information_cutoff_time
    ):
        raise ContractError("outcome observation precedes the information cutoff")
    if not _between_inclusive(
        observation.observed_at,
        spec.evaluation_window.start,
        spec.evaluation_window.end,
    ):
        raise ContractError("outcome observation falls outside the evaluation window")
    return observation


def _event_value(spec: PredictionSpec, observed: Any) -> int:
    if spec.direction is None and spec.threshold is None:
        if isinstance(observed, bool):
            return int(observed)
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            numeric = _number("outcome observation value", observed)
            if numeric in (0.0, 1.0):
                return int(numeric)
        raise ContractError(
            "event scoring requires a binary outcome or a registered direction and threshold"
        )
    if spec.direction is None or spec.threshold is None:
        raise ContractError(
            "event resolution requires both direction and threshold when either is set"
        )

    observed_number = _number("outcome observation value", observed)
    threshold = _number("prediction_spec.threshold", spec.threshold)
    direction = _normalized_direction(spec.direction)
    operator = _DIRECTION_OPERATORS.get(direction)
    if operator == "gt":
        result = observed_number > threshold
    elif operator == "ge":
        result = observed_number >= threshold
    elif operator == "lt":
        result = observed_number < threshold
    elif operator == "le":
        result = observed_number <= threshold
    elif operator == "eq":
        result = observed_number == threshold
    elif operator == "ne":
        result = observed_number != threshold
    else:
        raise ContractError(f"unsupported event direction {spec.direction!r}")
    return int(result)


def _accuracy_value(spec: PredictionSpec, forecast: Any, observed: Any) -> float:
    if spec.direction is not None or spec.threshold is not None:
        predicted = int(_probability("accuracy forecast", forecast) >= 0.5)
        return float(predicted == _event_value(spec, observed))
    if (
        isinstance(forecast, (int, float))
        and not isinstance(forecast, bool)
        and isinstance(observed, (bool, int, float))
        and not (
            isinstance(observed, (int, float))
            and not isinstance(observed, bool)
            and float(observed) not in (0.0, 1.0)
        )
        and 0.0 <= float(forecast) <= 1.0
    ):
        predicted = int(float(forecast) >= 0.5)
        return float(predicted == _event_value(spec, observed))
    return float(forecast == observed)


def _score_one(spec: PredictionSpec, forecast: Any, observed: Any) -> float:
    if spec.scoring_rule == "brier":
        return (_probability("forecast", forecast) - _event_value(spec, observed)) ** 2
    if spec.scoring_rule == "absolute_error":
        return abs(_number("forecast", forecast) - _number("outcome", observed))
    if spec.scoring_rule == "squared_error":
        difference = _number("forecast", forecast) - _number("outcome", observed)
        return difference * difference
    if spec.scoring_rule == "accuracy":
        return _accuracy_value(spec, forecast, observed)
    raise ContractError(
        f"scoring rule {spec.scoring_rule!r} has no built-in deterministic scorer"
    )


def _unique_observations(
    spec: PredictionSpec,
    observations: Sequence[OutcomeObservation],
) -> Tuple[OutcomeObservation, ...]:
    by_id = {}
    for observation in observations:
        validated = record_outcome(spec, observation)
        existing = by_id.get(validated.id)
        if existing is not None and existing != validated:
            raise ContractError(
                f"outcome observation id {validated.id!r} has conflicting records"
            )
        by_id[validated.id] = validated
    return tuple(by_id[observation_id] for observation_id in sorted(by_id))


def mark_resolved(
    spec: PredictionSpec,
    observations: Sequence[OutcomeObservation],
) -> PredictionSpec:
    """Reach resolved status only after at least one valid linked observation."""
    if spec.status not in ("frozen", "awaiting_outcome"):
        raise ContractError(
            "only a frozen or awaiting prediction can become resolved"
        )
    if not _unique_observations(spec, observations):
        raise ContractError("resolved status requires at least one valid observation")
    return replace(spec, status="resolved")


def _score_id(
    spec: PredictionSpec,
    observations: Sequence[OutcomeObservation],
    status: str,
    scored_at: str,
    run_id: str,
    scoring_method_version: str,
) -> str:
    return stable_id(
        "predictive-score",
        {
            "prediction_spec_id": spec.id,
            "outcome_observation_ids": [item.id for item in observations],
            "status": status,
            "scored_at": scored_at,
            "run_id": run_id,
            "scoring_method_version": scoring_method_version,
        },
    )


def score_prediction(
    spec: PredictionSpec,
    observations: Sequence[OutcomeObservation],
    *,
    scored_at: str,
    run_id: str,
    score_id: Optional[str] = None,
    scoring_method_version: str = SCORING_METHOD_VERSION,
) -> PredictiveScore:
    """Score valid observations or return an honest non-numeric status result."""
    if not isinstance(spec, PredictionSpec):
        raise TypeError("score_prediction expects a PredictionSpec")
    if spec.status == "draft":
        raise ContractError("a draft prediction cannot be scored")

    if spec.status == "unresolvable":
        if observations:
            raise ContractError(
                "an unresolvable prediction cannot carry outcome observations"
            )
        status = "unresolvable"
        return PredictiveScore(
            id=score_id
            or _score_id(
                spec, (), status, scored_at, run_id, scoring_method_version
            ),
            prediction_spec_id=spec.id,
            outcome_observation_ids=(),
            metric=spec.scoring_rule,
            value=None,
            benchmark_value=None,
            sample_size=0,
            uncertainty=None,
            scoring_method_version=scoring_method_version,
            scored_at=scored_at,
            run_id=run_id,
            status=status,
            reason=spec.status_reason,
        )

    valid_observations = _unique_observations(spec, observations)
    if not valid_observations:
        status = "awaiting_outcome"
        return PredictiveScore(
            id=score_id
            or _score_id(
                spec, (), status, scored_at, run_id, scoring_method_version
            ),
            prediction_spec_id=spec.id,
            outcome_observation_ids=(),
            metric=spec.scoring_rule,
            value=None,
            benchmark_value=None,
            sample_size=0,
            uncertainty=None,
            scoring_method_version=scoring_method_version,
            scored_at=scored_at,
            run_id=run_id,
            status=status,
            reason="no valid outcome observation has been recorded",
        )

    if spec.scoring_rule not in _SUPPORTED_SCORING_RULES:
        raise ContractError(
            f"scoring rule {spec.scoring_rule!r} is not implemented by {SCORING_METHOD_VERSION}"
        )
    for observation in valid_observations:
        if not _not_before(scored_at, observation.observed_at):
            raise ContractError("score timestamp precedes an outcome observation")

    values = tuple(
        _score_one(spec, spec.probability_or_forecast, observation.value)
        for observation in valid_observations
    )
    value = statistics.fmean(values)
    uncertainty = (
        statistics.stdev(values) / math.sqrt(len(values))
        if len(values) > 1
        else None
    )
    benchmark_value = None
    if spec.benchmark_or_base_rate is not None:
        benchmark_value = statistics.fmean(
            _score_one(spec, spec.benchmark_or_base_rate, observation.value)
            for observation in valid_observations
        )

    status = "resolved"
    return PredictiveScore(
        id=score_id
        or _score_id(
            spec,
            valid_observations,
            status,
            scored_at,
            run_id,
            scoring_method_version,
        ),
        prediction_spec_id=spec.id,
        outcome_observation_ids=tuple(item.id for item in valid_observations),
        metric=spec.scoring_rule,
        value=value,
        benchmark_value=benchmark_value,
        sample_size=len(valid_observations),
        uncertainty=uncertainty,
        scoring_method_version=scoring_method_version,
        scored_at=scored_at,
        run_id=run_id,
        status=status,
    )


@dataclass(frozen=True)
class PredictiveAggregate:
    """A transparent score summary that suppresses small-sample numerics."""

    status: str
    metric: Optional[str]
    score_count: int
    resolved_count: int
    awaiting_outcome_count: int
    unresolvable_count: int
    observation_count: int
    minimum_resolved_count: int
    benchmark_count: int
    mean_score: Optional[float]
    mean_benchmark: Optional[float]
    uncertainty: Optional[float]
    uncertainty_method: Optional[str]
    score_ids: Tuple[str, ...]
    reason: Optional[str] = None


def aggregate_scores(
    scores: Sequence[PredictiveScore],
    *,
    minimum_resolved_count: int = DEFAULT_MINIMUM_RESOLVED,
) -> PredictiveAggregate:
    """Aggregate one metric only, withholding numerics below the declared gate."""
    if (
        not isinstance(minimum_resolved_count, int)
        or isinstance(minimum_resolved_count, bool)
        or minimum_resolved_count < 2
    ):
        raise ContractError("minimum_resolved_count must be an integer of at least 2")

    by_id = {}
    for score in scores:
        if not isinstance(score, PredictiveScore):
            raise TypeError("aggregate_scores expects PredictiveScore records")
        existing = by_id.get(score.id)
        if existing is not None and existing != score:
            raise ContractError(f"predictive score id {score.id!r} has conflicting records")
        by_id[score.id] = score
    ordered = tuple(by_id[score_id] for score_id in sorted(by_id))
    prediction_spec_ids = [score.prediction_spec_id for score in ordered]
    if len(set(prediction_spec_ids)) != len(prediction_spec_ids):
        raise ContractError(
            "aggregate_scores accepts only one score record per prediction spec"
        )
    metrics = {score.metric for score in ordered}
    if len(metrics) > 1:
        raise ContractError("predictive scores with different metrics cannot be aggregated")
    metric = next(iter(metrics)) if metrics else None
    resolved = tuple(score for score in ordered if score.status == "resolved")
    awaiting_count = sum(score.status == "awaiting_outcome" for score in ordered)
    unresolvable_count = sum(score.status == "unresolvable" for score in ordered)
    observation_count = sum(score.sample_size for score in resolved)
    benchmark_scores = tuple(
        score.benchmark_value
        for score in resolved
        if score.benchmark_value is not None
    )

    if len(resolved) < minimum_resolved_count:
        return PredictiveAggregate(
            status="insufficient_sample",
            metric=metric,
            score_count=len(ordered),
            resolved_count=len(resolved),
            awaiting_outcome_count=awaiting_count,
            unresolvable_count=unresolvable_count,
            observation_count=observation_count,
            minimum_resolved_count=minimum_resolved_count,
            benchmark_count=len(benchmark_scores),
            mean_score=None,
            mean_benchmark=None,
            uncertainty=None,
            uncertainty_method=None,
            score_ids=tuple(score.id for score in ordered),
            reason=(
                f"requires at least {minimum_resolved_count} resolved predictions; "
                f"received {len(resolved)}"
            ),
        )

    resolved_values = tuple(score.value for score in resolved if score.value is not None)
    uncertainty = statistics.stdev(resolved_values) / math.sqrt(len(resolved_values))
    return PredictiveAggregate(
        status="aggregated",
        metric=metric,
        score_count=len(ordered),
        resolved_count=len(resolved),
        awaiting_outcome_count=awaiting_count,
        unresolvable_count=unresolvable_count,
        observation_count=observation_count,
        minimum_resolved_count=minimum_resolved_count,
        benchmark_count=len(benchmark_scores),
        mean_score=statistics.fmean(resolved_values),
        mean_benchmark=(
            statistics.fmean(benchmark_scores)
            if len(benchmark_scores) >= minimum_resolved_count
            else None
        ),
        uncertainty=uncertainty,
        uncertainty_method="standard_error_of_prediction_scores",
        score_ids=tuple(score.id for score in ordered),
    )


__all__ = [
    "DEFAULT_MINIMUM_RESOLVED",
    "PredictiveAggregate",
    "SCORING_METHOD_VERSION",
    "aggregate_scores",
    "check_eligibility",
    "export_prediction_candidates",
    "freeze_prediction",
    "mark_awaiting_outcome",
    "mark_resolved",
    "mark_unresolvable",
    "new_prediction_version",
    "record_outcome",
    "register_and_freeze",
    "score_prediction",
]
