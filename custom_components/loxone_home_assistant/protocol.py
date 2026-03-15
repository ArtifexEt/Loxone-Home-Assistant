"""Helpers for parsing the Loxone websocket protocol."""

from __future__ import annotations

import ast
import contextlib
import json
import re
import struct
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


if sys.version_info >= (3, 10):
    def slotted_dataclass(cls):
        return dataclass(slots=True)(cls)
else:
    def slotted_dataclass(cls):
        return dataclass(cls)


@slotted_dataclass
class PendingHeader:
    """Header for a pending binary websocket payload."""

    identifier: int
    estimated: bool


def parse_api_key_payload(payload: Any) -> Mapping[str, Any] | None:
    """Parse the `jdev/cfg/apiKey` response payload."""
    if not isinstance(payload, Mapping):
        return None
    root = payload.get("LL")
    if not isinstance(root, Mapping):
        return None
    value = deserialize_value(root.get("value"))
    if isinstance(value, Mapping):
        return value
    return None


def parse_header(data: bytes) -> PendingHeader:
    """Parse the 8-byte Loxone websocket header."""
    # Loxone marks "estimated length" using the lowest bit in byte 2.
    return PendingHeader(identifier=data[1], estimated=bool(data[2] & 0x01))


def normalize_uuid(value: str) -> str:
    """Normalize UUIDs to canonical lowercase 8-4-4-4-12 format."""
    compact = value.strip().lower().replace("-", "")
    if len(compact) != 32:
        return value.strip().lower()
    with contextlib.suppress(ValueError):
        return str(uuid.UUID(hex=compact))
    return value.strip().lower()


def _loxone_uuid_from_bytes_le(raw: bytes) -> str:
    return str(uuid.UUID(bytes_le=raw))


def parse_value_state_table(data: bytes) -> dict[str, float]:
    """Parse a binary value-state table payload."""
    values: dict[str, float] = {}
    offset = 0
    while offset + 24 <= len(data):
        state_uuid = _loxone_uuid_from_bytes_le(data[offset : offset + 16])
        offset += 16
        value = struct.unpack("<d", data[offset : offset + 8])[0]
        offset += 8
        values[state_uuid] = value
    return values


def parse_text_state_table(data: bytes) -> dict[str, str]:
    """Parse a binary text-state table payload."""
    values: dict[str, str] = {}
    offset = 0
    while offset + 36 <= len(data):
        state_uuid = _loxone_uuid_from_bytes_le(data[offset : offset + 16])
        offset += 16
        offset += 16  # icon UUID
        text_length = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        padded_length = (text_length + 3) & ~0x03
        raw_text = data[offset : offset + text_length]
        offset += padded_length
        values[state_uuid] = raw_text.decode("utf-8", errors="ignore")
    return values


def deserialize_value(value: Any) -> Any:
    """Deserialize JSON-like values returned by the Miniserver."""
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        return value

    if stripped.startswith("{") or stripped.startswith("["):
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(stripped)
        normalized = _normalize_loxone_literals(stripped)
        with contextlib.suppress(SyntaxError, ValueError):
            return ast.literal_eval(normalized)

    return value


def _normalize_loxone_literals(value: str) -> str:
    """Convert JavaScript-style literals in Loxone payloads to Python syntax."""
    normalized = re.sub(r"\btrue\b", "True", value, flags=re.IGNORECASE)
    normalized = re.sub(r"\bfalse\b", "False", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bnull\b", "None", normalized, flags=re.IGNORECASE)
    return normalized
