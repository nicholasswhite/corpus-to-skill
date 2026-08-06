## ⚙️ How it works

```
One file · a folder · a glob · a list of paths
     │
     ▼
Step 1.5 — "Technical or text-heavy sources?"
     │
     ├── technical → Docling  (tables + code blocks as markdown, ~1.5s/page)
     └── text      → pdftotext → pypdf → pdfminer  (instant)
     │
     ▼
Inherited Book-to-Skill extractor: scripts/extract.py <paths…> --mode <technical|text>
  per source: PDF → pdftotext/Docling · EPUB → ebooklib → stdlib zipfile · DOCX/HTML/RTF/…
  (one bad source is skipped with a warning; the rest still process)
     │
     ├── /tmp/corpus_skill_work/full_text.txt   (all sources merged, with source markers)
     └── /tmp/corpus_skill_work/metadata.json   (aggregated stats + per-source array)
               │
               ▼
          Agent analyzes corpus structure
          (corpus title, per-source creators, document/section inventory)
          ── or, if targeting an existing skill: folds new content in (Mode 4)
               │
               ▼
          Generates per-content-unit summaries  (800–1,200 tokens each)
          (chapters/ remains the compatibility storage directory)
          technical → includes Code Examples + Reference Tables sections
          Generates glossary, patterns, cheatsheet
          Generates master SKILL.md with core mental models + source-aware inventory
               │
               ▼
          Skill written to one of:
            ~/.copilot/skills/<slug>/   (GitHub Copilot CLI)
            ~/.agents/skills/<slug>/    (Copilot CLI or Amp, cross-agent)
            ~/.claude/skills/<slug>/    (Claude Code)
          /tmp/corpus_skill_work/       🗑️  cleaned up
```

**Single-source extraction benchmark** (103-page technical book, CPU only):

| Method | Time | Tokens | Tables | Code blocks |
|--------|------|--------|--------|-------------|
| pdftotext | 0.1s | 27K | 0 | 0 |
| Docling | 164s | 27K (+1.2%) | 48 | 36 |

**Single-source corpus examples** (measured: pages, extracted tokens, units auto-detected,
estimated one-pass cost on Claude Sonnet 4.5 at \$3/\$15 per MTok):

| Source | Format | Pages | Tokens | Units | ~Cost |
|------|--------|------:|-------:|---------:|------:|
| Think Python 2 | PDF | 244 | 119K | 19 | \$0.88 |
| Working Backwards | PDF | 371 | 175K | 10 | \$0.96 |
| Pro Git | PDF | 501 | 229K | — † | \$1.23 |
| Moby-Dick | EPUB | — | 301K | — † | \$1.42 |

† Chapter auto-detection needs explicit `Chapter N` / `Capítulo N` headings. Pro Git
uses section titles and Moby-Dick uses chapter *titles* / roman numerals, so neither
auto-segments — extraction and conversion still work, but you point at sections
manually. These examples cost roughly **\$1 per source** at the listed sizes; a multi-source
corpus scales with its combined tokens and generated content units.

<details>
<summary>Design principles (click to expand)</summary>

1. **Density over completeness** — a 1,000-token summary beats a 10,000-token excerpt
2. **Practitioner voice** — "Use X when Y", not "The source explains X"
3. **Front-loaded SKILL.md** — compaction keeps the first ~5,000 tokens; the most important content comes first
4. **On-demand content units** — the source-aware topic index tells the agent which file to read; units load only when needed
5. **Never raw text** — always synthesize, summarize, extract signal from the source

</details>

---


---

[← Back to the README](../README.md)
