"""Explicit stability labels for the reusable framework interfaces.

These labels describe compatibility promises, not implementation quality or
truth.  ``scaffolded`` means a typed seam exists but concrete policy remains the
responsibility of an adapter.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


STABLE: Final[str] = "stable"
EXPERIMENTAL: Final[str] = "experimental"
SCAFFOLDED: Final[str] = "scaffolded"

CORE_INTERFACE_STABILITY: Final[str] = STABLE
EVALUATION_INTERFACE_STABILITY: Final[str] = EXPERIMENTAL
PREDICTION_INTERFACE_STABILITY: Final[str] = EXPERIMENTAL
ADAPTER_INTERFACE_STABILITY: Final[str] = SCAFFOLDED

INTERFACE_STABILITY: Mapping[str, str] = MappingProxyType(
    {
        "adapter": ADAPTER_INTERFACE_STABILITY,
        "core": CORE_INTERFACE_STABILITY,
        "evaluation": EVALUATION_INTERFACE_STABILITY,
        "prediction": PREDICTION_INTERFACE_STABILITY,
    }
)


__all__ = [
    "ADAPTER_INTERFACE_STABILITY",
    "CORE_INTERFACE_STABILITY",
    "EVALUATION_INTERFACE_STABILITY",
    "EXPERIMENTAL",
    "INTERFACE_STABILITY",
    "PREDICTION_INTERFACE_STABILITY",
    "SCAFFOLDED",
    "STABLE",
]
