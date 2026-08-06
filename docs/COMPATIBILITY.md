# Compatibility

Corpus to Skill has two canonical entry surfaces and one inherited extraction
surface. The product rename does not erase the original Book to Skill lineage or
break users of its standalone extractor.

## 1. Agent Skill: `/corpus-to-skill`

The root `SKILL.md` defines the model-driven conversion workflow:

```text
/corpus-to-skill <path-to-document-folder-or-glob>... [skill-name-slug]
```

With no special instruction, a document path starts Full Conversion. The skill
also recognizes analyze-only, generate-from-prior-analysis, and update/fold-in
requests. It asks whether content is technical or text-heavy, runs the existing
extractor, presents the cost preflight, and asks before generation.

The destination remains host-aware. It is selected from the existing Copilot,
Amp, or Claude skill roots according to the rules in `SKILL.md`; a missing or
ambiguous destination is resolved with the user rather than silently changed.
The generated skill artifact remains:

```text
<skills-home>/<slug>/
├── SKILL.md
├── chapters/ch<NN>-<slug>.md
├── glossary.md
├── patterns.md
└── cheatsheet.md
```

The Agent Skill sets `CORPUS_SKILL_WORKDIR` to a dedicated temporary
`corpus_skill_work` directory and cleans it only after the generated skill has
been handled. Corpus commands use a distinct output scope and must not rewrite
an existing generated skill unless the user invokes the established update,
overwrite, or rename flow.

## 2. Corpus compiler

The deterministic manifest compiler uses the canonical project name:

```text
corpus-to-skill validate <manifest>
corpus-to-skill build <manifest> --output <directory>
python -m corpus_to_skill validate <manifest>
python -m corpus_to_skill build <manifest> --output <directory>
```

The former `book_to_skill.corpus` module path remains importable as a
compatibility namespace, but new code should import `corpus_to_skill` directly.
The Python distribution itself deliberately remains `book-to-skill`: changing
that identifier while shipping the inherited package would create overlapping
install ownership and make ordinary upgrades unsafe.

## 3. Inherited standalone extraction CLI

The inherited extraction engine is not the model-driven skill generator. These
entry points remain equivalent and protected:

```text
book-to-skill <path-or-folder-or-glob>... [options]
python -m book_to_skill <path-or-folder-or-glob>... [options]
python scripts/extract.py <path-or-folder-or-glob>... [options]
```

`scripts/extract.py` is the backward-compatible shim. The supported options and
defaults are:

- `--mode technical|text`; default `text`, with invalid values falling back to
  `text`.
- `--install-missing ask|yes|no`; default is
  `CORPUS_SKILL_INSTALL_MISSING`, then the legacy
  `BOOK_SKILL_INSTALL_MISSING`, then `ask`. Bare `--install-missing` means
  `yes`, and `--no-install-missing` means `no`.
- `--check` prints dependency status and exits successfully even when optional
  extractors are absent.
- `-h` and `--help` print usage to stderr and exit successfully.
- Unknown flags warn on stderr and are ignored.

The standalone CLI has no skill-name positional and no output-path flag: every
non-flag token is an input path. The extractor accepts `.pdf`, `.epub`, `.docx`,
`.rtf`, `.txt`, `.text`, `.md`, `.markdown`, `.rst`, `.adoc`, `.asciidoc`,
`.html`, `.htm`, `.xhtml`, `.mobi`, `.azw`, and `.azw3`.
Explicit files may also be recognized as PDF, EPUB, or DOCX by their contents.
Explicit input order is retained; directory and glob expansions are sorted, and
duplicate paths keep their first occurrence.

### Configuration and artifacts

`CORPUS_SKILL_WORKDIR` selects the work directory for the renamed Agent Skill.
The inherited `BOOK_SKILL_WORKDIR` remains a supported fallback. Both are read
when `book_to_skill.config` is imported; without either variable, the extractor
retains its legacy `<system-temp>/book_skill_work` default. A successful
extraction writes UTF-8 files with fixed names:

```text
<workdir>/full_text.txt
<workdir>/metadata.json
```

For one source, `full_text.txt` contains the established 80-character source
boundary, absolute source path, and sanitized extracted text. `metadata.json`
keeps these top-level keys:

```text
source_file, filename, format, extraction_method, extraction_mode,
file_size_mb, pages, chars, words, estimated_tokens,
estimated_tokens_human, output_text, total_sources, sources,
chapters_detected, chapter_headings_sample, has_toc
```

The nested `sources` item keeps source identity, extraction method, size and page
label/count, text metrics, chapter count, and ToC status. It does not embed raw
text. Top-level text metrics describe the consolidated `full_text.txt`, including
its source boundary.

Normal and partial-success runs exit 0. Help and dependency-check runs exit 0.
No arguments, no resolved supported files, or all sources failing exit 1. A
single-source API failure raises `ExtractionError`; unexpected errors propagate.

## Public Python imports

The package-root import surface remains:

```python
from book_to_skill import (
    ExtractionError,
    extract_single_file,
    main,
    resolve_input_files,
)
```

`resolve_input_files(paths)` returns resolved `Path` objects.
`extract_single_file(input_path, extraction_mode, install_mode)` returns the
single-source text-and-metadata dictionary and does not write the consolidated
artifacts. Package-root `main()` is the existing `book_to_skill.utils.main`.

## Compatibility gate

`tests/test_legacy_contract.py` characterizes the inherited shim and module entry
points, their exit/help behavior, work-directory and filename contract, exact
metadata key sets and relationships, unknown-flag behavior, and package-root API
exports. Corpus tests exercise the canonical `corpus_to_skill` namespace and
compiler CLI. Both surfaces must remain green.
