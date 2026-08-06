"""Offline end-to-end tests for the additive corpus-to-skill command path."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import corpus_to_skill.pipeline as corpus_pipeline
from corpus_to_skill.budget import (
    CorpusBudgetExceeded,
    CorpusResourceBudget,
)
from corpus_to_skill.pipeline import CorpusPipelineError, build_corpus


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "corpus_demo"
FIXED_TIME = "2026-01-02T00:00:00Z"


def _clock():
    return FIXED_TIME


def _copy_fixture(target: Path) -> Path:
    shutil.copytree(FIXTURE_DIR, target)
    return target / "manifest.json"


def _synthesis_semantics(result):
    return sorted(
        (
            assertion.text,
            assertion.status,
            tuple(sorted(assertion.canonical_claim_ids)),
            tuple(sorted(assertion.supporting_source_claim_ids)),
            tuple(sorted(assertion.opposing_source_claim_ids)),
            assertion.condition_summary,
        )
        for assertion in result.synthesis.assertions
    )


def test_three_source_pipeline_emits_every_required_artifact(tmp_path):
    output = tmp_path / "output"
    result = build_corpus(FIXTURE_DIR / "manifest.json", output, clock=_clock)

    assert len(result.source_records) == 3
    assert len(result.source_claims) == 9
    assert len(result.canonical_claims) == 7
    assert len({claim.id for claim in result.source_claims}) == 9
    assert len({claim.id for claim in result.canonical_claims}) == 7
    assert any(
        relation.relation_type == "conditional_disagreement"
        for relation in result.relations
    )
    assert result.synthesis.assertions
    assert result.synthesis.disputes

    required = {
        "artifacts/corpus-manifest.json",
        "artifacts/source-records.jsonl",
        "artifacts/source-claims.jsonl",
        "artifacts/canonical-claims.jsonl",
        "artifacts/relations.jsonl",
        "artifacts/claim-graph.json",
        "artifacts/synthesis.json",
        "artifacts/runs.jsonl",
        "artifacts/recovery-checkpoint.json",
        "skill/resilient-queue-operations/SKILL.md",
        "skill/resilient-queue-operations/chapters/00-sources-and-method.md",
        "skill/resilient-queue-operations/chapters/99-disputes-and-gaps.md",
        "skill/resilient-queue-operations/glossary.md",
        "skill/resilient-queue-operations/patterns.md",
        "skill/resilient-queue-operations/cheatsheet.md",
        "skill/resilient-queue-operations/source-registry.json",
        "skill/resilient-queue-operations/traceability.json",
        "skill/resilient-queue-operations/build-manifest.json",
    }
    present = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert required <= present

    skill_root = output / "skill" / "resilient-queue-operations"
    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in skill_root.rglob("*")
        if path.is_file()
    )
    assert str(FIXTURE_DIR.resolve()) not in rendered
    assert "conditional" in rendered.casefold()
    traceability = json.loads(
        (skill_root / "traceability.json").read_text(encoding="utf-8")
    )
    assert traceability["assertions"]
    assert all(
        any(
            source_claim["role"] == "supporting"
            and source_claim["verified_by_provenance_resolver"] is True
            and source_claim["locators"]
            for source_claim in item["source_claims"]
        )
        for item in traceability["assertions"]
    )


def test_unchanged_rerun_reuses_all_source_claim_caches(tmp_path):
    output = tmp_path / "output"
    first = build_corpus(FIXTURE_DIR / "manifest.json", output, clock=_clock)
    second = build_corpus(FIXTURE_DIR / "manifest.json", output, clock=_clock)

    assert len(second.reused_source_ids) == 3
    assert {record.id for record in first.source_records} == {
        record.id for record in second.source_records
    }
    assert [claim.id for claim in first.source_claims] == [
        claim.id for claim in second.source_claims
    ]
    assert first.graph.id == second.graph.id
    assert first.build_manifest == second.build_manifest
    assert second.resource_usage.model_calls == 0
    cache_state = json.loads(
        (output / "artifacts" / "cache-state.json").read_text(encoding="utf-8")
    )
    assert cache_state["resource_usage"] == dict(second.resource_usage.as_dict())
    assert cache_state["resource_budget"]["max_model_calls"] == 0


def test_changed_source_invalidates_only_its_claim_cache(tmp_path):
    fixture = tmp_path / "fixture"
    manifest = _copy_fixture(fixture)
    output = tmp_path / "output"
    first = build_corpus(manifest, output, clock=_clock)

    changed = fixture / "bursty-capacity.md"
    changed.write_text(
        changed.read_text(encoding="utf-8")
        + "\n- Operators should measure rejection rates during load tests.\n",
        encoding="utf-8",
    )
    second = build_corpus(manifest, output, clock=_clock)

    first_ids = {record.id for record in first.source_records}
    second_ids = {record.id for record in second.source_records}
    assert len(first_ids & second_ids) == 2
    assert set(second.reused_source_ids) == first_ids & second_ids
    assert len(second.source_claims) == 10


def test_opt_in_pruning_removes_verified_stale_cache_and_preserves_unknown_files(
    tmp_path,
):
    fixture = tmp_path / "fixture"
    manifest = _copy_fixture(fixture)
    output = tmp_path / "output"
    build_corpus(manifest, output, clock=_clock)
    old_cache_dirs = {path.name for path in (output / "artifacts" / "cache").iterdir()}

    changed = fixture / "bursty-capacity.md"
    changed.write_text(
        changed.read_text(encoding="utf-8")
        + "\n- Operators should measure rejection rates during load tests.\n",
        encoding="utf-8",
    )
    unknown = output / "artifacts" / "cache" / "user-owned" / "keep.txt"
    unknown.parent.mkdir()
    unknown.write_text("preserve me", encoding="utf-8")

    result = build_corpus(manifest, output, prune_cache=True, clock=_clock)

    new_cache_dirs = {
        path.name
        for path in (output / "artifacts" / "cache").iterdir()
        if path.name != "user-owned"
    }
    assert len(old_cache_dirs - new_cache_dirs) == 1
    assert len(new_cache_dirs) == 3
    assert len(result.pruned_cache_files) == 3
    assert unknown.read_text(encoding="utf-8") == "preserve me"
    assert "artifacts/cache/user-owned" in result.preserved_cache_paths


def test_pruning_preserves_modified_stale_extracted_text(tmp_path):
    fixture = tmp_path / "fixture"
    manifest = _copy_fixture(fixture)
    output = tmp_path / "output"
    first = build_corpus(manifest, output, clock=_clock)
    old_record = next(
        record for record in first.source_records if record.source_ref == "bursty-capacity.md"
    )
    stale_extracted = output / old_record.extracted_text_ref
    stale_extracted.write_text("locally modified", encoding="utf-8")
    changed = fixture / "bursty-capacity.md"
    changed.write_text(
        changed.read_text(encoding="utf-8")
        + "\n- Operators should measure rejection rates during load tests.\n",
        encoding="utf-8",
    )

    result = build_corpus(manifest, output, prune_cache=True, clock=_clock)

    assert stale_extracted.read_text(encoding="utf-8") == "locally modified"
    assert old_record.extracted_text_ref in result.preserved_cache_paths


def test_pruning_preserves_stale_cache_when_declared_checksum_no_longer_matches(
    tmp_path,
):
    fixture = tmp_path / "fixture"
    manifest = _copy_fixture(fixture)
    output = tmp_path / "output"
    first = build_corpus(manifest, output, clock=_clock)
    old_record = next(
        record for record in first.source_records if record.source_ref == "bursty-capacity.md"
    )
    stale_cache = next(
        path
        for path in (output / "artifacts" / "cache").iterdir()
        if json.loads(
            (path / "source-record.json").read_text(encoding="utf-8")
        )["id"]
        == old_record.id
    )
    claims_path = stale_cache / "source-claims.jsonl"
    claims_path.write_text(
        claims_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    changed = fixture / "bursty-capacity.md"
    changed.write_text(
        changed.read_text(encoding="utf-8")
        + "\n- Operators should measure rejection rates during load tests.\n",
        encoding="utf-8",
    )

    result = build_corpus(manifest, output, prune_cache=True, clock=_clock)

    relative_cache = stale_cache.relative_to(output).as_posix()
    assert stale_cache.is_dir()
    assert relative_cache in result.preserved_cache_paths


def test_resource_budgets_fail_closed_without_model_calls(tmp_path):
    with pytest.raises(CorpusBudgetExceeded, match="max_sources"):
        build_corpus(
            FIXTURE_DIR / "manifest.json",
            tmp_path / "too-many-sources",
            budget=CorpusResourceBudget(max_sources=2),
            clock=_clock,
        )
    assert not (tmp_path / "too-many-sources").exists()

    with pytest.raises(CorpusBudgetExceeded, match="max_source_bytes"):
        build_corpus(
            FIXTURE_DIR / "manifest.json",
            tmp_path / "oversized-source",
            budget=CorpusResourceBudget(max_source_bytes=1),
            clock=_clock,
        )
    assert not (tmp_path / "oversized-source").exists()

    output = tmp_path / "too-many-claims"
    with pytest.raises(CorpusBudgetExceeded, match="max_claims"):
        build_corpus(
            FIXTURE_DIR / "manifest.json",
            output,
            budget=CorpusResourceBudget(max_claims=8),
            clock=_clock,
        )
    assert (output / "artifacts" / "corpus-manifest.json").is_file()
    assert not (output / "skill").exists()

    with pytest.raises(ValueError, match="exactly zero model calls"):
        CorpusResourceBudget(max_model_calls=1)


def test_interrupted_compile_resumes_from_durable_source_caches(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "output"
    real_compile = corpus_pipeline.compile_corpus_skill

    def fail_after_writes(*args, **kwargs):
        real_compile(*args, **kwargs)
        raise RuntimeError("simulated interruption after compiler writes")

    monkeypatch.setattr(corpus_pipeline, "compile_corpus_skill", fail_after_writes)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        build_corpus(FIXTURE_DIR / "manifest.json", output, clock=_clock)

    checkpoint_path = output / "artifacts" / "recovery-checkpoint.json"
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert interrupted["status"] == "in_progress"
    assert interrupted["last_completed_stage"] == "synthesize"
    assert len(interrupted["completed_source_record_ids"]) == 3

    monkeypatch.setattr(corpus_pipeline, "compile_corpus_skill", real_compile)
    resumed = build_corpus(FIXTURE_DIR / "manifest.json", output, clock=_clock)

    assert len(resumed.reused_source_ids) == 3
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert completed["status"] == "completed"
    assert completed["last_completed_stage"] == "compile"


def test_source_must_match_the_run_fingerprint(tmp_path, monkeypatch):
    real_fingerprints = corpus_pipeline._source_fingerprints

    def stale_fingerprints(manifest, manifest_dir):
        values = list(real_fingerprints(manifest, manifest_dir))
        values[0] = values[0].split(":sha256:", 1)[0] + ":sha256:" + "0" * 64
        return tuple(values)

    monkeypatch.setattr(corpus_pipeline, "_source_fingerprints", stale_fingerprints)
    with pytest.raises(CorpusPipelineError, match="run fingerprint"):
        build_corpus(
            FIXTURE_DIR / "manifest.json",
            tmp_path / "output",
            clock=_clock,
        )

    assert not (tmp_path / "output" / "artifacts" / "source-records.jsonl").exists()
    assert not (tmp_path / "output" / "skill").exists()


def test_source_order_does_not_change_graph_or_synthesis_semantics(tmp_path):
    fixture = tmp_path / "fixture"
    manifest_path = _copy_fixture(fixture)
    normal = build_corpus(manifest_path, tmp_path / "normal", clock=_clock)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["source_entries"] = list(reversed(payload["source_entries"]))
    reversed_path = fixture / "reversed.json"
    reversed_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    reversed_result = build_corpus(
        reversed_path, tmp_path / "reversed", clock=_clock
    )

    assert {claim.id for claim in normal.canonical_claims} == {
        claim.id for claim in reversed_result.canonical_claims
    }
    assert normal.graph.id == reversed_result.graph.id
    assert _synthesis_semantics(normal) == _synthesis_semantics(reversed_result)


def test_one_missing_source_yields_scoped_partial_build(tmp_path):
    fixture = tmp_path / "fixture"
    manifest_path = _copy_fixture(fixture)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["source_entries"][2]["input_ref"] = "missing.md"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = build_corpus(manifest_path, tmp_path / "output", clock=_clock)

    assert len(result.source_records) == 2
    assert any("missing" in note for note in result.limitations)
    assert (tmp_path / "output" / "artifacts" / "synthesis.json").is_file()


def test_build_requires_synthesis_eligible_claims_from_two_sources(tmp_path):
    fixture = tmp_path / "fixture"
    manifest_path = _copy_fixture(fixture)
    for filename in ("admission-control.md", "bursty-capacity.md"):
        (fixture / filename).write_text(
            "# Context only\n\nThis ordinary prose has no explicit claim marker.\n",
            encoding="utf-8",
        )

    with pytest.raises(CorpusPipelineError, match="fewer than two sources"):
        build_corpus(manifest_path, tmp_path / "output", clock=_clock)

    assert not (tmp_path / "output" / "skill").exists()


def test_unrelated_nonempty_output_directory_is_refused(tmp_path):
    output = tmp_path / "unrelated"
    output.mkdir()
    (output / "user-owned.txt").write_text("preserve me", encoding="utf-8")

    with pytest.raises(CorpusPipelineError, match="nonempty"):
        build_corpus(FIXTURE_DIR / "manifest.json", output, clock=_clock)
    assert (output / "user-owned.txt").read_text(encoding="utf-8") == "preserve me"


def test_corrupt_existing_corpus_marker_is_refused_safely(tmp_path):
    output = tmp_path / "corrupt"
    marker = output / "artifacts" / "corpus-manifest.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(CorpusPipelineError, match="cannot be verified"):
        build_corpus(FIXTURE_DIR / "manifest.json", output, clock=_clock)
    assert marker.read_text(encoding="utf-8") == "{not valid json"


def test_module_cli_validate_and_build(tmp_path):
    validate = subprocess.run(
        [
            sys.executable,
            "-m",
            "corpus_to_skill",
            "validate",
            str(FIXTURE_DIR / "manifest.json"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert validate.returncode == 0, validate.stderr
    assert json.loads(validate.stdout)["status"] == "valid"

    output = tmp_path / "cli-output"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "corpus_to_skill",
            "build",
            str(FIXTURE_DIR / "manifest.json"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    summary = json.loads(build.stdout)
    assert summary["status"] == "completed"
    assert summary["sources"] == 3
    assert (output / "skill" / "resilient-queue-operations" / "SKILL.md").is_file()


def test_legacy_corpus_module_path_remains_compatible(tmp_path):
    import book_to_skill.corpus as legacy_corpus
    from book_to_skill.corpus.budget import (
        CorpusResourceBudget as LegacyCorpusResourceBudget,
    )
    from book_to_skill.corpus.cli import main as legacy_cli_main
    from book_to_skill.corpus.pipeline import build_corpus as legacy_build_corpus
    from corpus_to_skill import load_manifest as canonical_load_manifest
    from corpus_to_skill.budget import CorpusResourceBudget as CanonicalCorpusResourceBudget
    from corpus_to_skill.cli import main as canonical_cli_main
    from corpus_to_skill.pipeline import build_corpus as canonical_build_corpus

    assert legacy_corpus.load_manifest is canonical_load_manifest
    assert LegacyCorpusResourceBudget is CanonicalCorpusResourceBudget
    assert legacy_cli_main is canonical_cli_main
    assert legacy_build_corpus is canonical_build_corpus

    result = legacy_build_corpus(
        FIXTURE_DIR / "manifest.json",
        tmp_path / "legacy-output",
        budget=LegacyCorpusResourceBudget(),
        clock=_clock,
    )
    assert len(result.source_records) == 3

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "book_to_skill.corpus",
            "validate",
            str(FIXTURE_DIR / "manifest.json"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "valid"
