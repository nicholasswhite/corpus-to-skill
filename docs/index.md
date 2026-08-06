---
hide:
  - navigation
  - toc
---

# Corpus to Skill

<p style="font-size: 1.25rem; max-width: 42rem;">
Turn a document or deliberately assembled body of writing into a structured, on-demand agent skill — named frameworks, decision rules, and anti-patterns. <strong>Structure, not a summary.</strong>
</p>

[Get started](guide.md){ .md-button .md-button--primary }
[Skill reference](skill-reference.md){ .md-button }
[GitHub](https://github.com/nicholasswhite/corpus-to-skill){ .md-button }

!!! note "Lineage and differences"
    Corpus to Skill is an independently maintained derivative of
    [Virgilio Junior's original `book-to-skill`](https://github.com/virgiliojr94/book-to-skill).

    **Inherited foundation:** the original host-agent document-conversion
    workflow, multi-format `book_to_skill` extractor, and layered generated-skill
    layout remain foundational here and continue to receive explicit credit.

    **Added in this repository:** a separate manifest-driven, offline corpus
    compiler; versioned domain-neutral claim and cross-source synthesis models;
    checksum-verified source spans and inspectable claim, relation, dispute, gap,
    and traceability artifacts; plus deterministic resource, cache, recovery, and
    pruning controls.

    The upstream MIT notice and Git history are preserved. The new name describes
    the expanded scope; it does not claim that inherited work originated here or
    imply upstream endorsement. See [Architecture](ARCHITECTURE.md) and
    [Compatibility](COMPATIBILITY.md) for the detailed boundary. The Python
    distribution retains its legacy identifier for safe upgrades.

---

## Why Corpus to Skill

<div class="grid cards" markdown>

-   :material-file-document-multiple:{ .lg .middle } __Multi-format__

    ---

    PDF, EPUB, DOCX, HTML, Markdown, RTF, MOBI/AZW (via Calibre). Extraction runs
    locally with graceful stdlib fallbacks — no upload, no lock-in.

-   :material-brain:{ .lg .middle } __Structure, not summaries__

    ---

    Named frameworks, mental models, decision rules, and anti-patterns from the
    source corpus, captured with its exact terms rather than flattened summaries.

-   :material-flash:{ .lg .middle } __On-demand chapters__

    ---

    Layered source and topic files load only when relevant, so a large corpus
    costs tokens proportional to the question, not the total page count.

-   :material-robot-happy:{ .lg .middle } __Multi-agent__

    ---

    One `SKILL.md` runs on Claude Code, GitHub Copilot CLI, and Amp through the
    open Agent Skills standard.

</div>

## Install

**As an agent skill** (gives you the `/corpus-to-skill` command in Claude Code, Copilot CLI, Amp):

```bash
git clone https://github.com/nicholasswhite/corpus-to-skill.git ~/.claude/skills/corpus-to-skill
# then, in your agent session:
/corpus-to-skill /path/to/source-folder [skill-name]
```

**As the inherited extractor CLI** (optional; the package also installs the corpus compiler):

```bash
pip install "book-to-skill[pdf,epub,docx]"
book-to-skill /path/to/book.pdf --mode text
```

## Learn more

<div class="grid cards" markdown>

-   :material-sitemap:{ .lg .middle } __[Architecture](ARCHITECTURE.md)__

    ---

    How the deterministic extractor and the spec-driven generator fit together.

-   :material-speedometer:{ .lg .middle } __[Performance](PERFORMANCE.md)__

    ---

    The measured Discovery Loop Tax and real per-conversion token cost.

-   :material-book-open-page-variant:{ .lg .middle } __[Skill Reference](skill-reference.md)__

    ---

    The full `SKILL.md` spec: every step, depth budget, and quality rule.

-   :material-license:{ .lg .middle } __[License](https://github.com/nicholasswhite/corpus-to-skill/blob/master/LICENSE.md)__

    ---

    Corpus to Skill is available under the MIT License, including its inherited notice.

</div>
