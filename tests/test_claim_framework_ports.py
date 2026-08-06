"""Focused runtime tests for domain-neutral extension protocols."""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from claim_framework.jsonio import sha256_text
from claim_framework.ports import (
    AuthorizedOutcomeResolver,
    DomainAdapter,
    DomainProfileProvider,
    EvaluationRubricProvider,
    PredictiveEligibilityProvider,
    PredictivePowerPort,
)
from claim_framework.prediction import (
    aggregate_scores,
    check_eligibility,
    freeze_prediction,
    record_outcome,
    score_prediction,
)
from claim_framework.records import (
    CanonicalClaim,
    ClaimSemantics,
    DomainProfile,
    EligibilityResult,
    EvaluationDimension,
    EvaluationRubric,
    EvaluationWindow,
    MetricDefinition,
    OutcomeObservation,
    PredictionSpec,
    PredictiveScore,
    SkillBuildManifest,
    SourceClaim,
    SynthesisArtifact,
)
from claim_framework.status import (
    ADAPTER_INTERFACE_STABILITY,
    CORE_INTERFACE_STABILITY,
    EVALUATION_INTERFACE_STABILITY,
    EXPERIMENTAL,
    INTERFACE_STABILITY,
    PREDICTION_INTERFACE_STABILITY,
    SCAFFOLDED,
    STABLE,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
CUTOFF = "2026-01-01T00:00:00Z"
WINDOW_START = "2026-01-02T00:00:00Z"
WINDOW_END = "2026-01-04T00:00:00Z"
OUTCOME_SOURCE = "fixture://incident-outcomes-v1"


def _claim() -> CanonicalClaim:
    return CanonicalClaim(
        id="claim-response-time",
        canonical_proposition=(
            "Median incident response time will be below 30 minutes this week."
        ),
        claim_type="predictive",
        semantics=ClaimSemantics(
            subject="synthetic incidents",
            relation="have median response time",
            object_or_value="below 30 minutes",
            polarity="positive",
            outcome={"metric": "median_response_time_minutes"},
            time_horizon="one week",
        ),
        member_source_claim_ids=("source-claim-response-time",),
        preserved_variants=("Synthetic prospective incident-response claim.",),
        normalization_rationale="Synthetic non-book protocol fixture.",
        normalization_run_id="run-normalize-protocol",
    )


def _draft() -> PredictionSpec:
    return PredictionSpec(
        id="prediction-response-time",
        canonical_claim_id="claim-response-time",
        target_metric="median_response_time_minutes",
        population="synthetic incidents",
        direction="below",
        threshold=30.0,
        unit="minutes",
        probability_or_forecast=0.75,
        issue_or_information_cutoff_time=CUTOFF,
        evaluation_window=EvaluationWindow(WINDOW_START, WINDOW_END),
        outcome_data_source=OUTCOME_SOURCE,
        resolution_rule="Use the authorized fixture's recorded median.",
        scoring_rule="brier",
        benchmark_or_base_rate=0.5,
    )


def _profile() -> DomainProfile:
    return DomainProfile(
        id="incident-response",
        version="1.0.0",
        condition_fields=("incident_severity",),
        metric_definitions=(
            MetricDefinition(
                id="median_response_time_minutes",
                description="Median elapsed minutes from alert to response.",
                unit="minutes",
            ),
        ),
        evidence_rubric_refs=("rubric://incident-evidence-v1",),
        outcome_resolver_refs=(OUTCOME_SOURCE,),
        predictive_eligibility_rules_ref="rules://incident-prediction-v1",
    )


def _rubric() -> EvaluationRubric:
    return EvaluationRubric(
        id="incident-evidence",
        version="1.0.0",
        target_type="evidence",
        dimensions=(
            EvaluationDimension(
                id="reproducibility",
                question="Can another reviewer reproduce the observation?",
                scale={"minimum": 0.0, "maximum": 1.0},
            ),
        ),
    )


class SyntheticDomainAdapter:
    """Non-book adapter whose external outcome source is a local fixture."""

    @property
    def id(self) -> str:
        return "incident-response-adapter"

    @property
    def version(self) -> str:
        return "1.0.0"

    def domain_profile(self) -> DomainProfile:
        return _profile()

    def evaluation_rubrics(self):
        return (_rubric(),)

    def determine_predictive_eligibility(
        self, claim: CanonicalClaim
    ) -> EligibilityResult:
        return check_eligibility(claim)

    def resolve_authorized_outcomes(self, prediction: PredictionSpec):
        return (
            OutcomeObservation(
                id="observation-response-time",
                prediction_spec_id=prediction.id,
                observed_at="2026-01-03T00:00:00Z",
                value=24.0,
                source_ref=prediction.outcome_data_source,
                source_checksum=sha256_text("fixture median: 24"),
                collection_run_id="run-collect-protocol",
            ),
        )

    def normalize_terminology(self, claim: SourceClaim) -> SourceClaim:
        return claim

    def condition_fields(self):
        return self.domain_profile().condition_fields

    def metric_definitions(self):
        return {
            metric.id: {
                "description": metric.description,
                "unit": metric.unit,
            }
            for metric in self.domain_profile().metric_definitions
        }

    def render_skill(
        self,
        synthesis: SynthesisArtifact,
        build_manifest: SkillBuildManifest,
    ):
        return {"SKILL.md": f"# {synthesis.corpus_id}\n\n{build_manifest.id}\n"}

    def validate_skill(self, files):
        return () if "SKILL.md" in files else ("SKILL.md is required",)


class SyntheticPredictivePort:
    """Thin adapter around the implemented prospective functions."""

    def check_eligibility(self, claim: CanonicalClaim) -> EligibilityResult:
        return check_eligibility(claim)

    def register_and_freeze(
        self,
        spec: PredictionSpec,
        claim: CanonicalClaim,
        *,
        registered_at: str,
    ) -> PredictionSpec:
        return freeze_prediction(spec, claim, registered_at=registered_at)

    def record_outcome(
        self,
        spec: PredictionSpec,
        observation: OutcomeObservation,
    ) -> OutcomeObservation:
        return record_outcome(spec, observation)

    def score(
        self,
        prediction: PredictionSpec,
        observations,
        *,
        scored_at: str,
        run_id: str,
    ) -> PredictiveScore:
        return score_prediction(
            prediction,
            observations,
            scored_at=scored_at,
            run_id=run_id,
        )

    def aggregate(self, scores, *, minimum_resolved_count: int):
        return aggregate_scores(
            scores,
            minimum_resolved_count=minimum_resolved_count,
        )


def test_domain_adapter_composes_all_typed_non_book_provider_ports():
    adapter = SyntheticDomainAdapter()
    claim = _claim()
    draft = _draft()

    assert isinstance(adapter, DomainProfileProvider)
    assert isinstance(adapter, EvaluationRubricProvider)
    assert isinstance(adapter, PredictiveEligibilityProvider)
    assert isinstance(adapter, AuthorizedOutcomeResolver)
    assert isinstance(adapter, DomainAdapter)
    assert adapter.domain_profile().metric_definitions[0].id == draft.target_metric
    assert adapter.evaluation_rubrics()[0].target_type == "evidence"
    assert adapter.determine_predictive_eligibility(claim).eligible

    observations = adapter.resolve_authorized_outcomes(draft)
    assert len(observations) == 1
    assert observations[0].source_ref == OUTCOME_SOURCE


def test_incomplete_provider_is_not_misreported_as_a_full_domain_adapter():
    class ProfileOnly:
        def domain_profile(self):
            return _profile()

    provider = ProfileOnly()
    assert isinstance(provider, DomainProfileProvider)
    assert not isinstance(provider, DomainAdapter)


def test_predictive_port_matches_and_exercises_the_prospective_lifecycle():
    port = SyntheticPredictivePort()
    adapter = SyntheticDomainAdapter()
    claim = _claim()
    draft = _draft()

    assert isinstance(port, PredictivePowerPort)
    assert port.check_eligibility(claim).eligible
    frozen = port.register_and_freeze(draft, claim, registered_at=CUTOFF)
    observations = tuple(
        port.record_outcome(frozen, observation)
        for observation in adapter.resolve_authorized_outcomes(frozen)
    )
    score = port.score(
        frozen,
        observations,
        scored_at="2026-01-04T00:00:00Z",
        run_id="run-score-protocol",
    )
    aggregate = port.aggregate((score,), minimum_resolved_count=2)

    assert score.status == "resolved"
    assert aggregate.status == "insufficient_sample"
    assert aggregate.resolved_count == 1


def test_predictive_protocol_declares_required_registration_and_scoring_context():
    freeze_parameters = inspect.signature(
        PredictivePowerPort.register_and_freeze
    ).parameters
    score_parameters = inspect.signature(PredictivePowerPort.score).parameters

    assert tuple(freeze_parameters) == (
        "self",
        "spec",
        "claim",
        "registered_at",
    )
    assert freeze_parameters["registered_at"].kind is inspect.Parameter.KEYWORD_ONLY
    assert tuple(score_parameters) == (
        "self",
        "prediction",
        "observations",
        "scored_at",
        "run_id",
    )
    assert score_parameters["scored_at"].kind is inspect.Parameter.KEYWORD_ONLY


def test_interface_stability_labels_are_explicit_and_read_only():
    assert CORE_INTERFACE_STABILITY == STABLE
    assert EVALUATION_INTERFACE_STABILITY == EXPERIMENTAL
    assert PREDICTION_INTERFACE_STABILITY == EXPERIMENTAL
    assert ADAPTER_INTERFACE_STABILITY == SCAFFOLDED
    assert dict(INTERFACE_STABILITY) == {
        "adapter": "scaffolded",
        "core": "stable",
        "evaluation": "experimental",
        "prediction": "experimental",
    }
    with pytest.raises(TypeError):
        INTERFACE_STABILITY["core"] = "experimental"  # type: ignore[index]


def test_importing_ports_and_status_keeps_engines_lazy():
    script = """
import sys
import claim_framework.ports
import claim_framework.status
assert 'book_to_skill' not in sys.modules
assert 'corpus_to_skill' not in sys.modules
assert 'claim_framework.evaluation' not in sys.modules
assert 'claim_framework.prediction' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
