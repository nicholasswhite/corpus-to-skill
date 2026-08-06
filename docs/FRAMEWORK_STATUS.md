# Framework status, compatibility, and operations

This page records the support boundary for the additive corpus workflow and the
domain-neutral `claim_framework`. It complements the [corpus workflow](CORPUS_WORKFLOW.md)
and the [compatibility contract](COMPATIBILITY.md).

## Feature stability

The labels below describe the current repository contract. They do not imply
that an unreleased API has been published as a separate package.

| Status | Meaning |
|---|---|
| **Stable / protected** | Compatibility tests guard the public behavior. A breaking change requires explicit approval and a migration or compatibility plan. |
| **Supported** | Implemented, documented, and covered by the normal offline suite within the stated v1 scope. Additive evolution is expected. |
| **Experimental** | Implemented and tested, but the API or policy may change before it is promoted. Persisted records remain subject to the schema rules below. |
| **Scaffolded** | A contract, protocol, or documented boundary exists, but no complete operational adapter or workflow is promised. |

| Feature | Status | Current boundary |
|---|---|---|
| `/corpus-to-skill` host-agent workflow | **Stable within its specification** | The root `SKILL.md` defines document analysis, generation, update/fold-in, quality, and cleanup behavior across compatible hosts. |
| Inherited `book-to-skill` extraction CLI and `book_to_skill` API | **Stable / protected** | Existing inputs, defaults, public imports, exit behavior, and artifact locations are characterized in `tests/test_legacy_contract.py`. |
| Persisted `claim_framework` schema v1 and canonical JSON | **Supported** | Readers accept exactly schema `1.0`, reject unknown fields, and are guarded by a golden record for every registered persisted type. |
| Local `ClaimStore`, source spans, provenance resolution, normalization, relations, and synthesis | **Supported** | Domain-neutral, deterministic local APIs; heuristic relation labels still require review for consequential use. |
| `corpus-to-skill` text/Markdown-family workflow | **Supported MVP** | Two or more local relative sources, exact offsets into sanitized extracted text, deterministic offline extraction, and a separate output tree. |
| Evaluation rubrics and synthesis evaluation adapter | **Supported** | Versioned dimensions, explicit missing states, and transparent optional aggregation; no universal truth or evidence score. |
| Predictive registration, outcomes, scoring, and aggregation | **Experimental** | Prospective synthetic/external records only. No historical performance is claimed, and no live outcome resolver ships. |
| `DomainAdapter`, `ClaimExtractor`, and `PredictivePowerPort` extension protocols | **Scaffolded** | Extension seams exist, but the corpus CLI does not resolve `domain_profile` to a trusted executable adapter. |
| Rich-document and authorized-URI corpus adapters | **Scaffolded** | PDF, EPUB, DOCX, HTML, RTF, and URI ingestion do not yet provide the exact-offset corpus contract. |
| Recovery checkpoints and selective cache pruning | **Supported** | Builds record the last durable stage and completed source IDs. `build --prune-cache` removes only previously declared, checksum-matching generated cache files; later reasoning stages still rerun. |
| Schema migration CLI and downstream-result caching | **Scaffolded** | Schema v1 needs no migration yet. There is no separate `resume` or `migrate` subcommand, and normalization through compilation reruns. |
| Live/model integrations and performance benchmarks | **Scaffolded** | Pytest opt-in gates exist. The repository currently supplies only a gate-characterization fixture, not a live provider adapter or a release benchmark. |
| Separate publication of `claim_framework` | **Scaffolded** | It ships in the retained `book-to-skill` distribution without book/parser imports. Cross-repository extraction or publication needs separate approval. |

## Schema v1 compatibility contract

`schema_version` is the persisted data version, not the Python package version.
All registered top-level records currently write `"1.0"`. The reader fails closed
when the version is missing or unsupported, or when an object has unknown fields.
It does not silently reinterpret a newer record as v1.

The static fixture at `tests/fixtures/schema_v1/records.json` contains one complete
example for every class in `claim_framework.records.PERSISTED_RECORD_TYPES`.
`tests/test_schema_compatibility.py` enforces both directions of the contract:

1. the fixture type names must exactly equal the runtime registry, so adding or
   removing a persisted type cannot bypass compatibility review; and
2. every v1 object must load, convert back to the same plain value, produce the
   same canonical JSON, and load again as an equal record.

Treat the v1 fixture as an immutable compatibility artifact. Correcting a typo
in descriptive fixture text is fine only when it does not conceal a persisted
shape or semantic change.

### Change rules

- Internal implementation changes that leave the persisted shape and meaning
  unchanged do not require a schema bump.
- Renaming or deleting a field, changing a field's type or meaning, changing a
  required value, or changing canonical interpretation requires a new schema
  version.
- Do not change existing v1 artifacts in place. Writers for a new version must
  emit new files or a new output tree.
- Keep v1 readable until a documented support decision explicitly retires it.
- Preserve source checksums, locators, source-claim membership, evidence links,
  dissent, human-review lineage, and run lineage across any migration. If a
  value cannot be preserved, record that limitation instead of guessing.

### Adding a second schema version

Before a v2 writer becomes the default:

1. add a complete `tests/fixtures/schema_v2/` golden set while leaving the v1
   fixture unchanged;
2. keep a version-dispatched v1 decoder, or add a deterministic v1-to-v2
   migration that never overwrites its input;
3. add tests that load supported historical versions and verify every preserved
   provenance and review field;
4. document the exact command/API, destination, rollback procedure, and any
   lossy fields; and
5. make the new writer opt-in until backward-read and migration tests are green.

There is no migration command today because `1.0` is the only supported schema.
If a reader reports an unknown version, stop: preserve the artifact, use a tool
version that supports it, or wait for an explicit migration. Do not edit the
`schema_version` string by hand.

## Resource and cost budget

The v1 corpus path is deterministic and offline, but relationship candidate work
can grow roughly with the square of the active canonical-claim count. Builds
enforce the following default hard limits before proceeding and record both the
selected budget and actual usage in `artifacts/cache-state.json`.

| Resource | v1 budget | Action when the budget is exceeded |
|---|---|---|
| Network/model cost for normal builds and tests | **Exactly 0 calls and $0** | The built-in budget rejects any nonzero model-call allowance. Treat a proposed network/model adapter as separately reviewed live work. |
| Declared source count | **1,000 maximum** | Lower with `--max-sources`; raise deliberately only after assessing candidate-pair and storage growth. The manifest still requires at least two sources. |
| Raw local source bytes | **50 MiB per source; 500 MiB total** | Adjust with `--max-source-bytes` and `--max-total-source-bytes`. Limits are checked before hashing and again against ingested bytes. |
| Extracted source claims | **100,000 maximum** | Adjust with `--max-claims`; partition a large corpus or add a measured retrieval/index strategy before raising it. |

The defaults are denial-of-service guardrails, not performance guarantees or
benchmark results. The Python API can construct an explicit budget with a
different positive limit (or `None` for a deliberately uncapped local resource),
but it can never authorize model calls. Record machine, corpus size, claim count,
elapsed time, peak memory, output bytes, and version when adding a representative
benchmark; mark it `performance` so it remains opt-in.

## CLI checkpoints and recovery

The available commands are:

```bash
corpus-to-skill validate corpus/manifest.json
corpus-to-skill build corpus/manifest.json --output corpus-build
corpus-to-skill build corpus/manifest.json --output corpus-build --force
corpus-to-skill build corpus/manifest.json --output corpus-build --prune-cache
```

`validate` is a read-only manifest-contract checkpoint. It does not read source
files. `build` runs ingestion through compilation. Re-running the same command is
the current resume operation; there is no separate `resume` flag. `--force`
bypasses source-claim cache reads, but it is not a cleanup command and does not
make the whole build transactional.

Checkpoint behavior is deliberately inspectable but not a whole-directory
transaction:

- the corpus ownership marker is written before later stages;
- `artifacts/recovery-checkpoint.json` records `in_progress` plus the last
  completed stage and completed source-record IDs, then `completed` after a
  successful compile and cache-state write;
- sanitized extracted text and content-addressed source-claim caches are written
  per source and can be reused after interruption;
- normalization, relation classification, synthesis, and compilation rerun; and
- the skill `build-manifest.json` is written last and is the successful compiler
  commit marker.

After a failed or interrupted build:

1. Preserve the output directory for diagnosis. Do not publish it merely because
   an older `build-manifest.json` is present.
2. Correct the scoped input or configuration error and rerun the exact `build`
   command. Use `--force` when a valid-looking source-claim cache is suspect.
3. Require exit status 0 and a JSON summary with `"status": "completed"`.
4. Verify that the current skill `build-manifest.json` names the intended corpus,
   synthesis artifact, compiler version, and output checksums.
5. If the ownership marker is corrupt or belongs to another corpus, do not edit
   it in place. Build into a new empty output directory and retain the old one
   until the new result is verified.

Per-file writes are atomic, but the full output directory is not a transaction.
A failed update can leave valid new intermediates beside the previous successful
skill manifest. The offline suite injects an interruption after compiler writes
and verifies that all source caches are reused on the successful rerun.

## Cache pruning and output rotation

There is no separate `prune` subcommand. Pruning is an opt-in post-success step:

```bash
corpus-to-skill build corpus/manifest.json --output corpus-build --prune-cache
```

The compiler independently reconciles generated skill files declared by a prior
manifest whose checksums still match. `--prune-cache` then considers obsolete
content-addressed source caches and their extracted text. It deletes them only
when the preceding `cache-state.json` declared their checksums, the current bytes
still match, their records parse consistently, and no unknown file is present.
Modified, corrupt, undeclared, symlinked, and user-owned paths are preserved and
listed in the command summary/cache state.

Fresh-output rotation remains the simplest option for a minimal publishable
snapshot or when preserved paths need manual review:

```bash
corpus-to-skill build corpus/manifest.json --output corpus-build-next
```

Verify `corpus-build-next` completely, then archive or retire the previous output
as one unit according to local retention policy. Never manually prune while a
build is running.

## Opt-in live and performance tests

The default suite must stay offline. Tests marked `live` or `performance` are
skipped unless their corresponding opt-in flag is present. The marker selects a
test category; the flag confirms authorization to run it:

```bash
# Normal offline suite; live/performance tests remain skipped.
python -m pytest

# Select and authorize live/model tests.
python -m pytest -m live --run-live

# Select and authorize performance/resource-envelope tests.
python -m pytest -m performance --run-performance

# Explicitly authorize both categories in a full run.
python -m pytest --run-live --run-performance
```

Before adding or running a real live test, document the provider, endpoint,
credentials it expects, data that leaves the machine, estimated maximum cost,
rate/timeout behavior, and cleanup. Use synthetic or public data and never print
secrets. `--run-live` only lifts pytest's safety skip; it does not provide
credentials or authorize an undocumented external side effect.

The current opt-in sample only proves that both gates default to skipped and can
be explicitly enabled. It does not contact a service or constitute a performance
benchmark.
