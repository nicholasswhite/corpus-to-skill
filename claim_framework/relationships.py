"""Deterministic candidate retrieval and conservative claim comparison."""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Dict, FrozenSet, Iterable, Optional, Set, Tuple

from claim_framework.jsonio import canonical_dumps, stable_id
from claim_framework.normalize import (
    condition_identity,
    identity_value,
    normalized_text,
    semantics_identity,
)
from claim_framework.records import (
    CanonicalClaim,
    ClaimRelation,
    ClassificationInfo,
    Condition,
    ContractError,
    HumanOverride,
    RELATION_TYPES,
    ScopeAnalysis,
)


DEFAULT_CLASSIFICATION_RUN_ID = "relationship-rules-v1"
_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "for",
    "from", "in", "is", "it", "of", "on", "or", "that", "the", "to",
    "under", "with",
}
_NEGATIONS = {"no", "none", "not", "never", "without"}
_UNIVERSAL = {"all", "always", "each", "every"}
_NEGATIVE_UNIVERSAL = {"no", "none", "never"}
_EXISTENTIAL = {"a", "an", "any", "at least one", "some"}
_POSITIVE_DIRECTIONS = {
    "above", "build", "gain", "greater", "higher", "improve", "increase",
    "outperform", "positive", "rise",
}
_NEGATIVE_DIRECTIONS = {
    "below", "decline", "decrease", "fall", "lower", "negative", "reduce",
    "underperform", "worsen",
}


def _tokens(value: Optional[str]) -> FrozenSet[str]:
    if not value:
        return frozenset()
    return frozenset(
        token
        for token in _TOKEN.findall(normalized_text(value))
        if token not in _STOPWORDS
    )


def _soft_token_overlap(left: FrozenSet[str], right: FrozenSet[str]) -> bool:
    if left & right:
        return True
    return any(
        min(len(left_token), len(right_token)) >= 4
        and (
            left_token.startswith(right_token[:4])
            or right_token.startswith(left_token[:4])
        )
        for left_token in left
        for right_token in right
    )


def _same_optional(left: Optional[str], right: Optional[str]) -> bool:
    return (
        left is not None
        and right is not None
        and normalized_text(left) == normalized_text(right)
    )


def _pair_by_id(
    claims: Iterable[CanonicalClaim],
) -> Tuple[CanonicalClaim, ...]:
    by_id: Dict[str, CanonicalClaim] = {}
    for claim in claims:
        if not isinstance(claim, CanonicalClaim):
            raise TypeError("relationship classification expects CanonicalClaim records")
        existing = by_id.get(claim.id)
        if existing is not None and existing != claim:
            raise ContractError(
                f"canonical claim id {claim.id!r} appears with conflicting records"
            )
        by_id[claim.id] = claim
    return tuple(by_id[claim_id] for claim_id in sorted(by_id))


def candidate_score(left: CanonicalClaim, right: CanonicalClaim) -> int:
    """Score semantic relatedness only; the score is never a relation label."""
    left_semantics = left.semantics
    right_semantics = right.semantics
    score = 0

    if _same_optional(left_semantics.subject, right_semantics.subject):
        score += 6
    elif _soft_token_overlap(
        _tokens(left_semantics.subject), _tokens(right_semantics.subject)
    ):
        score += 3

    if _same_optional(left_semantics.relation, right_semantics.relation):
        score += 3

    left_metric = left_semantics.outcome.get("metric")
    right_metric = right_semantics.outcome.get("metric")
    if (
        isinstance(left_metric, str)
        and isinstance(right_metric, str)
        and normalized_text(left_metric) == normalized_text(right_metric)
    ):
        score += 3

    if (
        left_semantics.object_or_value is not None
        and identity_value(left_semantics.object_or_value)
        == identity_value(right_semantics.object_or_value)
    ):
        score += 2

    left_words = _tokens(left.canonical_proposition)
    right_words = _tokens(right.canonical_proposition)
    shared = left_words & right_words
    if len(shared) >= 2 or _soft_token_overlap(left_words, right_words):
        score += 2

    return score


def retrieve_candidates(
    canonical_claims: Sequence[CanonicalClaim],
) -> Tuple[Tuple[CanonicalClaim, CanonicalClaim], ...]:
    """Return related pairs in stable endpoint order.

    Candidate retrieval is deliberately broad.  Every returned pair still
    passes through structured scope checks and may become an abstention.
    """
    claims = _pair_by_id(canonical_claims)
    pairs = [
        (left, right)
        for left, right in itertools.combinations(claims, 2)
        if candidate_score(left, right) >= 3
    ]
    return tuple(pairs)


def _condition_map(conditions: Sequence[Condition]) -> Dict[str, Condition]:
    mapped: Dict[str, Condition] = {}
    for condition in conditions:
        key = canonical_dumps(condition_identity(condition))
        existing = mapped.get(key)
        if existing is None or canonical_dumps(condition) < canonical_dumps(existing):
            mapped[key] = condition
    return mapped


def _string_overlap(left: Optional[str], right: Optional[str]) -> str:
    if left is None and right is None:
        return "unknown"
    if left is None or right is None:
        return "unknown"
    left_normalized = normalized_text(left)
    right_normalized = normalized_text(right)
    if left_normalized == right_normalized:
        return "same"
    if _soft_token_overlap(_tokens(left), _tokens(right)):
        return "partial"
    return "disjoint"


def _mapping_alignment(left: Mapping[str, object], right: Mapping[str, object]) -> str:
    if not left and not right:
        return "aligned"
    if not left or not right:
        return "unknown"
    return "aligned" if identity_value(left) == identity_value(right) else "divergent"


def _objective_alignment(left: Optional[str], right: Optional[str]) -> str:
    if left is None and right is None:
        return "aligned"
    if left is None or right is None:
        return "unknown"
    return "aligned" if normalized_text(left) == normalized_text(right) else "divergent"


def analyze_scope(left: CanonicalClaim, right: CanonicalClaim) -> ScopeAnalysis:
    left_conditions = _condition_map(left.semantics.conditions)
    right_conditions = _condition_map(right.semantics.conditions)
    left_keys = set(left_conditions)
    right_keys = set(right_conditions)

    if left_keys == right_keys:
        condition_overlap = "same"
    elif not left_keys or not right_keys:
        condition_overlap = "unknown"
    elif left_keys & right_keys:
        condition_overlap = "partial"
    else:
        condition_overlap = "disjoint"

    shared = tuple(
        left_conditions[key] for key in sorted(left_keys & right_keys)
    )
    left_only = tuple(
        left_conditions[key] for key in sorted(left_keys - right_keys)
    )
    right_only = tuple(
        right_conditions[key] for key in sorted(right_keys - left_keys)
    )
    return ScopeAnalysis(
        term_definition_alignment=_mapping_alignment(
            left.semantics.definitions, right.semantics.definitions
        ),
        population_overlap=_string_overlap(
            left.semantics.population, right.semantics.population
        ),
        temporal_overlap=_string_overlap(
            left.semantics.temporal_scope, right.semantics.temporal_scope
        ),
        condition_overlap=condition_overlap,
        objective_alignment=_objective_alignment(
            left.semantics.objective, right.semantics.objective
        ),
        shared_conditions=shared,
        left_only_conditions=left_only,
        right_only_conditions=right_only,
    )


def _normalized_quantifier(value: Optional[str]) -> Optional[str]:
    return normalized_text(value) if value else None


def _surface_negation(left: str, right: str) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    left_negated = bool(left_tokens & _NEGATIONS)
    right_negated = bool(right_tokens & _NEGATIONS)
    if left_negated == right_negated:
        return False
    left_without = left_tokens - _NEGATIONS
    right_without = right_tokens - _NEGATIONS
    union = left_without | right_without
    return bool(union) and len(left_without & right_without) / len(union) >= 0.6


def _outcome_direction(semantics: object) -> Optional[str]:
    outcome = getattr(semantics, "outcome")
    raw = outcome.get("direction")
    if not isinstance(raw, str):
        return None
    normalized = normalized_text(raw)
    if normalized in _POSITIVE_DIRECTIONS:
        return "positive"
    if normalized in _NEGATIVE_DIRECTIONS:
        return "negative"
    return normalized


def _same_core(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    return (
        _same_optional(left.semantics.subject, right.semantics.subject)
        and _same_optional(left.semantics.relation, right.semantics.relation)
    )


def _same_object(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    if (
        left.semantics.object_or_value is None
        or right.semantics.object_or_value is None
    ):
        return left.semantics.object_or_value is right.semantics.object_or_value
    return identity_value(left.semantics.object_or_value) == identity_value(
        right.semantics.object_or_value
    )


def _opposite_polarity(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    return {left.semantics.polarity, right.semantics.polarity} == {
        "positive",
        "negative",
    }


def _quantifier_counterexample(
    left: CanonicalClaim, right: CanonicalClaim
) -> bool:
    left_quantifier = _normalized_quantifier(left.semantics.quantifier)
    right_quantifier = _normalized_quantifier(right.semantics.quantifier)
    if left_quantifier in _UNIVERSAL and right_quantifier in _EXISTENTIAL:
        return _opposite_polarity(left, right) or not _same_object(left, right)
    if right_quantifier in _UNIVERSAL and left_quantifier in _EXISTENTIAL:
        return _opposite_polarity(left, right) or not _same_object(left, right)
    if left_quantifier in _NEGATIVE_UNIVERSAL and right_quantifier in _EXISTENTIAL:
        return right.semantics.polarity != "negative"
    if right_quantifier in _NEGATIVE_UNIVERSAL and left_quantifier in _EXISTENTIAL:
        return left.semantics.polarity != "negative"
    return False


def _opposing_outcomes(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    left_metric = left.semantics.outcome.get("metric")
    right_metric = right.semantics.outcome.get("metric")
    if not isinstance(left_metric, str) or not isinstance(right_metric, str):
        return False
    if normalized_text(left_metric) != normalized_text(right_metric):
        return False
    left_direction = _outcome_direction(left.semantics)
    right_direction = _outcome_direction(right.semantics)
    return (
        left_direction is not None
        and right_direction is not None
        and {left_direction, right_direction} == {"positive", "negative"}
    )


def _apparent_opposition(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    if not _same_core(left, right):
        return _opposing_outcomes(left, right) or _surface_negation(
            left.canonical_proposition, right.canonical_proposition
        )
    return (
        (_opposite_polarity(left, right) and _same_object(left, right))
        or _quantifier_counterexample(left, right)
        or _opposing_outcomes(left, right)
        or (
            not _same_object(left, right)
            and (
                left.claim_type in {"normative", "procedural", "comparative"}
                or right.claim_type in {"normative", "procedural", "comparative"}
            )
        )
        or _surface_negation(
            left.canonical_proposition, right.canonical_proposition
        )
    )


def _direct_incompatibility(
    left: CanonicalClaim, right: CanonicalClaim
) -> bool:
    """Return true only for propositions that cannot coexist in aligned scope."""
    if not _same_core(left, right):
        return _opposing_outcomes(left, right) or _surface_negation(
            left.canonical_proposition, right.canonical_proposition
        )
    if _quantifier_counterexample(left, right) or _opposing_outcomes(left, right):
        return True

    left_quantifier = _normalized_quantifier(left.semantics.quantifier)
    right_quantifier = _normalized_quantifier(right.semantics.quantifier)
    if (
        left_quantifier in _EXISTENTIAL
        and right_quantifier in _EXISTENTIAL
    ):
        # Some members may have a property while some others do not.
        return False

    hedged_modalities = {"can", "could", "may", "might", "tends", "sometimes"}
    left_modality = (
        normalized_text(left.semantics.modality)
        if left.semantics.modality
        else None
    )
    right_modality = (
        normalized_text(right.semantics.modality)
        if right.semantics.modality
        else None
    )
    if (
        left_modality in hedged_modalities
        or right_modality in hedged_modalities
    ) and left_modality != right_modality:
        return False

    return (
        _same_object(left, right)
        and _opposite_polarity(left, right)
    ) or _surface_negation(
        left.canonical_proposition, right.canonical_proposition
    )


def _asymmetric_missing_scope(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    pairs = (
        (left.semantics.population, right.semantics.population),
        (left.semantics.temporal_scope, right.semantics.temporal_scope),
        (left.semantics.time_horizon, right.semantics.time_horizon),
    )
    return any((left_value is None) != (right_value is None) for left_value, right_value in pairs)


def _differing_material_scope(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    return any(
        (
            identity_value(left.semantics.assumptions)
            != identity_value(right.semantics.assumptions),
            identity_value(left.semantics.geography)
            != identity_value(right.semantics.geography),
            identity_value(left.semantics.time_horizon)
            != identity_value(right.semantics.time_horizon),
            identity_value(left.semantics.modality)
            != identity_value(right.semantics.modality),
        )
    )


def _conflict_dimensions(
    left: CanonicalClaim,
    right: CanonicalClaim,
    scope: ScopeAnalysis,
) -> Tuple[str, ...]:
    dimensions: Set[str] = set()
    if left.claim_type != right.claim_type:
        dimensions.add("claim_type")
    if left.semantics.polarity != right.semantics.polarity:
        dimensions.add("polarity")
    if identity_value(left.semantics.quantifier) != identity_value(
        right.semantics.quantifier
    ):
        dimensions.add("quantifier")
    if not _same_object(left, right):
        dimensions.add("object_or_value")
    if scope.term_definition_alignment == "divergent":
        dimensions.add("definition")
    if scope.population_overlap in {"partial", "disjoint"}:
        dimensions.add("population")
    if scope.temporal_overlap in {"partial", "disjoint"}:
        dimensions.add("temporal_scope")
    if scope.condition_overlap in {"partial", "disjoint"}:
        dimensions.add("conditions")
    if scope.objective_alignment == "divergent":
        dimensions.add("objective")
    if identity_value(left.semantics.assumptions) != identity_value(
        right.semantics.assumptions
    ):
        dimensions.add("assumptions")
    if identity_value(left.semantics.geography) != identity_value(
        right.semantics.geography
    ):
        dimensions.add("geography")
    if identity_value(left.semantics.time_horizon) != identity_value(
        right.semantics.time_horizon
    ):
        dimensions.add("time_horizon")
    if _opposing_outcomes(left, right):
        dimensions.add("outcome_direction")
    return tuple(sorted(dimensions))


def _condition_keys(claim: CanonicalClaim) -> Set[str]:
    return set(_condition_map(claim.semantics.conditions))


def _is_equivalent(left: CanonicalClaim, right: CanonicalClaim) -> bool:
    return (
        left.claim_type == right.claim_type
        and semantics_identity(left.semantics) == semantics_identity(right.semantics)
        and normalized_text(left.canonical_proposition)
        == normalized_text(right.canonical_proposition)
    )


def _relation_decision(
    left: CanonicalClaim,
    right: CanonicalClaim,
    scope: ScopeAnalysis,
) -> Tuple[str, str, str]:
    """Return relation type, directionality, and deterministic rationale."""
    apparent_opposition = _apparent_opposition(left, right)

    if _is_equivalent(left, right):
        return (
            "equivalent",
            "symmetric",
            "Claim type, proposition, semantics, and recorded scope are equivalent.",
        )

    if apparent_opposition and left.claim_type != right.claim_type:
        return (
            "insufficient_information",
            "symmetric",
            "The apparent conflict crosses different claim types, so logical "
            "incompatibility cannot be inferred.",
        )

    if apparent_opposition and _asymmetric_missing_scope(left, right):
        return (
            "insufficient_information",
            "symmetric",
            "An apparent conflict has asymmetric missing population or time scope, "
            "so material alignment cannot be established.",
        )

    left_quantifier = _normalized_quantifier(left.semantics.quantifier)
    right_quantifier = _normalized_quantifier(right.semantics.quantifier)
    known_quantifiers = _UNIVERSAL | _NEGATIVE_UNIVERSAL | _EXISTENTIAL
    if apparent_opposition and any(
        value is not None and value not in known_quantifiers
        for value in (left_quantifier, right_quantifier)
    ):
        return (
            "insufficient_information",
            "symmetric",
            "At least one free-text quantifier is outside the controlled "
            "comparison vocabulary, so the classifier abstains.",
        )

    if apparent_opposition and any(
        (
            bool(left.semantics.definitions)
            != bool(right.semantics.definitions),
            bool(left.semantics.conditions)
            != bool(right.semantics.conditions),
            bool(left.semantics.assumptions)
            != bool(right.semantics.assumptions),
            (left.semantics.geography is None)
            != (right.semantics.geography is None),
            (
                left.claim_type in {"normative", "procedural", "comparative"}
                or right.claim_type in {"normative", "procedural", "comparative"}
            )
            and (
                (left.semantics.objective is None)
                != (right.semantics.objective is None)
            ),
        )
    ):
        return (
            "insufficient_information",
            "symmetric",
            "An apparent conflict has asymmetric missing definitions, conditions, "
            "assumptions, geography, or objective, so the classifier abstains.",
        )

    if apparent_opposition and scope.term_definition_alignment == "divergent":
        return (
            "tension",
            "symmetric",
            "The claims appear opposed but use divergent recorded definitions; "
            "this is a definition mismatch, not a direct contradiction.",
        )

    if scope.objective_alignment == "divergent":
        return (
            "alternative",
            "symmetric",
            "The claims optimize divergent recorded objectives and are conditional "
            "alternatives rather than a winner and loser.",
        )

    left_conditions = _condition_keys(left)
    right_conditions = _condition_keys(right)
    if apparent_opposition and (
        scope.condition_overlap in {"partial", "disjoint"}
        or scope.population_overlap in {"partial", "disjoint"}
        or scope.temporal_overlap in {"partial", "disjoint"}
        or (
            left.semantics.assumptions
            and right.semantics.assumptions
            and identity_value(left.semantics.assumptions)
            != identity_value(right.semantics.assumptions)
        )
        or (
            left.semantics.geography is not None
            and right.semantics.geography is not None
            and normalized_text(left.semantics.geography)
            != normalized_text(right.semantics.geography)
        )
        or (
            left.semantics.time_horizon is not None
            and right.semantics.time_horizon is not None
            and normalized_text(left.semantics.time_horizon)
            != normalized_text(right.semantics.time_horizon)
        )
    ):
        return (
            "conditional_disagreement",
            "symmetric",
            "The conclusions differ while recorded conditions or scope differ, "
            "so both may apply in their respective contexts.",
        )

    if (
        _same_core(left, right)
        and left.claim_type == right.claim_type
        and _same_object(left, right)
        and left.semantics.polarity == right.semantics.polarity
        and left_conditions != right_conditions
        and (left_conditions < right_conditions or right_conditions < left_conditions)
    ):
        if len(left_conditions) > len(right_conditions):
            directionality = "left_to_right"
        else:
            directionality = "right_to_left"
        return (
            "qualification",
            directionality,
            "The more specific claim adds recorded conditions to the broader "
            "claim; the qualifier points toward the broader position.",
        )

    if (
        _direct_incompatibility(left, right)
        and left.claim_type == right.claim_type
        and scope.term_definition_alignment == "aligned"
        and scope.condition_overlap == "same"
        and scope.objective_alignment == "aligned"
        and not _differing_material_scope(left, right)
    ):
        return (
            "contradiction",
            "symmetric",
            "The propositions are incompatible under materially aligned recorded "
            "definitions, conditions, assumptions, objectives, and scope.",
        )

    if (
        _same_core(left, right)
        and left.claim_type == right.claim_type
        and _same_object(left, right)
        and left.semantics.polarity == right.semantics.polarity
        and not _differing_material_scope(left, right)
    ):
        return (
            "agreement",
            "symmetric",
            "The claims state a compatible conclusion under aligned recorded scope.",
        )

    if (
        _same_optional(left.semantics.subject, right.semantics.subject)
        and left.semantics.relation
        and right.semantics.relation
        and not _same_optional(left.semantics.relation, right.semantics.relation)
        and not apparent_opposition
    ):
        return (
            "orthogonal",
            "symmetric",
            "The claims concern compatible but different properties of the same "
            "recorded subject.",
        )

    if (
        _same_core(left, right)
        and not _same_object(left, right)
        and left.claim_type in {"normative", "procedural", "comparative"}
        and right.claim_type in {"normative", "procedural", "comparative"}
    ):
        return (
            "alternative",
            "symmetric",
            "The claims present different actions on the same decision axis without "
            "enough aligned information to select one.",
        )

    return (
        "insufficient_information",
        "symmetric",
        "The claims are related candidates, but the recorded semantics do not "
        "establish agreement, conflict, or a directional relationship.",
    )


def classify_relation(
    left: CanonicalClaim,
    right: CanonicalClaim,
    classification_run_id: str = DEFAULT_CLASSIFICATION_RUN_ID,
) -> ClaimRelation:
    """Classify one candidate pair with stable endpoints and an auditable scope."""
    if left.id == right.id:
        raise ContractError("cannot classify a canonical claim against itself")
    if not isinstance(classification_run_id, str) or not classification_run_id.strip():
        raise ContractError("classification_run_id must be a non-empty string")
    if right.id < left.id:
        left, right = right, left

    scope = analyze_scope(left, right)
    relation_type, directionality, rationale = _relation_decision(
        left, right, scope
    )
    conflict_dimensions = _conflict_dimensions(left, right, scope)
    identity = {
        "left_claim_id": left.id,
        "right_claim_id": right.id,
        "relation_type": relation_type,
        "directionality": directionality,
        "scope_analysis": {
            "term_definition_alignment": scope.term_definition_alignment,
            "population_overlap": scope.population_overlap,
            "temporal_overlap": scope.temporal_overlap,
            "condition_overlap": scope.condition_overlap,
            "objective_alignment": scope.objective_alignment,
            "shared_conditions": [
                condition_identity(item) for item in scope.shared_conditions
            ],
            "left_only_conditions": [
                condition_identity(item) for item in scope.left_only_conditions
            ],
            "right_only_conditions": [
                condition_identity(item) for item in scope.right_only_conditions
            ],
        },
        "conflict_dimensions": list(conflict_dimensions),
    }
    return ClaimRelation(
        id=stable_id("claim_relation", identity),
        left_claim_id=left.id,
        right_claim_id=right.id,
        relation_type=relation_type,
        directionality=directionality,
        scope_analysis=scope,
        conflict_dimensions=conflict_dimensions,
        rationale=rationale,
        supporting_source_claim_ids=tuple(
            sorted(
                set(left.member_source_claim_ids)
                | set(right.member_source_claim_ids)
            )
        ),
        classification=ClassificationInfo(run_id=classification_run_id),
    )


def classify_relations(
    canonical_claims: Sequence[CanonicalClaim],
    classification_run_id: str = DEFAULT_CLASSIFICATION_RUN_ID,
) -> Tuple[ClaimRelation, ...]:
    relations = [
        classify_relation(left, right, classification_run_id)
        for left, right in retrieve_candidates(canonical_claims)
    ]
    return tuple(sorted(relations, key=lambda relation: relation.id))


def apply_human_override(
    relation: ClaimRelation,
    new_relation_type: str,
    *,
    reviewer: str,
    timestamp: str,
    reason: str,
) -> ClaimRelation:
    """Return a corrected relation with an auditable link to the prior record."""

    if not isinstance(relation, ClaimRelation):
        raise TypeError("relation must be a ClaimRelation")
    if new_relation_type not in RELATION_TYPES:
        raise ContractError(
            "new_relation_type must be one of " + ", ".join(RELATION_TYPES)
        )
    override = HumanOverride(
        field="relation_type",
        prior_value=relation.relation_type,
        new_value=new_relation_type,
        reviewer=reviewer,
        timestamp=timestamp,
        reason=reason,
        prior_record_id=relation.id,
    )
    identity = {
        "prior_relation_id": relation.id,
        "left_claim_id": relation.left_claim_id,
        "right_claim_id": relation.right_claim_id,
        "override": override,
    }
    return replace(
        relation,
        id=stable_id("claim_relation_override", identity),
        relation_type=new_relation_type,
        classification=replace(
            relation.classification,
            review_status="corrected",
            classifier_confidence=None,
        ),
        human_override=override,
    )


retrieve_candidate_pairs = retrieve_candidates


__all__ = [
    "DEFAULT_CLASSIFICATION_RUN_ID",
    "analyze_scope",
    "apply_human_override",
    "candidate_score",
    "classify_relation",
    "classify_relations",
    "retrieve_candidate_pairs",
    "retrieve_candidates",
]
