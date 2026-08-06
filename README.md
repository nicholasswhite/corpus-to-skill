# Corpus to Skill

<sub><em><strong>Corpus</strong> (noun) — a collection of written texts, especially the complete works of an author or a body of writing on a particular subject.</em></sub>

**A corpus-to-Agent-Skill toolkit:** turn one document or a deliberately
assembled body of writing into a reusable Agent Skill, with a constrained local
compiler for source-traceable text corpora.

> [!NOTE]
> **Lineage and differences.** Corpus to Skill is an independently maintained
> derivative of [Virgilio Junior's original
> `book-to-skill`](https://github.com/virgiliojr94/book-to-skill).
>
> **What comes from the original:** the core workflow that reads common document
> formats and turns them into an Agent Skill organized into a main guide,
> chapters or sections, a glossary, patterns, and a cheatsheet.
>
> **Where Corpus to Skill extends the original:**
>
> - It adds a separate local builder for a chosen collection of text and
>   Markdown files, while keeping the original document workflow.
> - It tracks important ideas back to the exact source passage they came from.
> - It compares ideas across sources and keeps agreements, disagreements,
>   important conditions, and missing evidence visible instead of blending
>   everything into one summary.
> - It saves records you can inspect and reuses work from files that have not
>   changed, making a collection easier to audit, update, and rebuild.
> - It includes reusable pieces for other tools that compare sources and
>   evidence, not only books.
>
> The upstream MIT notice and Git history are preserved. The Corpus to Skill name
> describes this expanded scope; it does not claim that inherited work originated
> here or imply that the original author endorses or maintains this project. See
> [Architecture](docs/ARCHITECTURE.md) and
> [Compatibility](docs/COMPATIBILITY.md) for the component-by-component boundary.

For agent users who repeatedly consult source material, developers who need
local extraction, and advanced users with deliberately structured multi-source
text corpora.

The extractor and host-agent workflow are established. The local corpus
compiler is a working, bounded text/Markdown MVP. Evaluation and prediction are
experimental; richer adapters are scaffolded.

**Compatibility.** The project, root Agent Skill, and corpus compiler use
`corpus-to-skill`. The inherited extractor keeps `book-to-skill`,
`book_to_skill`, and `BOOK_SKILL_*` as supported surfaces. The Python
distribution also retains the `book-to-skill` identifier so existing installs
upgrade safely instead of creating two packages that own the same files;
framework APIs remain under `claim_framework`.

## Quickstart: build a corpus skill

Prerequisites: Git and Python 3.9 or newer. In a fresh checkout:

```bash
git clone https://github.com/nicholasswhite/corpus-to-skill.git
cd corpus-to-skill
python -m pip install .
corpus-to-skill build tests/fixtures/corpus_demo/manifest.json --output corpus-build
```

The bundled example uses three synthetic Markdown sources about queue
operations. The verified build produces 9 source claims, 7 canonical claims,
3 relations, inspectable ledgers, and an Agent Skill tree under
`corpus-build/skill/`. The built-in corpus path makes zero model calls and needs
no service credentials.

## Choose a path

Corpus to Skill has two workflows and one reusable extraction utility. They do not
share the same inputs, outputs, provenance, or model behavior.

- **Host-agent document workflow:** Give `/corpus-to-skill` one or more supported
  document files, folders, or globs. A compatible host agent uses the extractor,
  analyzes the material, and authors a layered Agent Skill.
- **Local corpus workflow:** Give `corpus-to-skill` a manifest with at least two
  successfully ingested relative local text/Markdown-family sources. It emits
  claim and relation ledgers, synthesis and traceability records, and a generated
  Agent Skill tree.
- **Standalone extraction utility:** Use `book-to-skill` directly with PDF,
  EPUB, DOCX, HTML, RTF, text/Markdown-family files, or Calibre-supported
  MOBI/AZW files. It writes only `full_text.txt` and `metadata.json`; it is not a
  third synthesis workflow or a finished Agent Skill.

## Install and use

This checkout declares version 1.3.0 and Python 3.9 or newer. Its published
Python distribution remains `book-to-skill` for safe in-place upgrades while
also installing the canonical `corpus-to-skill` compiler command.

### Host-agent document workflow

Clone this repository into an Agent Skill directory discovered by your host.
Discovery remains host-dependent; use a compatible skill root listed in
`SKILL.md` and consult your host's documentation for setup details. The root
[`SKILL.md`](SKILL.md) defines the host-dependent
`/corpus-to-skill <path-to-document-folder-or-glob>... [skill-name-slug]`
workflow. The host agent runs the extractor, analyzes the material, and writes
the layered skill.

Installing the Python distribution alone does **not** register this Agent Skill.
Generation quality, model use, and reproducibility depend on the host agent; the
standalone Python CLI does not perform that synthesis.

### Standalone extraction CLI

The base installation handles the built-in text path and dependency-free
fallbacks where available. Install the common Python backends for PDF, EPUB,
DOCX, and RTF with:

```bash
python -m pip install ".[pdf,epub,docx,rtf]"
```

The declared extras are `pdf`, `epub`, `docx`, `rtf`, `technical`, and `all`.
The `technical` extra adds Docling, while `all` aggregates every Python backend.
MOBI/AZW extraction instead requires the external Calibre `ebook-convert`
program. Rich formats differ in their dependencies and extraction quality.

```bash
book-to-skill --check
book-to-skill tests/fixtures/corpus_demo/bounded-queues.md
```

The second command extracts one local Markdown fixture. It writes consolidated
UTF-8 `full_text.txt` and `metadata.json` under `BOOK_SKILL_WORKDIR`, when set,
or under the system temporary `book_skill_work` directory. These files are
intermediates, not a generated skill or a provenance graph.

The same CLI is exposed by the `book_to_skill` module and accepts the same input
paths and flags as the console script. See
[`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) for the protected
CLI and Python API contract.

### Manifest-driven corpus compiler

A corpus manifest uses schema version `1.0`, declares at least two sources, and
refers to them with portable paths relative to the manifest. The current path
accepts `.txt`, `.text`, `.md`, `.markdown`, `.rst`, `.adoc`, and `.asciidoc`.

```bash
corpus-to-skill validate tests/fixtures/corpus_demo/manifest.json
corpus-to-skill build tests/fixtures/corpus_demo/manifest.json --output corpus-build
```

`validate` checks the manifest schema, identifiers, source count, and safe
relative references. It does not read source files, confirm that they exist, or
perform a dry-run build. `build` ingests the files, extracts eligible claims,
normalizes conservative identities, classifies relations, records disputes and
gaps, and compiles the output tree.

The built-in extractor considers Markdown-style bullets, numbered items, and
paragraphs beginning with `Claim:` or `Assertion:`. It ignores ordinary prose
and is not a general natural-language or citation extractor.

Selected output paths include:

```text
corpus-build/
├── artifacts/
│   ├── extracted/
│   ├── source-records.jsonl
│   ├── source-claims.jsonl
│   ├── canonical-claims.jsonl
│   ├── relations.jsonl
│   ├── claim-graph.json
│   ├── synthesis.json
│   └── runs.jsonl
└── skill/<corpus-slug>/
    ├── SKILL.md
    ├── chapters/
    ├── glossary.md
    ├── patterns.md
    ├── cheatsheet.md
    ├── source-registry.json
    ├── traceability.json
    └── build-manifest.json
```

The output also contains cache, checkpoint, and build-state records. See the
complete [corpus workflow](docs/CORPUS_WORKFLOW.md) for the manifest and output
contracts.

## What the corpus path records

- **Source traceability:** Rendered assertions resolve through canonical and
  source claim IDs to checksum-verified character offsets in sanitized extracted
  text. This does not cover ignored prose, raw-byte positions, or external
  citations.
- **Qualified synthesis:** Claims begin unreviewed with heuristic confidence.
  Rules preserve conditional disagreement and gaps; they do not fact-check,
  rank sources, or choose the correct position. The CLI withholds
  consensus/minority inference because it has no source-independence groups.
- **Reproducible structure:** Stable IDs, canonical ordering, cache reuse, and
  generated-content checksums support reproducible unchanged reruns. Timestamps,
  run ledgers, and cache state can still change between runs.
- **Local execution:** Normal built-in corpus builds make zero model calls and
  require no service credentials. This statement does not apply to the
  host-agent document workflow or to installation.
- **Defense in depth:** Ingestion removes known invisible Unicode controls and
  quarantines prompt-shaped claims before rendered synthesis. This is not a
  complete prompt-injection, content-security, or malware scanner.
- **Sensitive output:** Extracted text, source-faithful claims,
  metadata, and excerpts remain in the build artifacts. Secure the output and
  do not share it unless the underlying rights and sensitivity allow that use.

## Maturity and boundaries

| Status | Current boundary |
|---|---|
| **Stable within defined contracts** | Protected extractor and Python API behavior; the host-agent workflow specification; the record core; and `corpus-to-skill` only within its bounded text/Markdown MVP contract |
| **Experimental** | Versioned evaluation APIs and prospective prediction, caller-supplied outcome, scoring, and aggregation APIs; these are not run by `corpus-to-skill build` |
| **Scaffolded** | Domain/model adapter seams, rich-document and authorized-URI corpus adapters, outcome resolution, review/override UI, downstream-stage caching, migration CLI, live providers and benchmarks, and separate `claim_framework` publication |
| **Planned** | General remote ingestion, exact-offset rich-document corpus support, and automatic domain-profile execution remain possible future work without a release commitment |

Current limitations and non-goals:

- The rich-format extractor and the compiler with checksum-verified
  sanitized-text offsets are separate paths. PDF, EPUB, DOCX, HTML, RTF,
  MOBI/AZW, and URIs are not corpus inputs.
- A corpus build needs at least two successfully ingested sources and at least
  two sources that yield eligible claims.
- `configuration_ref` participates in build identity and cache invalidation; it
  is not loaded as a configuration file. `domain_profile` is retained as
  metadata but does not select an executable adapter.
- The compiler has no review UI or override-ledger import. Heuristic relation
  labels require human review for consequential use.
- Evaluation and prediction are separate APIs, not build stages. The project
  claims no historical accuracy, calibration, predictive power, universal
  evidence score, or truth score.
- Corpus to Skill is not a truth engine, RAG system, autonomous researcher, or a
  guarantee that generated material is correct or safe to redistribute.

## Development

Install the development tools, then run the verified offline checks:

```bash
python -m pip install pytest ruff
python -m pytest -q
python -m ruff check .
python -m examples.non_book_claims
```

The normal test suite keeps live and performance tests behind explicit opt-in
gates. Read [`docs/FRAMEWORK_STATUS.md`](docs/FRAMEWORK_STATUS.md) before adding
or authorizing either category. Contribution guidance is in
[`CONTRIBUTING.md`](CONTRIBUTING.md), and security reporting instructions are in
[`SECURITY.md`](SECURITY.md).

## License and attribution

This repository began as a derivative of [Virgilio Junior's original
`book-to-skill`](https://github.com/virgiliojr94/book-to-skill) project. It
retains the relevant upstream Git history and is now independently maintained.
The host-agent workflow and extraction engine descend from that lineage; this
repository adds a separately scoped corpus and claim-framework direction and
exposes the project skill as `/corpus-to-skill`. It is not maintained by or
affiliated with the original project, and no sponsorship, endorsement,
partnership, or continuing affiliation is implied.

The software remains available under the [MIT License](LICENSE.md), including
the inherited original notice:

```text
Copyright (c) 2025 virgiliojr94
```

The software license does not determine whether you may process or redistribute
third-party source material. You are responsible for the rights, access rules,
and retention requirements that apply to your inputs and generated artifacts.
