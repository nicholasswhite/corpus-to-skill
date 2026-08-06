"""Provenance-preserving local source ingestion for the corpus workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from book_to_skill.parsers.text import read_text_file
from book_to_skill.sanitize import sanitize_extracted_text
from claim_framework.jsonio import sha256_bytes, sha256_text, stable_id
from claim_framework.records import CorpusManifest, SourceEntry, SourceRecord


TEXT_SOURCE_EXTENSIONS = {
    ".txt", ".text", ".md", ".markdown", ".rst", ".adoc", ".asciidoc",
}
ADAPTER_VERSION = "text-source-v1"


class CorpusIngestionError(ValueError):
    """Raised when a manifest source cannot be ingested faithfully."""


@dataclass(frozen=True)
class IngestedSource:
    record: SourceRecord
    text: str
    raw_byte_count: int
    removed_invisible_codepoints: int = 0


def _resolve_local_source(manifest_dir: Path, input_ref: str) -> Path:
    if "://" in input_ref:
        raise CorpusIngestionError(
            "the v1 corpus adapter accepts local relative paths only; authorized URI adapters are future work"
        )
    candidate = Path(input_ref)
    if candidate.is_absolute():
        raise CorpusIngestionError(
            "source input_ref must be relative to the manifest for portable provenance"
        )
    root = manifest_dir.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CorpusIngestionError("source input_ref escapes the manifest directory") from exc
    if not resolved.is_file():
        raise CorpusIngestionError(f"source file does not exist: {input_ref}")
    if resolved.suffix.lower() not in TEXT_SOURCE_EXTENSIONS:
        raise CorpusIngestionError(
            f"{resolved.suffix or '<none>'} lacks an exact-offset v1 corpus adapter; "
            "use a supported text/Markdown source or add a locator-preserving adapter"
        )
    return resolved


def _infer_media_type(path: Path, declared: str) -> str:
    if declared:
        return declared
    if path.suffix.lower() in {".md", ".markdown"}:
        return "text/markdown"
    return "text/plain"


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return fallback


def ingest_source(
    manifest: CorpusManifest,
    entry: SourceEntry,
    manifest_dir: Path,
    ingestion_run_id: str,
) -> IngestedSource:
    """Read one local source as data and return a portable source record."""

    source_path = _resolve_local_source(manifest_dir, entry.input_ref)
    raw_bytes = source_path.read_bytes()
    decoded = read_text_file(str(source_path))
    if decoded is None:
        raise CorpusIngestionError(f"source could not be decoded: {entry.input_ref}")
    if source_path.read_bytes() != raw_bytes:
        raise CorpusIngestionError(
            f"source changed while it was being ingested: {entry.input_ref}"
        )
    text, removed = sanitize_extracted_text(decoded)
    if not text.strip():
        raise CorpusIngestionError(
            f"source contains no visible text after sanitization: {entry.input_ref}"
        )

    overrides: Mapping[str, object] = entry.metadata_overrides
    title = str(overrides.get("title") or _first_heading(text, source_path.stem))
    creators_value = overrides.get("creators") or ()
    if isinstance(creators_value, str):
        creators = (creators_value,)
    else:
        creators = tuple(str(item) for item in creators_value)  # type: ignore[arg-type]
    content_checksum = sha256_bytes(raw_bytes)
    extracted_checksum = sha256_text(text)
    source_record_id = stable_id(
        "source",
        {
            "corpus_id": manifest.id,
            "source_id": entry.source_id,
            "content_checksum": content_checksum,
            "parser_version": ADAPTER_VERSION,
        },
    )
    record = SourceRecord(
        id=source_record_id,
        corpus_id=manifest.id,
        title=title,
        creators=creators,
        edition=str(overrides["edition"]) if overrides.get("edition") else None,
        publication_date=(
            str(overrides["publication_date"])
            if overrides.get("publication_date")
            else None
        ),
        media_type=_infer_media_type(source_path, entry.media_type or ""),
        source_ref=Path(entry.input_ref).as_posix(),
        content_checksum=content_checksum,
        rights_or_access_notes=(
            str(overrides["rights_or_access_notes"])
            if overrides.get("rights_or_access_notes")
            else None
        ),
        parser_name="book_to_skill.parsers.text.read_text_file",
        parser_version=ADAPTER_VERSION,
        ingestion_run_id=ingestion_run_id,
        extracted_text_ref=f"artifacts/extracted/{source_record_id}.txt",
        extracted_text_checksum=extracted_checksum,
    )
    return IngestedSource(
        record=record,
        text=text,
        raw_byte_count=len(raw_bytes),
        removed_invisible_codepoints=removed,
    )
