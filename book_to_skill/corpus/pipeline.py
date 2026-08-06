"""Replayable stage orchestration for the additive corpus-to-skill workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from book_to_skill.corpus.budget import (
    DEFAULT_CORPUS_RESOURCE_BUDGET,
    CorpusResourceBudget,
    CorpusResourceUsage,
)
from book_to_skill.corpus.cache import CachePruneResult, prune_obsolete_cache
from book_to_skill.corpus.compiler import compile_corpus_skill
from book_to_skill.corpus.extraction import EXTRACTOR_VERSION, extract_claims
from book_to_skill.corpus.ingestion import (
    ADAPTER_VERSION,
    CorpusIngestionError,
    ingest_source,
)
from book_to_skill.corpus.manifest import load_manifest
from claim_framework.jsonio import (
    canonical_bytes,
    sha256_bytes,
    sha256_file,
    sha256_text,
    stable_id,
)
from claim_framework.normalize import normalize_claims
from claim_framework.provenance import ProvenanceResolver
from claim_framework.records import (
    CanonicalClaim,
    ClaimGraphSnapshot,
    ClaimRelation,
    CorpusManifest,
    RunRecord,
    SkillBuildManifest,
    SourceClaim,
    SourceRecord,
    SynthesisArtifact,
)
from claim_framework.relationships import classify_relations
from claim_framework.store import ArtifactStore, StoreError
from claim_framework.synthesis import synthesize


PIPELINE_VERSION = "corpus-pipeline-v1"


class CorpusPipelineError(RuntimeError):
    """Raised when a corpus build cannot reach a valid phase gate."""


@dataclass(frozen=True)
class PipelineResult:
    manifest: CorpusManifest
    source_records: Tuple[SourceRecord, ...]
    source_claims: Tuple[SourceClaim, ...]
    canonical_claims: Tuple[CanonicalClaim, ...]
    relations: Tuple[ClaimRelation, ...]
    graph: ClaimGraphSnapshot
    synthesis: SynthesisArtifact
    build_manifest: SkillBuildManifest
    runs: Tuple[RunRecord, ...]
    reused_source_ids: Tuple[str, ...]
    limitations: Tuple[str, ...]
    resource_usage: CorpusResourceUsage
    pruned_cache_files: Tuple[str, ...]
    preserved_cache_paths: Tuple[str, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_id(stage: str, configuration_hash: str, input_ids: Tuple[str, ...]) -> str:
    return stable_id(
        "run",
        {
            "stage": stage,
            "implementation_version": PIPELINE_VERSION,
            "configuration_hash": configuration_hash,
            "input_artifact_ids": sorted(input_ids),
        },
    )


def _run_record(
    stage: str,
    run_id: str,
    configuration_hash: str,
    input_ids: Tuple[str, ...],
    timestamp: str,
    status: str = "completed",
    limitations: Tuple[str, ...] = (),
    implementation_version: str = PIPELINE_VERSION,
) -> RunRecord:
    return RunRecord(
        id=run_id,
        stage=stage,
        implementation_version=implementation_version,
        schema_versions={"claim_framework": "1.0"},
        configuration_hash=configuration_hash,
        input_artifact_ids=tuple(sorted(input_ids)),
        started_at=timestamp,
        completed_at=timestamp,
        status=status,
        limitations=limitations,
    )


def _configuration_hash(manifest: CorpusManifest) -> str:
    return sha256_bytes(
        canonical_bytes(
            {
                "manifest": manifest,
                "pipeline_version": PIPELINE_VERSION,
                "ingestion_adapter": ADAPTER_VERSION,
                "claim_extractor": EXTRACTOR_VERSION,
            }
        )
    )


def _source_fingerprints(manifest: CorpusManifest, manifest_dir: Path) -> Tuple[str, ...]:
    fingerprints = []
    root = manifest_dir.resolve()
    for entry in manifest.source_entries:
        candidate = (root / entry.input_ref).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            fingerprints.append(f"{entry.source_id}:invalid-path")
            continue
        if candidate.is_file():
            fingerprints.append(f"{entry.source_id}:sha256:{sha256_file(candidate)}")
        else:
            fingerprints.append(f"{entry.source_id}:missing")
    return tuple(sorted(fingerprints))


def _check_source_size_budget(
    manifest: CorpusManifest,
    manifest_dir: Path,
    budget: CorpusResourceBudget,
) -> None:
    """Reject oversized local inputs before hashing or reading them into memory."""

    root = manifest_dir.resolve()
    total_bytes = 0
    for entry in manifest.source_entries:
        candidate = (root / entry.input_ref).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        source_bytes = candidate.stat().st_size
        total_bytes += source_bytes
        budget.check_source_bytes(source_bytes, total_bytes)


def _cache_root(source_record_id: str, extraction_configuration_hash: str) -> str:
    return (
        f"artifacts/cache/{sha256_text(source_record_id)[:16]}-"
        f"{extraction_configuration_hash[:16]}"
    )


def _load_declared_cache_checksums(store: ArtifactStore) -> dict:
    if not store.exists("artifacts/cache-state.json"):
        return {}
    try:
        payload = json.loads(store.read_text("artifacts/cache-state.json"))
    except (StoreError, ValueError):
        return {}
    values = payload.get("managed_cache_checksums")
    if not isinstance(values, dict):
        return {}
    verified = {}
    for path, checksum in values.items():
        if (
            isinstance(path, str)
            and isinstance(checksum, str)
            and len(checksum) == 64
            and all(char in "0123456789abcdef" for char in checksum.casefold())
        ):
            verified[path] = checksum.casefold()
    return verified


def _managed_cache_checksums(
    store: ArtifactStore,
    cache_roots: Tuple[str, ...],
    extracted_refs: Tuple[str, ...],
) -> dict:
    checksums = {}
    managed_refs = list(extracted_refs)
    for cache_root in cache_roots:
        managed_refs.extend(
            (
                f"{cache_root}/source-record.json",
                f"{cache_root}/source-claims.jsonl",
            )
        )
    for relative_ref in sorted(set(managed_refs)):
        path = store.path_for(relative_ref)
        if path.is_file() and not path.is_symlink():
            checksums[relative_ref] = sha256_file(path)
    return checksums


def _write_recovery_checkpoint(
    store: ArtifactStore,
    *,
    manifest: CorpusManifest,
    configuration_hash: str,
    timestamp: str,
    last_completed_stage: str,
    completed_source_record_ids: Tuple[str, ...] = (),
    status: str = "in_progress",
) -> None:
    store.write_json(
        "artifacts/recovery-checkpoint.json",
        {
            "schema_version": "1.0",
            "checkpoint_version": "corpus-recovery-v1",
            "corpus_id": manifest.id,
            "pipeline_version": PIPELINE_VERSION,
            "configuration_hash": configuration_hash,
            "status": status,
            "last_completed_stage": last_completed_stage,
            "completed_source_record_ids": sorted(completed_source_record_ids),
            "updated_at": timestamp,
        },
    )


def _validate_output_scope(store: ArtifactStore, manifest: CorpusManifest) -> None:
    existing = [path for path in store.root.iterdir()]
    marker = "artifacts/corpus-manifest.json"
    if not existing:
        return
    if not store.exists(marker):
        raise CorpusPipelineError(
            "output directory is nonempty and is not an existing corpus build; choose a separate output directory"
        )
    try:
        prior = store.read_json(marker, CorpusManifest)
    except (StoreError, ValueError) as exc:
        raise CorpusPipelineError(f"existing corpus marker cannot be verified: {exc}") from exc
    if prior.id != manifest.id:
        raise CorpusPipelineError(
            f"output directory belongs to corpus {prior.id!r}, not {manifest.id!r}"
        )


def build_corpus(
    manifest_path: Union[str, Path],
    output_dir: Union[str, Path],
    *,
    force: bool = False,
    prune_cache: bool = False,
    budget: CorpusResourceBudget = DEFAULT_CORPUS_RESOURCE_BUDGET,
    clock: Callable[[], str] = _utc_now,
) -> PipelineResult:
    """Build a provenance-complete corpus skill without touching legacy output."""

    manifest, resolved_manifest_path = load_manifest(manifest_path)
    if not isinstance(budget, CorpusResourceBudget):
        raise TypeError("budget must be a CorpusResourceBudget")
    budget.check_source_count(len(manifest.source_entries))
    _check_source_size_budget(manifest, resolved_manifest_path.parent, budget)
    store = ArtifactStore(output_dir)
    _validate_output_scope(store, manifest)
    # Establish ownership immediately.  If a later stage is interrupted, a
    # subsequent run may safely resume this corpus instead of treating its
    # content-addressed intermediates as an unrelated nonempty directory.
    store.write_json("artifacts/corpus-manifest.json", manifest)
    configuration_hash = _configuration_hash(manifest)
    extraction_configuration_hash = sha256_bytes(
        canonical_bytes(
            {
                "configuration_ref": manifest.configuration_ref,
                "extractor_version": EXTRACTOR_VERSION,
            }
        )
    )
    timestamp = clock()
    _write_recovery_checkpoint(
        store,
        manifest=manifest,
        configuration_hash=configuration_hash,
        timestamp=timestamp,
        last_completed_stage="manifest",
    )
    source_fingerprints = _source_fingerprints(manifest, resolved_manifest_path.parent)
    ingest_run_id = _run_id("ingest", configuration_hash, source_fingerprints)

    source_records: List[SourceRecord] = []
    source_claims: List[SourceClaim] = []
    reused_source_ids: List[str] = []
    limitations: List[str] = []
    extraction_runs = {}
    source_bytes = 0
    current_cache_roots: List[str] = []

    for entry in manifest.source_entries:
        try:
            ingested = ingest_source(
                manifest,
                entry,
                resolved_manifest_path.parent,
                ingest_run_id,
            )
        except (CorpusIngestionError, OSError, UnicodeError) as exc:
            if isinstance(exc, CorpusIngestionError):
                reason = str(exc).replace(str(resolved_manifest_path.parent), "[manifest directory]")
            elif isinstance(exc, UnicodeError):
                reason = "source text could not be decoded safely"
            else:
                reason = "operating-system read error"
            limitations.append(
                f"source {entry.source_id!r} was skipped: "
                f"{type(exc).__name__}: {reason}"
            )
            continue

        expected_fingerprint = (
            f"{entry.source_id}:sha256:{ingested.record.content_checksum}"
        )
        if expected_fingerprint not in source_fingerprints:
            raise CorpusPipelineError(
                f"source {entry.source_id!r} changed after the run fingerprint was established; rerun the build"
            )

        source_records.append(ingested.record)
        candidate_source_bytes = source_bytes + ingested.raw_byte_count
        budget.check_source_bytes(
            ingested.raw_byte_count,
            candidate_source_bytes,
        )
        source_bytes = candidate_source_bytes
        store.write_text(ingested.record.extracted_text_ref, ingested.text)
        if ingested.removed_invisible_codepoints:
            limitations.append(
                f"source {entry.source_id!r}: removed "
                f"{ingested.removed_invisible_codepoints} invisible Unicode code point(s)"
            )

        extract_run_id = _run_id(
            "extract", extraction_configuration_hash, (ingested.record.id,)
        )
        extraction_runs[extract_run_id] = _run_record(
            "extract",
            extract_run_id,
            extraction_configuration_hash,
            (ingested.record.id,),
            timestamp,
            limitations=(f"extractor={EXTRACTOR_VERSION}",),
            implementation_version=EXTRACTOR_VERSION,
        )
        # Keep content-addressed cache components short enough for Windows'
        # legacy MAX_PATH while verifying the full IDs inside cached records.
        cache_root = _cache_root(
            ingested.record.id,
            extraction_configuration_hash,
        )
        current_cache_roots.append(cache_root)
        cache_claims = f"{cache_root}/source-claims.jsonl"
        cached: Optional[Tuple[SourceClaim, ...]] = None
        if not force and store.exists(cache_claims):
            try:
                candidate_claims = store.read_jsonl(cache_claims, SourceClaim)
                if all(claim.source_id == ingested.record.id for claim in candidate_claims):
                    cached = candidate_claims
            except (StoreError, ValueError):
                cached = None

        if cached is None:
            claims = extract_claims(ingested, extract_run_id)
            store.write_json(f"{cache_root}/source-record.json", ingested.record)
            store.write_jsonl(cache_claims, claims)
        else:
            claims = cached
            reused_source_ids.append(ingested.record.id)
        budget.check_claim_count(len(source_claims) + len(claims))
        source_claims.extend(claims)
        _write_recovery_checkpoint(
            store,
            manifest=manifest,
            configuration_hash=configuration_hash,
            timestamp=timestamp,
            last_completed_stage="extract",
            completed_source_record_ids=tuple(
                record.id for record in source_records
            ),
        )

    if len(source_records) < 2:
        raise CorpusPipelineError(
            "fewer than two sources were ingested successfully; a corpus build was not produced"
        )

    source_records_tuple = tuple(sorted(source_records, key=lambda item: item.id))
    source_claims_tuple = tuple(sorted(source_claims, key=lambda item: item.id))
    rejected_count = sum(
        claim.extraction.review_status == "rejected" for claim in source_claims_tuple
    )
    active_claims = tuple(
        claim
        for claim in source_claims_tuple
        if claim.extraction.review_status != "rejected"
    )
    if rejected_count:
        limitations.append(
            f"{rejected_count} source claim(s) matched untrusted-instruction safety rules and were excluded from synthesis"
        )
    if not active_claims:
        raise CorpusPipelineError("no synthesis-eligible source claims were extracted")
    active_source_ids = {claim.source_id for claim in active_claims}
    if len(active_source_ids) < 2:
        raise CorpusPipelineError(
            "fewer than two sources produced synthesis-eligible claims; "
            "a cross-source corpus build was not produced"
        )

    normalize_run_id = _run_id(
        "normalize", configuration_hash, tuple(claim.id for claim in active_claims)
    )
    canonical_claims = normalize_claims(active_claims, normalize_run_id)
    _write_recovery_checkpoint(
        store,
        manifest=manifest,
        configuration_hash=configuration_hash,
        timestamp=timestamp,
        last_completed_stage="normalize",
        completed_source_record_ids=tuple(record.id for record in source_records_tuple),
    )
    relate_run_id = _run_id(
        "relate", configuration_hash, tuple(claim.id for claim in canonical_claims)
    )
    relations = classify_relations(canonical_claims, relate_run_id)
    _write_recovery_checkpoint(
        store,
        manifest=manifest,
        configuration_hash=configuration_hash,
        timestamp=timestamp,
        last_completed_stage="relate",
        completed_source_record_ids=tuple(record.id for record in source_records_tuple),
    )
    graph = ClaimGraphSnapshot(
        id=stable_id(
            "claim-graph",
            {
                "corpus_id": manifest.id,
                "claims": sorted(claim.id for claim in canonical_claims),
                "relations": sorted(relation.id for relation in relations),
            },
        ),
        corpus_id=manifest.id,
        canonical_claim_ids=tuple(claim.id for claim in canonical_claims),
        relation_ids=tuple(relation.id for relation in relations),
        run_id=relate_run_id,
    )
    synthesize_run_id = _run_id(
        "synthesize",
        configuration_hash,
        tuple(claim.id for claim in canonical_claims)
        + tuple(relation.id for relation in relations),
    )
    synthesis = synthesize(
        manifest.id,
        canonical_claims,
        active_claims,
        relations,
        synthesize_run_id,
    )
    _write_recovery_checkpoint(
        store,
        manifest=manifest,
        configuration_hash=configuration_hash,
        timestamp=timestamp,
        last_completed_stage="synthesize",
        completed_source_record_ids=tuple(record.id for record in source_records_tuple),
    )

    ingest_status = "partial" if limitations else "completed"
    runs = (
        _run_record(
            "ingest",
            ingest_run_id,
            configuration_hash,
            source_fingerprints,
            timestamp,
            status=ingest_status,
            limitations=tuple(limitations),
            implementation_version=ADAPTER_VERSION,
        ),
        *tuple(extraction_runs[run_id] for run_id in sorted(extraction_runs)),
        _run_record("normalize", normalize_run_id, configuration_hash, tuple(claim.id for claim in active_claims), timestamp),
        _run_record("relate", relate_run_id, configuration_hash, tuple(claim.id for claim in canonical_claims), timestamp),
        _run_record("synthesize", synthesize_run_id, configuration_hash, tuple(claim.id for claim in canonical_claims) + tuple(relation.id for relation in relations), timestamp),
    )

    store.write_jsonl("artifacts/source-records.jsonl", source_records_tuple)
    store.write_jsonl("artifacts/source-claims.jsonl", source_claims_tuple)
    store.write_jsonl("artifacts/canonical-claims.jsonl", canonical_claims)
    store.write_jsonl("artifacts/relations.jsonl", relations)
    store.write_json("artifacts/claim-graph.json", graph)
    store.write_json("artifacts/synthesis.json", synthesis)
    store.write_jsonl("artifacts/runs.jsonl", runs)
    provenance = ProvenanceResolver(
        source_records_tuple,
        source_claims_tuple,
        canonical_claims,
        store,
    )
    provenance.validate_synthesis(synthesis)
    build_manifest = compile_corpus_skill(
        manifest=manifest,
        source_records=source_records_tuple,
        source_claims=source_claims_tuple,
        canonical_claims=canonical_claims,
        claim_relations=relations,
        synthesis=synthesis,
        provenance=provenance,
        output_store=store,
        configuration_hash=configuration_hash,
        clock=clock,
    )

    prune_result = CachePruneResult()
    prior_cache_checksums = _load_declared_cache_checksums(store)
    if prune_cache:
        prune_result = prune_obsolete_cache(
            store,
            current_cache_roots=current_cache_roots,
            current_extracted_refs=(
                record.extracted_text_ref for record in source_records_tuple
            ),
            declared_checksums=prior_cache_checksums,
        )

    resource_usage = CorpusResourceUsage(
        source_count=len(source_records_tuple),
        source_bytes=source_bytes,
        source_claim_count=len(source_claims_tuple),
    )
    managed_cache_checksums = _managed_cache_checksums(
        store,
        tuple(sorted(current_cache_roots)),
        tuple(sorted(record.extracted_text_ref for record in source_records_tuple)),
    )
    store.write_json(
        "artifacts/cache-state.json",
        {
            "schema_version": "1.0",
            "configuration_hash": configuration_hash,
            "extraction_configuration_hash": extraction_configuration_hash,
            "source_fingerprints": list(source_fingerprints),
            "reused_source_ids": sorted(reused_source_ids),
            "resource_budget": dict(budget.as_dict()),
            "resource_usage": dict(resource_usage.as_dict()),
            "prune_cache_requested": prune_cache,
            "pruned_cache_files": list(prune_result.removed_files),
            "preserved_cache_paths": list(prune_result.preserved_paths),
            "managed_cache_checksums": managed_cache_checksums,
        },
    )
    _write_recovery_checkpoint(
        store,
        manifest=manifest,
        configuration_hash=configuration_hash,
        timestamp=timestamp,
        last_completed_stage="compile",
        completed_source_record_ids=tuple(record.id for record in source_records_tuple),
        status="completed",
    )

    return PipelineResult(
        manifest=manifest,
        source_records=source_records_tuple,
        source_claims=source_claims_tuple,
        canonical_claims=canonical_claims,
        relations=relations,
        graph=graph,
        synthesis=synthesis,
        build_manifest=build_manifest,
        runs=runs,
        reused_source_ids=tuple(sorted(reused_source_ids)),
        limitations=tuple(limitations),
        resource_usage=resource_usage,
        pruned_cache_files=prune_result.removed_files,
        preserved_cache_paths=prune_result.preserved_paths,
    )
