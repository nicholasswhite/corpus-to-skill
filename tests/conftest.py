"""Repository-wide pytest safety gates for optional tests."""

from __future__ import annotations

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests that may contact external services, incur cost, or send data off-machine",
    )
    parser.addoption(
        "--run-performance",
        action="store_true",
        default=False,
        help="run opt-in performance and resource-envelope tests",
    )


def pytest_collection_modifyitems(config, items):
    gates = (
        (
            "live",
            "--run-live",
            "live/model tests are disabled; pass --run-live after reviewing data and cost",
        ),
        (
            "performance",
            "--run-performance",
            "performance tests are disabled; pass --run-performance to enable them",
        ),
    )
    for marker_name, option_name, reason in gates:
        if config.getoption(option_name):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker_name in item.keywords:
                item.add_marker(skip)
