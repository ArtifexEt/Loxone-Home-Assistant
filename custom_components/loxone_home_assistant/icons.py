"""Helpers for validating and serving Loxone icon paths."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

ICON_PROXY_BASE_PATH = "/api/loxone_home_assistant/icon"


def normalize_icon_path(value: str | None) -> str | None:
    """Return a safe relative icon path or ``None``."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None

    split = urlsplit(raw)
    # Accept only relative icon paths from Loxone structure payloads.
    if split.scheme or split.netloc or split.query or split.fragment:
        return None

    normalized = split.path.replace("\\", "/").strip().lstrip("/")
    if not normalized:
        return None
    if any(segment in {"", ".", ".."} for segment in normalized.split("/")):
        return None
    return normalized


def encode_icon_key(icon_path: str | None) -> str | None:
    """Encode one normalized icon path for URL transport."""
    normalized = normalize_icon_path(icon_path)
    if normalized is None:
        return None
    return quote(normalized, safe="")


def decode_icon_key(value: str | None) -> str | None:
    """Decode and validate one URL-safe icon key."""
    if not isinstance(value, str):
        return None
    return normalize_icon_path(unquote(value))


def icon_proxy_url(serial: str | None, icon_path: str | None) -> str | None:
    """Return local Home Assistant proxy URL for one icon path."""
    if not isinstance(serial, str):
        return None
    serial_value = serial.strip()
    if not serial_value:
        return None
    encoded_icon = encode_icon_key(icon_path)
    if encoded_icon is None:
        return None
    return f"{ICON_PROXY_BASE_PATH}/{quote(serial_value, safe='')}/{encoded_icon}"
