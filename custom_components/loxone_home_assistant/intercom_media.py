"""Shared media helpers for the Loxone Intercom."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

try:
    from aiohttp import BasicAuth, ClientError
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    class ClientError(Exception):
        """Fallback network error type used in tests without aiohttp."""

    class BasicAuth:  # type: ignore[no-redef]
        """Fallback auth container used in tests without aiohttp."""

        def __init__(self, login: str, password: str) -> None:
            self.login = login
            self.password = password

from .entity import normalize_state_name
from .intercom import (
    intercom_address_state_name,
    intercom_last_bell_events_state_name,
    intercom_last_bell_timestamp_state_name,
    resolve_intercom_http_url,
)
from .models import LoxoneControl

STREAM_DETAIL_PATHS = (
    "videoInfo.streamUrl",
    "videoInfo.streamUrlExtern",
    "videoInfo.streamUrlIntern",
    "streamUrl",
)
SNAPSHOT_DETAIL_PATHS = (
    "videoInfo.alertImage",
    "videoInfo.liveImageUrl",
    "videoInfo.liveImage",
    "videoInfo.imageUrl",
    "alertImage",
    "liveImageUrl",
    "liveImage",
    "imageUrl",
)
LAST_BELL_EVENTS_DETAIL_PATHS = (
    "lastBellEvents",
    "videoInfo.lastBellEvents",
)
HISTORY_URL_DETAIL_PATHS = (
    "eventHistoryUrl",
    "videoInfo.eventHistoryUrl",
    "securedDetails.videoInfo.eventHistoryUrl",
    "videoSettings.eventHistoryUrl",
    "videoSettingsExtern.eventHistoryUrl",
    "videoSettingsIntern.eventHistoryUrl",
)
INTERCOM_DYNAMIC_PAYLOAD_STATE_NAMES = (
    "videoInfo",
    "videoSettings",
    "videoSettingsExtern",
    "videoSettingsIntern",
)
INTERCOM_HISTORY_SELECTED_ATTR = "_intercom_selected_history_timestamps"
INTERCOM_HISTORY_DIRECT_URL_PREFIX = "url:"
INTERCOM_HISTORY_URL_KEY_HINTS = (
    "history",
    "event",
    "bell",
    "answer",
    "record",
    "last",
)
INTERCOM_EVENT_COLLECTION_PATHS = (
    "events",
    "items",
    "entries",
    "records",
    "answers",
    "calls",
    "ringEvents",
    "history",
    "lastBellEvents",
    "value",
    "result",
)
INTERCOM_EVENT_IMAGE_KEY_CANDIDATES = (
    "imageUrl",
    "imagePath",
    "image",
    "alertImage",
    "snapshot",
    "photo",
    "thumb",
    "thumbPath",
    "thumbnail",
    "thumbnailUrl",
    "snapshotUrl",
    "photoUrl",
    "liveImageUrl",
)
INTERCOM_EVENT_TIMESTAMP_KEY_CANDIDATES = (
    "timestamp",
    "ts",
    "time",
    "eventTime",
    "historyDate",
    "date",
    "created",
    "createdAt",
    "recordedAt",
    "snapshotTime",
    "lastBellTimestamp",
)


@dataclass(frozen=True)
class IntercomHistoryEntry:
    """One Loxone Intercom history image reference."""

    raw_timestamp: str
    image_url: str
    event_time: datetime | None


def intercom_auth_credentials(bridge) -> tuple[str | None, str]:
    """Return HTTP auth for the Intercom camera itself."""
    configured_username = _coerce_text(getattr(bridge, "intercom_username", None))
    configured_password = getattr(bridge, "intercom_password", None)
    default_username = _coerce_text(getattr(bridge, "username", None))
    default_password = getattr(bridge, "password", None)

    username = configured_username or default_username
    if username is None:
        return None, ""

    password_source = configured_password if configured_password is not None else default_password
    password = "" if password_source is None else str(password_source)
    return username, password


def miniserver_auth_credentials(bridge) -> tuple[str | None, str]:
    """Return HTTP auth for Miniserver-served image endpoints."""
    username = _coerce_text(getattr(bridge, "username", None))
    if username is None:
        return None, ""
    password = getattr(bridge, "password", None)
    return username, "" if password is None else str(password)


def intercom_stream_url(
    bridge,
    control: LoxoneControl,
    *,
    secured_details: Mapping[str, Any] | None = None,
    state_value_getter,
) -> str | None:
    return _resolve_intercom_media_url(
        bridge,
        control,
        detail_paths=STREAM_DETAIL_PATHS,
        secured_details=secured_details,
        state_value_getter=state_value_getter,
    )


def intercom_snapshot_url(
    bridge,
    control: LoxoneControl,
    *,
    secured_details: Mapping[str, Any] | None = None,
    state_value_getter,
) -> str | None:
    return _resolve_intercom_media_url(
        bridge,
        control,
        detail_paths=SNAPSHOT_DETAIL_PATHS,
        secured_details=secured_details,
        state_value_getter=state_value_getter,
    )


def intercom_last_bell_events(
    bridge,
    control: LoxoneControl,
    *,
    secured_details: Mapping[str, Any] | None = None,
    state_value_getter,
) -> tuple[str, ...]:
    state_name = intercom_last_bell_events_state_name(control)
    if state_name is not None:
        from_state = _parse_last_bell_events(state_value_getter(state_name))
        if from_state:
            return from_state

    for payload_state_name in _dynamic_payload_state_names(control):
        raw_value = _payload_value(
            state_value_getter(payload_state_name),
            LAST_BELL_EVENTS_DETAIL_PATHS,
        )
        parsed = _parse_last_bell_events(raw_value)
        if parsed:
            return parsed

    raw_details_value = _mapping_value(control.details, LAST_BELL_EVENTS_DETAIL_PATHS)
    parsed = _parse_last_bell_events(raw_details_value)
    if parsed:
        return parsed

    if secured_details is not None:
        parsed = _parse_last_bell_events(
            _mapping_value(secured_details, LAST_BELL_EVENTS_DETAIL_PATHS),
        )
        if parsed:
            return parsed

    return ()


def intercom_last_bell_timestamp(
    bridge,
    control: LoxoneControl,
    *,
    last_bell_events: tuple[str, ...] | None = None,
    state_value_getter,
) -> datetime | None:
    history_tokens = last_bell_events or intercom_last_bell_events(
        bridge,
        control,
        state_value_getter=state_value_getter,
    )
    if history_tokens:
        return coerce_intercom_datetime(history_tokens[0])

    state_name = intercom_last_bell_timestamp_state_name(control)
    if state_name is not None:
        return coerce_intercom_datetime(state_value_getter(state_name))
    return None


def intercom_history_entries(
    bridge,
    control: LoxoneControl,
    *,
    last_bell_events: tuple[str, ...] | None = None,
    state_value_getter,
) -> tuple[IntercomHistoryEntry, ...]:
    history_tokens = last_bell_events or intercom_last_bell_events(
        bridge,
        control,
        state_value_getter=state_value_getter,
    )
    entries: list[IntercomHistoryEntry] = []
    for raw_timestamp in history_tokens:
        image_url = intercom_history_image_url(bridge, control, raw_timestamp)
        if image_url is None:
            continue
        entries.append(
            IntercomHistoryEntry(
                raw_timestamp=raw_timestamp,
                image_url=image_url,
                event_time=coerce_intercom_datetime(raw_timestamp),
            )
        )
    return tuple(entries)


async def async_intercom_history_entries(
    bridge,
    control: LoxoneControl,
    *,
    secured_details: Mapping[str, Any] | None = None,
    last_bell_events: tuple[str, ...] | None = None,
    state_value_getter,
) -> tuple[IntercomHistoryEntry, ...]:
    """Return merged Intercom history from `lastBellEvents` and event-history payloads."""
    merged: dict[str, IntercomHistoryEntry] = {}

    for entry in intercom_history_entries(
        bridge,
        control,
        last_bell_events=last_bell_events,
        state_value_getter=state_value_getter,
    ):
        merged.setdefault(_history_entry_key(entry), entry)

    history_payload_url = intercom_history_payload_url(
        bridge,
        control,
        secured_details=secured_details,
        state_value_getter=state_value_getter,
    )
    if history_payload_url is None:
        return _sorted_history_entries(merged.values())

    payload = await _async_fetch_json(bridge, history_payload_url)
    if payload is None:
        return _sorted_history_entries(merged.values())

    address_value = _address_state_value(bridge, control)
    for event_entry in _history_entries_from_payload(
        payload,
        bridge,
        control,
        address_value=address_value,
    ):
        key = _history_entry_key(event_entry)
        existing = merged.get(key)
        if existing is None:
            merged[key] = event_entry
            continue
        if existing.event_time is None and event_entry.event_time is not None:
            merged[key] = event_entry

    return _sorted_history_entries(merged.values())


def intercom_history_payload_url(
    bridge,
    control: LoxoneControl,
    *,
    secured_details: Mapping[str, Any] | None = None,
    state_value_getter,
) -> str | None:
    resolved = _resolve_intercom_media_url(
        bridge,
        control,
        detail_paths=HISTORY_URL_DETAIL_PATHS,
        secured_details=secured_details,
        state_value_getter=state_value_getter,
    )
    if resolved is not None:
        return resolved

    address_value = _address_state_value(bridge, control)
    resolved = _resolve_url_from_payload_with_key_hints(
        bridge,
        control,
        control.details,
        key_hints=INTERCOM_HISTORY_URL_KEY_HINTS,
        address_value=address_value,
    )
    if resolved is not None:
        return resolved

    if secured_details is not None:
        resolved = _resolve_url_from_payload_with_key_hints(
            bridge,
            control,
            secured_details,
            key_hints=INTERCOM_HISTORY_URL_KEY_HINTS,
            address_value=address_value,
        )
        if resolved is not None:
            return resolved

    for payload_state_name in _dynamic_payload_state_names(control):
        resolved = _resolve_url_from_payload_with_key_hints(
            bridge,
            control,
            state_value_getter(payload_state_name),
            key_hints=INTERCOM_HISTORY_URL_KEY_HINTS,
            address_value=address_value,
        )
        if resolved is not None:
            return resolved

    return None


def intercom_history_image_url(
    bridge,
    control: LoxoneControl,
    raw_timestamp: str | None,
) -> str | None:
    text = _coerce_text(raw_timestamp)
    if text is None:
        return None
    if text.startswith(INTERCOM_HISTORY_DIRECT_URL_PREFIX):
        return resolve_intercom_http_url(
            bridge,
            control,
            text[len(INTERCOM_HISTORY_DIRECT_URL_PREFIX) :],
            address_value=_address_state_value(bridge, control),
        )
    return bridge.resolve_http_url(
        f"camimage/{quote(control.uuid_action, safe='')}/{quote(text, safe='')}"
    )


def intercom_selected_history_timestamp(bridge, uuid_action: str) -> str | None:
    selected = getattr(bridge, INTERCOM_HISTORY_SELECTED_ATTR, None)
    if not isinstance(selected, dict):
        return None
    return _coerce_text(selected.get(str(uuid_action)))


def set_intercom_selected_history_timestamp(
    bridge,
    uuid_action: str,
    raw_timestamp: str | None,
) -> None:
    selected = getattr(bridge, INTERCOM_HISTORY_SELECTED_ATTR, None)
    if not isinstance(selected, dict):
        selected = {}
        setattr(bridge, INTERCOM_HISTORY_SELECTED_ATTR, selected)

    text = _coerce_text(raw_timestamp)
    if text is None:
        selected.pop(str(uuid_action), None)
        return
    selected[str(uuid_action)] = text


def coerce_intercom_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            if len(raw) == 14:
                return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            return coerce_intercom_datetime(float(raw))
        iso_candidate = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(iso_candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _resolve_intercom_media_url(
    bridge,
    control: LoxoneControl,
    *,
    detail_paths: tuple[str, ...],
    secured_details: Mapping[str, Any] | None,
    state_value_getter,
) -> str | None:
    address_value = _address_state_value(bridge, control)

    for state_name in _matching_state_names(control, detail_paths):
        resolved = resolve_intercom_http_url(
            bridge,
            control,
            state_value_getter(state_name),
            address_value=address_value,
        )
        if resolved is not None:
            return resolved

    for payload_state_name in _dynamic_payload_state_names(control):
        resolved = resolve_intercom_http_url(
            bridge,
            control,
            _payload_value(state_value_getter(payload_state_name), detail_paths),
            address_value=address_value,
        )
        if resolved is not None:
            return resolved

    resolved = resolve_intercom_http_url(
        bridge,
        control,
        _mapping_value(control.details, detail_paths),
        address_value=address_value,
    )
    if resolved is not None:
        return resolved

    if secured_details is None:
        return None
    return resolve_intercom_http_url(
        bridge,
        control,
        _mapping_value(secured_details, detail_paths),
        address_value=address_value,
    )


def _address_state_value(bridge, control: LoxoneControl) -> Any:
    state_name = intercom_address_state_name(control)
    if state_name is None:
        return None
    return bridge.control_state(control, state_name)


def _matching_state_names(
    control: LoxoneControl,
    detail_paths: tuple[str, ...],
) -> tuple[str, ...]:
    wanted = {normalize_state_name(path.rsplit(".", 1)[-1]) for path in detail_paths}
    return tuple(
        state_name
        for state_name in control.states
        if normalize_state_name(state_name) in wanted
    )


def _dynamic_payload_state_names(control: LoxoneControl) -> tuple[str, ...]:
    return tuple(
        state_name
        for state_name in INTERCOM_DYNAMIC_PAYLOAD_STATE_NAMES
        if state_name in control.states
    )


def _parse_last_bell_events(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split("|") if part.strip()]
        parts.reverse()
        return tuple(parts)
    if isinstance(value, list):
        parts = [_coerce_text(item) for item in value]
        return tuple(part for part in parts if part is not None)
    if isinstance(value, Mapping):
        return _parse_last_bell_events(value.get("text") or value.get("value"))
    return ()


def _mapping_value(mapping: Any, detail_paths: tuple[str, ...]) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    for detail_path in detail_paths:
        current: Any = mapping
        for part in detail_path.split("."):
            if not isinstance(current, Mapping):
                current = None
                break
            current = _mapping_get_case_insensitive(current, part)
        if current is not None:
            return current
    return None


def _payload_value(payload: Any, detail_paths: tuple[str, ...]) -> Any:
    if isinstance(payload, list):
        normalized_names = {
            normalize_state_name(detail_path.rsplit(".", 1)[-1]) for detail_path in detail_paths
        }
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            name = _coerce_text(item.get("name") or item.get("key") or item.get("id"))
            if name is None or normalize_state_name(name) not in normalized_names:
                continue
            for key in ("value", "url", "path", "src", "href"):
                value = item.get(key)
                if value is not None:
                    return value
        return None
    return _mapping_value(payload, detail_paths)


def _history_entry_key(entry: IntercomHistoryEntry) -> str:
    return entry.raw_timestamp or entry.image_url


def _sorted_history_entries(
    entries: Any,
) -> tuple[IntercomHistoryEntry, ...]:
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                entry.event_time is not None,
                entry.event_time or datetime.min.replace(tzinfo=timezone.utc),
                entry.raw_timestamp,
                entry.image_url,
            ),
            reverse=True,
        )
    )


def _history_entries_from_payload(
    payload: Any,
    bridge,
    control: LoxoneControl,
    *,
    address_value: Any = None,
) -> tuple[IntercomHistoryEntry, ...]:
    entries: list[IntercomHistoryEntry] = []
    for event in _extract_intercom_events(
        payload,
        bridge,
        control,
        address_value=address_value,
    ):
        image_url = event["image_url"]
        if image_url is None:
            continue
        selection_value = _event_selection_value(event, image_url)
        if selection_value is None:
            continue
        entries.append(
            IntercomHistoryEntry(
                raw_timestamp=selection_value,
                image_url=image_url,
                event_time=event["timestamp"],
            )
        )
    return _sorted_history_entries(entries)


async def _async_fetch_json(bridge, url: str) -> Any | None:
    session = getattr(bridge, "_session", None)
    if session is None:
        return None

    request_auth = _request_auth_for_url(bridge, url)
    try:
        async with session.get(url, auth=request_auth) as response:
            response.raise_for_status()
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    return await response_json(content_type=None)
                except TypeError:
                    return await response_json()
                except ValueError:
                    pass
            response_text = getattr(response, "text", None)
            if callable(response_text):
                raw_text = await response_text()
            else:
                raw_payload = await response.read()
                raw_text = raw_payload.decode("utf-8")
            return json.loads(raw_text)
    except (ClientError, TimeoutError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None


def _request_auth_for_url(bridge, url: str) -> BasicAuth | None:
    if _is_miniserver_url(bridge, url):
        username, password = miniserver_auth_credentials(bridge)
    else:
        username, password = intercom_auth_credentials(bridge)
    if username is None:
        return None
    return BasicAuth(username, password)


def _is_miniserver_url(bridge, url: str) -> bool:
    parsed = urlsplit(url)
    if not parsed.hostname:
        return False
    if parsed.hostname != str(getattr(bridge, "host", "")):
        return False

    bridge_port = int(getattr(bridge, "port", 0) or 0)
    if parsed.port is not None:
        return parsed.port == bridge_port

    default_port = 443 if bool(getattr(bridge, "use_tls", True)) else 80
    return bridge_port == default_port


def _resolve_url_from_payload_with_key_hints(
    bridge,
    control: LoxoneControl,
    payload: Any,
    *,
    key_hints: tuple[str, ...],
    address_value: Any = None,
) -> str | None:
    if payload is None:
        return None

    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return None
        if raw.startswith("{") or raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except ValueError:
                return resolve_intercom_http_url(
                    bridge,
                    control,
                    raw,
                    address_value=address_value,
                )
            return _resolve_url_from_payload_with_key_hints(
                bridge,
                control,
                parsed,
                key_hints=key_hints,
                address_value=address_value,
            )
        return resolve_intercom_http_url(
            bridge,
            control,
            raw,
            address_value=address_value,
        )

    stack: list[Any] = [payload]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        if isinstance(current, list):
            stack.extend(current)
            continue
        if not isinstance(current, Mapping):
            continue

        indicator_value = _first_mapping_value(
            current,
            ("name", "key", "id", "field", "type", "label"),
        )
        candidate_value = _first_mapping_value(
            current,
            ("url", "path", "href", "src", "value", "data"),
        )
        indicator_text = normalize_state_name(str(indicator_value)) if indicator_value else ""
        if indicator_text and any(hint in indicator_text for hint in key_hints):
            resolved = resolve_intercom_http_url(
                bridge,
                control,
                candidate_value,
                address_value=address_value,
            )
            if resolved is not None:
                return resolved

        for key, value in current.items():
            normalized_key = normalize_state_name(str(key))
            key_matches_hints = any(hint in normalized_key for hint in key_hints)

            if isinstance(value, Mapping):
                if key_matches_hints:
                    nested_candidate = _first_mapping_value(
                        value,
                        ("url", "path", "href", "src", "value", "data"),
                    )
                    resolved = resolve_intercom_http_url(
                        bridge,
                        control,
                        nested_candidate,
                        address_value=address_value,
                    )
                    if resolved is not None:
                        return resolved
                stack.append(value)
                continue
            if isinstance(value, list):
                stack.append(value)
                continue

            if key_matches_hints:
                resolved = resolve_intercom_http_url(
                    bridge,
                    control,
                    value,
                    address_value=address_value,
                )
                if resolved is not None:
                    return resolved

            if key_matches_hints and isinstance(value, str):
                stack.append(value)
                continue

            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    stack.append(stripped)

    return None


def _extract_intercom_events(
    payload: Any,
    bridge,
    control: LoxoneControl,
    *,
    address_value: Any = None,
) -> list[dict[str, Any]]:
    events = _find_event_mappings(payload)
    normalized: list[dict[str, Any]] = []
    for event in events:
        timestamp = _event_timestamp(event)
        image_value = _first_mapping_value(event, INTERCOM_EVENT_IMAGE_KEY_CANDIDATES)
        image_url = resolve_intercom_http_url(
            bridge,
            control,
            image_value,
            address_value=address_value,
        )
        if timestamp is None and image_url is None:
            continue
        normalized.append(
            {
                "timestamp": timestamp,
                "raw_timestamp": _coerce_text(
                    _first_mapping_value(event, INTERCOM_EVENT_TIMESTAMP_KEY_CANDIDATES)
                ),
                "image_url": image_url,
            }
        )

    normalized.sort(
        key=lambda item: (
            item["timestamp"] is not None,
            item["timestamp"] or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return normalized


def _find_event_mappings(raw: Any) -> list[Mapping[str, Any]]:
    stack: list[Any] = [raw]
    event_mappings: list[Mapping[str, Any]] = []
    seen: set[int] = set()

    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        if isinstance(current, list):
            stack.extend(current)
            continue
        if not isinstance(current, Mapping):
            continue

        if _looks_like_intercom_event_mapping(current):
            event_mappings.append(current)

        for key in INTERCOM_EVENT_COLLECTION_PATHS:
            nested = _mapping_get_case_insensitive(current, key)
            if nested is not None:
                stack.append(nested)

        stack.extend(current.values())

    return event_mappings


def _looks_like_intercom_event_mapping(value: Mapping[str, Any]) -> bool:
    if _first_mapping_value(value, INTERCOM_EVENT_IMAGE_KEY_CANDIDATES) is not None:
        return True
    if _first_mapping_value(value, INTERCOM_EVENT_TIMESTAMP_KEY_CANDIDATES) is not None:
        return True
    return False


def _event_timestamp(event: Mapping[str, Any]) -> datetime | None:
    timestamp_raw = _first_mapping_value(event, INTERCOM_EVENT_TIMESTAMP_KEY_CANDIDATES)
    return coerce_intercom_datetime(timestamp_raw)


def _event_selection_value(event: Mapping[str, Any], image_url: str) -> str | None:
    raw_timestamp = _coerce_text(event.get("raw_timestamp"))
    extracted = _timestamp_from_camimage_url(image_url)
    if extracted is not None:
        return extracted
    if raw_timestamp is not None and coerce_intercom_datetime(raw_timestamp) is not None:
        return raw_timestamp
    return f"{INTERCOM_HISTORY_DIRECT_URL_PREFIX}{image_url}"


def _timestamp_from_camimage_url(image_url: str) -> str | None:
    if "/camimage/" not in image_url:
        return None
    candidate = image_url.rstrip("/").rsplit("/", 1)[-1]
    if not candidate:
        return None
    return candidate if coerce_intercom_datetime(candidate) is not None else None


def _first_mapping_value(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = _mapping_get_case_insensitive(mapping, key)
        if value is not None:
            return value
    return None


def _mapping_get_case_insensitive(mapping: Mapping[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]

    wanted = normalize_state_name(key)
    for current_key, value in mapping.items():
        if isinstance(current_key, str) and normalize_state_name(current_key) == wanted:
            return value
    return None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
