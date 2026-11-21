"""Read the native Gen 2 Intercom photo archive through the Miniserver proxy."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic, time
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from .intercom_signaling import IntercomSignaling, IntercomSignalingError
from .models import LoxoneControl

GEN2_ARCHIVE_SELECTION_PREFIX = "gen2:"
_LOGGER = logging.getLogger(__name__)
_ARCHIVE_LIMIT = 100
_ARCHIVE_TIMEOUT = 15
_ARCHIVE_REFRESH_INTERVAL = 25
_PHOTO_CACHE_INTERVAL = 15
_LATEST_TIMESTAMP = 253_402_300_799  # Last second representable by Python datetime.
_LOXONE_EPOCH = 1_230_768_000
_PHOTO_TOKEN_RETRY = 30
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


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


@dataclass(slots=True)
class _ArchiveAccess:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expires_at: float = 0
    token: str | None = field(default=None, repr=False)


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
                photo = await _async_download_photo(bridge, signaling, path) if path is not None else None
        except (IntercomSignalingError, OSError, TimeoutError):
            photo = None

        cache.photo_timestamp = timestamp
        cache.photo_expires_at = monotonic() + _PHOTO_CACHE_INTERVAL
        cache.photo = photo
        return photo


async def _async_download_photo(bridge, signaling: IntercomSignaling, path: str) -> bytes | None:
    token = await _async_archive_token(bridge)
    if token is not None:
        photo = await signaling.async_get_archive_photo(path, auth_token=token)
        if photo is not None:
            return photo
    return await signaling.async_get_archive_photo(path)


async def _async_archive_token(bridge) -> str | None:
    send = getattr(bridge, "async_send_raw_command", None)
    client_uuid = getattr(bridge, "client_uuid", None)
    username = getattr(bridge, "username", None)
    if send is None or not client_uuid or not isinstance(username, str):
        return None
    access = getattr(bridge, "_intercom_archive_access", None)
    if access is None:
        access = bridge._intercom_archive_access = _ArchiveAccess()

    async with access.lock:
        if monotonic() < access.expires_at:
            return access.token
        access.token = None
        access.expires_at = monotonic() + _PHOTO_TOKEN_RETRY
        username = quote(username.strip(), safe="")
        # Keep this permission-0 credential separate from the integration's login token.
        parts = str(uuid5(NAMESPACE_URL, f"loxone-intercom-archive:{client_uuid}")).split("-")
        archive_uuid = "-".join((*parts[:3], parts[3] + parts[4]))
        try:
            # The vendor uses a fresh getkey challenge for an existing login token.
            key = (await send("jdev/sys/getkey")).get("value")
            if not isinstance(key, str) or not re.fullmatch(r"(?:[\da-fA-F]{2}){1,128}", key, re.ASCII):
                _LOGGER.debug("Miniserver returned an invalid Loxone Intercom photo token challenge")
                return None
            if not bridge.token:
                return None
            proof = hmac.digest(bytes.fromhex(key), bridge.token.encode(), "sha256").hex()
            response = await send(
                f"jdev/sys/getjwt/{proof}/{username}/0/{archive_uuid}/"
                "Home%20Assistant%20Intercom?tokenHash=true"
            )
        except Exception:  # The bridge may raise connection/authentication errors or return a malformed challenge.
            _LOGGER.debug("Could not request a scoped Loxone Intercom photo token")
            return None

        value = response.get("value")
        if not isinstance(value, Mapping):
            return None
        token = value.get("token")
        payload = _archive_token_payload(token)
        if payload is None or value.get("tokenRights", 0) not in (0, "0"):
            _LOGGER.debug("Miniserver did not provide a scoped Loxone Intercom photo token")
            return None

        expires_at = value.get("validUntil")
        try:
            duration = int(expires_at) + _LOXONE_EPOCH - time() - 10
        except (TypeError, ValueError, OverflowError):
            duration = _PHOTO_TOKEN_RETRY
        if duration <= 0:
            return None
        access.token = token
        access.expires_at = monotonic() + duration
        _LOGGER.debug("Miniserver issued a scoped Loxone Intercom photo token")
        return token


def _archive_token_payload(token: Any) -> Mapping[str, Any] | None:
    if not isinstance(token, str) or len(token) > 8192 or not _JWT.fullmatch(token):
        return None
    payload = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if (
        not isinstance(claims, Mapping)
        or not claims.get("msSerial")
        or claims.get("tokenRights", 0) not in (0, "0")
    ):
        return None
    return claims


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
