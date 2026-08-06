"""Additive corpus-to-skill workflow; the legacy extractor remains separate."""

from book_to_skill.corpus.budget import (
    DEFAULT_CORPUS_RESOURCE_BUDGET,
    CorpusBudgetExceeded,
    CorpusResourceBudget,
    CorpusResourceUsage,
)
from book_to_skill.corpus.extraction import extract_claims, infer_semantics
from book_to_skill.corpus.ingestion import IngestedSource, ingest_source
from book_to_skill.corpus.manifest import load_manifest

__all__ = [
    "IngestedSource",
    "CorpusBudgetExceeded",
    "CorpusResourceBudget",
    "CorpusResourceUsage",
    "DEFAULT_CORPUS_RESOURCE_BUDGET",
    "extract_claims",
    "infer_semantics",
    "ingest_source",
    "load_manifest",
]
