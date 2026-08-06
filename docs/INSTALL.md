## 📥 Install

> **Two ways to use it, do not confuse them:**
> - **As an agent skill** (the `/corpus-to-skill` command in Claude Code, Copilot CLI, or Amp) → **`git clone` into your skills folder** (below). This gives you the full host-agent conversion flow.
> - **As a Python package** → `pip install book-to-skill`. The distribution keeps its legacy identifier for safe upgrades and installs both the `corpus-to-skill` compiler and inherited `book-to-skill` extraction CLI. It does **not** register the Agent Skill. See [the CLI section](#python-package-and-clis-pip).

The skill follows the open [Agent Skills](https://github.com/agentskills/agentskills) standard, so a single install works for any compatible host.

**GitHub Copilot CLI** (personal skill):

```bash
git clone https://github.com/nicholasswhite/book-to-skill.git ~/.copilot/skills/corpus-to-skill
# then, in a `copilot` session:
/skills reload
/skills info corpus-to-skill
```

Or the cross-agent path that Copilot CLI and Amp both discover:

```bash
git clone https://github.com/nicholasswhite/book-to-skill.git ~/.agents/skills/corpus-to-skill
```

**Claude Code**:

Copy this into your Claude Code session:

```
Install corpus-to-skill: https://raw.githubusercontent.com/nicholasswhite/book-to-skill/master/SKILL.md
```

Or manually using standard `git clone` (ensures modular engine files are fetched correctly):

```bash
git clone https://github.com/nicholasswhite/book-to-skill.git ~/.claude/skills/corpus-to-skill
```

Then in any agent session:

```bash
/corpus-to-skill ~/path/to/your-sources/
# or
/corpus-to-skill ~/papers/paper.pdf ~/notes/project.md
```

### Python package and CLIs (pip)

`pip install book-to-skill` is a **separate, optional** path. The distribution
name is retained to make upgrades from earlier releases safe. It installs the
manifest-driven `corpus-to-skill` compiler plus the inherited text-extraction
engine for scripting; it does **not** register the `/corpus-to-skill` Agent
Skill (use the `git clone` path above for that).

```bash
pip install "book-to-skill[pdf,epub,docx]"          # compiler + optional extractors
corpus-to-skill validate path/to/manifest.json       # corpus workflow
book-to-skill ~/path/to/book.pdf --mode text         # inherited extractor
book-to-skill --check                                 # extractor dependency report
```

---

[← Back to the README](../README.md)
