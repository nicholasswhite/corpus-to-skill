## 🚀 Usage

```
/book-to-skill <path-to-document-folder-or-glob>... [skill-name-slug]
```

Supported document formats: PDF, EPUB, DOCX, TXT, Markdown, reStructuredText, AsciiDoc, HTML, RTF, MOBI/AZW/AZW3.

**Examples:**

```bash
# Process several files together into a unified skill
/book-to-skill ~/papers/paper1.pdf ~/notes/export.txt unified-research

# Process all supported files in a folder together
/book-to-skill ~/workspace/project-docs/ project-knowledge

# Process files matching a glob pattern
/book-to-skill "~/books/*.epub" my-library

# Update/fold new material into an existing skill folder
/book-to-skill ~/articles/new-paper.pdf ~/.claude/skills/project-knowledge
```

After the skill is created, use it like any other agent skill:

```bash
/designing-data-intensive-apps                  # load core mental models
/designing-data-intensive-apps replication      # find and explain a topic
/designing-data-intensive-apps ch05             # dive into chapter 5
/designing-data-intensive-apps "what chapters do you have?"
```

In GitHub Copilot CLI you may need to run `/skills reload` after the file is written so the new skill appears in `/skills list`. Claude Code and Amp pick it up on the next session.

---

## Additive corpus workflow

Use the separate corpus command when two or more local sources need an
inspectable source-claim ledger, exact locators, cross-source relationships, and
a corpus-derived skill:

```bash
corpus-to-skill validate incident-corpus/manifest.json
corpus-to-skill build incident-corpus/manifest.json --output corpus-build
```

The corpus command does not change `/book-to-skill` syntax or write into an
existing legacy skill. Its current exact-offset adapter accepts local
plain-text and Markdown-family sources; rich-document corpus adapters are not
implemented yet.

See [Corpus workflow](CORPUS_WORKFLOW.md) for the manifest v1 contract, module
form, output artifacts, cache/update behavior, privacy guidance, and Phase 6-8
status and limitations.

Operational budgets, interruption recovery, safe cache rotation, schema upgrade
rules, and the live/performance pytest gates are documented in
[Framework status, compatibility, and operations](FRAMEWORK_STATUS.md).

---

[← Back to the README](../README.md)
