"""Deterministically compile a provenance-complete corpus skill.

The compiler is deliberately a separate, additive path.  It consumes reviewed
claim-framework records and writes only beneath ``skill/<corpus-slug>/`` in the
provided artifact store.  It never reads source text itself; the supplied
``ProvenanceResolver`` is the authority for verifying every synthesis assertion
before the first output byte is written.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

from book_to_skill.corpus.security import instruction_pattern_ids
from book_to_skill.sanitize import sanitize_extracted_text
from claim_framework.jsonio import (
    canonical_dumps,
    sha256_bytes,
    sha256_text,
    stable_id,
    to_plain,
)
from claim_framework.provenance import ProvenanceResolver, ProvenanceTrace
from claim_framework.records import (
    CanonicalClaim,
    ClaimRelation,
    Condition,
    CorpusManifest,
    DisputeRecord,
    SkillBuildManifest,
    SourceClaim,
    SourceRecord,
    SourceSpan,
    SynthesisArtifact,
    SynthesisAssertion,
    TopicCluster,
)
from claim_framework.store import ArtifactStore


COMPILER_VERSION = "corpus-skill-compiler-v1"

_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s<>\"']+")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![\w:/])/(?:[^/\s<>\"']+/)+[^\s<>\"']*")
_MARKDOWN_ESCAPE = re.compile(r"([\\`*_[\]<>#])")
_STATUS_ORDER = {
    "consensus": 0,
    "conditional": 1,
    "contested": 2,
    "minority_view": 3,
    "unresolved": 4,
}
_STATUS_LABEL = {
    "consensus": "Consensus",
    "conditional": "Conditional guidance",
    "contested": "Contested",
    "minority_view": "Minority view",
    "unresolved": "Unresolved",
}


class CorpusCompilationError(ValueError):
    """Raised before output when corpus records cannot form a safe skill."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        raise CorpusCompilationError("clock must return an ISO-8601 string or datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _index(records: Iterable[Any], label: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for record in records:
        record_id = getattr(record, "id", None)
        if not isinstance(record_id, str) or not record_id:
            raise CorpusCompilationError(f"{label} contains a record without an id")
        if _looks_absolute(record_id):
            raise CorpusCompilationError(f"{label} id must not be an absolute path")
        if record_id in result:
            raise CorpusCompilationError(f"duplicate {label} id: {record_id}")
        result[record_id] = record
    return result


def _looks_absolute(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace("\\", "/")
    return bool(
        re.match(r"^[A-Za-z]:/", normalized)
        or normalized.startswith("//")
        or normalized.startswith("/")
    )


def _portable_ref(value: Optional[str]) -> Optional[str]:
    """Return a portable relative ref, omitting absolute or escaping paths."""

    if not value:
        return None
    if "://" in value:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.netloc:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        # Queries and fragments frequently contain signed access tokens or
        # user-specific selectors.  Portable provenance records the stable
        # resource location without those credential-bearing components.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    normalized = value.replace("\\", "/")
    if _looks_absolute(normalized):
        return None
    candidate = PurePosixPath(normalized)
    if not candidate.parts or ".." in candidate.parts:
        return None
    return candidate.as_posix()


def _slugify(value: str, fallback_identity: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    if not slug:
        slug = "corpus-" + stable_id("slug", fallback_identity).rsplit("_", 1)[-1][:12]
    slug = slug[:64].rstrip("-")
    return slug or "corpus-skill"


class _SafeText:
    """Render untrusted record text without emitting prompt-shaped content."""

    def __init__(self) -> None:
        self.rule_ids: Set[str] = set()
        self.absolute_paths_omitted = 0
        self.invisible_codepoints_removed = 0

    def plain(self, value: Any, fallback: str = "[redacted unsafe text]") -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            raw = canonical_dumps(value)
        else:
            raw = str(value)
        sanitized, removed = sanitize_extracted_text(raw)
        self.invisible_codepoints_removed += removed
        sanitized = " ".join(sanitized.split())
        findings = instruction_pattern_ids(sanitized)
        if findings:
            self.rule_ids.update(findings)
            return fallback
        sanitized, windows_count = _WINDOWS_ABSOLUTE_PATH.subn(
            "[portable reference omitted]", sanitized
        )
        sanitized, posix_count = _POSIX_ABSOLUTE_PATH.subn(
            "[portable reference omitted]", sanitized
        )
        self.absolute_paths_omitted += windows_count + posix_count
        return sanitized or fallback

    def markdown(self, value: Any, fallback: str = "[redacted unsafe text]") -> str:
        return _MARKDOWN_ESCAPE.sub(r"\\\1", self.plain(value, fallback=fallback))


@dataclass(frozen=True)
class _Inputs:
    manifest: CorpusManifest
    sources: Mapping[str, SourceRecord]
    source_claims: Mapping[str, SourceClaim]
    canonical_claims: Mapping[str, CanonicalClaim]
    relations: Mapping[str, ClaimRelation]
    synthesis: SynthesisArtifact
    traces: Mapping[str, ProvenanceTrace]


@dataclass
class _RenderState:
    inputs: _Inputs
    safe_text: _SafeText
    skill_slug: str
    assertion_exclusions: Dict[str, Tuple[str, ...]]
    chapter_paths_by_assertion: Dict[str, List[str]]

    def is_renderable(self, assertion: SynthesisAssertion) -> bool:
        return assertion.id not in self.assertion_exclusions


def _validate_inputs(
    manifest: CorpusManifest,
    source_records: Sequence[SourceRecord],
    source_claims: Sequence[SourceClaim],
    canonical_claims: Sequence[CanonicalClaim],
    claim_relations: Sequence[ClaimRelation],
    synthesis: SynthesisArtifact,
    provenance: ProvenanceResolver,
) -> _Inputs:
    if synthesis.corpus_id != manifest.id:
        raise CorpusCompilationError(
            "synthesis corpus_id does not match the corpus manifest"
        )

    sources = _index(source_records, "source record")
    source_claim_index = _index(source_claims, "source claim")
    canonical_index = _index(canonical_claims, "canonical claim")
    relation_index = _index(claim_relations, "claim relation")
    assertion_index = _index(synthesis.assertions, "synthesis assertion")
    cluster_index = _index(synthesis.topic_clusters, "topic cluster")
    _index(synthesis.disputes, "dispute")
    _index(synthesis.unresolved_questions, "synthesis gap")

    for source in sources.values():
        if source.corpus_id != manifest.id:
            raise CorpusCompilationError(
                f"source record {source.id} belongs to corpus {source.corpus_id}, not {manifest.id}"
            )
    for claim in source_claim_index.values():
        if claim.source_id not in sources:
            raise CorpusCompilationError(
                f"source claim {claim.id} references missing source record {claim.source_id}"
            )
    for claim in canonical_index.values():
        missing = sorted(set(claim.member_source_claim_ids) - set(source_claim_index))
        if missing:
            raise CorpusCompilationError(
                f"canonical claim {claim.id} references missing source claims: {', '.join(missing)}"
            )
    for relation in relation_index.values():
        for claim_id in (relation.left_claim_id, relation.right_claim_id):
            if claim_id not in canonical_index:
                raise CorpusCompilationError(
                    f"claim relation {relation.id} references missing canonical claim {claim_id}"
                )
        missing = sorted(
            set(relation.supporting_source_claim_ids) - set(source_claim_index)
        )
        if missing:
            raise CorpusCompilationError(
                f"claim relation {relation.id} references missing source claims: {', '.join(missing)}"
            )
    for assertion in assertion_index.values():
        missing_canonical = sorted(
            set(assertion.canonical_claim_ids) - set(canonical_index)
        )
        missing_sources = sorted(
            (
                set(assertion.supporting_source_claim_ids)
                | set(assertion.opposing_source_claim_ids)
            )
            - set(source_claim_index)
        )
        if missing_canonical or missing_sources:
            details = []
            if missing_canonical:
                details.append("canonical claims " + ", ".join(missing_canonical))
            if missing_sources:
                details.append("source claims " + ", ".join(missing_sources))
            raise CorpusCompilationError(
                f"synthesis assertion {assertion.id} references missing " + "; ".join(details)
            )
    for cluster in cluster_index.values():
        missing_assertions = sorted(set(cluster.assertion_ids) - set(assertion_index))
        missing_canonical = sorted(
            set(cluster.canonical_claim_ids) - set(canonical_index)
        )
        if missing_assertions or missing_canonical:
            raise CorpusCompilationError(
                f"topic cluster {cluster.id} contains orphaned claim or assertion references"
            )

    known_position_ids = set(canonical_index) | set(source_claim_index)
    for dispute in synthesis.disputes:
        missing = sorted(set(dispute.position_claim_ids) - known_position_ids)
        if missing:
            raise CorpusCompilationError(
                f"dispute {dispute.id} references missing positions: {', '.join(missing)}"
            )
    for gap in synthesis.unresolved_questions:
        missing = sorted(set(gap.related_claim_ids) - known_position_ids)
        if missing:
            raise CorpusCompilationError(
                f"synthesis gap {gap.id} references missing claims: {', '.join(missing)}"
            )

    # This call is intentionally the final validation operation and precedes
    # every store write.  It verifies extracted-text and excerpt checksums in
    # addition to checking canonical/source lineage.
    traces_sequence = tuple(provenance.validate_synthesis(synthesis))
    traces: Dict[str, ProvenanceTrace] = {}
    for trace in traces_sequence:
        trace_id = getattr(trace, "assertion_id", None)
        if not isinstance(trace_id, str) or not trace_id:
            raise CorpusCompilationError(
                "provenance resolver returned a trace without an assertion_id"
            )
        if trace_id in traces:
            raise CorpusCompilationError(
                f"duplicate provenance trace assertion_id: {trace_id}"
            )
        traces[trace_id] = trace
    expected_assertion_ids = set(assertion_index)
    if set(traces) != expected_assertion_ids:
        missing = sorted(expected_assertion_ids - set(traces))
        extra = sorted(set(traces) - expected_assertion_ids)
        raise CorpusCompilationError(
            "provenance resolver did not return exactly one trace per assertion "
            f"(missing={missing}, extra={extra})"
        )

    return _Inputs(
        manifest=manifest,
        sources=sources,
        source_claims=source_claim_index,
        canonical_claims=canonical_index,
        relations=relation_index,
        synthesis=synthesis,
        traces=traces,
    )


def _assertion_sort_key(assertion: SynthesisAssertion) -> Tuple[int, str]:
    return (_STATUS_ORDER.get(assertion.status, 99), assertion.id)


def _cluster_sort_key(cluster: TopicCluster) -> Tuple[str, str]:
    return (" ".join(cluster.topic.casefold().split()), cluster.id)


def _condition_text(condition: Condition, safe: _SafeText) -> str:
    field = safe.markdown(condition.field)
    operator = safe.markdown(condition.operator.replace("_", " "))
    value = safe.markdown(condition.value)
    return f"{field} {operator} {value}"


def _conditions_for_assertion(
    assertion: SynthesisAssertion,
    inputs: _Inputs,
    safe: _SafeText,
) -> Tuple[str, ...]:
    values: List[str] = []
    if assertion.condition_summary:
        values.append(safe.markdown(assertion.condition_summary))
    for canonical_id in assertion.canonical_claim_ids:
        semantics = inputs.canonical_claims[canonical_id].semantics
        values.extend(_condition_text(item, safe) for item in semantics.conditions)
        values.extend(
            f"Assumption: {safe.markdown(item)}" for item in semantics.assumptions
        )
        if semantics.objective:
            values.append(f"Objective: {safe.markdown(semantics.objective)}")
        if semantics.population:
            values.append(f"Population: {safe.markdown(semantics.population)}")
        if semantics.geography:
            values.append(f"Geography: {safe.markdown(semantics.geography)}")
        if semantics.temporal_scope:
            values.append(
                f"Temporal scope: {safe.markdown(semantics.temporal_scope)}"
            )
        if semantics.time_horizon:
            values.append(f"Time horizon: {safe.markdown(semantics.time_horizon)}")
    return tuple(dict.fromkeys(value for value in values if value))


def _locator_plain(span: SourceSpan, safe: _SafeText) -> Mapping[str, Any]:
    locator = span.locator
    result: Dict[str, Any] = {}
    for name in ("page", "chapter", "section", "paragraph", "start_offset", "end_offset"):
        value = getattr(locator, name)
        if value is not None:
            result[name] = safe.plain(value) if isinstance(value, str) else value
    if span.excerpt_checksum:
        result["excerpt_checksum"] = span.excerpt_checksum
    return result


def _locator_markdown(span: SourceSpan, safe: _SafeText) -> str:
    locator = span.locator
    parts: List[str] = []
    if locator.page is not None:
        parts.append(f"page {locator.page}")
    if locator.chapter:
        parts.append(f"chapter {safe.markdown(locator.chapter)}")
    if locator.section:
        parts.append(f"section {safe.markdown(locator.section)}")
    if locator.paragraph is not None:
        parts.append(f"paragraph {locator.paragraph}")
    if locator.start_offset is not None and locator.end_offset is not None:
        parts.append(f"characters {locator.start_offset}-{locator.end_offset}")
    return "; ".join(parts) or "recorded locator"


def _trace_locations(
    assertion: SynthesisAssertion,
    state: _RenderState,
    role: str,
) -> Tuple[str, ...]:
    claim_ids = (
        assertion.supporting_source_claim_ids
        if role == "supporting"
        else assertion.opposing_source_claim_ids
    )
    allowed = set(claim_ids)
    locations: List[Tuple[str, str, SourceSpan]] = []
    if role == "supporting":
        for resolved in state.inputs.traces[assertion.id].spans:
            if resolved.source_claim_id in allowed:
                locations.append(
                    (resolved.source_record_id, resolved.source_claim_id, resolved.span)
                )
    else:
        for claim_id in claim_ids:
            claim = state.inputs.source_claims[claim_id]
            for span in claim.source_spans:
                locations.append((claim.source_id, claim.id, span))
    locations.sort(
        key=lambda item: (
            item[0],
            item[1],
            canonical_dumps(_locator_plain(item[2], state.safe_text)),
        )
    )
    return tuple(
        f"{state.safe_text.markdown(source_id)} ({_locator_markdown(span, state.safe_text)})"
        for source_id, _claim_id, span in locations
    )


def _source_locations_for_claim_ids(
    claim_ids: Iterable[str], state: _RenderState
) -> Tuple[str, ...]:
    source_claim_ids: Set[str] = set()
    for claim_id in claim_ids:
        if claim_id in state.inputs.source_claims:
            source_claim_ids.add(claim_id)
        elif claim_id in state.inputs.canonical_claims:
            source_claim_ids.update(
                state.inputs.canonical_claims[claim_id].member_source_claim_ids
            )
    locations: List[Tuple[str, str, SourceSpan]] = []
    for source_claim_id in sorted(source_claim_ids):
        claim = state.inputs.source_claims[source_claim_id]
        for span in claim.source_spans:
            locations.append((claim.source_id, claim.id, span))
    locations.sort(
        key=lambda item: (
            item[0],
            item[1],
            canonical_dumps(_locator_plain(item[2], state.safe_text)),
        )
    )
    return tuple(
        f"{state.safe_text.markdown(source_id)} ({_locator_markdown(span, state.safe_text)})"
        for source_id, _source_claim_id, span in locations
    )


def _render_assertion(assertion: SynthesisAssertion, state: _RenderState) -> List[str]:
    safe = state.safe_text
    lines = [f"- **{_STATUS_LABEL[assertion.status]}:** {safe.markdown(assertion.text)}"]
    conditions = _conditions_for_assertion(assertion, state.inputs, safe)
    if conditions:
        lines.append("  - Applies when: " + "; ".join(conditions))
    elif assertion.status == "conditional":
        lines.append("  - Applies when: conditions are not fully specified; review before use.")
    supporting = _trace_locations(assertion, state, "supporting")
    if supporting:
        lines.append("  - Supporting sources: " + "; ".join(supporting))
    opposing = _trace_locations(assertion, state, "opposing")
    if opposing:
        lines.append("  - Opposing sources: " + "; ".join(opposing))
    lines.append(
        "  - Trace: assertion "
        + safe.markdown(assertion.id)
        + "; canonical claims "
        + ", ".join(safe.markdown(item) for item in assertion.canonical_claim_ids)
    )
    return lines


def _cluster_assertions(cluster: TopicCluster, state: _RenderState) -> Tuple[SynthesisAssertion, ...]:
    assertions = {item.id: item for item in state.inputs.synthesis.assertions}
    selected: Set[str] = set(cluster.assertion_ids)
    if not selected:
        cluster_claims = set(cluster.canonical_claim_ids)
        selected.update(
            assertion.id
            for assertion in assertions.values()
            if cluster_claims.intersection(assertion.canonical_claim_ids)
        )
    return tuple(sorted((assertions[item] for item in selected), key=_assertion_sort_key))


def _chapter_plan(state: _RenderState) -> Tuple[Tuple[TopicCluster, str], ...]:
    plan: List[Tuple[TopicCluster, str]] = []
    used: Set[str] = set()
    for index, cluster in enumerate(
        sorted(state.inputs.synthesis.topic_clusters, key=_cluster_sort_key), start=1
    ):
        base = _slugify(cluster.topic, cluster.id)
        filename = f"{index:02d}-{base}.md"
        if filename in used:
            suffix = stable_id("chapter", cluster.id).rsplit("_", 1)[-1][:8]
            filename = f"{index:02d}-{base[:53].rstrip('-')}-{suffix}.md"
        used.add(filename)
        plan.append((cluster, f"chapters/{filename}"))
    return tuple(plan)


def _render_topic_chapter(
    cluster: TopicCluster, path: str, state: _RenderState
) -> str:
    safe = state.safe_text
    assertions = _cluster_assertions(cluster, state)
    lines = [
        f"# {safe.markdown(cluster.topic)}",
        "",
        f"Cluster ID: `{safe.markdown(cluster.id)}`",
        "",
        "Use each item according to its recorded synthesis status. A consensus label is not a truth score.",
        "",
    ]
    renderable = [item for item in assertions if state.is_renderable(item)]
    for status in _STATUS_ORDER:
        group = [item for item in renderable if item.status == status]
        if not group:
            continue
        lines.extend([f"## {_STATUS_LABEL[status]}", ""])
        for assertion in group:
            lines.extend(_render_assertion(assertion, state))
            lines.append("")
            state.chapter_paths_by_assertion.setdefault(assertion.id, []).append(path)

    omitted = [item for item in assertions if not state.is_renderable(item)]
    if omitted:
        lines.extend(
            [
                "## Omitted material",
                "",
                "The following assertion records are available only in the traceability map because their source review or safety status prevents prose rendering:",
                "",
            ]
        )
        for assertion in omitted:
            reasons = ", ".join(state.assertion_exclusions[assertion.id])
            lines.append(f"- `{safe.markdown(assertion.id)}` ({safe.markdown(reasons)})")
        lines.append("")

    if not assertions:
        lines.extend(
            [
                "No synthesis assertions were assigned to this cluster. Treat this as a coverage gap.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_skill_md(
    state: _RenderState, chapter_plan: Sequence[Tuple[TopicCluster, str]]
) -> str:
    safe = state.safe_text
    corpus_name = safe.plain(state.inputs.manifest.name, "the compiled corpus")
    description = (
        f"Apply evidence-traceable guidance synthesized from {corpus_name}. "
        "Use for its covered topics, decision conditions, disputes, and documented knowledge gaps."
    )
    lines = [
        "---",
        f"name: {state.skill_slug}",
        "description: " + json.dumps(description, ensure_ascii=False),
        "---",
        "",
        f"# {safe.markdown(state.inputs.manifest.name, 'Compiled corpus guidance')}",
        "",
        "Apply this corpus synthesis without treating repetition as proof or resolving recorded dissent by default.",
        "",
        "## Workflow",
        "",
        "1. Read [sources and method](chapters/00-sources-and-method.md) to understand scope and limitations.",
        "2. Open the topic chapter that matches the question and apply only guidance whose conditions hold.",
        "3. Check [disputes and gaps](chapters/99-disputes-and-gaps.md) before giving a recommendation.",
        "4. Use [traceability](traceability.json) for source IDs, exact locators, claim lineage, and run IDs.",
        "",
        "## Topic chapters",
        "",
    ]
    if chapter_plan:
        for cluster, path in chapter_plan:
            lines.append(f"- [{safe.markdown(cluster.topic)}]({path})")
    else:
        lines.append("- No topic clusters were synthesized; review the documented gaps.")
    lines.extend(
        [
            "",
            "## Quick references",
            "",
            "- [Cheatsheet](cheatsheet.md) for status-aware guidance at a glance.",
            "- [Patterns](patterns.md) for conditional decisions and claim relationships.",
            "- [Glossary](glossary.md) for definitions that may change conclusions.",
            "- [Source registry](source-registry.json) for portable source metadata and checksums.",
            "- [Build manifest](build-manifest.json) for compiler identity and output checksums.",
            "",
        ]
    )
    return "\n".join(lines)


def _security_limitation_lines(state: _RenderState) -> List[str]:
    lines: List[str] = []
    rejected = [
        claim
        for claim in state.inputs.source_claims.values()
        if claim.extraction.review_status == "rejected"
    ]
    rejected_rule_ids: Set[str] = set()
    for claim in rejected:
        rejected_rule_ids.update(claim.extraction.safety_finding_ids)
        rejected_rule_ids.update(instruction_pattern_ids(claim.original_assertion))
    all_rule_ids = sorted(rejected_rule_ids | state.safe_text.rule_ids)
    if rejected:
        lines.append(
            f"- {len(rejected)} rejected source claim record(s) were excluded from rendered prose."
        )
    if all_rule_ids:
        lines.append("- Safety rule IDs observed: " + ", ".join(all_rule_ids) + ".")
    if state.safe_text.absolute_paths_omitted:
        lines.append("- Absolute filesystem references were omitted for portability.")
    if state.safe_text.invisible_codepoints_removed:
        lines.append("- Invisible Unicode code points were removed from rendered metadata.")
    return lines


def _render_sources_and_method(state: _RenderState) -> str:
    safe = state.safe_text
    manifest = state.inputs.manifest
    lines = [
        "# Sources and method",
        "",
        "## Scope",
        "",
        f"This skill compiles corpus `{safe.markdown(manifest.id)}` ({safe.markdown(manifest.name)}) into status-aware, evidence-traceable guidance.",
        "It does not assign a universal truth score and does not use predictive scoring.",
        "",
        "## Sources",
        "",
        "| Source record ID | Title | Creators | Media type |",
        "| --- | --- | --- | --- |",
    ]
    for source in sorted(state.inputs.sources.values(), key=lambda item: item.id):
        creators = ", ".join(safe.markdown(item) for item in source.creators) or "Not recorded"
        lines.append(
            "| "
            + " | ".join(
                (
                    safe.markdown(source.id),
                    safe.markdown(source.title, "Redacted source title"),
                    creators,
                    safe.markdown(source.media_type),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Portable source metadata and content checksums are in [source-registry.json](../source-registry.json). Source excerpts are intentionally not copied into this skill.",
            "",
            "## Method",
            "",
            "1. Ingest source records with checksummed extracted text.",
            "2. Extract source-faithful atomic claims with locators.",
            "3. Normalize claims while preserving variants and conditions.",
            "4. Classify relationships without forcing uncertain pairs into agreement or contradiction.",
            "5. Synthesize consensus, conditional, contested, minority, and unresolved assertions.",
            "6. Verify every assertion through the provenance resolver before writing this skill.",
            "",
            "## Coverage and limitations",
            "",
        ]
    )
    coverage = [safe.markdown(item) for item in state.inputs.synthesis.coverage_notes]
    coverage.extend(_security_limitation_lines(state))
    if coverage:
        for item in coverage:
            lines.append(item if item.startswith("-") else "- " + item)
    else:
        lines.append("- No additional coverage notes were recorded.")
    lines.extend(
        [
            "- Source count is not treated as proof, and duplicated views are not silently promoted to consensus.",
            "- Unresolved and minority positions remain visible in the disputes chapter.",
            "",
            "## Updating the corpus",
            "",
            "Add the source to the versioned corpus manifest, ingest and extract only that source, then rerun normalization, affected relationship classification, synthesis, and compilation. Preserve unchanged stable record IDs so unaffected artifacts remain comparable.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_dispute(dispute: DisputeRecord, state: _RenderState) -> List[str]:
    safe = state.safe_text
    lines = [
        f"## {safe.markdown(dispute.topic)}",
        "",
        f"- Dispute ID: `{safe.markdown(dispute.id)}`",
        f"- Status: {safe.markdown(dispute.status.replace('_', ' '))}",
        f"- Conflict type: {safe.markdown(dispute.conflict_type.replace('_', ' '))}",
        "- Positions: " + ", ".join(safe.markdown(item) for item in dispute.position_claim_ids),
    ]
    if dispute.shared_assumptions:
        lines.append(
            "- Shared assumptions: "
            + "; ".join(safe.markdown(item) for item in dispute.shared_assumptions)
        )
    if dispute.differing_assumptions:
        lines.append(
            "- Differing assumptions: "
            + "; ".join(safe.markdown(item) for item in dispute.differing_assumptions)
        )
    if dispute.key_variables:
        lines.append(
            "- Key variables: "
            + "; ".join(safe.markdown(item) for item in dispute.key_variables)
        )
    if dispute.reconciliation:
        lines.append("- Conditional reconciliation: " + safe.markdown(dispute.reconciliation))
    locations = _source_locations_for_claim_ids(dispute.position_claim_ids, state)
    if locations:
        lines.append("- Source locations: " + "; ".join(locations))
    lines.append("")
    return lines


def _render_disputes_and_gaps(state: _RenderState) -> str:
    safe = state.safe_text
    lines = [
        "# Disputes and gaps",
        "",
        "Do not select a winner merely because one view appears in more sources. Apply reconciliations only under their recorded conditions.",
        "",
        "# Recorded disputes",
        "",
    ]
    if state.inputs.synthesis.disputes:
        for dispute in sorted(state.inputs.synthesis.disputes, key=lambda item: item.id):
            lines.extend(_render_dispute(dispute, state))
    else:
        lines.extend(["No explicit disputes were recorded.", ""])

    lines.extend(["# Non-consensus synthesis assertions", ""])
    non_consensus = sorted(
        (
            item
            for item in state.inputs.synthesis.assertions
            if item.status in {"contested", "minority_view", "unresolved"}
            and state.is_renderable(item)
        ),
        key=_assertion_sort_key,
    )
    if non_consensus:
        for assertion in non_consensus:
            lines.extend(_render_assertion(assertion, state))
            lines.append("")
            state.chapter_paths_by_assertion.setdefault(assertion.id, []).append(
                "chapters/99-disputes-and-gaps.md"
            )
    else:
        lines.extend(["No renderable contested, minority, or unresolved assertions were recorded.", ""])

    assigned = {
        assertion_id
        for assertion_id, paths in state.chapter_paths_by_assertion.items()
        if paths
    }
    unclustered = sorted(
        (
            item
            for item in state.inputs.synthesis.assertions
            if state.is_renderable(item) and item.id not in assigned
        ),
        key=_assertion_sort_key,
    )
    if unclustered:
        lines.extend(["# Unclustered synthesis assertions", ""])
        for assertion in unclustered:
            lines.extend(_render_assertion(assertion, state))
            lines.append("")
            state.chapter_paths_by_assertion.setdefault(assertion.id, []).append(
                "chapters/99-disputes-and-gaps.md"
            )

    lines.extend(["# Knowledge gaps", ""])
    if state.inputs.synthesis.unresolved_questions:
        for gap in sorted(state.inputs.synthesis.unresolved_questions, key=lambda item: item.id):
            related = (
                "; related claims "
                + ", ".join(safe.markdown(item) for item in gap.related_claim_ids)
                if gap.related_claim_ids
                else ""
            )
            lines.append(
                f"- **{safe.markdown(gap.id)}:** {safe.markdown(gap.text)}{related}"
            )
    else:
        lines.append("- No explicit synthesis gaps were recorded.")

    excluded = sorted(state.assertion_exclusions)
    if excluded:
        lines.extend(["", "# Excluded records", ""])
        lines.append(
            "These assertion records were not rendered as guidance. Their IDs and structural lineage remain in traceability.json."
        )
        lines.append("")
        for assertion_id in excluded:
            reasons = ", ".join(state.assertion_exclusions[assertion_id])
            lines.append(f"- `{safe.markdown(assertion_id)}` ({safe.markdown(reasons)})")

    lines.extend(["", "# Coverage limitations", ""])
    limitations = [safe.markdown(item) for item in state.inputs.synthesis.coverage_notes]
    limitations.extend(_security_limitation_lines(state))
    if limitations:
        for item in limitations:
            lines.append(item if item.startswith("-") else "- " + item)
    else:
        lines.append("- No additional coverage limitations were recorded.")
    return "\n".join(lines).rstrip() + "\n"


def _definition_entries(state: _RenderState) -> Mapping[str, List[Tuple[str, str]]]:
    safe = state.safe_text
    entries: Dict[str, List[Tuple[str, str]]] = {}
    for claim in sorted(state.inputs.canonical_claims.values(), key=lambda item: item.id):
        for term, definition in sorted(
            claim.semantics.definitions.items(), key=lambda item: str(item[0]).casefold()
        ):
            safe_term = safe.plain(term)
            safe_definition = safe.plain(definition)
            entries.setdefault(safe_term, []).append((safe_definition, claim.id))
    return entries


def _render_glossary(state: _RenderState) -> str:
    safe = state.safe_text
    lines = [
        "# Glossary",
        "",
        "Definitions can change whether two claims agree. Keep differing definitions separate.",
        "",
    ]
    entries = _definition_entries(state)
    if not entries:
        lines.extend(["No explicit governing definitions were recorded.", ""])
    else:
        for term in sorted(entries, key=str.casefold):
            lines.extend([f"## {safe.markdown(term)}", ""])
            for definition, claim_id in sorted(set(entries[term])):
                locations = _source_locations_for_claim_ids((claim_id,), state)
                location_text = "; sources " + "; ".join(locations) if locations else ""
                lines.append(
                    f"- {safe.markdown(definition)} (canonical claim `{safe.markdown(claim_id)}`{location_text})"
                )
            lines.append("")
    return "\n".join(lines)


def _render_patterns(state: _RenderState) -> str:
    safe = state.safe_text
    lines = [
        "# Decision patterns",
        "",
        "Use these as condition-aware patterns, not unconditional rules.",
        "",
        "## Conditional synthesis",
        "",
    ]
    conditional = sorted(
        (
            item
            for item in state.inputs.synthesis.assertions
            if state.is_renderable(item)
            and (item.status == "conditional" or _conditions_for_assertion(item, state.inputs, safe))
        ),
        key=_assertion_sort_key,
    )
    if conditional:
        for assertion in conditional:
            lines.extend(_render_assertion(assertion, state))
            lines.append("")
    else:
        lines.extend(["No explicit conditional decision patterns were synthesized.", ""])

    lines.extend(["## Relationship patterns", ""])
    interesting = {
        "refinement",
        "qualification",
        "contradiction",
        "conditional_disagreement",
        "tension",
        "alternative",
        "insufficient_information",
    }
    relations = [
        item
        for item in sorted(state.inputs.relations.values(), key=lambda relation: relation.id)
        if item.relation_type in interesting
    ]
    if relations:
        for relation in relations:
            lines.append(
                f"- **{safe.markdown(relation.relation_type.replace('_', ' '))}:** "
                f"`{safe.markdown(relation.left_claim_id)}` and `{safe.markdown(relation.right_claim_id)}`. "
                + safe.markdown(relation.rationale)
            )
            if relation.conflict_dimensions:
                lines.append(
                    "  - Conflict dimensions: "
                    + "; ".join(safe.markdown(item) for item in relation.conflict_dimensions)
                )
            locations = _source_locations_for_claim_ids(
                (relation.left_claim_id, relation.right_claim_id), state
            )
            if locations:
                lines.append("  - Source locations: " + "; ".join(locations))
    else:
        lines.append("No qualifying, conflicting, alternative, or uncertain relationship patterns were recorded.")
    lines.append("")
    return "\n".join(lines)


def _render_cheatsheet(state: _RenderState) -> str:
    safe = state.safe_text
    lines = [
        "# Cheatsheet",
        "",
        "Confirm conditions and review disputes before applying any item.",
        "",
    ]
    renderable = sorted(
        (item for item in state.inputs.synthesis.assertions if state.is_renderable(item)),
        key=_assertion_sort_key,
    )
    for status in _STATUS_ORDER:
        group = [item for item in renderable if item.status == status]
        if not group:
            continue
        lines.extend([f"## {_STATUS_LABEL[status]}", ""])
        for assertion in group:
            conditions = _conditions_for_assertion(assertion, state.inputs, safe)
            suffix = " — Conditions: " + "; ".join(conditions) if conditions else ""
            source_ids = sorted(
                {
                    state.inputs.source_claims[claim_id].source_id
                    for claim_id in assertion.supporting_source_claim_ids
                }
            )
            lines.append(
                f"- {safe.markdown(assertion.text)}{suffix} "
                f"(sources: {', '.join(safe.markdown(item) for item in source_ids)})"
            )
        lines.append("")
    if not renderable:
        lines.extend(["No synthesis assertions are eligible for prose rendering.", ""])
    lines.extend(
        [
            "See [disputes and gaps](chapters/99-disputes-and-gaps.md) for minority views, unresolved variables, and coverage limits.",
            "",
        ]
    )
    return "\n".join(lines)


def _source_registry(state: _RenderState) -> Mapping[str, Any]:
    safe = state.safe_text
    manifest_entries = []
    for entry in sorted(state.inputs.manifest.source_entries, key=lambda item: item.source_id):
        portable = _portable_ref(entry.input_ref)
        manifest_entries.append(
            {
                "source_id": entry.source_id,
                "input_ref": portable,
                "input_ref_omitted": portable is None,
                "media_type": entry.media_type,
            }
        )
    sources = []
    for source in sorted(state.inputs.sources.values(), key=lambda item: item.id):
        portable_source = _portable_ref(source.source_ref)
        portable_extracted = _portable_ref(source.extracted_text_ref)
        sources.append(
            {
                "source_record_id": source.id,
                "title": safe.plain(source.title, "Redacted source title"),
                "creators": [safe.plain(item, "Redacted creator") for item in source.creators],
                "edition": safe.plain(source.edition) if source.edition else None,
                "publication_date": safe.plain(source.publication_date) if source.publication_date else None,
                "media_type": safe.plain(source.media_type),
                "source_ref": portable_source,
                "source_ref_omitted": portable_source is None,
                "content_checksum": source.content_checksum,
                "extracted_text_ref": portable_extracted,
                "extracted_text_ref_omitted": portable_extracted is None,
                "extracted_text_checksum": source.extracted_text_checksum,
                "parser": {
                    "name": safe.plain(source.parser_name),
                    "version": safe.plain(source.parser_version),
                },
                "ingestion_run_id": source.ingestion_run_id,
                "rights_or_access_notes": (
                    safe.plain(source.rights_or_access_notes)
                    if source.rights_or_access_notes
                    else None
                ),
            }
        )
    return {
        "schema_version": "1.0",
        "corpus_id": state.inputs.manifest.id,
        "corpus_name": safe.plain(state.inputs.manifest.name),
        "domain_profile": state.inputs.manifest.domain_profile,
        "manifest_created_at": state.inputs.manifest.created_at,
        "manifest_sources": manifest_entries,
        "source_records": sources,
    }


def _trace_claim_role(
    source_claim_id: str, role: str, state: _RenderState, verified: bool
) -> Mapping[str, Any]:
    claim = state.inputs.source_claims[source_claim_id]
    return {
        "role": role,
        "source_claim_id": claim.id,
        "source_record_id": claim.source_id,
        "review_status": claim.extraction.review_status,
        "verified_by_provenance_resolver": verified,
        "locators": [
            _locator_plain(span, state.safe_text)
            for span in sorted(
                claim.source_spans,
                key=lambda item: canonical_dumps(_locator_plain(item, state.safe_text)),
            )
        ],
        "extraction_run_id": claim.extraction.run_id,
    }


def _traceability(state: _RenderState) -> Mapping[str, Any]:
    safe = state.safe_text
    assertions = []
    for assertion in sorted(state.inputs.synthesis.assertions, key=lambda item: item.id):
        trace = state.inputs.traces[assertion.id]
        verified_claim_ids = set(trace.source_claim_ids)
        claims = [
            _trace_claim_role(claim_id, "supporting", state, claim_id in verified_claim_ids)
            for claim_id in assertion.supporting_source_claim_ids
        ]
        claims.extend(
            _trace_claim_role(claim_id, "opposing", state, False)
            for claim_id in assertion.opposing_source_claim_ids
        )
        renderable = state.is_renderable(assertion)
        assertions.append(
            {
                "assertion_id": assertion.id,
                "status": assertion.status,
                "rendered": renderable,
                "rendered_text": safe.plain(assertion.text) if renderable else None,
                "exclusion_reasons": list(state.assertion_exclusions.get(assertion.id, ())),
                "condition_summary": (
                    safe.plain(assertion.condition_summary)
                    if renderable and assertion.condition_summary
                    else None
                ),
                "canonical_claim_ids": list(assertion.canonical_claim_ids),
                "source_claims": claims,
                "run_ids": list(trace.run_ids),
                "rendered_in": sorted(set(state.chapter_paths_by_assertion.get(assertion.id, ()))),
            }
        )

    canonical_claims = [
        {
            "canonical_claim_id": claim.id,
            "claim_type": claim.claim_type,
            "status": claim.status,
            "review_status": claim.review_status,
            "member_source_claim_ids": list(claim.member_source_claim_ids),
            "normalization_run_id": claim.normalization_run_id,
        }
        for claim in sorted(state.inputs.canonical_claims.values(), key=lambda item: item.id)
    ]
    relations = [
        {
            "relation_id": relation.id,
            "left_claim_id": relation.left_claim_id,
            "right_claim_id": relation.right_claim_id,
            "relation_type": relation.relation_type,
            "directionality": relation.directionality,
            "conflict_dimensions": [safe.plain(item) for item in relation.conflict_dimensions],
            "supporting_source_claim_ids": list(relation.supporting_source_claim_ids),
            "classification": {
                "run_id": relation.classification.run_id,
                "review_status": relation.classification.review_status,
            },
        }
        for relation in sorted(state.inputs.relations.values(), key=lambda item: item.id)
    ]
    return {
        "schema_version": "1.0",
        "corpus_id": state.inputs.manifest.id,
        "synthesis_artifact_id": state.inputs.synthesis.id,
        "synthesis_run_id": state.inputs.synthesis.run_id,
        "topic_clusters": [
            {
                "cluster_id": cluster.id,
                "topic": safe.plain(cluster.topic),
                "canonical_claim_ids": list(cluster.canonical_claim_ids),
                "assertion_ids": list(cluster.assertion_ids),
            }
            for cluster in sorted(state.inputs.synthesis.topic_clusters, key=_cluster_sort_key)
        ],
        "assertions": assertions,
        "canonical_claims": canonical_claims,
        "claim_relations": relations,
        "disputes": [
            {
                "dispute_id": dispute.id,
                "status": dispute.status,
                "conflict_type": dispute.conflict_type,
                "position_claim_ids": list(dispute.position_claim_ids),
            }
            for dispute in sorted(state.inputs.synthesis.disputes, key=lambda item: item.id)
        ],
        "gaps": [
            {"gap_id": gap.id, "related_claim_ids": list(gap.related_claim_ids)}
            for gap in sorted(
                state.inputs.synthesis.unresolved_questions, key=lambda item: item.id
            )
        ],
    }


def _preflight_text(state: _RenderState) -> None:
    """Collect safety findings before limitation sections are rendered."""

    safe = state.safe_text
    safe.plain(state.inputs.manifest.name)
    for source in state.inputs.sources.values():
        safe.plain(source.title)
        for creator in source.creators:
            safe.plain(creator)
        if source.rights_or_access_notes:
            safe.plain(source.rights_or_access_notes)
    for assertion in state.inputs.synthesis.assertions:
        if state.is_renderable(assertion):
            safe.plain(assertion.text)
            safe.plain(assertion.rationale)
            if assertion.condition_summary:
                safe.plain(assertion.condition_summary)
    for cluster in state.inputs.synthesis.topic_clusters:
        safe.plain(cluster.topic)
    for dispute in state.inputs.synthesis.disputes:
        safe.plain(dispute.topic)
        for value in (
            *dispute.shared_assumptions,
            *dispute.differing_assumptions,
            *dispute.key_variables,
        ):
            safe.plain(value)
        if dispute.reconciliation:
            safe.plain(dispute.reconciliation)
    for gap in state.inputs.synthesis.unresolved_questions:
        safe.plain(gap.text)
    for note in state.inputs.synthesis.coverage_notes:
        safe.plain(note)
    for canonical in state.inputs.canonical_claims.values():
        semantics = canonical.semantics
        for key, value in semantics.definitions.items():
            safe.plain(key)
            safe.plain(value)
        for value in semantics.assumptions:
            safe.plain(value)
        for condition in semantics.conditions:
            safe.plain(condition.field)
            safe.plain(condition.operator)
            safe.plain(condition.value)
        for value in (
            semantics.objective,
            semantics.population,
            semantics.geography,
            semantics.temporal_scope,
            semantics.time_horizon,
        ):
            if value:
                safe.plain(value)
    for relation in state.inputs.relations.values():
        safe.plain(relation.rationale)
        for value in relation.conflict_dimensions:
            safe.plain(value)


def _assertion_exclusions(inputs: _Inputs) -> Dict[str, Tuple[str, ...]]:
    exclusions: Dict[str, Tuple[str, ...]] = {}
    for assertion in inputs.synthesis.assertions:
        reasons: Set[str] = set()
        referenced_source_claims = (
            tuple(assertion.supporting_source_claim_ids)
            + tuple(assertion.opposing_source_claim_ids)
        )
        if any(
            inputs.source_claims[claim_id].extraction.review_status == "rejected"
            for claim_id in referenced_source_claims
        ):
            reasons.add("rejected-source-claim")
        for rule_id in instruction_pattern_ids(assertion.text):
            reasons.add("unsafe-synthesis-text:" + rule_id)
        if reasons:
            exclusions[assertion.id] = tuple(sorted(reasons))
    return exclusions


def _json_document(value: Any) -> str:
    return canonical_dumps(value) + "\n"


def _relative_to(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _prior_managed_outputs(
    output_store: ArtifactStore,
    corpus_id: str,
) -> Tuple[Dict[str, Set[str]], Tuple[str, ...]]:
    """Return checksum-bound files and verified manifest markers from prior builds."""

    skill_directory = output_store.root / "skill"
    if not skill_directory.is_dir():
        return {}, ()

    managed_checksums: Dict[str, Set[str]] = {}
    manifest_paths: List[str] = []
    for candidate in sorted(skill_directory.glob("*/build-manifest.json")):
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(output_store.root).as_posix()
        except ValueError as exc:
            raise CorpusCompilationError(
                "a prior build-manifest path escapes the output store"
            ) from exc
        try:
            prior = output_store.read_json(relative, SkillBuildManifest)
        except (OSError, ValueError) as exc:
            raise CorpusCompilationError(
                f"prior corpus build manifest cannot be verified: {relative}"
            ) from exc
        if prior.corpus_id != corpus_id:
            continue

        manifest_parent = PurePosixPath(relative).parent
        for path, checksum in prior.output_checksums.items():
            portable_path = PurePosixPath(path)
            if not _relative_to(portable_path, manifest_parent):
                raise CorpusCompilationError(
                    "prior build manifest declares an output outside its skill root: "
                    f"{path}"
                )
            # Resolve now so unsafe/escaping paths fail before any write or delete.
            output_store.path_for(path)
            managed_checksums.setdefault(path, set()).add(checksum.lower())
        manifest_paths.append(relative)
    return managed_checksums, tuple(sorted(set(manifest_paths)))


def _plan_stale_managed_files(
    output_store: ArtifactStore,
    corpus_id: str,
    current_paths: Set[str],
) -> Tuple[Tuple[str, str], ...]:
    """Preflight stale files, refusing to remove modified prior output."""

    checksums, manifest_paths = _prior_managed_outputs(output_store, corpus_id)
    stale_paths = (set(checksums) | set(manifest_paths)) - current_paths
    verified: List[Tuple[str, str]] = []
    for path in sorted(stale_paths):
        target = output_store.path_for(path)
        if not target.exists():
            continue
        if not target.is_file():
            raise CorpusCompilationError(
                f"stale managed output is not a regular file: {path}"
            )
        actual = sha256_bytes(target.read_bytes())
        expected = checksums.get(path)
        if expected is not None:
            if actual not in expected:
                raise CorpusCompilationError(
                    "refusing to remove a modified prior corpus output: " + path
                )
        verified.append((path, actual))
    return tuple(verified)


def _remove_stale_managed_files(
    output_store: ArtifactStore,
    stale_paths: Sequence[Tuple[str, str]],
) -> None:
    """Delete only preflighted files, then prune only directories left empty."""

    parents = set()
    for path, preflight_checksum in stale_paths:
        target = output_store.path_for(path)
        if target.exists() and not target.is_file():
            raise CorpusCompilationError(
                "stale managed output changed type during rebuild: " + path
            )
        if (
            target.is_file()
            and sha256_bytes(target.read_bytes()) != preflight_checksum
        ):
            raise CorpusCompilationError(
                "refusing to remove a prior corpus output modified during rebuild: "
                + path
            )
        try:
            target.unlink()
        except FileNotFoundError:
            continue
        parents.add(target.parent)

    skill_directory = (output_store.root / "skill").resolve()
    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        current = parent
        while current != skill_directory and current != output_store.root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def compile_corpus_skill(
    manifest: CorpusManifest,
    source_records: Iterable[SourceRecord],
    source_claims: Iterable[SourceClaim],
    canonical_claims: Iterable[CanonicalClaim],
    claim_relations: Iterable[ClaimRelation],
    synthesis: SynthesisArtifact,
    provenance: ProvenanceResolver,
    output_store: ArtifactStore,
    configuration_hash: str,
    clock: Callable[[], Any] = _utc_now,
    compiler_version: str = COMPILER_VERSION,
) -> SkillBuildManifest:
    """Compile records into ``skill/<slug>/`` and return the build manifest.

    All record collections are materialized and sorted internally.  Reordering
    equivalent inputs therefore does not affect generated content, checksums,
    or the stable manifest ID.  ``build-manifest.json`` is intentionally absent
    from ``output_checksums`` to avoid self-checksum recursion.
    """

    if not isinstance(configuration_hash, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", configuration_hash
    ):
        raise CorpusCompilationError(
            "configuration_hash must be a 64-character SHA-256 hex digest"
        )
    if not isinstance(compiler_version, str) or not compiler_version.strip():
        raise CorpusCompilationError("compiler_version must be a non-empty string")
    if not isinstance(output_store, ArtifactStore):
        raise CorpusCompilationError("output_store must be an ArtifactStore")

    inputs = _validate_inputs(
        manifest,
        tuple(source_records),
        tuple(source_claims),
        tuple(canonical_claims),
        tuple(claim_relations),
        synthesis,
        provenance,
    )
    skill_slug = _slugify(manifest.name, manifest.id)
    safe = _SafeText()
    state = _RenderState(
        inputs=inputs,
        safe_text=safe,
        skill_slug=skill_slug,
        assertion_exclusions=_assertion_exclusions(inputs),
        chapter_paths_by_assertion={},
    )
    _preflight_text(state)

    skill_root = f"skill/{skill_slug}"
    chapter_plan = _chapter_plan(state)
    documents: Dict[str, str] = {}
    for cluster, relative_path in chapter_plan:
        documents[f"{skill_root}/{relative_path}"] = _render_topic_chapter(
            cluster, relative_path, state
        )

    # Render disputes before traceability so its rendered_in map is complete.
    disputes = _render_disputes_and_gaps(state)
    documents.update(
        {
            f"{skill_root}/SKILL.md": _render_skill_md(state, chapter_plan),
            f"{skill_root}/chapters/00-sources-and-method.md": _render_sources_and_method(state),
            f"{skill_root}/chapters/99-disputes-and-gaps.md": disputes,
            f"{skill_root}/glossary.md": _render_glossary(state),
            f"{skill_root}/patterns.md": _render_patterns(state),
            f"{skill_root}/cheatsheet.md": _render_cheatsheet(state),
            f"{skill_root}/traceability.json": _json_document(_traceability(state)),
            f"{skill_root}/source-registry.json": _json_document(_source_registry(state)),
        }
    )

    output_checksums = {
        path: sha256_text(text) for path, text in sorted(documents.items())
    }
    rendered_assertions = [
        assertion
        for assertion in synthesis.assertions
        if state.is_renderable(assertion)
    ]
    included_claim_ids = tuple(
        sorted(
            {
                claim_id
                for assertion in rendered_assertions
                for claim_id in assertion.canonical_claim_ids
            }
        )
    )
    unresolved_dispute_ids = tuple(
        sorted(
            dispute.id
            for dispute in synthesis.disputes
            if dispute.status != "reconciled_conditionally"
        )
    )
    configuration_hash = configuration_hash.lower()
    build_id = stable_id(
        "skill-build",
        {
            "corpus_id": manifest.id,
            "source_record_ids": sorted(inputs.sources),
            "synthesis_artifact_id": synthesis.id,
            "included_claim_ids": included_claim_ids,
            "unresolved_dispute_ids": unresolved_dispute_ids,
            "compiler_version": compiler_version,
            "configuration_hash": configuration_hash,
            "output_checksums": output_checksums,
        },
    )
    build_manifest = SkillBuildManifest(
        id=build_id,
        corpus_id=manifest.id,
        source_record_ids=tuple(sorted(inputs.sources)),
        synthesis_artifact_id=synthesis.id,
        included_claim_ids=included_claim_ids,
        unresolved_dispute_ids=unresolved_dispute_ids,
        domain_profile_id_and_version=manifest.domain_profile,
        compiler_version=compiler_version,
        configuration_hash=configuration_hash,
        generated_at=_timestamp(clock()),
        output_checksums=output_checksums,
    )
    manifest_path = f"{skill_root}/build-manifest.json"
    current_managed_paths = set(documents) | {manifest_path}
    stale_managed_paths = _plan_stale_managed_files(
        output_store,
        manifest.id,
        current_managed_paths,
    )

    # Per-file writes are atomic in ArtifactStore.  The manifest is the commit
    # marker and is written last; it never claims a checksum for itself.  Only
    # checksum-verified files declared by prior manifests for this corpus are
    # removed, so user-owned or modified files are never silently discarded.
    for path in sorted(documents):
        output_store.write_text(path, documents[path], output_checksums[path])
    _remove_stale_managed_files(output_store, stale_managed_paths)
    output_store.write_text(manifest_path, _json_document(to_plain(build_manifest)))
    return build_manifest


# Short aliases make the logical CorpusWorkflow.render_skill port convenient
# without changing the explicit compiler API used by orchestration.
compile_skill = compile_corpus_skill
render_skill = compile_corpus_skill


__all__ = [
    "COMPILER_VERSION",
    "CorpusCompilationError",
    "compile_corpus_skill",
    "compile_skill",
    "render_skill",
]
