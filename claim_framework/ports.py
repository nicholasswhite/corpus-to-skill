"""Domain-neutral extension ports; implementations live outside the core.

The protocols in this module intentionally depend only on persisted record
contracts.  Importing them must not load the evaluation or prediction engines,
which keeps adapters usable in small non-book applications.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, Tuple, TypeVar, runtime_checkable

from claim_framework.records import (
    CanonicalClaim,
    DomainProfile,
    EligibilityResult,
    EvaluationRubric,
    OutcomeObservation,
    PredictionSpec,
    PredictiveScore,
    RunRecord,
    SkillBuildManifest,
    SourceClaim,
    SourceRecord,
    SynthesisArtifact,
)


@runtime_checkable
class ClaimExtractor(Protocol):
    """Turn one verified extracted source into source-faithful atomic claims."""

    @property
    def implementation_version(self) -> str:
        ...

    def extract_claims(
        self,
        source: SourceRecord,
        extracted_text: str,
        run: RunRecord,
    ) -> Sequence[SourceClaim]:
        ...


@runtime_checkable
class DomainProfileProvider(Protocol):
    """Provide one declarative, versioned profile for a domain."""

    def domain_profile(self) -> DomainProfile:
        ...


@runtime_checkable
class EvaluationRubricProvider(Protocol):
    """Provide the versioned rubrics that an adapter is prepared to apply."""

    def evaluation_rubrics(self) -> Tuple[EvaluationRubric, ...]:
        ...


@runtime_checkable
class PredictiveEligibilityProvider(Protocol):
    """Apply domain eligibility policy without constructing a forecast."""

    def determine_predictive_eligibility(
        self, claim: CanonicalClaim
    ) -> EligibilityResult:
        ...


@runtime_checkable
class AuthorizedOutcomeResolver(Protocol):
    """Resolve observations only through sources authorized by the adapter.

    Resolution is deliberately an adapter seam: authentication, network I/O,
    and source-specific parsing do not belong in the generic framework.  Every
    returned observation must still be validated by
    :meth:`PredictivePowerPort.record_outcome` before scoring.
    """

    def resolve_authorized_outcomes(
        self, prediction: PredictionSpec
    ) -> Sequence[OutcomeObservation]:
        ...


@runtime_checkable
class DomainAdapter(
    DomainProfileProvider,
    EvaluationRubricProvider,
    PredictiveEligibilityProvider,
    AuthorizedOutcomeResolver,
    Protocol,
):
    """Supply domain policy and rendering without coupling the core to it.

    ``condition_fields`` and ``metric_definitions`` are retained as lightweight
    compatibility conveniences.  New adapters should also expose their typed
    definitions through :meth:`domain_profile`.
    """

    @property
    def id(self) -> str:
        ...

    @property
    def version(self) -> str:
        ...

    def normalize_terminology(self, claim: SourceClaim) -> SourceClaim:
        ...

    def condition_fields(self) -> Tuple[str, ...]:
        ...

    def metric_definitions(self) -> Mapping[str, Mapping[str, Any]]:
        ...

    def render_skill(
        self,
        synthesis: SynthesisArtifact,
        build_manifest: SkillBuildManifest,
    ) -> Mapping[str, str]:
        ...

    def validate_skill(self, files: Mapping[str, str]) -> Tuple[str, ...]:
        ...


PredictiveProfileT = TypeVar("PredictiveProfileT", covariant=True)


@runtime_checkable
class PredictivePowerPort(
    Protocol[PredictiveProfileT]
):
    """Optional prospective interface aligned with the framework lifecycle.

    The aggregate result stays generic because the current aggregate is an
    experimental, non-persisted view defined by the prediction engine.  Making
    that implementation type part of this core port would defeat lazy imports
    and prematurely stabilize it.
    """

    def check_eligibility(self, claim: CanonicalClaim) -> EligibilityResult:
        ...

    def register_and_freeze(
        self,
        spec: PredictionSpec,
        claim: CanonicalClaim,
        *,
        registered_at: str,
    ) -> PredictionSpec:
        ...

    def record_outcome(
        self,
        spec: PredictionSpec,
        observation: OutcomeObservation,
    ) -> OutcomeObservation:
        ...

    def score(
        self,
        prediction: PredictionSpec,
        observations: Sequence[OutcomeObservation],
        *,
        scored_at: str,
        run_id: str,
    ) -> PredictiveScore:
        ...

    def aggregate(
        self,
        scores: Sequence[PredictiveScore],
        *,
        minimum_resolved_count: int,
    ) -> PredictiveProfileT:
        ...


__all__ = [
    "AuthorizedOutcomeResolver",
    "ClaimExtractor",
    "DomainAdapter",
    "DomainProfileProvider",
    "EvaluationRubricProvider",
    "PredictiveEligibilityProvider",
    "PredictivePowerPort",
]
