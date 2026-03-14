"""Helpers for validating Miniserver software compatibility."""

from __future__ import annotations

import re

MIN_SUPPORTED_VERSION = (10, 2)
MIN_SUPPORTED_VERSION_TEXT = "10.2"

_VERSION_PART_RE = re.compile(r"\d+")


def parse_miniserver_version(value: str | None) -> tuple[int, ...] | None:
    """Parse `major.minor.patch.build` style version strings."""
    if value is None:
        return None
    parts = [int(item) for item in _VERSION_PART_RE.findall(str(value))]
    if len(parts) < 2:
        return None
    return tuple(parts)


def is_supported_miniserver_version(value: str | None) -> bool:
    """Return True when the detected Miniserver version is supported."""
    parsed = parse_miniserver_version(value)
    if parsed is None:
        return False
    return parsed[:2] >= MIN_SUPPORTED_VERSION
