"""Helpers for parsing the Loxone websocket protocol."""

from __future__ import annotations

import ast
import json
import re
import struct
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_HEADER = struct.Struct("<BBBxI")
_VALUE_EVENT = struct.Struct("<16sd")
_TEXT_EVENT = struct.Struct("<16s16xI")
_LOXONE_LITERAL = re.compile(
    r'''"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b(true|false|null)\b''',
    re.IGNORECASE | re.DOTALL,
)
_PYTHON_LITERALS = {"true": "True", "false": "False", "null": "None"}


@dataclass(frozen=True, slots=True)
class PendingHeader:
    """Header for a pending binary websocket payload."""

    identifier: int
    estimated: bool
    length: int


def parse_api_key_payload(payload: Any) -> Mapping[str, Any] | None:
    """Parse the `jdev/cfg/apiKey` response payload."""
    if not isinstance(payload, Mapping):
        return None
    root = payload.get("LL")
    if not isinstance(root, Mapping):
        return None
    value = deserialize_value(root.get("value"))
    return value if isinstance(value, Mapping) else None


def parse_header(data: bytes) -> PendingHeader | None:
    """Recognize and decode an 8-byte Loxone websocket header."""
    if len(data) != _HEADER.size or data[0] != 0x03:
        return None
    _, identifier, flags, length = _HEADER.unpack(data)
    return PendingHeader(identifier, bool(flags & 0x01), length)


def normalize_uuid(value: str) -> str:
    """Normalize UUIDs to canonical lowercase 8-4-4-4-12 format."""
    value = value.strip().lower()
    compact = value.replace("-", "")
    if len(compact) != 32:
        return value
    try:
        return str(uuid.UUID(hex=compact))
    except ValueError:
        return value


def parse_value_state_table(data: bytes) -> dict[str, float]:
    """Parse a binary value-state table payload."""
    values: dict[str, float] = {}
    for offset in range(0, len(data) - _VALUE_EVENT.size + 1, _VALUE_EVENT.size):
        state_uuid, value = _VALUE_EVENT.unpack_from(data, offset)
        values[str(uuid.UUID(bytes_le=state_uuid))] = value
    return values


def parse_text_state_table(data: bytes) -> dict[str, str]:
    """Parse a binary text-state table payload."""
    values: dict[str, str] = {}
    offset = 0
    while offset + _TEXT_EVENT.size <= len(data):
        state_uuid, length = _TEXT_EVENT.unpack_from(data, offset)
        start = offset + _TEXT_EVENT.size
        offset = start + ((length + 3) & ~0x03)
        if offset > len(data):
            break
        raw_text = data[start : start + length]
        values[str(uuid.UUID(bytes_le=state_uuid))] = raw_text.decode("utf-8", errors="ignore")
    return values


def deserialize_value(value: Any) -> Any:
    """Deserialize JSON-like values returned by the Miniserver."""
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return value

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(_LOXONE_LITERAL.sub(_replace_loxone_literal, stripped))
    except (SyntaxError, ValueError):
        return value


def _replace_loxone_literal(match: re.Match[str]) -> str:
    # The pattern consumes quoted strings as a whole to preserve their contents.
    literal = match.group(1)
    return _PYTHON_LITERALS[literal.lower()] if literal else match.group()
