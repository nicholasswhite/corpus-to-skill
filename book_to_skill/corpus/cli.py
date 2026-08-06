"""Run the canonical corpus CLI through its former module path."""

from corpus_to_skill.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
