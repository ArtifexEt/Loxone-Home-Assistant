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

from .intercom_signaling import IntercomSignaling, IntercomSignalingError, async_get_scoped_archive_photo
from .models import LoxoneControl

GEN2_ARCHIVE_SELECTION_PREFIX = "gen2:"
_LOGGER = logging.getLogger(__name__)
_ARCHIVE_LIMIT = 100
_ARCHIVE_TIMEOUT = 15
_ARCHIVE_REFRESH_INTERVAL = 25
_ARCHIVE_RETRY_INTERVAL = 3
_THUMBNAIL_DOWNLOAD_LIMIT = 3
_PHOTO_CACHE_INTERVAL = 15
_LATEST_TIMESTAMP = 253_402_300_799  # Last second representable by Python datetime.
_LOXONE_EPOCH = 1_230_768_000
_PHOTO_TOKEN_RETRY = 30
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class IntercomArchiveUnavailableError(Exception):
    """The latest attempt to read the Intercom photo archive failed."""


@dataclass(frozen=True, slots=True)
class IntercomArchiveEntry:
    """Publish a timestamp without exposing authentication or a media URL."""

    timestamp: int

    @property
    def selection_value(self) -> str:
        return f"{GEN2_ARCHIVE_SELECTION_PREFIX}{self.timestamp}"


@dataclass(frozen=True, slots=True)
class _ActivityPhotos:
    image: str
    thumbnail: str | None

    @property
    def preview_paths(self) -> tuple[str, ...]:
        if self.thumbnail and self.thumbnail != self.image:
            return self.thumbnail, self.image
        return (self.image,)


@dataclass(slots=True)
class _ArchiveCache:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refreshed_for: tuple[Any, Any] | None = None
    next_refresh: float = 0
    refresh_failed: bool = False
    entries: tuple[IntercomArchiveEntry, ...] = ()
    paths: dict[int, _ActivityPhotos] = field(default_factory=dict, repr=False)
    photo_timestamp: int | None = None
    photo_expires_at: float = 0
    photo: bytes | None = field(default=None, repr=False)


@dataclass(slots=True)
class _ArchiveAccess:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    thumbnail_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(_THUMBNAIL_DOWNLOAD_LIMIT))
    legacy_thumbnail_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
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


async def async_gen2_archive_entries(
    bridge, control: LoxoneControl, *, require_fresh: bool = False
) -> tuple[IntercomArchiveEntry, ...]:
    """Return archive timestamps, optionally requiring the last refresh to succeed."""
    if not is_gen2_archive_control(bridge, control):
        return ()

    cache = _archive_cache(bridge, control)
    async with cache.lock:
        hint = _refresh_hint(bridge, control)
        if monotonic() >= cache.next_refresh or (
            not cache.refresh_failed and hint != cache.refreshed_for
        ):
            try:
                async with asyncio.timeout(_ARCHIVE_TIMEOUT), _archive_connection(bridge, control) as signaling:
                    paths = await _async_activity_paths(signaling)
            except (IntercomSignalingError, OSError, TimeoutError):
                cache.refresh_failed = True
                cache.next_refresh = monotonic() + _ARCHIVE_RETRY_INTERVAL
            else:
                cache.paths = paths
                cache.entries = _entries_from_paths(paths)
                cache.refresh_failed = False
                cache.next_refresh = monotonic() + _ARCHIVE_REFRESH_INTERVAL
            cache.refreshed_for = hint

        if require_fresh and cache.refresh_failed:
            raise IntercomArchiveUnavailableError("The Intercom photo archive is temporarily unavailable.")
        return cache.entries


async def async_gen2_archive_image(bridge, control: LoxoneControl, selection_value: str) -> bytes | None:
    """Fetch the selected photo with a fresh private activity path and session token."""
    if not is_gen2_archive_control(bridge, control):
        return None
    timestamp = _selection_timestamp(selection_value)
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
                cache.paths = paths
                cache.entries = _entries_from_paths(paths)
                cache.refresh_failed = False
                cache.next_refresh = monotonic() + _ARCHIVE_REFRESH_INTERVAL
                cache.refreshed_for = hint
                activity = paths.get(timestamp)
                photo = await _async_download_photo(bridge, signaling, activity.image) if activity else None
        except (IntercomSignalingError, OSError, TimeoutError):
            photo = None

        cache.photo_timestamp = timestamp
        cache.photo_expires_at = monotonic() + _PHOTO_CACHE_INTERVAL
        cache.photo = photo
        return photo


async def async_gen2_archive_thumbnail(bridge, control: LoxoneControl, selection_value: str) -> bytes | None:
    """Fetch one private activity preview through the authenticated Miniserver."""
    if not is_gen2_archive_control(bridge, control) or not getattr(bridge, "token", None):
        return None
    timestamp = _selection_timestamp(selection_value)
    if timestamp is None:
        return None

    access = _archive_access(bridge)
    try:
        async with asyncio.timeout(_ARCHIVE_TIMEOUT), access.thumbnail_slots:
            await async_gen2_archive_entries(bridge, control)
            activity = _archive_cache(bridge, control).paths.get(timestamp)
            if activity is None:
                return None

            token = await _async_archive_token(bridge)
            if token is not None:
                for path in activity.preview_paths:
                    photo = await async_get_scoped_archive_photo(bridge, control.details["deviceUuid"], path, token)
                    if photo is not None:
                        return photo

            # Challenge authentication requires a live signaling session. Keep at
            # most one gallery challenge active on each Miniserver at a time.
            async with access.legacy_thumbnail_lock, _archive_connection(bridge, control) as signaling:
                paths = await _async_activity_paths(signaling)
                if (activity := paths.get(timestamp)) is not None:
                    for path in activity.preview_paths:
                        if (photo := await signaling.async_get_archive_photo(path)) is not None:
                            return photo
    except (IntercomSignalingError, OSError, TimeoutError):
        pass
    return None


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
    access = _archive_access(bridge)

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


def _archive_access(bridge) -> _ArchiveAccess:
    access = getattr(bridge, "_intercom_archive_access", None)
    if access is None:
        access = bridge._intercom_archive_access = _ArchiveAccess()
    return access


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


async def _async_activity_paths(signaling: IntercomSignaling) -> dict[int, _ActivityPhotos]:
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
        if timestamp is not None and isinstance(path, str) and 0 < len(path) <= 4096:
            thumbnail = item.get("thumbPath")
            if not isinstance(thumbnail, str) or not 0 < len(thumbnail) <= 4096:
                thumbnail = None
            paths.setdefault(timestamp, _ActivityPhotos(path, thumbnail))
    return paths


def _entries_from_paths(paths: dict[int, _ActivityPhotos]) -> tuple[IntercomArchiveEntry, ...]:
    return tuple(IntercomArchiveEntry(timestamp) for timestamp in sorted(paths, reverse=True))


def _selection_timestamp(selection_value: str) -> int | None:
    if selection_value.startswith(GEN2_ARCHIVE_SELECTION_PREFIX):
        return _coerce_timestamp(selection_value[len(GEN2_ARCHIVE_SELECTION_PREFIX) :])
    return None


def _coerce_timestamp(value: Any) -> int | None:
    if isinstance(value, str) and 1 <= len(value) <= 12 and value.isascii() and value.isdecimal():
        value = int(value)
    elif isinstance(value, float) and value.is_integer():
        value = int(value)
    if type(value) is int and 0 < value <= _LATEST_TIMESTAMP:
        return value
    return None
