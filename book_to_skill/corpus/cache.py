"""Conservative, opt-in pruning for corpus-owned content-addressed caches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Set, Tuple

from claim_framework.jsonio import sha256_file
from claim_framework.records import SourceClaim, SourceRecord
from claim_framework.store import ArtifactStore, StoreError


_CACHE_DIR = re.compile(r"^[0-9a-f]{16}-[0-9a-f]{16}$")
_EXTRACTED_FILE = re.compile(r"^source_[0-9a-f]{64}\.txt$")
_CACHE_FILES = {"source-record.json", "source-claims.jsonl"}


@dataclass(frozen=True)
class CachePruneResult:
    removed_files: Tuple[str, ...] = ()
    preserved_paths: Tuple[str, ...] = ()


def _relative(store: ArtifactStore, path: Path) -> str:
    return path.relative_to(store.root).as_posix()


def _verified_stale_cache(
    store: ArtifactStore,
    cache_dir: Path,
    declared_checksums: Mapping[str, str],
) -> Tuple[bool, str, str]:
    children = tuple(cache_dir.iterdir())
    if any(child.is_symlink() for child in children):
        return False, "", ""
    if any(not child.is_file() or child.name not in _CACHE_FILES for child in children):
        return False, "", ""
    if {child.name for child in children} != _CACHE_FILES:
        return False, "", ""

    record_ref = _relative(store, cache_dir / "source-record.json")
    claims_ref = _relative(store, cache_dir / "source-claims.jsonl")
    for relative_ref in (record_ref, claims_ref):
        expected = declared_checksums.get(relative_ref)
        if expected is None or sha256_file(store.path_for(relative_ref)) != expected:
            return False, "", ""
    try:
        record = store.read_json(record_ref, SourceRecord)
        claims = store.read_jsonl(claims_ref, SourceClaim)
    except (StoreError, ValueError):
        return False, "", ""
    if any(claim.source_id != record.id for claim in claims):
        return False, "", ""
    extracted_ref = Path(record.extracted_text_ref).as_posix()
    try:
        extracted_path = store.path_for(extracted_ref)
    except StoreError:
        return False, extracted_ref, record.extracted_text_checksum
    if (
        not extracted_path.is_file()
        or extracted_path.is_symlink()
        or not _EXTRACTED_FILE.fullmatch(extracted_path.name)
        or sha256_file(extracted_path) != record.extracted_text_checksum
        or declared_checksums.get(extracted_ref) != record.extracted_text_checksum
    ):
        return False, extracted_ref, record.extracted_text_checksum
    return True, record.extracted_text_ref, record.extracted_text_checksum


def prune_obsolete_cache(
    store: ArtifactStore,
    *,
    current_cache_roots: Iterable[str],
    current_extracted_refs: Iterable[str],
    declared_checksums: Mapping[str, str],
) -> CachePruneResult:
    """Remove only verified stale generated entries and preserve unknown content."""

    current_roots: Set[str] = {Path(item).as_posix().rstrip("/") for item in current_cache_roots}
    current_extracted: Set[str] = {Path(item).as_posix() for item in current_extracted_refs}
    removed = []
    preserved = []
    stale_extracted = {}

    cache_parent = store.path_for("artifacts/cache")
    if cache_parent.is_dir() and not cache_parent.is_symlink():
        for cache_dir in sorted(cache_parent.iterdir(), key=lambda item: item.name):
            relative_dir = _relative(store, cache_dir)
            if relative_dir in current_roots:
                continue
            if (
                not cache_dir.is_dir()
                or cache_dir.is_symlink()
                or not _CACHE_DIR.fullmatch(cache_dir.name)
            ):
                preserved.append(relative_dir)
                continue
            verified, extracted_ref, extracted_checksum = _verified_stale_cache(
                store,
                cache_dir,
                declared_checksums,
            )
            if not verified:
                preserved.append(relative_dir)
                if extracted_ref:
                    preserved.append(Path(extracted_ref).as_posix())
                continue
            for filename in sorted(_CACHE_FILES):
                path = cache_dir / filename
                path.unlink()
                removed.append(_relative(store, path))
            cache_dir.rmdir()
            if extracted_ref:
                stale_extracted[Path(extracted_ref).as_posix()] = extracted_checksum

    for extracted_ref in sorted(set(stale_extracted) - current_extracted):
        path = store.path_for(extracted_ref)
        if (
            not path.is_file()
            or path.is_symlink()
            or not _EXTRACTED_FILE.fullmatch(path.name)
        ):
            if path.exists() or path.is_symlink():
                preserved.append(extracted_ref)
            continue
        if sha256_file(path) != stale_extracted[extracted_ref]:
            preserved.append(extracted_ref)
            continue
        path.unlink()
        removed.append(extracted_ref)

    return CachePruneResult(
        removed_files=tuple(sorted(removed)),
        preserved_paths=tuple(sorted(set(preserved))),
    )


__all__ = ["CachePruneResult", "prune_obsolete_cache"]
