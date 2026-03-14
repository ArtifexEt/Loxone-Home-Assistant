"""Shared option helpers for the Loxone integration."""

from __future__ import annotations

from typing import Any


def option_enabled(value: Any, default: bool) -> bool:
    """Return bool option value with tolerant coercion."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
    return default
