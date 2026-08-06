"""Small, atomic filesystem storage for framework artifacts and claim ledgers."""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import fields
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple, Type, TypeVar, Union

from claim_framework.jsonio import (
    RECORD_TYPES,
    canonical_dumps,
    dumps_jsonl,
    loads_jsonl,
    loads_record,
    sha256_bytes,
    sha256_text,
)


T = TypeVar("T")


class StoreError(RuntimeError):
    """Base error for artifact storage failures."""


class ArtifactNotFound(StoreError):
    """Raised when a requested artifact does not exist."""


class ArtifactChecksumMismatch(StoreError):
    """Raised when stored bytes do not match their expected checksum."""


class UnsafeArtifactPath(StoreError):
    """Raised when a relative artifact path escapes the configured store root."""


class UnsupportedRecordType(StoreError):
    """Raised when ClaimStore is asked to persist an unregistered record type."""


class InvalidRecordId(StoreError):
    """Raised when a record ID cannot be used as a store identity."""


class RecordNotFound(ArtifactNotFound):
    """Raised when an immutable logical record does not exist."""


class RecordIdCollision(StoreError):
    """Raised when an existing type/ID is associated with different content."""


class RecordStoreCorruption(StoreError):
    """Raised when stored content does not match its deterministic location."""


class RecordQueryError(StoreError):
    """Raised when a query references fields outside a record contract."""


class ArtifactStore:
    """Directory-backed artifact store using atomic sibling-temp replacement."""

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _target(self, relative_path: Union[str, Path]) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise UnsafeArtifactPath(f"artifact path must be relative to the store: {relative_path!s}")
        target = (self.root / relative).resolve(strict=False)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise UnsafeArtifactPath(f"artifact path escapes the store: {relative_path!s}") from exc
        return target

    def path_for(self, relative_path: Union[str, Path]) -> Path:
        return self._target(relative_path)

    def exists(self, relative_path: Union[str, Path]) -> bool:
        return self._target(relative_path).is_file()

    def write_bytes(
        self,
        relative_path: Union[str, Path],
        payload: bytes,
        expected_checksum: str = None,
    ) -> str:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        actual_checksum = sha256_bytes(payload)
        if expected_checksum is not None and actual_checksum != expected_checksum.lower():
            raise ArtifactChecksumMismatch(
                f"payload checksum mismatch for {relative_path!s}: expected {expected_checksum}, got {actual_checksum}"
            )

        target = self._target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after creating parents so a pre-existing symlink cannot be
        # used to redirect the final replacement outside the store.
        target = self._target(relative_path)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(target.parent),
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
            temporary_name = None
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass
        return actual_checksum

    def read_bytes(
        self,
        relative_path: Union[str, Path],
        expected_checksum: str = None,
    ) -> bytes:
        target = self._target(relative_path)
        try:
            payload = target.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactNotFound(f"artifact not found: {relative_path!s}") from exc
        if expected_checksum is not None:
            actual_checksum = sha256_bytes(payload)
            if actual_checksum != expected_checksum.lower():
                raise ArtifactChecksumMismatch(
                    f"stored checksum mismatch for {relative_path!s}: expected {expected_checksum}, got {actual_checksum}"
                )
        return payload

    def write_text(
        self,
        relative_path: Union[str, Path],
        text: str,
        expected_checksum: str = None,
    ) -> str:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        return self.write_bytes(relative_path, text.encode("utf-8"), expected_checksum)

    def read_text(
        self,
        relative_path: Union[str, Path],
        expected_checksum: str = None,
    ) -> str:
        try:
            return self.read_bytes(relative_path, expected_checksum).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StoreError(f"artifact is not valid UTF-8: {relative_path!s}") from exc

    def write_json(self, relative_path: Union[str, Path], value: Any) -> str:
        return self.write_text(relative_path, canonical_dumps(value) + "\n")

    def read_json(self, relative_path: Union[str, Path], record_type: Union[str, Type[T]]) -> T:
        return loads_record(self.read_text(relative_path), record_type)

    def write_jsonl(self, relative_path: Union[str, Path], values: Sequence[Any]) -> str:
        return self.write_text(relative_path, dumps_jsonl(values))

    def read_jsonl(
        self,
        relative_path: Union[str, Path],
        record_type: Union[str, Type[T]],
    ) -> Tuple[T, ...]:
        return loads_jsonl(self.read_text(relative_path), record_type)


_RECORD_NAMESPACE = Path("records")


def _record_id_digest(record_id: str) -> str:
    return sha256_text(record_id)


def _require_record_id(record_id: str) -> None:
    if not isinstance(record_id, str) or not record_id.strip():
        raise InvalidRecordId("record_id must be a non-empty string")


def _resolve_record_type(record_type: Union[str, Type[T]]) -> Type[T]:
    if isinstance(record_type, str):
        try:
            resolved = RECORD_TYPES[record_type]
        except KeyError as exc:
            raise UnsupportedRecordType(
                f"unregistered record type: {record_type!r}"
            ) from exc
    elif isinstance(record_type, type):
        resolved = RECORD_TYPES.get(record_type.__name__)
        if resolved is not record_type:
            raise UnsupportedRecordType(
                f"unregistered record type: {record_type.__name__!r}"
            )
    else:
        raise UnsupportedRecordType(
            "record_type must be a registered record class or class name"
        )
    if "id" not in {field.name for field in fields(resolved)}:
        raise UnsupportedRecordType(
            f"record type {resolved.__name__!r} has no immutable id field"
        )
    return resolved  # type: ignore[return-value]


class ClaimStore:
    """Small deterministic store for immutable, registered framework records.

    Records live beneath the wrapped :class:`ArtifactStore`.  Raw IDs never
    appear in paths: each filename is the full SHA-256 digest of the ID, split
    by its first two characters.  Saving the same record is idempotent, while
    reusing a type/ID for different content is rejected without overwriting.
    """

    def __init__(self, root: Union[str, Path, ArtifactStore]) -> None:
        self.artifacts = root if isinstance(root, ArtifactStore) else ArtifactStore(root)
        self._write_lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self.artifacts.root

    def _relative_path(
        self,
        record_type: Union[str, Type[T]],
        record_id: str,
    ) -> Path:
        resolved = _resolve_record_type(record_type)
        _require_record_id(record_id)
        digest = _record_id_digest(record_id)
        return (
            _RECORD_NAMESPACE
            / resolved.__name__
            / digest[:2]
            / f"{digest}.json"
        )

    def record_path(
        self,
        record_type: Union[str, Type[T]],
        record_id: str,
    ) -> Path:
        """Return the safe absolute storage path for a logical record."""
        return self.artifacts.path_for(self._relative_path(record_type, record_id))

    def exists(
        self,
        record_type: Union[str, Type[T]],
        record_id: str,
    ) -> bool:
        return self.artifacts.exists(self._relative_path(record_type, record_id))

    def _read_at(
        self,
        relative_path: Path,
        record_type: Type[T],
        expected_id: Optional[str] = None,
    ) -> T:
        try:
            text = self.artifacts.read_text(relative_path)
        except ArtifactNotFound as exc:
            raise RecordNotFound(
                f"record not found: {record_type.__name__}/{expected_id or relative_path.name}"
            ) from exc
        except UnsafeArtifactPath:
            raise
        except (OSError, StoreError) as exc:
            raise RecordStoreCorruption(
                f"stored {record_type.__name__} record cannot be read safely"
            ) from exc
        try:
            record = loads_record(text, record_type)
        except (TypeError, ValueError) as exc:
            raise RecordStoreCorruption(
                f"invalid stored {record_type.__name__} record"
            ) from exc
        record_id = getattr(record, "id")
        if expected_id is not None and record_id != expected_id:
            raise RecordStoreCorruption(
                "stored record ID does not match the requested hashed identity"
            )
        expected_path = self._relative_path(record_type, record_id)
        if expected_path != relative_path:
            raise RecordStoreCorruption(
                "stored record ID does not match its deterministic hashed path"
            )
        if text != canonical_dumps(record) + "\n":
            raise RecordStoreCorruption(
                "stored record is not in canonical deterministic JSON form"
            )
        return record

    def _write_text_once(self, relative_path: Path, text: str) -> Optional[str]:
        """Atomically create a complete file, returning None if it exists.

        A fully flushed sibling temporary file is hard-linked into its final
        hashed name.  Link creation cannot replace an existing file, so two
        ClaimStore instances cannot silently overwrite the same logical ID.
        """
        payload = text.encode("utf-8")
        checksum = sha256_bytes(payload)
        target = self.artifacts.path_for(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self.artifacts.path_for(relative_path)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(target.parent),
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_name, target)
            except FileExistsError:
                return None
            except OSError as exc:
                raise StoreError(
                    "immutable record creation requires same-filesystem "
                    "hard-link support"
                ) from exc
            return checksum
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass

    def save(self, record: T) -> str:
        """Persist one record immutably and return its content checksum."""
        record_type = _resolve_record_type(type(record))
        record_id = getattr(record, "id")
        _require_record_id(record_id)
        relative_path = self._relative_path(record_type, record_id)
        canonical_text = canonical_dumps(record) + "\n"

        with self._write_lock:
            if self.artifacts.exists(relative_path):
                existing = self._read_at(relative_path, record_type)
                if getattr(existing, "id") != record_id:
                    raise RecordIdCollision(
                        "distinct record IDs produced the same hashed storage path"
                    )
                if existing != record:
                    raise RecordIdCollision(
                        f"immutable record collision for {record_type.__name__}/{record_id}"
                    )
                return sha256_text(canonical_text)

            checksum = self._write_text_once(relative_path, canonical_text)
            if checksum is None:
                existing = self._read_at(relative_path, record_type)
                if getattr(existing, "id") != record_id:
                    raise RecordIdCollision(
                        "distinct record IDs produced the same hashed storage path"
                    )
                if existing != record:
                    raise RecordIdCollision(
                        f"immutable record collision for {record_type.__name__}/{record_id}"
                    )
                return sha256_text(canonical_text)
            stored = self._read_at(relative_path, record_type, record_id)
            if stored != record:
                raise RecordIdCollision(
                    f"record changed while saving {record_type.__name__}/{record_id}"
                )
            return checksum

    def get(
        self,
        record_type: Union[str, Type[T]],
        record_id: str,
    ) -> T:
        """Load one record by registered type and exact logical ID."""
        resolved = _resolve_record_type(record_type)
        _require_record_id(record_id)
        relative_path = self._relative_path(resolved, record_id)
        return self._read_at(relative_path, resolved, record_id)

    def query(
        self,
        record_type: Union[str, Type[T]],
        **filters: Any,
    ) -> Tuple[T, ...]:
        """Return records matching exact top-level fields, ordered by ID."""
        resolved = _resolve_record_type(record_type)
        field_names = {field.name for field in fields(resolved)}
        unknown_filters = sorted(set(filters) - field_names)
        if unknown_filters:
            raise RecordQueryError(
                "unknown query field(s) for "
                f"{resolved.__name__}: {', '.join(unknown_filters)}"
            )

        type_relative = _RECORD_NAMESPACE / resolved.__name__
        type_root = self.artifacts.path_for(type_relative)
        if not type_root.exists():
            return ()
        if not type_root.is_dir():
            raise RecordStoreCorruption(
                f"record namespace for {resolved.__name__} is not a directory"
            )

        matches = []
        for path in sorted(type_root.rglob("*.json"), key=lambda item: item.as_posix()):
            relative_path = path.relative_to(self.root)
            record = self._read_at(relative_path, resolved)
            if all(getattr(record, name) == value for name, value in filters.items()):
                matches.append(record)
        return tuple(sorted(matches, key=lambda record: record.id))


__all__ = [
    "ArtifactChecksumMismatch",
    "ArtifactNotFound",
    "ArtifactStore",
    "ClaimStore",
    "InvalidRecordId",
    "RecordIdCollision",
    "RecordNotFound",
    "RecordQueryError",
    "RecordStoreCorruption",
    "StoreError",
    "UnsupportedRecordType",
    "UnsafeArtifactPath",
]
