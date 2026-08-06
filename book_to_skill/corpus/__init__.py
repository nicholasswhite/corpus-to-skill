"""Compatibility namespace for the former ``book_to_skill.corpus`` path.

New code should import :mod:`corpus_to_skill` directly.  Legacy submodules are
aliases of the canonical modules so classes keep one identity across both
namespaces.
"""

from importlib import import_module as _import_module
import sys as _sys

_SUBMODULES = (
    "budget",
    "cache",
    "security",
    "ingestion",
    "manifest",
    "extraction",
    "compiler",
    "pipeline",
)

for _name in _SUBMODULES:
    _module = _import_module(f"corpus_to_skill.{_name}")
    _sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

from corpus_to_skill import *  # noqa: E402,F401,F403
from corpus_to_skill import __all__ as __all__  # noqa: E402
