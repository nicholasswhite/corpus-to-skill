"""Subprocess-only fixture used to characterize pytest opt-in markers."""

import pytest


@pytest.mark.live
def test_live_marker():
    assert True


@pytest.mark.performance
def test_performance_marker():
    assert True
