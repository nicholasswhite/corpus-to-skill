"""Conservative, deterministic canonicalization for source-faithful claims.

Canonical claims are an index over source claims, not replacements for them.
This module therefore groups records only when their claim type and complete
recorded semantics match.  When structured subject/relation data is missing,
only an exact normalized proposition is eligible for grouping.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Iterable, List, Tuple

from claim_framework.jsonio import canonical_dumps, stable_id
from claim_framework.records import (
    CanonicalClaim,
    ClaimSemantics,
    Condition,
    ContractError,
    SourceClaim,
)


DEFAULT_NORMALIZATION_RUN_ID = "normalize-rules-v1"
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalized_text(value: str) -> str:
    """Normalize presentation-only differences without guessing synonyms."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_NON_WORD.sub(" ", normalized).split())


def identity_value(value: Any) -> Any:
    """Return a deterministically ordered, JSON-compatible identity value."""
    if isinstance(value, Condition):
        return {
            "field": normalized_text(value.field),
            "operator": normalized_text(value.operator),
            "value": identity_value(value.value),
        }
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("semantic mapping keys must be strings")
        converted = {}
        for key, item in value.items():
            normalized_key = normalized_text(key)
            normalized_value = identity_value(item)
            if (
                normalized_key in converted
                and converted[normalized_key] != normalized_value
            ):
                raise ContractError(
                    f"semantic mapping has colliding normalized key {normalized_key!r}"
                )
            converted[normalized_key] = normalized_value
        return {key: converted[key] for key in sorted(converted)}
    if isinstance(value, (tuple, list)):
        return [identity_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise ContractError(
            "sets are not deterministic persisted semantic values; use a sequence"
        )
    if isinstance(value, str):
        return normalized_text(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("semantic numbers must be finite")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ContractError(
        f"unsupported persisted semantic value: {type(value).__name__}"
    )


def condition_identity(condition: Condition) -> Dict[str, Any]:
    value = identity_value(condition)
    assert isinstance(value, dict)
    return value


def semantics_identity(semantics: ClaimSemantics) -> Dict[str, Any]:
    """Include every field that can materially change a claim's scope."""
    return {
        "subject": identity_value(semantics.subject),
        "relation": identity_value(semantics.relation),
        "object_or_value": identity_value(semantics.object_or_value),
        "polarity": semantics.polarity,
        "quantifier": identity_value(semantics.quantifier),
        "modality": identity_value(semantics.modality),
        "population": identity_value(semantics.population),
        "geography": identity_value(semantics.geography),
        "temporal_scope": identity_value(semantics.temporal_scope),
        "definitions": identity_value(semantics.definitions),
        "assumptions": sorted(
            {normalized_text(item) for item in semantics.assumptions}
        ),
        "conditions": sorted(
            (condition_identity(item) for item in semantics.conditions),
            key=canonical_dumps,
        ),
        "objective": identity_value(semantics.objective),
        "outcome": identity_value(semantics.outcome),
        "time_horizon": identity_value(semantics.time_horizon),
    }


def _has_structured_identity(semantics: ClaimSemantics) -> bool:
    return bool(
        semantics.subject
        and semantics.relation
        and (
            semantics.object_or_value is not None
            or semantics.outcome
        )
    )


def _group_identity(claim: SourceClaim) -> Dict[str, Any]:
    semantics = semantics_identity(claim.semantics)
    identity = {
        "claim_type": claim.claim_type,
        "semantics": semantics,
    }
    if not _has_structured_identity(claim.semantics):
        identity["normalized_proposition"] = normalized_text(claim.proposition)
        # Missing structured identity is an abstention, not evidence that two
        # records are semantically equivalent, even when wording happens to
        # match exactly.
        identity["incomplete_source_claim_id"] = claim.id
    return identity


def _deduplicate_claim_ids(claims: Iterable[SourceClaim]) -> Tuple[SourceClaim, ...]:
    by_id: Dict[str, SourceClaim] = {}
    for claim in claims:
        if not isinstance(claim, SourceClaim):
            raise TypeError("normalize expects SourceClaim records")
        existing = by_id.get(claim.id)
        if existing is not None and existing != claim:
            raise ContractError(
                f"source claim id {claim.id!r} appears with conflicting records"
            )
        by_id[claim.id] = claim
    return tuple(by_id[claim_id] for claim_id in sorted(by_id))


def _variants(claims: Sequence[SourceClaim]) -> Tuple[str, ...]:
    values = {
        text.strip()
        for claim in claims
        for text in (claim.original_assertion, claim.proposition)
        if text.strip()
    }
    return tuple(sorted(values, key=lambda text: (normalized_text(text), text)))


def _canonical_proposition(claims: Sequence[SourceClaim]) -> str:
    chosen = min(
        claims,
        key=lambda claim: (
            normalized_text(claim.proposition),
            claim.proposition,
            claim.id,
        ),
    )
    return chosen.proposition.strip()


def normalize(
    source_claims: Sequence[SourceClaim],
    normalization_run_id: str = DEFAULT_NORMALIZATION_RUN_ID,
) -> Tuple[CanonicalClaim, ...]:
    """Group only materially equivalent claims and retain all source members.

    Conditions, definitions, population, objective, temporal scope, assumptions,
    polarity, quantifier, modality, and outcomes all participate in the group
    identity.  Any difference in those fields keeps claims separate.
    """
    if not isinstance(normalization_run_id, str) or not normalization_run_id.strip():
        raise ContractError("normalization_run_id must be a non-empty string")

    unique_claims = _deduplicate_claim_ids(source_claims)
    grouped: Dict[str, List[SourceClaim]] = defaultdict(list)
    identities: Dict[str, Dict[str, Any]] = {}
    for claim in unique_claims:
        identity = _group_identity(claim)
        identity_key = canonical_dumps(identity)
        grouped[identity_key].append(claim)
        identities[identity_key] = identity

    canonical_claims = []
    for identity_key in sorted(grouped):
        members = tuple(sorted(grouped[identity_key], key=lambda claim: claim.id))
        structured = all(
            _has_structured_identity(member.semantics) for member in members
        )
        rationale = (
            "Grouped source claims only because claim type and every recorded "
            "semantic and scope field match; source IDs and wording variants "
            "remain preserved."
            if structured
            else
            "Structured subject/relation identity was incomplete, so this "
            "source claim remains a singleton canonical claim pending review."
        )
        canonical_claims.append(
            CanonicalClaim(
                id=stable_id("canonical_claim", identities[identity_key]),
                canonical_proposition=_canonical_proposition(members),
                claim_type=members[0].claim_type,
                semantics=members[0].semantics,
                member_source_claim_ids=tuple(member.id for member in members),
                preserved_variants=_variants(members),
                normalization_rationale=rationale,
                normalization_run_id=normalization_run_id,
            )
        )

    return tuple(sorted(canonical_claims, key=lambda claim: claim.id))


normalize_claims = normalize


__all__ = [
    "DEFAULT_NORMALIZATION_RUN_ID",
    "condition_identity",
    "identity_value",
    "normalize",
    "normalize_claims",
    "normalized_text",
    "semantics_identity",
]
