"""Traceable synthesis over canonical claims and classified relationships."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Dict, Iterable, List, Optional, Set, Tuple

from claim_framework.jsonio import canonical_dumps, stable_id
from claim_framework.normalize import condition_identity, identity_value, normalized_text
from claim_framework.records import (
    CanonicalClaim,
    ClaimRelation,
    ContractError,
    DisputeRecord,
    SourceClaim,
    SynthesisArtifact,
    SynthesisAssertion,
    SynthesisGap,
    TopicCluster,
)


DEFAULT_SYNTHESIS_RUN_ID = "synthesis-rules-v1"
_CONFLICT_RELATIONS = {
    "contradiction",
    "tension",
    "conditional_disagreement",
    "alternative",
}
_CONDITIONAL_RELATIONS = {
    "conditional_disagreement",
    "alternative",
    "qualification",
    "refinement",
}
_SUPPORT_RELATIONS = {"equivalent", "agreement", "support"}


def _deduplicate_records(records: Iterable[object], kind: str) -> Dict[str, object]:
    by_id: Dict[str, object] = {}
    for record in records:
        record_id = getattr(record, "id", None)
        if not isinstance(record_id, str):
            raise TypeError(f"{kind} records must expose a string id")
        existing = by_id.get(record_id)
        if existing is not None and existing != record:
            raise ContractError(f"{kind} id {record_id!r} has conflicting records")
        by_id[record_id] = record
    return by_id


def _validate_inputs(
    canonical_claims: Sequence[CanonicalClaim],
    source_claims: Sequence[SourceClaim],
    relations: Sequence[ClaimRelation],
) -> Tuple[
    Dict[str, CanonicalClaim],
    Dict[str, SourceClaim],
    Dict[str, ClaimRelation],
]:
    canonical_map = _deduplicate_records(canonical_claims, "canonical claim")
    source_map = _deduplicate_records(source_claims, "source claim")
    relation_map = _deduplicate_records(relations, "claim relation")

    if any(not isinstance(item, CanonicalClaim) for item in canonical_map.values()):
        raise TypeError("canonical_claims must contain CanonicalClaim records")
    if any(not isinstance(item, SourceClaim) for item in source_map.values()):
        raise TypeError("source_claims must contain SourceClaim records")
    if any(not isinstance(item, ClaimRelation) for item in relation_map.values()):
        raise TypeError("relations must contain ClaimRelation records")

    for canonical in canonical_map.values():
        missing = sorted(set(canonical.member_source_claim_ids) - set(source_map))
        if missing:
            raise ContractError(
                f"canonical claim {canonical.id!r} references missing source "
                f"claim(s): {', '.join(missing)}"
            )
        for source_claim_id in canonical.member_source_claim_ids:
            if not source_map[source_claim_id].source_spans:
                raise ContractError(
                    f"source claim {source_claim_id!r} has no provenance spans"
                )

    for relation in relation_map.values():
        missing_endpoints = sorted(
            {
                relation.left_claim_id,
                relation.right_claim_id,
            }
            - set(canonical_map)
        )
        if missing_endpoints:
            raise ContractError(
                f"relation {relation.id!r} references missing canonical "
                f"claim(s): {', '.join(missing_endpoints)}"
            )
        missing_sources = sorted(
            set(relation.supporting_source_claim_ids) - set(source_map)
        )
        if missing_sources:
            raise ContractError(
                f"relation {relation.id!r} references missing source claim(s): "
                f"{', '.join(missing_sources)}"
            )

    return (
        {key: canonical_map[key] for key in sorted(canonical_map)},  # type: ignore[return-value]
        {key: source_map[key] for key in sorted(source_map)},  # type: ignore[return-value]
        {key: relation_map[key] for key in sorted(relation_map)},  # type: ignore[return-value]
    )


def _incident_relations(
    canonical_map: Mapping[str, CanonicalClaim],
    relation_map: Mapping[str, ClaimRelation],
) -> Dict[str, Tuple[ClaimRelation, ...]]:
    incident: Dict[str, List[ClaimRelation]] = {
        claim_id: [] for claim_id in canonical_map
    }
    for relation in relation_map.values():
        incident[relation.left_claim_id].append(relation)
        incident[relation.right_claim_id].append(relation)
    return {
        claim_id: tuple(sorted(items, key=lambda item: item.id))
        for claim_id, items in incident.items()
    }


def _other_endpoint(relation: ClaimRelation, claim_id: str) -> str:
    if relation.left_claim_id == claim_id:
        return relation.right_claim_id
    if relation.right_claim_id == claim_id:
        return relation.left_claim_id
    raise ContractError(
        f"relation {relation.id!r} is not incident to claim {claim_id!r}"
    )


def _supporting_canonical_ids(
    claim: CanonicalClaim,
    incident: Sequence[ClaimRelation],
) -> Tuple[str, ...]:
    """Return the asserted claim first, followed by every backing claim.

    The first ID remains the assertion's primary claim for topic-cluster lookup.
    Any canonical claim whose members expand the supporting-source set must also
    be recorded here so provenance validation can traverse that lineage.
    """

    supporting = {claim.id}
    for relation in incident:
        if relation.relation_type in _SUPPORT_RELATIONS:
            supporting.add(_other_endpoint(relation, claim.id))
    return (claim.id,) + tuple(sorted(supporting - {claim.id}))


def _support_ids(
    canonical_claim_ids: Sequence[str],
    canonical_map: Mapping[str, CanonicalClaim],
) -> Tuple[str, ...]:
    supporting: Set[str] = set()
    for claim_id in canonical_claim_ids:
        supporting.update(canonical_map[claim_id].member_source_claim_ids)
    return tuple(sorted(supporting))


def _opposing_ids(
    claim: CanonicalClaim,
    incident: Sequence[ClaimRelation],
    canonical_map: Mapping[str, CanonicalClaim],
) -> Tuple[str, ...]:
    opposing: Set[str] = set()
    for relation in incident:
        if relation.relation_type in _CONFLICT_RELATIONS:
            other = canonical_map[_other_endpoint(relation, claim.id)]
            opposing.update(other.member_source_claim_ids)
    return tuple(sorted(opposing))


def _known_independent_groups(
    source_claim_ids: Sequence[str],
    independence_groups: Optional[Mapping[str, str]],
) -> Optional[Set[str]]:
    if not independence_groups:
        return None
    groups = []
    for source_claim_id in source_claim_ids:
        group = independence_groups.get(source_claim_id)
        if not isinstance(group, str) or not group.strip():
            return None
        groups.append(group)
    return set(groups)


def _assertion_status(
    claim: CanonicalClaim,
    incident: Sequence[ClaimRelation],
    canonical_map: Mapping[str, CanonicalClaim],
    independence_groups: Optional[Mapping[str, str]],
) -> str:
    relation_types = {relation.relation_type for relation in incident}
    if relation_types & {"contradiction", "tension"}:
        own_groups = _known_independent_groups(
            claim.member_source_claim_ids, independence_groups
        )
        neighbor_group_counts = []
        for relation in incident:
            if relation.relation_type not in {"contradiction", "tension"}:
                continue
            other = canonical_map[_other_endpoint(relation, claim.id)]
            groups = _known_independent_groups(
                other.member_source_claim_ids, independence_groups
            )
            if groups is None:
                neighbor_group_counts = []
                break
            neighbor_group_counts.append(len(groups))
        if (
            own_groups is not None
            and neighbor_group_counts
            and len(own_groups) < max(neighbor_group_counts)
        ):
            return "minority_view"
        return "contested"
    if relation_types & _CONDITIONAL_RELATIONS:
        return "conditional"
    if "insufficient_information" in relation_types:
        return "unresolved"

    supporting_canonical_ids = _supporting_canonical_ids(claim, incident)
    support_ids = _support_ids(supporting_canonical_ids, canonical_map)
    support_groups = _known_independent_groups(
        support_ids, independence_groups
    )
    if (
        relation_types <= (_SUPPORT_RELATIONS | {"orthogonal"})
        and support_groups is not None
        and len(support_groups) >= 2
    ):
        return "consensus"
    return "unresolved"


def _condition_summary(claim: CanonicalClaim) -> Optional[str]:
    parts = []
    for condition in sorted(
        claim.semantics.conditions,
        key=lambda item: canonical_dumps(condition_identity(item)),
    ):
        parts.append(
            f"{condition.field} {condition.operator} "
            f"{canonical_dumps(identity_value(condition.value))}"
        )
    if claim.semantics.objective:
        parts.append(f"objective: {claim.semantics.objective}")
    if claim.semantics.population:
        parts.append(f"population: {claim.semantics.population}")
    if claim.semantics.temporal_scope:
        parts.append(f"temporal scope: {claim.semantics.temporal_scope}")
    if claim.semantics.time_horizon:
        parts.append(f"time horizon: {claim.semantics.time_horizon}")
    return "; ".join(parts) if parts else None


def _assertion_rationale(status: str) -> str:
    rationales = {
        "consensus": (
            "Recorded independent support groups agree and no opposing "
            "relationship is present; this status is not a truth score."
        ),
        "contested": (
            "At least one classified direct conflict or definition tension "
            "opposes this position."
        ),
        "conditional": (
            "The position applies conditionally alongside a classified "
            "alternative, qualification, refinement, or conditional disagreement."
        ),
        "minority_view": (
            "Explicit independence-group metadata shows fewer supporting groups "
            "than an opposing position; the view remains preserved without "
            "selecting a winner."
        ),
        "unresolved": (
            "Recorded relationships or independence evidence are insufficient "
            "to assign consensus, conflict, or conditional resolution."
        ),
    }
    return rationales[status]


def _build_assertions(
    canonical_map: Mapping[str, CanonicalClaim],
    incident_map: Mapping[str, Tuple[ClaimRelation, ...]],
    independence_groups: Optional[Mapping[str, str]],
) -> Tuple[SynthesisAssertion, ...]:
    assertions = []
    for claim in canonical_map.values():
        incident = incident_map[claim.id]
        status = _assertion_status(
            claim, incident, canonical_map, independence_groups
        )
        supporting_canonical_ids = _supporting_canonical_ids(claim, incident)
        support_ids = _support_ids(supporting_canonical_ids, canonical_map)
        opposing_ids = _opposing_ids(claim, incident, canonical_map)
        condition_summary = (
            _condition_summary(claim) if status == "conditional" else None
        )
        identity = {
            "text": normalized_text(claim.canonical_proposition),
            "status": status,
            "canonical_claim_ids": list(supporting_canonical_ids),
            "supporting_source_claim_ids": list(support_ids),
            "opposing_source_claim_ids": list(opposing_ids),
            "condition_summary": condition_summary,
        }
        assertions.append(
            SynthesisAssertion(
                id=stable_id("synthesis_assertion", identity),
                text=claim.canonical_proposition,
                status=status,
                canonical_claim_ids=supporting_canonical_ids,
                supporting_source_claim_ids=support_ids,
                opposing_source_claim_ids=opposing_ids,
                condition_summary=condition_summary,
                rationale=_assertion_rationale(status),
            )
        )
    return tuple(sorted(assertions, key=lambda assertion: assertion.id))


def _topic_for_claims(claims: Sequence[CanonicalClaim]) -> str:
    subjects = sorted(
        {
            normalized_text(claim.semantics.subject)
            for claim in claims
            if claim.semantics.subject
        }
    )
    if subjects:
        return subjects[0]
    propositions = sorted(
        normalized_text(claim.canonical_proposition) for claim in claims
    )
    return propositions[0] if propositions else "unresolved topic"


def _components(
    canonical_map: Mapping[str, CanonicalClaim],
    relation_map: Mapping[str, ClaimRelation],
) -> Tuple[Tuple[str, ...], ...]:
    adjacency: Dict[str, Set[str]] = {
        claim_id: set() for claim_id in canonical_map
    }
    for relation in relation_map.values():
        adjacency[relation.left_claim_id].add(relation.right_claim_id)
        adjacency[relation.right_claim_id].add(relation.left_claim_id)

    components = []
    remaining = set(adjacency)
    while remaining:
        start = min(remaining)
        queue = deque([start])
        component = set()
        while queue:
            claim_id = queue.popleft()
            if claim_id in component:
                continue
            component.add(claim_id)
            queue.extend(sorted(adjacency[claim_id] - component))
        remaining.difference_update(component)
        components.append(tuple(sorted(component)))
    return tuple(sorted(components))


def _build_clusters(
    canonical_map: Mapping[str, CanonicalClaim],
    relation_map: Mapping[str, ClaimRelation],
    assertions: Sequence[SynthesisAssertion],
) -> Tuple[TopicCluster, ...]:
    assertion_by_claim = {
        assertion.canonical_claim_ids[0]: assertion.id for assertion in assertions
    }
    clusters = []
    for claim_ids in _components(canonical_map, relation_map):
        claims = tuple(canonical_map[claim_id] for claim_id in claim_ids)
        topic = _topic_for_claims(claims)
        identity = {"topic": topic, "canonical_claim_ids": list(claim_ids)}
        clusters.append(
            TopicCluster(
                id=stable_id("topic_cluster", identity),
                topic=topic,
                canonical_claim_ids=claim_ids,
                assertion_ids=tuple(
                    sorted(assertion_by_claim[claim_id] for claim_id in claim_ids)
                ),
            )
        )
    return tuple(sorted(clusters, key=lambda cluster: cluster.id))


def _shared_and_differing_assumptions(
    left: CanonicalClaim, right: CanonicalClaim
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    left_assumptions = {
        normalized_text(item) for item in left.semantics.assumptions
    }
    right_assumptions = {
        normalized_text(item) for item in right.semantics.assumptions
    }
    return (
        tuple(sorted(left_assumptions & right_assumptions)),
        tuple(sorted(left_assumptions ^ right_assumptions)),
    )


def _conflict_type(relation: ClaimRelation) -> str:
    if relation.relation_type == "contradiction":
        return "direct"
    if relation.relation_type == "conditional_disagreement":
        return "conditional_disagreement"
    if "definition" in relation.conflict_dimensions:
        return "definition_mismatch"
    if "objective" in relation.conflict_dimensions:
        return "objective_mismatch"
    if "population" in relation.conflict_dimensions:
        return "population_mismatch"
    if {"temporal_scope", "time_horizon"} & set(relation.conflict_dimensions):
        return "temporal_mismatch"
    if relation.relation_type == "alternative":
        return "scope_mismatch"
    return "unresolved"


def _build_disputes(
    canonical_map: Mapping[str, CanonicalClaim],
    relation_map: Mapping[str, ClaimRelation],
) -> Tuple[DisputeRecord, ...]:
    disputes = []
    for relation in relation_map.values():
        if relation.relation_type not in _CONFLICT_RELATIONS:
            continue
        left = canonical_map[relation.left_claim_id]
        right = canonical_map[relation.right_claim_id]
        shared, differing = _shared_and_differing_assumptions(left, right)
        conflict_type = _conflict_type(relation)
        conditional = relation.relation_type in {
            "conditional_disagreement",
            "alternative",
        }
        key_variables = tuple(sorted(set(relation.conflict_dimensions)))
        reconciliation = None
        if conditional:
            variables = ", ".join(key_variables) if key_variables else "recorded scope"
            reconciliation = (
                "Apply each position only under its recorded conditions and "
                f"objective; compare the unresolved variables: {variables}."
            )
        position_ids = tuple(sorted((left.id, right.id)))
        identity = {
            "position_claim_ids": list(position_ids),
            "conflict_type": conflict_type,
            "key_variables": list(key_variables),
        }
        disputes.append(
            DisputeRecord(
                id=stable_id("dispute", identity),
                topic=_topic_for_claims((left, right)),
                position_claim_ids=position_ids,
                conflict_type=conflict_type,
                shared_assumptions=shared,
                differing_assumptions=differing,
                key_variables=key_variables,
                reconciliation=reconciliation,
                status=(
                    "reconciled_conditionally" if conditional else "unresolved"
                ),
            )
        )
    return tuple(sorted(disputes, key=lambda dispute: dispute.id))


def _build_gaps(
    canonical_map: Mapping[str, CanonicalClaim],
    relation_map: Mapping[str, ClaimRelation],
    incident_map: Mapping[str, Tuple[ClaimRelation, ...]],
) -> Tuple[SynthesisGap, ...]:
    gaps = []
    for relation in relation_map.values():
        if relation.relation_type != "insufficient_information":
            continue
        related = tuple(sorted((relation.left_claim_id, relation.right_claim_id)))
        gaps.append(
            SynthesisGap(
                id=stable_id(
                    "synthesis_gap",
                    {"kind": "insufficient_scope", "claim_ids": list(related)},
                ),
                text=(
                    "Recorded scope is insufficient to classify the relationship "
                    f"between {related[0]!r} and {related[1]!r}."
                ),
                related_claim_ids=related,
            )
        )
    for claim_id, incident in incident_map.items():
        if incident:
            continue
        gaps.append(
            SynthesisGap(
                id=stable_id(
                    "synthesis_gap",
                    {"kind": "no_comparator", "claim_ids": [claim_id]},
                ),
                text=(
                    f"No comparable canonical claim was retrieved for {claim_id!r}; "
                    "agreement and disagreement remain unresolved."
                ),
                related_claim_ids=(claim_id,),
            )
        )
    unique = {gap.id: gap for gap in gaps}
    return tuple(unique[gap_id] for gap_id in sorted(unique))


def synthesize(
    corpus_id: str,
    canonical_claims: Sequence[CanonicalClaim],
    source_claims: Sequence[SourceClaim],
    relations: Sequence[ClaimRelation],
    synthesis_run_id: str = DEFAULT_SYNTHESIS_RUN_ID,
    *,
    independence_groups: Optional[Mapping[str, str]] = None,
) -> SynthesisArtifact:
    """Build a provenance-complete synthesis without truth or confidence scores."""
    if not isinstance(corpus_id, str) or not corpus_id.strip():
        raise ContractError("corpus_id must be a non-empty string")
    if not isinstance(synthesis_run_id, str) or not synthesis_run_id.strip():
        raise ContractError("synthesis_run_id must be a non-empty string")

    canonical_map, source_map, relation_map = _validate_inputs(
        canonical_claims, source_claims, relations
    )
    incident_map = _incident_relations(canonical_map, relation_map)
    assertions = _build_assertions(
        canonical_map, incident_map, independence_groups
    )
    clusters = _build_clusters(canonical_map, relation_map, assertions)
    disputes = _build_disputes(canonical_map, relation_map)
    gaps = _build_gaps(canonical_map, relation_map, incident_map)

    coverage_notes = [
        "Every synthesis assertion lists canonical and source claim IDs for "
        "provenance resolution to source spans.",
        "Synthesis statuses describe recorded relationships; they are not truth, "
        "confidence, evidence-quality, or predictive-power scores.",
    ]
    if independence_groups is None:
        coverage_notes.append(
            "Source independence was not supplied, so consensus and minority "
            "status were not inferred from raw source counts."
        )
    else:
        unmapped = sorted(set(source_map) - set(independence_groups))
        if unmapped:
            coverage_notes.append(
                "Consensus and minority status were withheld where independence "
                "metadata was incomplete."
            )
    coverage_notes_tuple = tuple(coverage_notes)
    identity = {
        "corpus_id": corpus_id,
        "topic_cluster_ids": [cluster.id for cluster in clusters],
        "assertion_ids": [assertion.id for assertion in assertions],
        "dispute_ids": [dispute.id for dispute in disputes],
        "gap_ids": [gap.id for gap in gaps],
        "coverage_notes": list(coverage_notes_tuple),
    }
    return SynthesisArtifact(
        id=stable_id("synthesis", identity),
        corpus_id=corpus_id,
        topic_clusters=clusters,
        assertions=assertions,
        disputes=disputes,
        unresolved_questions=gaps,
        coverage_notes=coverage_notes_tuple,
        run_id=synthesis_run_id,
    )


synthesize_claims = synthesize


__all__ = [
    "DEFAULT_SYNTHESIS_RUN_ID",
    "synthesize",
    "synthesize_claims",
]
