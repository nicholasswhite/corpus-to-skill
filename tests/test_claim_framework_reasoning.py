"""Focused reference cases for domain-neutral claim reasoning."""

from __future__ import annotations

import itertools

import pytest

from claim_framework.jsonio import canonical_dumps, loads_record, sha256_text, to_plain
from claim_framework.normalize import identity_value, normalize
from claim_framework.records import (
    ClaimSemantics,
    Condition,
    ContractError,
    ExtractionInfo,
    Locator,
    SourceClaim,
    SourceSpan,
)
from claim_framework.relationships import (
    apply_human_override,
    classify_relation,
    classify_relations,
    retrieve_candidates,
)
from claim_framework.synthesis import synthesize


def _source_claim(
    claim_id,
    proposition,
    semantics,
    *,
    claim_type="descriptive",
    source_id=None,
    original_assertion=None,
):
    source_id = source_id or f"source-{claim_id}"
    assertion = original_assertion or proposition
    return SourceClaim(
        id=claim_id,
        source_id=source_id,
        source_spans=(
            SourceSpan(
                source_id=source_id,
                locator=Locator(start_offset=0, end_offset=len(assertion)),
                excerpt=assertion,
                excerpt_checksum=sha256_text(assertion),
            ),
        ),
        original_assertion=assertion,
        proposition=proposition,
        claim_type=claim_type,
        semantics=semantics,
        extraction=ExtractionInfo("extract-fixture"),
    )


def _canonical_pair(left, right):
    claims = normalize((left, right), "normalize-fixture")
    assert len(claims) == 2
    return claims


def _relation_for(left, right):
    canonical = _canonical_pair(left, right)
    candidates = retrieve_candidates(canonical)
    assert len(candidates) == 1
    return canonical, classify_relation(*candidates[0], "relate-fixture")


def test_normalization_is_conservative_and_retains_members_variants_and_scope():
    low_rate = Condition("interest rate", "is", "low")
    long_horizon = Condition("holding period", "at_least", "ten years")
    shared = ClaimSemantics(
        subject="Housing Choice",
        relation="builds wealth",
        object_or_value="buy",
        polarity="positive",
        population="first-time buyers",
        definitions={"wealth": "after-tax net worth"},
        assumptions=("stable income", "adequate reserves"),
        conditions=(low_rate, long_horizon),
        objective="maximize long-term net worth",
        time_horizon="ten years",
    )
    reordered = ClaimSemantics(
        subject="housing choice",
        relation="BUILDS WEALTH",
        object_or_value="BUY",
        polarity="positive",
        population="First-Time Buyers",
        definitions={"Wealth": "After-tax net worth"},
        assumptions=("adequate reserves", "stable income"),
        conditions=(long_horizon, low_rate),
        objective="Maximize long-term net worth",
        time_horizon="Ten Years",
    )
    changed_scope = ClaimSemantics(
        subject="housing choice",
        relation="builds wealth",
        object_or_value="buy",
        polarity="positive",
        population="first-time buyers",
        definitions={"wealth": "after-tax net worth"},
        assumptions=("adequate reserves", "stable income"),
        conditions=(Condition("interest rate", "is", "high"), long_horizon),
        objective="maximize long-term net worth",
        time_horizon="ten years",
    )
    first = _source_claim(
        "claim-a",
        "Buying builds wealth in the recorded scope.",
        shared,
        original_assertion="Buying builds wealth under these conditions.",
    )
    second = _source_claim(
        "claim-b",
        "A purchase builds wealth in the recorded scope.",
        reordered,
        original_assertion="A purchase can build wealth here.",
    )
    third = _source_claim(
        "claim-c",
        "Buying builds wealth even when rates are high.",
        changed_scope,
    )

    forward = normalize((first, second, third), "normalize-fixture")
    reverse = normalize((third, second, first), "normalize-fixture")

    assert forward == reverse
    assert len(forward) == 2
    merged = next(
        claim for claim in forward if len(claim.member_source_claim_ids) == 2
    )
    assert merged.member_source_claim_ids == ("claim-a", "claim-b")
    assert set(merged.preserved_variants) == {
        first.original_assertion,
        first.proposition,
        second.original_assertion,
        second.proposition,
    }
    assert changed_scope.conditions != merged.semantics.conditions


def test_normalization_does_not_merge_claims_with_missing_structured_identity():
    semantics = ClaimSemantics(polarity="positive")
    left = _source_claim("claim-a", "The same words.", semantics)
    right = _source_claim("claim-b", "The same words.", semantics)

    normalized = normalize((left, right), "normalize-fixture")

    assert len(normalized) == 2
    assert all(len(claim.member_source_claim_ids) == 1 for claim in normalized)
    assert all("singleton" in claim.normalization_rationale for claim in normalized)


@pytest.mark.parametrize(
    "value",
    (
        {"A": None, "a": "conflicting"},
        {"a": "conflicting", "A": None},
    ),
)
def test_normalized_mapping_collisions_are_rejected_in_every_input_order(value):
    with pytest.raises(ContractError, match="colliding normalized key 'a'"):
        identity_value(value)


def test_direct_counterexample_is_a_symmetric_contradiction():
    left = _source_claim(
        "claim-all-white",
        "All swans in population P are white.",
        ClaimSemantics(
            subject="swans",
            relation="color",
            object_or_value="white",
            polarity="positive",
            quantifier="all",
            population="population P",
        ),
    )
    right = _source_claim(
        "claim-some-black",
        "Some swans in population P are black.",
        ClaimSemantics(
            subject="swans",
            relation="color",
            object_or_value="black",
            polarity="positive",
            quantifier="some",
            population="population P",
        ),
    )
    canonical, relation = _relation_for(left, right)

    assert relation.relation_type == "contradiction"
    assert relation.directionality == "symmetric"
    assert relation.left_claim_id < relation.right_claim_id
    assert {"object_or_value", "quantifier"} <= set(
        relation.conflict_dimensions
    )
    assert relation == classify_relation(canonical[1], canonical[0], "relate-fixture")


def test_human_relation_override_is_typed_audited_and_round_trips():
    left = _source_claim(
        "override-left",
        "All queues prevent overload.",
        ClaimSemantics(
            subject="all queues",
            relation="prevent",
            object_or_value="overload",
            polarity="positive",
            quantifier="all",
        ),
    )
    right = _source_claim(
        "override-right",
        "No queues prevent overload.",
        ClaimSemantics(
            subject="no queues",
            relation="prevent",
            object_or_value="overload",
            polarity="negative",
            quantifier="no",
        ),
    )
    _, relation = _relation_for(left, right)

    corrected = apply_human_override(
        relation,
        "tension",
        reviewer="reviewer-17",
        timestamp="2026-08-05T21:00:00Z",
        reason="The recorded definitions diverge in the reviewed source context.",
    )

    assert corrected.id != relation.id
    assert corrected.relation_type == "tension"
    assert corrected.classification.review_status == "corrected"
    assert corrected.human_override.prior_record_id == relation.id
    assert corrected.human_override.prior_value == "contradiction"
    assert corrected.human_override.new_value == "tension"
    assert loads_record(canonical_dumps(corrected), type(corrected)) == corrected

    with pytest.raises(ContractError, match="must change"):
        apply_human_override(
            relation,
            relation.relation_type,
            reviewer="reviewer-17",
            timestamp="2026-08-05T21:00:00Z",
            reason="No actual correction.",
        )


def test_existential_positive_and_negative_can_coexist():
    shared = {
        "subject": "swans",
        "relation": "is white",
        "object_or_value": "white",
        "quantifier": "some",
        "population": "population P",
    }
    left = _source_claim(
        "claim-some-white",
        "Some swans in population P are white.",
        ClaimSemantics(**shared, polarity="positive"),
    )
    right = _source_claim(
        "claim-some-not-white",
        "Some swans in population P are not white.",
        ClaimSemantics(**shared, polarity="negative"),
    )

    _, relation = _relation_for(left, right)

    assert relation.relation_type == "insufficient_information"
    assert relation.relation_type != "contradiction"


def test_different_conditions_produce_conditional_disagreement():
    left = _source_claim(
        "claim-buy",
        "Buying tends to build wealth with low rates and a long holding period.",
        ClaimSemantics(
            subject="housing choice",
            relation="preferred action",
            object_or_value="buy",
            polarity="positive",
            conditions=(
                Condition("interest rate", "is", "low"),
                Condition("holding period", "is", "long"),
            ),
            objective="maximize expected wealth",
            time_horizon="long",
        ),
        claim_type="comparative",
    )
    right = _source_claim(
        "claim-rent",
        "Renting can outperform with high rates and a short holding period.",
        ClaimSemantics(
            subject="housing choice",
            relation="preferred action",
            object_or_value="rent",
            polarity="positive",
            conditions=(
                Condition("interest rate", "is", "high"),
                Condition("holding period", "is", "short"),
            ),
            objective="maximize expected wealth",
            time_horizon="short",
        ),
        claim_type="comparative",
    )

    _, relation = _relation_for(left, right)

    assert relation.relation_type == "conditional_disagreement"
    assert relation.scope_analysis.condition_overlap == "disjoint"
    assert "conditions" in relation.conflict_dimensions
    assert "time_horizon" in relation.conflict_dimensions


def test_definition_mismatch_is_tension_not_contradiction():
    left = _source_claim(
        "claim-cash-flow",
        "A home is not an asset.",
        ClaimSemantics(
            subject="home",
            relation="is asset",
            object_or_value="asset",
            polarity="negative",
            definitions={"asset": "produces positive cash flow"},
        ),
        claim_type="definitional",
    )
    right = _source_claim(
        "claim-net-worth",
        "A home is an asset.",
        ClaimSemantics(
            subject="home",
            relation="is asset",
            object_or_value="asset",
            polarity="positive",
            definitions={"asset": "contributes to net worth"},
        ),
        claim_type="definitional",
    )

    canonical, relation = _relation_for(left, right)

    assert relation.relation_type == "tension"
    assert relation.scope_analysis.term_definition_alignment == "divergent"
    assert "definition" in relation.conflict_dimensions
    artifact = synthesize(
        "corpus-definitions",
        canonical,
        (left, right),
        (relation,),
        "synthesize-fixture",
    )
    assert artifact.disputes[0].conflict_type == "definition_mismatch"
    assert {item.status for item in artifact.assertions} == {"contested"}


def test_objective_mismatch_is_an_alternative():
    left = _source_claim(
        "claim-invest",
        "Invest surplus cash.",
        ClaimSemantics(
            subject="surplus cash decision",
            relation="allocate",
            object_or_value="invest",
            polarity="positive",
            objective="maximize expected return",
        ),
        claim_type="normative",
    )
    right = _source_claim(
        "claim-mortgage",
        "Pay off the mortgage.",
        ClaimSemantics(
            subject="surplus cash decision",
            relation="allocate",
            object_or_value="mortgage payoff",
            polarity="positive",
            objective="reduce fixed-obligation risk",
        ),
        claim_type="normative",
    )

    canonical, relation = _relation_for(left, right)

    assert relation.relation_type == "alternative"
    assert relation.scope_analysis.objective_alignment == "divergent"
    assert "objective" in relation.conflict_dimensions
    artifact = synthesize(
        "corpus-objectives",
        canonical,
        (left, right),
        (relation,),
        "synthesize-fixture",
    )
    assert artifact.disputes[0].conflict_type == "objective_mismatch"
    assert artifact.disputes[0].status == "reconciled_conditionally"


def test_compatible_properties_are_orthogonal():
    left = _source_claim(
        "claim-equity",
        "Homeownership builds equity.",
        ClaimSemantics(
            subject="homeownership",
            relation="builds",
            object_or_value="equity",
            polarity="positive",
        ),
    )
    right = _source_claim(
        "claim-maintenance",
        "Homeownership requires maintenance.",
        ClaimSemantics(
            subject="homeownership",
            relation="requires",
            object_or_value="maintenance",
            polarity="positive",
        ),
    )

    _, relation = _relation_for(left, right)

    assert relation.relation_type == "orthogonal"


def test_asymmetric_missing_scope_forces_abstention():
    left = _source_claim(
        "claim-scoped",
        "The intervention improves outcomes for adults.",
        ClaimSemantics(
            subject="intervention",
            relation="improves",
            object_or_value="outcomes",
            polarity="positive",
            population="adults",
            temporal_scope="one year",
        ),
    )
    right = _source_claim(
        "claim-unscoped",
        "The intervention does not improve outcomes.",
        ClaimSemantics(
            subject="intervention",
            relation="improves",
            object_or_value="outcomes",
            polarity="negative",
            temporal_scope="one year",
        ),
    )

    _, relation = _relation_for(left, right)

    assert relation.relation_type == "insufficient_information"
    assert relation.scope_analysis.population_overlap == "unknown"


def test_relations_and_synthesis_are_input_order_stable_and_traceable():
    left = _source_claim(
        "claim-a",
        "All swans in population P are white.",
        ClaimSemantics(
            subject="swans",
            relation="color",
            object_or_value="white",
            polarity="positive",
            quantifier="all",
            population="population P",
        ),
    )
    right = _source_claim(
        "claim-b",
        "Some swans in population P are black.",
        ClaimSemantics(
            subject="swans",
            relation="color",
            object_or_value="black",
            polarity="positive",
            quantifier="some",
            population="population P",
        ),
    )
    sources = (left, right)
    canonical = normalize(sources, "normalize-fixture")
    relations = classify_relations(canonical, "relate-fixture")
    artifact = synthesize(
        "corpus-fixture",
        canonical,
        sources,
        relations,
        "synthesize-fixture",
    )

    for source_order in itertools.permutations(sources):
        reversed_canonical = normalize(source_order, "normalize-fixture")
        reversed_relations = classify_relations(
            tuple(reversed(reversed_canonical)), "relate-fixture"
        )
        reversed_artifact = synthesize(
            "corpus-fixture",
            tuple(reversed(reversed_canonical)),
            source_order,
            tuple(reversed(reversed_relations)),
            "synthesize-fixture",
        )
        assert to_plain(reversed_canonical) == to_plain(canonical)
        assert to_plain(reversed_relations) == to_plain(relations)
        assert to_plain(reversed_artifact) == to_plain(artifact)

    assert {assertion.status for assertion in artifact.assertions} == {"contested"}
    assert len(artifact.disputes) == 1
    assert artifact.disputes[0].conflict_type == "direct"
    source_by_id = {claim.id: claim for claim in sources}
    for assertion in artifact.assertions:
        assert assertion.canonical_claim_ids
        assert assertion.supporting_source_claim_ids
        assert assertion.opposing_source_claim_ids
        assert all(
            source_by_id[source_id].source_spans
            for source_id in assertion.supporting_source_claim_ids
        )


def test_synthesis_emits_conditional_rules_and_unresolved_gaps():
    conditional_left = _source_claim(
        "claim-buy",
        "Buy when rates are low.",
        ClaimSemantics(
            subject="housing choice",
            relation="preferred action",
            object_or_value="buy",
            polarity="positive",
            conditions=(Condition("interest rate", "is", "low"),),
            objective="maximize expected wealth",
        ),
        claim_type="normative",
    )
    conditional_right = _source_claim(
        "claim-rent",
        "Rent when rates are high.",
        ClaimSemantics(
            subject="housing choice",
            relation="preferred action",
            object_or_value="rent",
            polarity="positive",
            conditions=(Condition("interest rate", "is", "high"),),
            objective="maximize expected wealth",
        ),
        claim_type="normative",
    )
    conditional_sources = (conditional_left, conditional_right)
    conditional_canonical = normalize(conditional_sources, "normalize-fixture")
    conditional_relations = classify_relations(
        conditional_canonical, "relate-fixture"
    )
    conditional_artifact = synthesize(
        "corpus-conditional",
        conditional_canonical,
        conditional_sources,
        conditional_relations,
        "synthesize-fixture",
    )

    assert {item.status for item in conditional_artifact.assertions} == {
        "conditional"
    }
    assert all(item.condition_summary for item in conditional_artifact.assertions)
    assert conditional_artifact.disputes[0].status == "reconciled_conditionally"
    assert conditional_artifact.disputes[0].reconciliation

    missing_left = _source_claim(
        "claim-scoped",
        "The intervention improves outcomes for adults.",
        ClaimSemantics(
            subject="intervention",
            relation="improves",
            object_or_value="outcomes",
            polarity="positive",
            population="adults",
        ),
    )
    missing_right = _source_claim(
        "claim-unscoped",
        "The intervention does not improve outcomes.",
        ClaimSemantics(
            subject="intervention",
            relation="improves",
            object_or_value="outcomes",
            polarity="negative",
        ),
    )
    missing_sources = (missing_left, missing_right)
    missing_canonical = normalize(missing_sources, "normalize-fixture")
    missing_relations = classify_relations(missing_canonical, "relate-fixture")
    missing_artifact = synthesize(
        "corpus-missing",
        missing_canonical,
        missing_sources,
        missing_relations,
        "synthesize-fixture",
    )

    assert {item.status for item in missing_artifact.assertions} == {"unresolved"}
    assert missing_artifact.unresolved_questions
    assert set(missing_artifact.unresolved_questions[0].related_claim_ids) == {
        claim.id for claim in missing_canonical
    }


def test_consensus_and_minority_require_explicit_independence_groups():
    shared_semantics = ClaimSemantics(
        subject="queue capacity",
        relation="prevents",
        object_or_value="overload",
        polarity="positive",
    )
    support_a = _source_claim(
        "support-a",
        "Bounded queues prevent overload.",
        shared_semantics,
    )
    support_b = _source_claim(
        "support-b",
        "Capacity-bounded queues prevent overload.",
        shared_semantics,
    )
    consensus_canonical = normalize(
        (support_a, support_b), "normalize-fixture"
    )

    without_groups = synthesize(
        "corpus-consensus",
        consensus_canonical,
        (support_a, support_b),
        (),
        "synthesize-fixture",
    )
    with_groups = synthesize(
        "corpus-consensus",
        consensus_canonical,
        (support_a, support_b),
        (),
        "synthesize-fixture",
        independence_groups={"support-a": "group-a", "support-b": "group-b"},
    )

    assert without_groups.assertions[0].status == "unresolved"
    assert with_groups.assertions[0].status == "consensus"
    assert any(
        "not inferred from raw source counts" in note
        for note in without_groups.coverage_notes
    )

    opposing = _source_claim(
        "opposing",
        "Bounded queues do not prevent overload.",
        ClaimSemantics(
            subject="queue capacity",
            relation="prevents",
            object_or_value="overload",
            polarity="negative",
        ),
    )
    all_sources = (support_a, support_b, opposing)
    contested_canonical = normalize(all_sources, "normalize-fixture")
    contested_relations = classify_relations(
        contested_canonical, "relate-fixture"
    )
    artifact = synthesize(
        "corpus-minority",
        contested_canonical,
        all_sources,
        contested_relations,
        "synthesize-fixture",
        independence_groups={
            "support-a": "group-a",
            "support-b": "group-b",
            "opposing": "group-c",
        },
    )

    statuses_by_text = {
        assertion.text: assertion.status for assertion in artifact.assertions
    }
    assert statuses_by_text[opposing.proposition] == "minority_view"
    assert set(statuses_by_text.values()) == {"contested", "minority_view"}
