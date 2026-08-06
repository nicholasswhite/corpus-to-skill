# Corpus workflow

The corpus workflow builds one provenance-aware skill from two or more local
sources. It is an additive path: it does not replace the existing
`/book-to-skill` agent workflow or the `book-to-skill` extraction CLI. See
[Protected Legacy Compatibility](LEGACY_COMPATIBILITY.md) for those preserved
contracts.

The current corpus implementation is an offline, deterministic MVP for
plain-text and Markdown-family files. It records source claims, conservative
cross-source relationships, disputes, gaps, and exact locators before compiling
a separate skill tree.

For feature-stability labels, the v1 compatibility/upgrade contract, enforced
resource budgets, interruption recovery, safe cache pruning, and opt-in test
commands, see [Framework status, compatibility, and operations](FRAMEWORK_STATUS.md).

## Quick start

Keep the manifest and its sources together so every `input_ref` can be a
portable path relative to the manifest:

```text
incident-corpus/
├── manifest.json
└── sources/
    ├── containment.md
    └── evidence-preservation.md
```

Validate the manifest, then build into a dedicated output directory:

```bash
corpus-to-skill validate incident-corpus/manifest.json
corpus-to-skill build incident-corpus/manifest.json --output corpus-build
```

The module form is equivalent:

```bash
python -m book_to_skill.corpus validate incident-corpus/manifest.json
python -m book_to_skill.corpus build incident-corpus/manifest.json --output corpus-build
```

`validate` checks the JSON contract, identifiers, source count, and that source
references are relative and non-URI. It does not resolve or read the source
files, check that they exist, or write build artifacts.
`build` performs ingestion through compilation. A successful command prints a
JSON summary; validation or build errors are written to stderr and exit with
status 2.

## Manifest v1

The manifest is UTF-8 JSON with `schema_version` exactly `"1.0"`. It requires at
least two source entries with unique IDs:

```json
{
  "schema_version": "1.0",
  "id": "incident-response-notes",
  "name": "Incident Response Notes",
  "domain_profile": "general@1.0",
  "configuration_ref": "explicit-markdown-v1",
  "created_at": "2026-08-05T00:00:00Z",
  "source_entries": [
    {
      "source_id": "containment-runbook",
      "input_ref": "sources/containment.md",
      "media_type": "text/markdown",
      "metadata_overrides": {
        "title": "Containment Runbook",
        "creators": ["Operations Team"],
        "rights_or_access_notes": "Internal operational material"
      }
    },
    {
      "source_id": "evidence-guide",
      "input_ref": "sources/evidence-preservation.md",
      "media_type": "text/markdown",
      "metadata_overrides": {
        "title": "Evidence Preservation Guide"
      }
    }
  ]
}
```

Manifest rules:

- `id` and each `source_id` use lowercase letters, digits, `_`, or `-`, and
  begin with a letter or digit.
- `input_ref` must be a portable relative local path. Absolute paths and values
  containing `://` are rejected during validation; paths that resolve outside
  the manifest directory are rejected during ingestion. Avoid `..` segments
  even when they would resolve back inside the directory.
- `configuration_ref` is a versioned cache/configuration identity. In this MVP
  it is not loaded as a configuration file; extraction still uses the built-in
  `explicit-markdown-v1` rules.
- `domain_profile` is optional metadata carried into the build. The current CLI
  does not load or execute a domain adapter from this value. The reusable core
  validates declarative `DomainProfile` and `MetricDefinition` records, but a
  resolver from this manifest string to a trusted profile is future adapter work.
- The v1 ingester consumes metadata overrides named `title`, `creators`, `edition`,
  `publication_date`, and `rights_or_access_notes`. They are descriptive; the
  tool does not verify rights or licensing.
- At least two entries must be declared and at least two sources must ingest
  successfully. A failing source is recorded as a build limitation when two or
  more other sources remain; otherwise the build stops.

Unknown manifest fields and unsupported schema versions are rejected rather
than ignored.

## Pipeline and behavior

```text
CorpusManifest
  -> SourceRecord[] + sanitized extracted text
  -> SourceClaim[]
  -> CanonicalClaim[]
  -> ClaimRelation[] + ClaimGraphSnapshot
  -> SynthesisArtifact
  -> corpus skill + SkillBuildManifest
```

All normal stages run locally and make no network or model calls. Records use
canonical JSON, content hashes, stable IDs, and stable ordering. Reordering the
same sources does not change the stable graph or synthesis semantics. The
time-bearing `runs.jsonl` and `build-manifest.json` files naturally receive new
timestamps on a later build, while the build ID excludes its generation time.
Parser, extractor, compiler, schema, and configuration versions remain part of
the recorded lineage.

This is deterministic rules-based synthesis, not a truth engine. It preserves
conditions and dissent, abstains when scope is insufficient, and never treats a
source count as proof. The corpus CLI currently supplies no source-independence
groups, so synthesis deliberately withholds consensus/minority inference that
would require that metadata.

## Output tree

`--output` must name an empty directory or an existing build owned by the same
corpus ID. A nonempty unrelated directory, or a directory marked for another
corpus, is refused.

The build writes its corpus ownership marker before later stages and uses
atomic per-file replacement, not a whole-directory transaction. A failed or
interrupted build can therefore leave valid intermediates, and an update can
leave the previous successful build manifest in place. Diagnose the error and
rerun the same corpus/output; treat an update as complete only after the command
succeeds and its `build-manifest.json` reflects the intended corpus.

```text
<output>/
├── artifacts/
│   ├── corpus-manifest.json
│   ├── extracted/
│   │   └── <source-record-id>.txt
│   ├── cache/
│   │   └── <source-hash>-<extraction-config-hash>/
│   │       ├── source-record.json
│   │       └── source-claims.jsonl
│   ├── source-records.jsonl
│   ├── source-claims.jsonl
│   ├── canonical-claims.jsonl
│   ├── relations.jsonl
│   ├── claim-graph.json
│   ├── synthesis.json
│   ├── runs.jsonl
│   ├── recovery-checkpoint.json
│   └── cache-state.json
└── skill/
    └── <corpus-slug>/
        ├── SKILL.md
        ├── chapters/
        │   ├── 00-sources-and-method.md
        │   ├── <topic-chapters>.md
        │   └── 99-disputes-and-gaps.md
        ├── glossary.md
        ├── patterns.md
        ├── cheatsheet.md
        ├── source-registry.json
        ├── traceability.json
        └── build-manifest.json
```

The ledgers retain the inspectable records for every stage. `traceability.json`
maps generated assertions through canonical and source claim IDs to source
locators and run IDs. `source-registry.json` contains portable source metadata
and checksums. `build-manifest.json` records compiler/configuration identity and
checksums for the files in the current generated skill; it intentionally does
not checksum itself.

## Exact-offset extraction MVP

The corpus adapter currently accepts `.txt`, `.text`, `.md`, `.markdown`,
`.rst`, `.adoc`, and `.asciidoc`. It deliberately does not reuse the legacy
PDF, EPUB, DOCX, HTML, RTF, or Calibre paths yet: those adapters do not currently
guarantee the exact character-offset provenance required by this workflow.

Offsets point into the sanitized text stored under `artifacts/extracted/`, not
raw byte positions in the original file. Decoding is BOM-aware and otherwise
tries UTF-8, CP1252, then Latin-1. Invisible Unicode removed during ingestion is
reported as a limitation, so the stored text and its offsets remain internally
consistent.

Claim extraction is intentionally narrow and line-oriented. It considers only:

- Markdown-style bullets beginning with `-`, `+`, or `*`;
- numbered list items such as `1.` or `1)`; and
- paragraphs explicitly prefixed with `Claim:` or `Assertion:`.

It uses small punctuation and conjunction rules to split independently stated
sentences or clauses. Ordinary prose is ignored. The semantic profile and claim
type come from conservative regular-expression rules; extracted claims begin as
`unreviewed` with a heuristic extraction confidence, not an evidence or truth
score. This is not a complete Markdown parser, citation parser, table reader, or
general natural-language claim extractor. Review the ledger before relying on a
build, especially when source files contain code fences, nested formatting, or
compound prose.

Normalization merges claims only when a complete structured identity is
available and their claim type plus every recorded semantic/scope field match.
Claims with incomplete subject/relation identity remain separate pending
review. Wording variants and member source claim IDs remain in the canonical
record. Relationship classification is also rules-based and may return
`insufficient_information`. The reusable core exposes typed reviewer,
timestamp, prior/new-value, and reason fields through `apply_human_override`,
but the corpus CLI has no review UI or override-ledger import yet.

## Updating a corpus and cache invalidation

To add or change material:

1. Keep the source beneath the manifest directory and add or update its relative
   `input_ref`.
2. Retain an existing `source_id` when it is still the same logical source; use a
   new ID for a genuinely new source or edition.
3. Treat the manifest as versioned input and change `created_at` or other
   metadata deliberately; manifest changes affect the overall build identity.
4. Run `validate`, then rerun `build` against the same output directory.
5. Inspect the command summary's `reused_sources` and `limitations`, then review
   the new build manifest, disputes, gaps, and traceability.

The source-claim cache key incorporates corpus/source identity, the raw content
checksum, parser version, `configuration_ref`, and extractor version. Therefore:

- unchanged sources reuse their cached `SourceClaim` records;
- changing one source's bytes or ID invalidates that source only;
- changing the corpus ID requires a new output directory and gives every source
  a new identity;
- changing `configuration_ref`, parser version, or extractor version invalidates
  all affected source caches; and
- `--force` bypasses all source-claim cache reads for that build.

Normalization, relationship classification, synthesis, and compilation are
rerun for the current corpus even when source claims are reused. Stable IDs make
unchanged logical records comparable, but the implementation does not yet cache
every downstream stage.

`build --prune-cache` performs conservative post-success cleanup. It removes an
obsolete content-addressed cache and its extracted text only when the preceding
`cache-state.json` declared all relevant checksums, the bytes still match, the
records remain internally consistent, and no unknown file is present. Modified,
corrupt, undeclared, symlinked, and user-owned paths are preserved and reported.
Without this flag, historical caches remain reusable/inspectable. The compiler
separately reconciles the generated skill tree: it removes stale files and old
skill slugs only when a prior same-corpus manifest declared the file and its
current checksum still matches. For a minimal publishable snapshot, a new empty
output directory is still simplest. Do not reuse an output directory for a
different corpus ID.

## Provenance, security, and privacy

The provenance chain is explicit:

- `SourceRecord` stores portable source identity plus raw-content and extracted-
  text checksums.
- `SourceClaim` stores a section/paragraph locator, exact character offsets, and
  a short excerpt/checksum when the assertion is at most 280 characters.
- `CanonicalClaim` retains every member source claim and wording variant.
- Relations, synthesis assertions, disputes, and gaps retain the relevant claim
  IDs and run lineage.
- Before compilation, the provenance resolver checksum-verifies extracted text
  and resolves every rendered assertion to valid source spans.
- The generated traceability and build manifests expose that lineage and the
  current output checksums for audit.

Source text is treated as untrusted data and is never executed. Ingestion strips
known invisible Unicode code points. Prompt-shaped or exfiltration-shaped list
items are retained in the source ledger as rejected claims, assigned zero
extraction confidence, and excluded from synthesis and rendered prose. The
compiler also redacts unsafe prompt-shaped metadata and absolute filesystem
paths from the generated skill. These checks are defense in depth, not a
complete content-security or malware scanner.

The pipeline is local, but its output is not automatically safe to share.
`artifacts/extracted/` contains sanitized copies of the source text, and the
claim ledger contains source-faithful assertions and short excerpts. Metadata
overrides and rejected text also remain in the inspectable artifacts even when
the generated skill redacts them. Keep corpus output private when the inputs are
private, licensed, confidential, or personal; secure the output directory and
follow the sources' rights and retention requirements. No credential should be
placed in a manifest or source.

## Schema compatibility policy

Version 1 writers emit `schema_version: "1.0"`, and the current reader accepts
exactly `1.0`. Missing versions, unknown fields, and any other version fail
closed. There is no implicit best-effort downgrade and no migration is needed
while v1 is the only persisted version.

The static `tests/fixtures/schema_v1/records.json` golden set covers every type
in `PERSISTED_RECORD_TYPES`; its test fails if the registry and fixture type sets
diverge or if any v1 record stops canonical-round-tripping. The complete change
rules, v2 checklist, and operator upgrade guidance are in
[Framework status, compatibility, and operations](FRAMEWORK_STATUS.md).

Before a second schema version can become the default, the project must add
fixtures and tests for v1 plus one of these explicit backward-read paths:

1. keep a v1 decoder alongside the newer decoder; or
2. provide a deterministic, auditable v1-to-new-version migration that writes a
   new artifact without overwriting its input.

A migration must preserve source checksums, locators, source-claim membership,
dissent, and run lineage, or report why it cannot. Silent reinterpretation of an
older artifact is not compatible behavior.

## Reusing the claim framework without books

`claim_framework` has no dependency on document parsers or corpus orchestration.
The incident-response example constructs source-faithful records directly,
normalizes, relates, and synthesizes them, then applies the Phase 6 synthesis
evaluation adapter with every synthetic assertion treated as span-resolved:

```bash
python -m examples.non_book_claims
```

See `examples/non_book_claims.py` in the repository. This demonstrates claim
reasoning and the evaluation framework; its structural/provenance score is not
an empirical evidence-quality benchmark or a predictive-performance result.

## Phase 6-8 status and limitations

The corpus CLI covers the Phase 2-5 vertical slice. The reusable framework also
contains Phase 6 and an experimental Phase 7 foundation, but neither is run
automatically by `corpus-to-skill build` or included in the corpus output tree:

- **Phase 6, evaluation — implemented:** immutable, versioned evaluation
  dimensions, rubrics, assessments, and records support explicit `unknown`,
  `not_applicable`, and `fail` missing-data policies. The evaluation engine
  validates caller-supplied assessments and can compute a transparent weighted
  aggregate whose method, weights, and included component IDs remain visible.
  A deterministic synthesis adapter evaluates provenance and structural
  completeness. It does not invent evidence assessments or treat the aggregate
  as a truth or predictive-power score.
- **Phase 7, predictive power — experimental foundation:** the framework
  implements claim eligibility decisions, immutable hash-verified prediction
  specs, provenance-bearing outcome observations, deterministic scoring and
  benchmark comparison, timing/leakage checks, and a corpus eligibility export.
  `freeze_prediction(spec, claim, registered_at=...)` recomputes eligibility
  from the actual canonical claim and hash-binds an explicit registration time
  that must be at or before the declared information cutoff. Successor drafts
  clear that timestamp and must be registered again.
  Pending or unresolvable outcomes receive no numeric score. These APIs make
  prospective measurement possible; they do not establish real historical
  accuracy, calibration, or predictive power for this project or any corpus.
  The corpus compiler continues to make no predictive-power claim.
- **Phase 8, extraction and hardening:** the domain-neutral framework ships in
  the same distribution but has not been extracted or published separately.
  The corpus path still lacks exact-offset rich-document/URI adapters, full
  downstream-result caching and a concrete domain adapter selected by
  `domain_profile`. Schema v1 has an exhaustive golden compatibility fixture;
  no migration tool is needed until a second schema exists. Enforced resource
  budgets, a durable recovery checkpoint, checksum-declared opt-in cache pruning,
  and explicit live/performance pytest gates are implemented. No real live
  provider adapter or representative release benchmark currently ships.
  Cross-repository extraction or publication requires separate approval.

These limitations are deliberate labels on the current boundary, not implied
capabilities.
