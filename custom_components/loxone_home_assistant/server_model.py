"""Helpers for detecting canonical Loxone server models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

DEFAULT_SERVER_MODEL = "Miniserver"
SERVER_MODEL_GO = "Miniserver Go"
SERVER_MODEL_COMPACT = "Miniserver Compact"

_GO_TOKEN_RE = re.compile(r"\bgo\b", re.IGNORECASE)

# Keep key order from most explicit to most generic.
MODEL_HINT_KEYS = (
    "deviceType",
    "hardwareType",
    "model",
    "type",
    "productName",
    "name",
    "msType",
    "msModel",
)


def detect_server_model(*values: Any) -> str:
    """Return canonical server model inferred from one or more raw values."""
    for value in values:
        model = _detect_server_model_from_value(value)
        if model is not None:
            return model
    return DEFAULT_SERVER_MODEL


def detect_server_model_from_mapping(
    payload: Mapping[str, Any],
    keys: tuple[str, ...] = MODEL_HINT_KEYS,
) -> str:
    """Return canonical server model inferred from a mapping payload."""
    for key in keys:
        if key in payload:
            model = _detect_server_model_from_value(payload[key])
            if model is not None:
                return model
    return DEFAULT_SERVER_MODEL


def _detect_server_model_from_value(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = text.casefold().replace("_", " ").replace("-", " ")
    if "compact" in normalized:
        return SERVER_MODEL_COMPACT
    if _GO_TOKEN_RE.search(normalized):
        return SERVER_MODEL_GO
    if "miniserver" in normalized:
        return DEFAULT_SERVER_MODEL
    return None
