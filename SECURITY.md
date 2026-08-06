# Security Policy

## Scope

Corpus to Skill includes local Python CLIs and a host-agent conversion workflow.
The inherited extractor and deterministic manifest compiler do not upload files,
phone home, or run a network service. The host-agent workflow can place source
content in the context of the model configured by your agent host, so that
host's data-handling and provider settings apply. The main security surfaces are:

- the Python extraction code (parsing untrusted document files), and
- untrusted instructions embedded in source material or generated skills, and
- the optional dependencies it can install on request (`pip install …` when you
  choose `--install-missing yes`).

## Supported versions

The latest released `1.x` version receives fixes. Please reproduce issues against
the most recent tag before reporting.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Instead use GitHub's
private vulnerability reporting:

- Go to the repository's **Security** tab → **Report a vulnerability**.

Include: affected version, a minimal reproduction (ideally a small sample file or
crafted input), and the impact you observed. We aim to acknowledge within a few days.

## Good practices for users

- Run `python3 scripts/extract.py --check` to see exactly which extractors are in
  use; install dependencies yourself if you prefer to control what is added.
- Only convert sources you trust and have the right to process (see the README's
  License and attribution section).
