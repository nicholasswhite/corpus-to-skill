"""Characterize the explicit opt-in boundary for live and performance tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "fixtures" / "optional_tests" / "opt_in_sample.py"


def _run_pytest(test_file: Path, *options: str):
    environment = dict(os.environ)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", *options],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_live_and_performance_markers_are_skipped_unless_explicitly_enabled():
    default = _run_pytest(SAMPLE)
    assert default.returncode == 0, default.stderr
    assert "2 skipped" in default.stdout

    enabled = _run_pytest(SAMPLE, "--run-live", "--run-performance")
    assert enabled.returncode == 0, enabled.stderr
    assert "2 passed" in enabled.stdout
