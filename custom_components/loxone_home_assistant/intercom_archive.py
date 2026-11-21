"""Read the native Gen 2 Intercom photo archive through the Miniserver proxy."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from .intercom_signaling import IntercomSignaling, IntercomSignalingError
from .models import LoxoneControl

GEN2_ARCHIVE_SELECTION_PREFIX = "gen2:"
_ARCHIVE_LIMIT = 100
_ARCHIVE_TIMEOUT = 15
_ARCHIVE_REFRESH_INTERVAL = 25
_PHOTO_CACHE_INTERVAL = 15
_LATEST_TIMESTAMP = 253_402_300_799  # Last second representable by Python datetime.


@dataclass(frozen=True, slots=True)
class IntercomArchiveEntry:
    """Publish a timestamp without exposing authentication or a media URL."""

    timestamp: int

    @property
    def selection_value(self) -> str:
        return f"{GEN2_ARCHIVE_SELECTION_PREFIX}{self.timestamp}"


@dataclass(slots=True)
class _ArchiveCache:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refreshed_for: tuple[Any, Any] | None = None
    next_refresh: float = 0
    entries: tuple[IntercomArchiveEntry, ...] = ()
    photo_timestamp: int | None = None
    photo_expires_at: float = 0
    photo: bytes | None = None


def is_gen2_archive_control(bridge, control: LoxoneControl) -> bool:
    """Return whether the control can use the local authenticated archive."""
    device_uuid = control.details.get("deviceUuid")
    return (
        control.type.casefold() == "intercomv2"
        and isinstance(device_uuid, str)
        and bool(device_uuid)
        and not control.details.get("trustMember")
        and bool(getattr(bridge, "use_tls", False))
        and not getattr(bridge, "_remote_serial", None)
    )


async def async_gen2_archive_entries(bridge, control: LoxoneControl) -> tuple[IntercomArchiveEntry, ...]:
    """Return the native archive's timestamps, sharing refreshes between entities."""
    if not is_gen2_archive_control(bridge, control):
        return ()

    cache = _archive_cache(bridge, control)
    async with cache.lock:
        hint = _refresh_hint(bridge, control)
        if monotonic() < cache.next_refresh and hint == cache.refreshed_for:
            return cache.entries

        try:
            async with asyncio.timeout(_ARCHIVE_TIMEOUT), _archive_connection(bridge, control) as signaling:
                paths = await _async_activity_paths(signaling)
        except (IntercomSignalingError, OSError, TimeoutError):
            pass
        else:
            cache.entries = _entries_from_paths(paths)
        cache.next_refresh = monotonic() + _ARCHIVE_REFRESH_INTERVAL
        cache.refreshed_for = hint
        return cache.entries


async def async_gen2_archive_image(bridge, control: LoxoneControl, selection_value: str) -> bytes | None:
    """Fetch the selected photo with a fresh private activity path and session token."""
    if not is_gen2_archive_control(bridge, control):
        return None
    if not selection_value.startswith(GEN2_ARCHIVE_SELECTION_PREFIX):
        return None
    timestamp = _coerce_timestamp(selection_value[len(GEN2_ARCHIVE_SELECTION_PREFIX) :])
    if timestamp is None:
        return None

    cache = _archive_cache(bridge, control)
    async with cache.lock:
        if timestamp == cache.photo_timestamp and monotonic() < cache.photo_expires_at:
            return cache.photo

        hint = _refresh_hint(bridge, control)
        try:
            async with asyncio.timeout(_ARCHIVE_TIMEOUT), _archive_connection(bridge, control) as signaling:
                paths = await _async_activity_paths(signaling)
                cache.entries = _entries_from_paths(paths)
                cache.next_refresh = monotonic() + _ARCHIVE_REFRESH_INTERVAL
                cache.refreshed_for = hint
                path = paths.get(timestamp)
                photo = await signaling.async_get_archive_photo(path) if path is not None else None
        except (IntercomSignalingError, OSError, TimeoutError):
            photo = None

        cache.photo_timestamp = timestamp
        cache.photo_expires_at = monotonic() + _PHOTO_CACHE_INTERVAL
        cache.photo = photo
        return photo


def _archive_cache(bridge, control: LoxoneControl) -> _ArchiveCache:
    archives = getattr(bridge, "_intercom_archive_cache", None)
    if archives is None:
        archives = bridge._intercom_archive_cache = {}
    key = (control.uuid_action, control.details["deviceUuid"])
    cache = archives.get(key)
    if cache is None:
        cache = archives[key] = _ArchiveCache()
    return cache


def _refresh_hint(bridge, control: LoxoneControl) -> tuple[Any, Any]:
    state = getattr(bridge, "control_state", None)
    if state is None:
        return None, None
    return state(control, "bell"), state(control, "lastBellUnix")


@asynccontextmanager
async def _archive_connection(bridge, control: LoxoneControl) -> AsyncIterator[IntercomSignaling]:
    if not getattr(bridge, "token", None):
        raise IntercomSignalingError("No authenticated Miniserver token is available.")
    signaling = await IntercomSignaling.async_connect(bridge, control.details["deviceUuid"])
    try:
        yield signaling
    finally:
        await signaling.async_close()


async def _async_activity_paths(signaling: IntercomSignaling) -> dict[int, str]:
    # The modern vendor command includes its activity response format version.
    try:
        records = await signaling.async_request("getLastActivities", [0, _ARCHIVE_LIMIT, 2])
    except IntercomSignalingError as err:
        if err.code not in {-32602, 400}:
            raise
        records = await signaling.async_request("getLastActivities", [0, _ARCHIVE_LIMIT])
    if not isinstance(records, list):
        raise IntercomSignalingError("The Intercom returned an invalid archive response.")

    paths = {}
    for item in records[:_ARCHIVE_LIMIT]:
        if not isinstance(item, Mapping) or item.get("error") not in (None, 0, "0"):
            continue
        timestamp = _coerce_timestamp(item.get("date"))
        path = item.get("imagePath")
        if timestamp is not None and isinstance(path, str) and path:
            paths.setdefault(timestamp, path)
    return paths


def _entries_from_paths(paths: dict[int, str]) -> tuple[IntercomArchiveEntry, ...]:
    return tuple(IntercomArchiveEntry(timestamp) for timestamp in sorted(paths, reverse=True))


def _coerce_timestamp(value: Any) -> int | None:
    if isinstance(value, str) and 1 <= len(value) <= 12 and value.isascii() and value.isdecimal():
        value = int(value)
    elif isinstance(value, float) and value.is_integer():
        value = int(value)
    if type(value) is int and 0 < value <= _LATEST_TIMESTAMP:
        return value
    return None
