"""Focused tests for reusable, non-predictive evaluation."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from claim_framework.evaluation import (
    DEFAULT_PROVENANCE_STRUCTURE_RUBRIC,
    WEIGHTED_MEAN_V1,
    EvaluationEngine,
    EvaluationError,
    MissingAssessmentError,
    evaluate,
    evaluate_synthesis_artifact,
)
from claim_framework.jsonio import canonical_dumps, loads_record
from claim_framework.records import (
    DimensionAssessment,
    EvaluationDimension,
    EvaluationRecord,
    EvaluationRubric,
    SynthesisArtifact,
    SynthesisAssertion,
    TopicCluster,
)


def _dimension(
    dimension_id,
    *,
    weight=None,
    missing_policy="unknown",
):
    return EvaluationDimension(
        id=dimension_id,
        question=f"How complete is {dimension_id}?",
        scale={
            "minimum": 0.0,
            "maximum": 1.0,
            "anchors": {"0": "absent", "1": "complete"},
        },
        weight=weight,
        missing_policy=missing_policy,
    )


def _rubric(*dimensions, aggregation_method=WEIGHTED_MEAN_V1):
    return EvaluationRubric(
        id="fixture-rubric",
        version="1.2.0",
        target_type="evidence",
        dimensions=dimensions,
        aggregation_method=aggregation_method,
    )


def _assessed(dimension_id, score):
    return DimensionAssessment(
        dimension_id=dimension_id,
        status="assessed",
        score=score,
        rationale="The fixture directly supplies this score.",
        evidence_refs=(f"evidence:{dimension_id}",),
        uncertainty={"kind": "fixture", "bounded": True},
    )


def test_missing_unknown_is_explicit_and_never_becomes_zero():
    rubric = _rubric(
        _dimension("observed", weight=1.0),
        _dimension("missing", weight=9.0, missing_policy="unknown"),
    )

    record = evaluate(
        "target-1",
        rubric,
        (_assessed("observed", 0.8),),
        run_id="evaluation-run-1",
    )

    missing = next(
        item for item in record.dimensions if item.dimension_id == "missing"
    )
    assert missing.status == "unknown"
    assert missing.score is None
    assert record.aggregate_score == pytest.approx(0.8)
    assert record.aggregate_component_ids == ("observed",)
    assert record.component_weights == {"observed": 1.0, "missing": 9.0}


def test_missing_not_applicable_is_distinct_and_excluded_from_aggregate():
    rubric = _rubric(
        _dimension("not_relevant", missing_policy="not_applicable"),
    )

    record = EvaluationEngine().evaluate(
        "target-2", rubric, run_id="evaluation-run-2"
    )

    assert record.dimensions[0].status == "not_applicable"
    assert record.dimensions[0].score is None
    assert record.aggregate_score is None
    assert record.aggregate_component_ids == ()


def test_fail_missing_policy_rejects_absent_and_unknown_results():
    rubric = _rubric(_dimension("required", missing_policy="fail"))

    with pytest.raises(MissingAssessmentError, match="requires an assessment"):
        evaluate("target-3", rubric, run_id="evaluation-run-3")

    unknown = DimensionAssessment(
        dimension_id="required",
        status="unknown",
        rationale="The required evidence could not be located.",
    )
    with pytest.raises(MissingAssessmentError, match="does not permit an unknown"):
        evaluate(
            "target-3",
            rubric,
            (unknown,),
            run_id="evaluation-run-3",
        )


def test_weighted_mean_exposes_every_weight_and_included_component():
    rubric = _rubric(
        _dimension("first", weight=1.0),
        _dimension("second", weight=3.0),
        _dimension("unknown", weight=8.0),
    )
    assessments = {
        "second": _assessed("second", 0.75),
        "first": _assessed("first", 0.25),
    }

    record = evaluate(
        {"id": "target-4", "target_type": "evidence"},
        rubric,
        assessor_results=assessments,
        run_id="evaluation-run-4",
        reviewer="fixture-reviewer",
    )

    assert record.aggregate_score == pytest.approx(0.625)
    assert record.aggregation_method == WEIGHTED_MEAN_V1
    assert record.aggregate_component_ids == ("first", "second")
    assert record.component_weights == {
        "first": 1.0,
        "second": 3.0,
        "unknown": 8.0,
    }
    assert [item.dimension_id for item in record.dimensions] == [
        "first",
        "second",
        "unknown",
    ]


def test_fixed_inputs_are_reproducible_and_records_round_trip():
    rubric = _rubric(
        _dimension("a", weight=2.0),
        _dimension("b", weight=1.0),
    )
    first = _assessed("a", 0.5)
    second = _assessed("b", 1.0)

    forward = evaluate(
        "stable-target",
        rubric,
        (first, second),
        run_id="stable-run",
    )
    reverse = evaluate(
        "stable-target",
        rubric,
        (second, first),
        run_id="stable-run",
    )

    assert reverse == forward
    assert reverse.id == forward.id
    assert loads_record(canonical_dumps(forward), EvaluationRecord) == forward
    assert loads_record(
        canonical_dumps(rubric), EvaluationRubric
    ) == rubric


def test_contracts_are_frozen_validate_scales_and_target_types():
    dimension = _dimension("bounded")
    with pytest.raises(FrozenInstanceError):
        dimension.id = "changed"
    with pytest.raises(TypeError):
        dimension.scale["minimum"] = -1.0
    with pytest.raises(TypeError):
        dimension.scale["anchors"]["0"] = "mutated"

    with pytest.raises(ValueError, match="explicitly define"):
        EvaluationDimension(
            id="ambiguous",
            question="Is this explicit?",
            scale={},
        )
    with pytest.raises(ValueError, match="between"):
        evaluate(
            "target",
            _rubric(dimension),
            (_assessed("bounded", 2.0),),
            run_id="run",
        )
    with pytest.raises(EvaluationError, match="cannot evaluate"):
        evaluate(
            {"id": "target", "target_type": "skill"},
            _rubric(dimension),
            (_assessed("bounded", 0.5),),
            run_id="run",
        )


def test_qualitative_assessment_is_preserved_but_not_aggregated():
    rubric = _rubric(_dimension("qualitative"))
    qualitative = DimensionAssessment(
        dimension_id="qualitative",
        status="assessed",
        rationale="Reviewed qualitatively; the rubric does not require a number.",
    )

    record = evaluate(
        "target", rubric, (qualitative,), run_id="qualitative-run"
    )

    assert record.dimensions == (qualitative,)
    assert record.aggregate_score is None
    assert record.aggregate_component_ids == ()


def _synthesis(assertions):
    assertion_ids = tuple(assertion.id for assertion in assertions)
    return SynthesisArtifact(
        id="synthesis-fixture",
        corpus_id="corpus-fixture",
        topic_clusters=(
            TopicCluster(
                id="topic-fixture",
                topic="Fixture topic",
                canonical_claim_ids=("canonical-fixture",),
                assertion_ids=assertion_ids,
            ),
        ),
        assertions=assertions,
        disputes=(),
        unresolved_questions=(),
        coverage_notes=("Fixture coverage is intentionally bounded.",),
        run_id="synthesis-run",
    )


def _synthesis_assertion(assertion_id):
    return SynthesisAssertion(
        id=assertion_id,
        text=f"Material assertion {assertion_id}.",
        status="consensus",
        canonical_claim_ids=("canonical-fixture",),
        supporting_source_claim_ids=("source-claim-fixture",),
        opposing_source_claim_ids=(),
        rationale="Fixture assertion with explicit claim links.",
    )


def test_synthesis_adapter_is_deterministic_and_uses_explicit_ratios():
    synthesis = _synthesis(
        (_synthesis_assertion("assertion-a"), _synthesis_assertion("assertion-b"))
    )

    forward = evaluate_synthesis_artifact(
        synthesis,
        ("assertion-b", "assertion-a"),
        "adapter-run",
    )
    reverse = evaluate_synthesis_artifact(
        synthesis,
        ("assertion-a", "assertion-b"),
        "adapter-run",
    )

    assert forward == reverse
    by_id = {item.dimension_id: item for item in forward.dimensions}
    assert by_id["provenance_completeness"].score == 1.0
    assert by_id["provenance_completeness"].evidence_refs == (
        "assertion-a",
        "assertion-b",
    )
    assert by_id["structural_completeness"].score == 1.0
    assert "Passed 5 of 5" in by_id["structural_completeness"].rationale
    assert forward.aggregate_score == 1.0


def test_synthesis_adapter_marks_empty_provenance_not_applicable():
    synthesis = _synthesis(())

    record = evaluate_synthesis_artifact(synthesis, (), "empty-adapter-run")

    provenance = next(
        item
        for item in record.dimensions
        if item.dimension_id == "provenance_completeness"
    )
    structure = next(
        item
        for item in record.dimensions
        if item.dimension_id == "structural_completeness"
    )
    assert provenance.status == "not_applicable"
    assert provenance.score is None
    assert structure.status == "assessed"
    assert record.aggregate_component_ids == ("structural_completeness",)


def test_default_example_is_named_versioned_and_domain_neutral():
    rubric = DEFAULT_PROVENANCE_STRUCTURE_RUBRIC

    assert rubric.id == "provenance-structure"
    assert rubric.version == "1.0.0"
    assert rubric.target_type == "synthesis"
    assert {item.id for item in rubric.dimensions} == {
        "provenance_completeness",
        "structural_completeness",
    }


def test_importing_evaluation_does_not_import_book_or_predictive_modules():
    script = """
import sys
import claim_framework.evaluation
assert not any(name.startswith('book_to_skill') for name in sys.modules)
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
