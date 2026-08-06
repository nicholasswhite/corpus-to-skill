"""Load and validate portable corpus manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Tuple, Union

from claim_framework.jsonio import JsonContractError, from_dict
from claim_framework.records import ContractError, CorpusManifest


_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ManifestError(ValueError):
    """Raised when a corpus manifest is unreadable or unsafe."""


def load_manifest(path: Union[str, Path]) -> Tuple[CorpusManifest, Path]:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise ManifestError(f"manifest does not exist: {path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest must be valid UTF-8 JSON: {exc}") from exc
    try:
        manifest = from_dict(CorpusManifest, payload)
    except (JsonContractError, ContractError) as exc:
        raise ManifestError(str(exc)) from exc

    if not _PORTABLE_ID.fullmatch(manifest.id):
        raise ManifestError("manifest id must contain lowercase letters, digits, '_' or '-'")
    for entry in manifest.source_entries:
        if not _PORTABLE_ID.fullmatch(entry.source_id):
            raise ManifestError(
                f"source_id {entry.source_id!r} must contain lowercase letters, digits, '_' or '-'"
            )
        normalized_ref = entry.input_ref.replace("\\", "/")
        ref = PurePosixPath(normalized_ref)
        if (
            ref.is_absolute()
            or normalized_ref.startswith("//")
            or re.match(r"^[A-Za-z]:/", normalized_ref)
            or "://" in normalized_ref
            or ".." in ref.parts
        ):
            raise ManifestError(
                f"source {entry.source_id!r} input_ref must be a portable relative local path"
            )
    return manifest, manifest_path
