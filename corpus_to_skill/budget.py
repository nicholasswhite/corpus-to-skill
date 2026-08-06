"""Deterministic resource envelopes for the offline corpus pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


DEFAULT_MAX_SOURCES = 1_000
DEFAULT_MAX_SOURCE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_TOTAL_SOURCE_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_CLAIMS = 100_000


class CorpusBudgetExceeded(ValueError):
    """Raised before a corpus run exceeds an explicit resource envelope."""


def _validate_optional_limit(name: str, value: Optional[int]) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise ValueError(f"{name} must be a positive integer or None")


@dataclass(frozen=True)
class CorpusResourceBudget:
    """Hard local limits; ``None`` means the caller deliberately chose no cap."""

    max_sources: Optional[int] = DEFAULT_MAX_SOURCES
    max_source_bytes: Optional[int] = DEFAULT_MAX_SOURCE_BYTES
    max_total_source_bytes: Optional[int] = DEFAULT_MAX_TOTAL_SOURCE_BYTES
    max_claims: Optional[int] = DEFAULT_MAX_CLAIMS
    max_model_calls: int = 0

    def __post_init__(self) -> None:
        for name in (
            "max_sources",
            "max_source_bytes",
            "max_total_source_bytes",
            "max_claims",
        ):
            _validate_optional_limit(name, getattr(self, name))
        if self.max_model_calls != 0:
            raise ValueError(
                "the built-in corpus pipeline is offline and permits exactly zero model calls"
            )

    def as_dict(self) -> Mapping[str, Optional[int]]:
        return {
            "max_sources": self.max_sources,
            "max_source_bytes": self.max_source_bytes,
            "max_total_source_bytes": self.max_total_source_bytes,
            "max_claims": self.max_claims,
            "max_model_calls": self.max_model_calls,
        }

    def check_source_count(self, count: int) -> None:
        if self.max_sources is not None and count > self.max_sources:
            raise CorpusBudgetExceeded(
                f"manifest contains {count} sources, exceeding max_sources={self.max_sources}"
            )

    def check_source_bytes(self, source_bytes: int, total_bytes: int) -> None:
        if self.max_source_bytes is not None and source_bytes > self.max_source_bytes:
            raise CorpusBudgetExceeded(
                f"source contains {source_bytes} bytes, exceeding "
                f"max_source_bytes={self.max_source_bytes}"
            )
        if (
            self.max_total_source_bytes is not None
            and total_bytes > self.max_total_source_bytes
        ):
            raise CorpusBudgetExceeded(
                f"ingested sources contain {total_bytes} bytes, exceeding "
                f"max_total_source_bytes={self.max_total_source_bytes}"
            )

    def check_claim_count(self, count: int) -> None:
        if self.max_claims is not None and count > self.max_claims:
            raise CorpusBudgetExceeded(
                f"extracted {count} claims, exceeding max_claims={self.max_claims}"
            )


DEFAULT_CORPUS_RESOURCE_BUDGET = CorpusResourceBudget()


@dataclass(frozen=True)
class CorpusResourceUsage:
    source_count: int
    source_bytes: int
    source_claim_count: int
    model_calls: int = 0

    def __post_init__(self) -> None:
        for name in ("source_count", "source_bytes", "source_claim_count", "model_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.model_calls != 0:
            raise ValueError("the built-in corpus pipeline cannot report model calls")

    def as_dict(self) -> Mapping[str, int]:
        return {
            "source_count": self.source_count,
            "source_bytes": self.source_bytes,
            "source_claim_count": self.source_claim_count,
            "model_calls": self.model_calls,
        }


__all__ = [
    "CorpusBudgetExceeded",
    "CorpusResourceBudget",
    "CorpusResourceUsage",
    "DEFAULT_CORPUS_RESOURCE_BUDGET",
    "DEFAULT_MAX_CLAIMS",
    "DEFAULT_MAX_SOURCE_BYTES",
    "DEFAULT_MAX_SOURCES",
    "DEFAULT_MAX_TOTAL_SOURCE_BYTES",
]
