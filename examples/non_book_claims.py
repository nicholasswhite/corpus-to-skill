"""Use the domain-neutral claim framework for incident-response guidance."""

from __future__ import annotations

import hashlib
import json
from typing import Tuple

from claim_framework import (
    CanonicalClaim,
    ClaimRelation,
    ClaimSemantics,
    Condition,
    ExtractionInfo,
    Locator,
    SourceClaim,
    SourceSpan,
    SynthesisArtifact,
    classify_relations,
    normalize,
    synthesize,
)
from claim_framework.evaluation import evaluate_synthesis_artifact


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _claim(
    claim_id: str,
    source_id: str,
    assertion: str,
    proposition: str,
    semantics: ClaimSemantics,
) -> SourceClaim:
    """Create one reviewed claim with a self-contained exact-offset span."""

    return SourceClaim(
        id=claim_id,
        source_id=source_id,
        source_spans=(
            SourceSpan(
                source_id=source_id,
                locator=Locator(
                    section="Containment",
                    paragraph=1,
                    start_offset=0,
                    end_offset=len(assertion),
                ),
                excerpt=assertion,
                excerpt_checksum=_checksum(assertion),
            ),
        ),
        original_assertion=assertion,
        proposition=proposition,
        claim_type="normative",
        semantics=semantics,
        extraction=ExtractionInfo(
            run_id="incident-example-extract-v1",
            review_status="accepted",
            extraction_confidence=1.0,
        ),
    )


def build_example() -> Tuple[
    Tuple[SourceClaim, ...],
    Tuple[CanonicalClaim, ...],
    Tuple[ClaimRelation, ...],
    SynthesisArtifact,
]:
    """Normalize, relate, and synthesize three non-book source claims."""

    isolate = ClaimSemantics(
        subject="incident responders",
        relation="contain compromised hosts",
        object_or_value="isolate affected hosts",
        polarity="positive",
        modality="should",
        conditions=(
            Condition(
                "incident phase",
                "is",
                "confirmed active lateral movement",
            ),
        ),
        objective="stop lateral movement",
    )
    preserve_evidence = ClaimSemantics(
        subject="incident responders",
        relation="contain compromised hosts",
        object_or_value="keep affected hosts connected",
        polarity="positive",
        modality="may",
        conditions=(
            Condition(
                "incident phase",
                "is",
                "confirmed active lateral movement",
            ),
        ),
        objective="preserve volatile evidence",
    )

    source_claims = (
        _claim(
            "source-claim-soc-runbook",
            "soc-runbook",
            "Responders should isolate affected hosts during active lateral movement.",
            "Isolate affected hosts during confirmed active lateral movement.",
            isolate,
        ),
        _claim(
            "source-claim-cloud-playbook",
            "cloud-playbook",
            "The response team should disconnect compromised endpoints during lateral movement.",
            "Disconnect compromised endpoints during confirmed active lateral movement.",
            isolate,
        ),
        _claim(
            "source-claim-forensics-guide",
            "forensics-guide",
            "Responders may keep an affected host connected to preserve volatile evidence.",
            "Keep affected hosts connected during confirmed active lateral movement.",
            preserve_evidence,
        ),
    )

    canonical_claims = normalize(source_claims, "incident-example-normalize-v1")
    relations = classify_relations(canonical_claims, "incident-example-relate-v1")
    synthesis = synthesize(
        "incident-response-example",
        canonical_claims,
        source_claims,
        relations,
        "incident-example-synthesize-v1",
    )
    return source_claims, canonical_claims, relations, synthesis


def main() -> None:
    source_claims, canonical_claims, relations, synthesis = build_example()
    evaluation = evaluate_synthesis_artifact(
        synthesis,
        (assertion.id for assertion in synthesis.assertions),
        "incident-example-evaluate-v1",
        reviewer="synthetic-example",
    )
    summary = {
        "source_claim_count": len(source_claims),
        "canonical_claim_count": len(canonical_claims),
        "canonical_members": sorted(
            len(claim.member_source_claim_ids) for claim in canonical_claims
        ),
        "relations": sorted(
            [
                {
                    "type": relation.relation_type,
                    "left": relation.left_claim_id,
                    "right": relation.right_claim_id,
                }
                for relation in relations
            ],
            key=lambda item: (item["type"], item["left"], item["right"]),
        ),
        "synthesis_assertions": sorted(
            [
                {
                    "status": assertion.status,
                    "text": assertion.text,
                    "supporting_source_claim_ids": list(
                        assertion.supporting_source_claim_ids
                    ),
                }
                for assertion in synthesis.assertions
            ],
            key=lambda item: (item["status"], item["text"]),
        ),
        "evaluation": {
            "rubric_id": evaluation.rubric_id,
            "rubric_version": evaluation.rubric_version,
            "dimensions": [
                {
                    "id": dimension.dimension_id,
                    "status": dimension.status,
                    "score": dimension.score,
                }
                for dimension in evaluation.dimensions
            ],
            "aggregate_score": evaluation.aggregate_score,
            "aggregation_method": evaluation.aggregation_method,
            "aggregate_component_ids": list(
                evaluation.aggregate_component_ids
            ),
        },
        "coverage_notes": list(synthesis.coverage_notes),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
