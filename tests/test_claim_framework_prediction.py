"""Hand-computed, offline checks for the prospective prediction foundation."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from claim_framework.jsonio import canonical_dumps, loads_record, sha256_text
from claim_framework.prediction import (
    aggregate_scores,
    check_eligibility,
    export_prediction_candidates,
    freeze_prediction,
    mark_awaiting_outcome,
    mark_resolved,
    mark_unresolvable,
    new_prediction_version,
    record_outcome,
    score_prediction,
)
from claim_framework.records import (
    CanonicalClaim,
    ClaimSemantics,
    ContractError,
    EligibilityResult,
    EvaluationWindow,
    OutcomeObservation,
    PredictionSpec,
    PredictiveScore,
)


CUTOFF = "2026-01-01T00:00:00Z"
REGISTERED_AT = "2025-12-31T12:00:00Z"
WINDOW_START = "2026-01-02T00:00:00Z"
WINDOW_END = "2026-01-04T00:00:00Z"
SOURCE_REF = "fixture://authorized-outcomes-v1"


def _claim(
    claim_id="claim-predictive",
    *,
    claim_type="predictive",
    outcome=None,
    time_horizon="two days",
):
    return CanonicalClaim(
        id=claim_id,
        canonical_proposition="Metric M will be above 10 during the registered window.",
        claim_type=claim_type,
        semantics=ClaimSemantics(
            subject="synthetic population",
            relation="has metric",
            object_or_value="M",
            polarity="positive",
            outcome={"metric": "M"} if outcome is None else outcome,
            time_horizon=time_horizon,
        ),
        member_source_claim_ids=(f"source-{claim_id}",),
        preserved_variants=("Synthetic prospective statement.",),
        normalization_rationale="Synthetic fixture.",
        normalization_run_id="run-normalize",
    )


def _draft(
    prediction_id="prediction-1",
    *,
    scoring_rule="brier",
    forecast=0.8,
    benchmark=0.5,
    direction="above",
    threshold=10.0,
):
    return PredictionSpec(
        id=prediction_id,
        canonical_claim_id="claim-predictive",
        target_metric="M",
        population="synthetic population",
        direction=direction,
        threshold=threshold,
        unit="synthetic units",
        probability_or_forecast=forecast,
        issue_or_information_cutoff_time=CUTOFF,
        evaluation_window=EvaluationWindow(WINDOW_START, WINDOW_END),
        outcome_data_source=SOURCE_REF,
        resolution_rule="Resolve using the recorded numeric fixture value.",
        scoring_rule=scoring_rule,
        benchmark_or_base_rate=benchmark,
    )


def _frozen(**changes):
    return freeze_prediction(
        _draft(**changes),
        _claim(),
        registered_at=REGISTERED_AT,
    )


def _observation(
    observation_id,
    value,
    *,
    prediction_spec_id="prediction-1",
    observed_at="2026-01-03T00:00:00Z",
    source_ref=SOURCE_REF,
):
    return OutcomeObservation(
        id=observation_id,
        prediction_spec_id=prediction_spec_id,
        observed_at=observed_at,
        value=value,
        source_ref=source_ref,
        source_checksum=sha256_text(f"{observation_id}:{value}"),
        collection_run_id="run-collect",
    )


@pytest.mark.parametrize("claim_type", ("normative", "definitional", "procedural"))
def test_eligibility_rejects_non_predictive_claim_types_with_reasons(claim_type):
    result = check_eligibility(_claim(claim_type=claim_type))

    assert result.status == "ineligible"
    assert not result.eligible
    assert any(claim_type in reason for reason in result.reasons)


def test_eligibility_rejects_non_falsifiable_claim_and_accepts_structured_one():
    missing = check_eligibility(
        _claim(outcome={}, time_horizon=None)
    )
    eligible = check_eligibility(_claim())

    assert missing.status == "ineligible"
    assert len(missing.reasons) == 2
    assert all("non-falsifiable" in reason for reason in missing.reasons)
    assert eligible == EligibilityResult("claim-predictive", "eligible")


def test_corpus_export_is_stable_and_never_invents_prediction_fields():
    eligible = _claim("claim-z")
    normative = _claim("claim-a", claim_type="normative")

    forward = export_prediction_candidates((eligible, normative))
    reverse = export_prediction_candidates((normative, eligible))

    assert forward == reverse
    assert tuple(result.canonical_claim_id for result in forward) == (
        "claim-a",
        "claim-z",
    )
    assert all(isinstance(result, EligibilityResult) for result in forward)
    assert not any(isinstance(result, PredictionSpec) for result in forward)


def test_freeze_hash_round_trip_rejects_edits_and_revision_increments_version():
    draft = _draft()
    frozen = freeze_prediction(
        draft,
        _claim(),
        registered_at=REGISTERED_AT,
    )

    assert draft.status == "draft"
    assert draft.frozen_content_hash is None
    assert frozen.status == "frozen"
    assert frozen.registered_at == REGISTERED_AT
    assert len(frozen.frozen_content_hash) == 64
    assert loads_record(canonical_dumps(frozen), PredictionSpec) == frozen

    with pytest.raises(ContractError, match="new prediction version"):
        replace(frozen, probability_or_forecast=0.7)
    with pytest.raises(ContractError, match="new prediction version"):
        replace(frozen, registered_at="2025-12-30T00:00:00Z")

    revision = new_prediction_version(
        frozen,
        "prediction-2",
        probability_or_forecast=0.7,
    )
    assert revision.status == "draft"
    assert revision.version == 2
    assert revision.supersedes_prediction_spec_id == frozen.id
    assert revision.frozen_content_hash is None
    assert revision.registered_at is None
    assert frozen.probability_or_forecast == 0.8


def test_freeze_rechecks_actual_claim_and_rejects_registration_after_cutoff():
    draft = _draft()
    forged = EligibilityResult("claim-predictive", "eligible")
    normative_claim = _claim(claim_type="normative")

    with pytest.raises(TypeError, match="CanonicalClaim"):
        freeze_prediction(draft, forged, registered_at=REGISTERED_AT)
    with pytest.raises(ContractError, match="ineligible claim"):
        freeze_prediction(
            draft,
            normative_claim,
            registered_at=REGISTERED_AT,
        )
    with pytest.raises(ContractError, match="target_metric"):
        freeze_prediction(
            replace(draft, target_metric="different metric"),
            _claim(),
            registered_at=REGISTERED_AT,
        )
    with pytest.raises(ContractError, match="at or before"):
        freeze_prediction(
            draft,
            _claim(),
            registered_at="2026-01-01T00:00:01Z",
        )

    at_cutoff = freeze_prediction(
        draft,
        _claim(),
        registered_at=CUTOFF,
    )
    assert at_cutoff.registered_at == CUTOFF


def test_pending_and_unresolvable_results_never_have_numeric_scores():
    frozen = _frozen()
    pending = score_prediction(
        frozen,
        (),
        scored_at="2026-01-03T12:00:00Z",
        run_id="run-score-pending",
    )
    unresolvable_spec = mark_unresolvable(
        frozen, "authorized outcome source was permanently withdrawn"
    )
    unresolvable = score_prediction(
        unresolvable_spec,
        (),
        scored_at="2026-01-05T00:00:00Z",
        run_id="run-score-unresolvable",
    )

    assert pending.status == "awaiting_outcome"
    assert pending.value is pending.benchmark_value is pending.uncertainty is None
    assert pending.sample_size == 0
    assert unresolvable.status == "unresolvable"
    assert unresolvable.value is None
    assert "withdrawn" in unresolvable.reason


@pytest.mark.parametrize(
    ("scoring_rule", "message"),
    (
        ("log_loss", "no implicit probability clipping policy"),
        ("custom", "external scorer"),
    ),
)
def test_freeze_rejects_scoring_rules_without_built_in_policy(scoring_rule, message):
    draft = _draft(scoring_rule=scoring_rule)

    with pytest.raises(ContractError, match=message):
        freeze_prediction(draft, _claim(), registered_at=REGISTERED_AT)


def test_freeze_rejects_incomplete_or_unsupported_event_resolution():
    with pytest.raises(ContractError, match="both direction and threshold"):
        freeze_prediction(
            _draft(direction="above", threshold=None),
            _claim(),
            registered_at=REGISTERED_AT,
        )
    with pytest.raises(ContractError, match="unsupported event direction"):
        freeze_prediction(
            _draft(direction="approximately", threshold=10),
            _claim(),
            registered_at=REGISTERED_AT,
        )


def test_resolved_lifecycle_requires_a_valid_observation():
    frozen = _frozen()
    awaiting = mark_awaiting_outcome(frozen)
    observation = _observation("observation-lifecycle", 12)

    with pytest.raises(ContractError, match="requires at least one"):
        mark_resolved(awaiting, ())

    resolved = mark_resolved(awaiting, (observation,))
    score = score_prediction(
        resolved,
        (observation,),
        scored_at="2026-01-04T12:00:00Z",
        run_id="run-score-lifecycle",
    )

    assert resolved.status == "resolved"
    assert awaiting.registered_at == frozen.registered_at == REGISTERED_AT
    assert resolved.registered_at == REGISTERED_AT
    assert score.status == "resolved"
    with pytest.raises(ContractError, match="only a frozen or awaiting"):
        mark_unresolvable(resolved, "too late")


def test_cutoff_window_linkage_source_and_scoring_time_prevent_leakage():
    with pytest.raises(ContractError, match="must be earlier"):
        replace(
            _draft(),
            issue_or_information_cutoff_time="2026-01-03T00:00:00Z",
        )

    frozen = _frozen()
    before_cutoff = _observation(
        "observation-before",
        12,
        observed_at="2025-12-31T00:00:00Z",
    )
    after_window = _observation(
        "observation-after",
        12,
        observed_at="2026-01-05T00:00:00Z",
    )
    wrong_link = _observation(
        "observation-wrong-link", 12, prediction_spec_id="prediction-other"
    )
    wrong_source = _observation(
        "observation-wrong-source", 12, source_ref="fixture://unregistered"
    )

    with pytest.raises(ContractError, match="precedes the information cutoff"):
        record_outcome(frozen, before_cutoff)
    with pytest.raises(ContractError, match="outside the evaluation window"):
        record_outcome(frozen, after_window)
    with pytest.raises(ContractError, match="different prediction spec"):
        record_outcome(frozen, wrong_link)
    with pytest.raises(ContractError, match="registered data source"):
        record_outcome(frozen, wrong_source)

    valid = _observation("observation-valid", 12)
    with pytest.raises(ContractError, match="precedes an outcome"):
        score_prediction(
            frozen,
            (valid,),
            scored_at="2026-01-02T12:00:00Z",
            run_id="run-score-too-early",
        )


def test_brier_and_benchmark_scores_match_hand_computation():
    frozen = _frozen()
    observations = (
        _observation("observation-event", 12, observed_at="2026-01-02T12:00:00Z"),
        _observation("observation-no-event", 8, observed_at="2026-01-03T12:00:00Z"),
    )

    result = score_prediction(
        frozen,
        observations,
        scored_at="2026-01-04T12:00:00Z",
        run_id="run-score-brier",
    )

    # ((0.8 - 1)^2 + (0.8 - 0)^2) / 2 = 0.34
    assert result.value == pytest.approx(0.34)
    # A 0.5 base rate has Brier score 0.25 for either binary outcome.
    assert result.benchmark_value == pytest.approx(0.25)
    assert result.uncertainty == pytest.approx(0.30)
    assert result.sample_size == 2


@pytest.mark.parametrize(
    ("scoring_rule", "expected", "benchmark_expected"),
    (
        ("absolute_error", 2.5, 2.5),
        ("squared_error", 6.5, 8.5),
    ),
)
def test_numeric_error_scores_match_hand_computation(
    scoring_rule, expected, benchmark_expected
):
    frozen = _frozen(
        scoring_rule=scoring_rule,
        forecast=10.0,
        benchmark=9.0,
        direction=None,
        threshold=None,
    )
    observations = (
        _observation("observation-low", 8, observed_at="2026-01-02T12:00:00Z"),
        _observation("observation-high", 13, observed_at="2026-01-03T12:00:00Z"),
    )

    result = score_prediction(
        frozen,
        observations,
        scored_at="2026-01-04T12:00:00Z",
        run_id=f"run-score-{scoring_rule}",
    )

    assert result.value == pytest.approx(expected)
    assert result.benchmark_value == pytest.approx(benchmark_expected)


def test_accuracy_resolves_registered_event_deterministically():
    frozen = _frozen(scoring_rule="accuracy", forecast=0.8, benchmark=0.2)
    result = score_prediction(
        frozen,
        (_observation("observation-accuracy", 12),),
        scored_at="2026-01-04T12:00:00Z",
        run_id="run-score-accuracy",
    )

    assert result.value == 1.0
    assert result.benchmark_value == 0.0


def _aggregate_score(score_id, status, value=None, benchmark=None):
    observation_ids = (f"observation-{score_id}",) if status == "resolved" else ()
    return PredictiveScore(
        id=score_id,
        prediction_spec_id=f"prediction-{score_id}",
        outcome_observation_ids=observation_ids,
        metric="brier",
        value=value,
        benchmark_value=benchmark,
        sample_size=len(observation_ids),
        uncertainty=None,
        scoring_method_version="fixture-v1",
        scored_at="2026-01-05T00:00:00Z",
        run_id="run-aggregate-fixture",
        status=status,
        reason=(None if status == "resolved" else f"fixture {status}"),
    )


def test_aggregate_exposes_counts_and_suppresses_small_sample_numbers():
    first = _aggregate_score("score-a", "resolved", 0.04, 0.25)
    second = _aggregate_score("score-b", "resolved", 0.16, 0.25)
    pending = _aggregate_score("score-c", "awaiting_outcome")
    unresolvable = _aggregate_score("score-d", "unresolvable")

    insufficient = aggregate_scores(
        (first, pending, unresolvable), minimum_resolved_count=2
    )
    aggregate = aggregate_scores(
        (unresolvable, second, pending, first), minimum_resolved_count=2
    )

    assert insufficient.status == "insufficient_sample"
    assert insufficient.mean_score is None
    assert insufficient.mean_benchmark is None
    assert insufficient.uncertainty is None
    assert insufficient.resolved_count == 1
    assert insufficient.awaiting_outcome_count == 1
    assert insufficient.unresolvable_count == 1

    assert aggregate.status == "aggregated"
    assert aggregate.resolved_count == 2
    assert aggregate.observation_count == 2
    assert aggregate.benchmark_count == 2
    assert aggregate.mean_score == pytest.approx(0.10)
    assert aggregate.mean_benchmark == pytest.approx(0.25)
    assert aggregate.uncertainty == pytest.approx(0.06)
    assert aggregate.uncertainty_method == "standard_error_of_prediction_scores"


def test_aggregate_rejects_multiple_score_attempts_for_one_prediction():
    first = _aggregate_score("score-a", "resolved", 0.04, 0.25)
    second_attempt = replace(
        _aggregate_score("score-b", "resolved", 0.16, 0.25),
        prediction_spec_id=first.prediction_spec_id,
    )

    with pytest.raises(ContractError, match="one score record per prediction"):
        aggregate_scores((first, second_attempt), minimum_resolved_count=2)


def test_prediction_module_import_does_not_import_book_to_skill():
    script = """
import sys
import claim_framework.prediction
assert 'book_to_skill' not in sys.modules, sorted(
    name for name in sys.modules if name.startswith('book_to_skill')
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
