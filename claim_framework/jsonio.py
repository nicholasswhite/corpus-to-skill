"""Deterministic JSON encoding and decoding for claim-framework records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping as MappingABC, Sequence as SequenceABC
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Tuple, Type, TypeVar, Union, get_args, get_origin, get_type_hints

from claim_framework import records


T = TypeVar("T")
_ID_PREFIX = re.compile(r"^[a-z][a-z0-9_-]*$")
_NONE_TYPE = type(None)


class JsonContractError(ValueError):
    """Raised when JSON cannot be mapped to the requested record contract."""


class UnsupportedSchemaVersion(JsonContractError):
    """Raised when persisted data declares an unsupported schema version."""


def _record_registry() -> Mapping[str, Type[Any]]:
    discovered = {
        value.__name__: value for value in records.PERSISTED_RECORD_TYPES
    }
    return MappingProxyType(dict(sorted(discovered.items())))


RECORD_TYPES = _record_registry()


def to_plain(value: Any) -> Any:
    """Recursively convert dataclasses into JSON-compatible plain values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, MappingABC):
        converted = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise JsonContractError("JSON object keys must be strings")
            converted[key] = to_plain(item)
        return converted
    if isinstance(value, (tuple, list)):
        return [to_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise JsonContractError(f"unsupported JSON value: {type(value).__name__}")


def canonical_dumps(value: Any) -> str:
    """Return compact UTF-8-safe JSON with deterministic key ordering."""
    return json.dumps(
        to_plain(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_dumps(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Union[str, Path]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, identity: Any) -> str:
    """Build a content-derived ID from canonical JSON and a readable prefix."""
    if not isinstance(prefix, str) or not _ID_PREFIX.fullmatch(prefix):
        raise ValueError("ID prefix must start with a lowercase letter and contain only lowercase letters, digits, '_' or '-'")
    return f"{prefix}_{sha256_bytes(canonical_bytes(identity))}"


def _resolve_record_type(record_type: Union[str, Type[T]]) -> Type[T]:
    if isinstance(record_type, str):
        try:
            return RECORD_TYPES[record_type]  # type: ignore[return-value]
        except KeyError as exc:
            raise JsonContractError(f"unknown record type: {record_type!r}") from exc
    if not isinstance(record_type, type) or not is_dataclass(record_type):
        raise TypeError("record_type must be a registered dataclass type or name")
    if record_type.__name__ not in RECORD_TYPES or RECORD_TYPES[record_type.__name__] is not record_type:
        raise JsonContractError(f"unregistered record type: {record_type.__name__}")
    return record_type


def _decode(annotation: Any, value: Any, path: str) -> Any:
    if annotation is Any:
        return value

    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if origin is Union:
        if value is None and _NONE_TYPE in arguments:
            return None
        candidates = tuple(item for item in arguments if item is not _NONE_TYPE)
        failures = []
        for candidate in candidates:
            try:
                return _decode(candidate, value, path)
            except (JsonContractError, TypeError, ValueError) as exc:
                failures.append(str(exc))
        raise JsonContractError(f"{path} does not match any allowed type: {'; '.join(failures)}")

    if origin in (tuple, Tuple):
        if not isinstance(value, list):
            raise JsonContractError(f"{path} must be a JSON array")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(_decode(arguments[0], item, f"{path}[{index}]") for index, item in enumerate(value))
        if len(value) != len(arguments):
            raise JsonContractError(f"{path} must contain exactly {len(arguments)} items")
        return tuple(_decode(item_type, item, f"{path}[{index}]") for index, (item_type, item) in enumerate(zip(arguments, value)))

    if origin in (dict, Mapping, MappingABC):
        if not isinstance(value, MappingABC):
            raise JsonContractError(f"{path} must be a JSON object")
        key_type, value_type = arguments or (str, Any)
        converted = {}
        for key, item in value.items():
            converted_key = _decode(key_type, key, f"{path}.<key>")
            converted[converted_key] = _decode(value_type, item, f"{path}.{key}")
        return converted

    if origin in (list, SequenceABC):
        if not isinstance(value, list):
            raise JsonContractError(f"{path} must be a JSON array")
        item_type = arguments[0] if arguments else Any
        return [_decode(item_type, item, f"{path}[{index}]") for index, item in enumerate(value)]

    if isinstance(annotation, type) and is_dataclass(annotation):
        return _from_mapping(annotation, value, path)

    if annotation is str:
        if not isinstance(value, str):
            raise JsonContractError(f"{path} must be a string")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise JsonContractError(f"{path} must be a boolean")
        return value
    if annotation is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise JsonContractError(f"{path} must be an integer")
        return value
    if annotation is float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise JsonContractError(f"{path} must be a number")
        return float(value)
    if value is None and annotation is _NONE_TYPE:
        return None
    return value


def _from_mapping(record_type: Type[T], payload: Any, path: str) -> T:
    if not isinstance(payload, MappingABC):
        raise JsonContractError(f"{path} must be a JSON object")

    record_fields = {item.name: item for item in fields(record_type)}
    unknown = sorted(set(payload) - set(record_fields))
    if unknown:
        raise JsonContractError(f"{path} has unknown field(s): {', '.join(unknown)}")

    if "schema_version" in record_fields:
        if "schema_version" not in payload:
            raise JsonContractError(f"{path}.schema_version is required")
        schema_version = payload["schema_version"]
        if schema_version != records.SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"{path}.schema_version {schema_version!r} is unsupported; expected {records.SCHEMA_VERSION!r}"
            )

    hints = get_type_hints(record_type)
    decoded = {
        name: _decode(hints.get(name, Any), value, f"{path}.{name}")
        for name, value in payload.items()
    }
    try:
        return record_type(**decoded)
    except (TypeError, ValueError) as exc:
        raise JsonContractError(f"invalid {path}: {exc}") from exc


def from_dict(record_type: Union[str, Type[T]], payload: Mapping[str, Any]) -> T:
    resolved = _resolve_record_type(record_type)
    return _from_mapping(resolved, payload, resolved.__name__)


def loads_record(text: str, record_type: Union[str, Type[T]]) -> T:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonContractError(f"invalid JSON: {exc.msg}") from exc
    return from_dict(record_type, payload)


def dumps_jsonl(records_to_write: SequenceABC) -> str:
    return "".join(f"{canonical_dumps(record)}\n" for record in records_to_write)


def loads_jsonl(text: str, record_type: Union[str, Type[T]]) -> Tuple[T, ...]:
    records_read = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records_read.append(loads_record(line, record_type))
        except JsonContractError as exc:
            raise JsonContractError(f"JSONL line {line_number}: {exc}") from exc
    return tuple(records_read)


def write_json(path: Union[str, Path], value: Any) -> None:
    Path(path).write_text(canonical_dumps(value) + "\n", encoding="utf-8")


def read_json(path: Union[str, Path], record_type: Union[str, Type[T]]) -> T:
    return loads_record(Path(path).read_text(encoding="utf-8"), record_type)


def write_jsonl(path: Union[str, Path], values: SequenceABC) -> None:
    Path(path).write_text(dumps_jsonl(values), encoding="utf-8")


def read_jsonl(path: Union[str, Path], record_type: Union[str, Type[T]]) -> Tuple[T, ...]:
    return loads_jsonl(Path(path).read_text(encoding="utf-8"), record_type)


__all__ = [
    "JsonContractError",
    "RECORD_TYPES",
    "UnsupportedSchemaVersion",
    "canonical_bytes",
    "canonical_dumps",
    "dumps_jsonl",
    "from_dict",
    "loads_jsonl",
    "loads_record",
    "read_json",
    "read_jsonl",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "stable_id",
    "to_plain",
    "write_json",
    "write_jsonl",
]
