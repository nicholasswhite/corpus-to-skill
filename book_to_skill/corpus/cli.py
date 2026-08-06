"""Command-line interface for the additive corpus workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from book_to_skill.corpus.budget import (
    DEFAULT_MAX_CLAIMS,
    DEFAULT_MAX_SOURCE_BYTES,
    DEFAULT_MAX_SOURCES,
    DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    CorpusResourceBudget,
)
from book_to_skill.corpus.manifest import ManifestError, load_manifest
from book_to_skill.corpus.pipeline import CorpusPipelineError, build_corpus
from claim_framework.records import ContractError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpus-to-skill",
        description="Build a provenance-complete skill from a versioned local corpus manifest.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a corpus manifest without writing artifacts")
    validate.add_argument("manifest", type=Path)

    build = commands.add_parser("build", help="run the offline corpus-to-skill pipeline")
    build.add_argument("manifest", type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument(
        "--force",
        action="store_true",
        help="re-extract claims instead of reusing content-addressed claim caches",
    )
    build.add_argument(
        "--prune-cache",
        action="store_true",
        help="after a successful build, remove only verified obsolete generated caches",
    )
    build.add_argument("--max-sources", type=int, default=DEFAULT_MAX_SOURCES)
    build.add_argument(
        "--max-source-bytes",
        type=int,
        default=DEFAULT_MAX_SOURCE_BYTES,
    )
    build.add_argument(
        "--max-total-source-bytes",
        type=int,
        default=DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    )
    build.add_argument("--max-claims", type=int, default=DEFAULT_MAX_CLAIMS)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            manifest, resolved = load_manifest(args.manifest)
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "manifest": str(resolved),
                        "corpus_id": manifest.id,
                        "sources": len(manifest.source_entries),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        budget = CorpusResourceBudget(
            max_sources=args.max_sources,
            max_source_bytes=args.max_source_bytes,
            max_total_source_bytes=args.max_total_source_bytes,
            max_claims=args.max_claims,
        )
        result = build_corpus(
            args.manifest,
            args.output,
            force=args.force,
            prune_cache=args.prune_cache,
            budget=budget,
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "corpus_id": result.manifest.id,
                    "sources": len(result.source_records),
                    "source_claims": len(result.source_claims),
                    "canonical_claims": len(result.canonical_claims),
                    "relations": len(result.relations),
                    "skill_build_id": result.build_manifest.id,
                    "output": str(args.output.resolve()),
                    "reused_sources": len(result.reused_source_ids),
                    "resource_usage": dict(result.resource_usage.as_dict()),
                    "pruned_cache_files": len(result.pruned_cache_files),
                    "preserved_cache_paths": list(result.preserved_cache_paths),
                    "limitations": list(result.limitations),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (ManifestError, CorpusPipelineError, ContractError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
