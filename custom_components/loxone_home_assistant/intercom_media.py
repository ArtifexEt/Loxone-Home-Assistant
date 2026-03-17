"""Shared media helpers for the Loxone Intercom."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

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
INTERCOM_DYNAMIC_PAYLOAD_STATE_NAMES = (
    "videoInfo",
    "videoSettings",
    "videoSettingsExtern",
    "videoSettingsIntern",
)
INTERCOM_HISTORY_SELECTED_ATTR = "_intercom_selected_history_timestamps"


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


def intercom_history_image_url(
    bridge,
    control: LoxoneControl,
    raw_timestamp: str | None,
) -> str | None:
    text = _coerce_text(raw_timestamp)
    if text is None:
        return None
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
