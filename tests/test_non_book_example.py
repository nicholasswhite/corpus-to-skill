"""Smoke-test the domain-neutral incident-response example."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "non_book_claims.py"


def test_non_book_example_uses_only_the_claim_framework_and_runs():
    tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.partition(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
    assert "book_to_skill" not in imported_roots
    assert "claim_framework" in imported_roots

    completed = subprocess.run(
        [sys.executable, "-m", "examples.non_book_claims"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["source_claim_count"] == 3
    assert summary["canonical_claim_count"] == 2
    assert summary["canonical_members"] == [1, 2]
    assert {item["type"] for item in summary["relations"]} == {"alternative"}
    assert {item["status"] for item in summary["synthesis_assertions"]} == {
        "conditional"
    }
    evaluation = summary["evaluation"]
    assert evaluation["rubric_id"] == "provenance-structure"
    assert evaluation["rubric_version"] == "1.0.0"
    assert evaluation["aggregate_score"] == 1.0
    assert {
        (item["id"], item["status"], item["score"])
        for item in evaluation["dimensions"]
    } == {
        ("provenance_completeness", "assessed", 1.0),
        ("structural_completeness", "assessed", 1.0),
    }
