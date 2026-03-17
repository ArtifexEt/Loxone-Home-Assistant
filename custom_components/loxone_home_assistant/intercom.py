"""Shared Intercom helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlsplit

from .const import (
    DOORBELL_STATE_CANDIDATES,
    INTERCOM_CALL_STATE_CANDIDATES,
    INTERCOM_CONTROL_TYPES,
    INTERCOM_HISTORY_STATE_CANDIDATES,
    INTERCOM_LIGHT_STATE_CANDIDATES,
    INTERCOM_PROXIMITY_STATE_CANDIDATES,
    INTERCOM_SYSTEM_SCHEMA_NAME_HINTS,
)
from .entity import first_matching_state_name, normalize_state_name
from .models import LoxoneControl

INTERCOM_TYPE_HINTS = (
    "intercom",
    "doorcontroller",
    "doorstation",
    "door station",
    "doorbell",
    "wideodomofon",
    "dzwonek",
    "bramofon",
)
INTERCOM_TYPE_HINTS_NORMALIZED = {
    normalize_state_name(value) for value in (*INTERCOM_CONTROL_TYPES, *INTERCOM_TYPE_HINTS)
}
INTERCOM_VIDEO_STATE_CANDIDATES = (
    "streamUrl",
    "videoStream",
    "videoUrl",
    "alertImage",
    "liveImage",
    "snapshot",
)

_DOORBELL_HINTS = ("bell", "ring", "doorbell", "call", "dzwonek")
_PROXIMITY_HINTS = ("prox", "near", "approach", "person", "presence", "motion", "distance")
_CALL_HINTS = ("call", "talk", "conversation", "voice")
_LIGHT_HINTS = ("light", "illum", "led", "flash", "flood")
_HISTORY_HINTS = ("lastbell", "history", "event", "snapshot", "answer", "record")
_ADDRESS_HINTS = ("address", "host", "hostname", "ip", "ipaddr", "ipaddress")
_ADDRESS_STATE_CANDIDATES = (
    "trustAddress",
    "address",
    "ipAddress",
    "deviceAddress",
    "deviceIp",
    "host",
    "hostname",
)
_MAC_HEX_CHARS = set("0123456789abcdefABCDEF")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_INTERCOM_COMMAND_SPECS: dict[str, tuple[int, int | None, tuple[int, ...]]] = {
    "answer": (0, 0, ()),
    "playTts": (1, 1, ()),
    "mute": (1, 1, (0,)),
    "setAnswers": (1, None, ()),
    "setallvideosettings": (4, 4, (0, 1, 2, 3)),
    "setvideosettings": (3, 3, (0, 1, 2)),
    "setframerate": (2, 2, (0, 1)),
    "setresolution": (2, 2, (0, 1)),
    "getnumberbellimages": (0, 0, ()),
    "setnumberbellimages": (1, 1, (0,)),
}
_INTERCOM_COMMAND_ALIASES: dict[str, str] = {}
for _canonical in _INTERCOM_COMMAND_SPECS:
    _INTERCOM_COMMAND_ALIASES[_NON_ALNUM_RE.sub("", _canonical.casefold())] = _canonical
_INTERCOM_COMMAND_ALIASES.update(
    {
        "tts": "playTts",
        "playtts": "playTts",
        "unmute": "mute",
        "setanswer": "setAnswers",
        "setallanswer": "setAnswers",
    }
)
_INTERCOM_COMMAND_ALIAS_DEFAULT_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "unmute": ("0",),
}
INTERCOM_DEFAULT_TTS_MESSAGE = ""
INTERCOM_DEFAULT_TTS_VOLUME = 60

def is_intercom_control(control: LoxoneControl) -> bool:
    """Return True when a control looks like an intercom/door station."""
    normalized_type = normalize_state_name(control.type)
    if normalized_type in INTERCOM_TYPE_HINTS_NORMALIZED:
        return True
    if any(hint in normalized_type for hint in INTERCOM_TYPE_HINTS_NORMALIZED):
        return True

    normalized_name = normalize_state_name(control.name)
    if any(hint in normalized_name for hint in INTERCOM_TYPE_HINTS_NORMALIZED):
        return True

    normalized_states = {normalize_state_name(name) for name in control.states}
    strong_signal_candidates = (
        *DOORBELL_STATE_CANDIDATES,
        *INTERCOM_VIDEO_STATE_CANDIDATES,
        *INTERCOM_HISTORY_STATE_CANDIDATES,
    )
    for candidate in strong_signal_candidates:
        if normalize_state_name(candidate) in normalized_states:
            return True

    weak_signal_candidates = (
        *INTERCOM_PROXIMITY_STATE_CANDIDATES,
        *INTERCOM_CALL_STATE_CANDIDATES,
        *INTERCOM_LIGHT_STATE_CANDIDATES,
    )
    has_weak_intercom_signal = any(
        normalize_state_name(candidate) in normalized_states
        for candidate in weak_signal_candidates
    )
    if has_weak_intercom_signal and any(
        hint in normalized_type or hint in normalized_name
        for hint in ("intercom", "door", "station", "gate", "wideodomofon", "dzwonek")
    ):
        return True

    return any(
        _nested_detail_value(control.details, detail_path) is not None
        for detail_path in (
            "videoInfo.streamUrl",
            "securedDetails.videoInfo.streamUrl",
            "lastBellEvents",
            "securedDetails.videoInfo.alertImage",
        )
    )


def resolve_intercom_command(command: str, arguments: Any = None) -> str:
    """Resolve and validate an Intercom command path with encoded arguments."""
    raw_command = str(command).strip()
    if not raw_command:
        raise ValueError("Intercom command cannot be empty.")

    normalized_command = _NON_ALNUM_RE.sub("", raw_command.casefold())
    canonical_command = _INTERCOM_COMMAND_ALIASES.get(normalized_command)
    if canonical_command is None:
        available = ", ".join(sorted(_INTERCOM_COMMAND_SPECS))
        raise ValueError(
            f"Unsupported intercom command '{raw_command}'. Available: {available}."
        )

    resolved_arguments = _coerce_intercom_arguments(canonical_command, arguments)
    if not resolved_arguments:
        default_arguments = _INTERCOM_COMMAND_ALIAS_DEFAULT_ARGUMENTS.get(normalized_command)
        if default_arguments is not None:
            resolved_arguments = list(default_arguments)

    min_args, max_args, numeric_indexes = _INTERCOM_COMMAND_SPECS[canonical_command]
    if len(resolved_arguments) < min_args:
        raise ValueError(
            f"Command '{canonical_command}' expects at least {min_args} argument(s)."
        )
    if max_args is not None and len(resolved_arguments) > max_args:
        raise ValueError(
            f"Command '{canonical_command}' expects at most {max_args} argument(s)."
        )

    for index in numeric_indexes:
        if index >= len(resolved_arguments):
            continue
        try:
            int(resolved_arguments[index], 10)
        except ValueError as err:
            raise ValueError(
                f"Argument {index + 1} for '{canonical_command}' must be an integer."
            ) from err

    encoded_arguments = [
        quote(argument, safe="(),:%._-") for argument in resolved_arguments
    ]
    if not encoded_arguments:
        return canonical_command
    return f"{canonical_command}/{'/'.join(encoded_arguments)}"


def _coerce_intercom_arguments(command: str, arguments: Any) -> list[str]:
    if arguments is None:
        return []

    if isinstance(arguments, str):
        raw = arguments.strip()
        if not raw:
            return []

        if command in {
            "setAnswers",
            "setallvideosettings",
            "setvideosettings",
            "setframerate",
            "setresolution",
        }:
            return _clean_intercom_argument_list(raw.split("/"))
        return [raw]

    if isinstance(arguments, Sequence):
        return _clean_intercom_argument_list(arguments)

    return _clean_intercom_argument_list((arguments,))


def _clean_intercom_argument_list(arguments: Sequence[Any]) -> list[str]:
    cleaned: list[str] = []
    for value in arguments:
        text = str(value).strip()
        if not text:
            continue
        cleaned.append(text)
    return cleaned


def intercom_doorbell_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(control, DOORBELL_STATE_CANDIDATES, _DOORBELL_HINTS)


def intercom_proximity_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(
        control,
        INTERCOM_PROXIMITY_STATE_CANDIDATES,
        _PROXIMITY_HINTS,
    )


def intercom_call_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(control, INTERCOM_CALL_STATE_CANDIDATES, _CALL_HINTS)


def intercom_light_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(control, INTERCOM_LIGHT_STATE_CANDIDATES, _LIGHT_HINTS)


def intercom_history_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(control, INTERCOM_HISTORY_STATE_CANDIDATES, _HISTORY_HINTS)


def intercom_last_bell_events_state_name(control: LoxoneControl) -> str | None:
    return first_matching_state_name(control, ("lastBellEvents",))


def intercom_last_bell_timestamp_state_name(control: LoxoneControl) -> str | None:
    return first_matching_state_name(control, ("lastBellTimestamp",))


def intercom_address_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(control, _ADDRESS_STATE_CANDIDATES, _ADDRESS_HINTS)


def resolve_intercom_http_url(
    bridge,
    control: LoxoneControl,
    value: Any,
    *,
    address_value: Any = None,
) -> str | None:
    """Resolve an Intercom media URL using the explicit device address."""
    text = _coerce_text(value)
    if text is None:
        return None

    if _is_absolute_http_url(text) or text.startswith("//"):
        return bridge.resolve_http_url(text)

    if not _looks_like_urlish_relative(text):
        return None

    if text.startswith("/camimage/") or text.startswith("camimage/"):
        return bridge.resolve_http_url(text)

    intercom_base_url = _intercom_base_url(
        bridge,
        control,
        address_value=address_value,
    )
    if intercom_base_url is not None:
        return _build_absolute_url(intercom_base_url, text)

    return bridge.resolve_http_url(text)


def intercom_boolean_state_roles(control: LoxoneControl) -> dict[str, str]:
    """Return role labels for recognizable intercom boolean states."""
    roles: dict[str, str] = {}
    candidates_by_role = (
        ("Doorbell", intercom_doorbell_state_name(control)),
        ("Proximity", intercom_proximity_state_name(control)),
        ("Call", intercom_call_state_name(control)),
        ("Light", intercom_light_state_name(control)),
    )
    for role, state_name in candidates_by_role:
        if state_name is not None:
            roles[state_name] = role
    return roles


def is_intercom_system_schema_webpage(
    control: LoxoneControl,
    controls_by_action: Mapping[str, LoxoneControl] | None = None,
) -> bool:
    """Return True for likely intercom system-schema webpage controls."""
    if control.type != "Webpage":
        return False

    normalized_name = control.name.casefold()
    schema_name_match = any(hint in normalized_name for hint in INTERCOM_SYSTEM_SCHEMA_NAME_HINTS)
    if not schema_name_match:
        return False

    parent: LoxoneControl | None = None
    if controls_by_action is not None and control.parent_uuid_action is not None:
        parent = controls_by_action.get(control.parent_uuid_action)
    if parent and is_intercom_control(parent):
        return True

    path_text = " ".join(part.casefold() for part in control.path)
    if any(hint in path_text for hint in ("intercom", "dzwonek", "wideodomofon", "door")):
        return True

    url = _coerce_text(_mapping_get_case_insensitive(control.details, "url"))
    if url is not None and any(
        hint in url.casefold() for hint in ("intercom", "door", "doorbell", "dzwonek")
    ):
        return True

    return False


def _first_state_with_fallback(
    control: LoxoneControl,
    candidates: tuple[str, ...],
    keyword_hints: tuple[str, ...],
) -> str | None:
    direct_match = first_matching_state_name(control, candidates)
    if direct_match is not None:
        return direct_match

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if any(hint in normalized for hint in keyword_hints):
            return state_name
    return None


def _intercom_base_url(bridge, control: LoxoneControl, *, address_value: Any = None) -> str | None:
    default_scheme = "https" if getattr(bridge, "use_tls", True) else "http"

    state_name = intercom_address_state_name(control)
    runtime_address_value = address_value
    if runtime_address_value is None and state_name is not None:
        runtime_address_value = bridge.control_state(control, state_name)

    from_address_state = _base_url_from_value(runtime_address_value, default_scheme=default_scheme)
    if from_address_state is not None:
        return from_address_state

    return None


def _base_url_from_value(value: Any, *, default_scheme: str) -> str | None:
    if value is None:
        return None

    if isinstance(value, Mapping):
        for key in (
            "url",
            "address",
            "host",
            "hostname",
            "ip",
            "ipAddress",
            "deviceIp",
            "deviceAddress",
            "value",
        ):
            nested = _mapping_get_case_insensitive(value, key)
            from_nested = _base_url_from_value(nested, default_scheme=default_scheme)
            if from_nested is not None:
                return from_nested

        for nested_value in value.values():
            from_nested = _base_url_from_value(nested_value, default_scheme=default_scheme)
            if from_nested is not None:
                return from_nested
        return None

    if isinstance(value, list):
        for item in value:
            from_item = _base_url_from_value(item, default_scheme=default_scheme)
            if from_item is not None:
                return from_item
        return None

    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    if raw.startswith("{") or raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return _base_url_from_value(parsed, default_scheme=default_scheme)

    lowered = raw.casefold()
    if lowered.startswith("sip:") and "@" in raw:
        host_candidate = raw.rsplit("@", 1)[-1]
        return _base_url_from_value(host_candidate, default_scheme=default_scheme)

    if raw.startswith("//"):
        parsed = urlsplit(f"{default_scheme}:{raw}")
        if parsed.hostname is None:
            return None
        port_part = f":{parsed.port}" if parsed.port else ""
        return f"{default_scheme}://{parsed.hostname}{port_part}"

    parsed_raw = urlsplit(raw)
    if parsed_raw.scheme in {"http", "https"} and parsed_raw.hostname:
        port_part = f":{parsed_raw.port}" if parsed_raw.port else ""
        return f"{parsed_raw.scheme}://{parsed_raw.hostname}{port_part}"

    if raw.startswith("/"):
        return None

    host_part = raw.split("/", 1)[0]
    parsed_host = urlsplit(f"{default_scheme}://{host_part}")
    host = parsed_host.hostname
    if host is None or not _is_likely_host(host):
        return None
    port_part = f":{parsed_host.port}" if parsed_host.port else ""
    return f"{default_scheme}://{host}{port_part}"


def _is_likely_host(value: str) -> bool:
    candidate = value.strip().strip("[]")
    if not candidate:
        return False
    if candidate.casefold() == "localhost":
        return True
    if ":" in candidate:
        return True
    if "." in candidate:
        return True
    return False


def _is_absolute_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _looks_like_urlish_relative(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        return True

    normalized = stripped.casefold()
    if normalized.startswith(("dev/", "jdev/", "api/", "rest/")):
        return True
    if "/" in stripped:
        return True

    return any(
        token in normalized
        for token in (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
            ".mjpg",
            ".mjpeg",
            ".mp4",
            ".m3u8",
            ".json",
            "snapshot",
            "stream",
            "video",
            "history",
            "event",
            "image",
        )
    )


def _build_absolute_url(base_url: str, path_or_url: str) -> str:
    parsed_base = urlsplit(base_url)
    path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
    return f"{parsed_base.scheme}://{parsed_base.netloc}{path}"


def _nested_detail_value(details: Mapping[str, Any], path: str) -> Any:
    current: Any = details
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = _mapping_get_case_insensitive(current, part)
    return current


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


def intercom_tts_message(bridge, uuid_action: str) -> str:
    store = _intercom_tts_message_store(bridge)
    value = store.get(uuid_action, INTERCOM_DEFAULT_TTS_MESSAGE)
    return str(value)


def set_intercom_tts_message(bridge, uuid_action: str, value: Any) -> str:
    text = str(value or "")
    _intercom_tts_message_store(bridge)[uuid_action] = text
    return text


def intercom_tts_volume(bridge, uuid_action: str) -> int:
    store = _intercom_tts_volume_store(bridge)
    raw = store.get(uuid_action, INTERCOM_DEFAULT_TTS_VOLUME)
    try:
        volume = int(raw)
    except (TypeError, ValueError):
        volume = INTERCOM_DEFAULT_TTS_VOLUME
    clamped = max(0, min(100, volume))
    store[uuid_action] = clamped
    return clamped


def set_intercom_tts_volume(bridge, uuid_action: str, value: Any) -> int:
    try:
        volume = int(float(value))
    except (TypeError, ValueError):
        volume = INTERCOM_DEFAULT_TTS_VOLUME
    clamped = max(0, min(100, volume))
    _intercom_tts_volume_store(bridge)[uuid_action] = clamped
    return clamped


def build_intercom_tts_command(message: Any, volume: Any = None) -> str | None:
    text = str(message or "").strip()
    if not text:
        return None

    encoded_message = quote(text, safe="")
    # Intercom firmware expects plain `tts/<message>`. Appending `/volume`
    # can be interpreted as part of the spoken text.
    del volume
    return f"tts/{encoded_message}"


def _intercom_tts_message_store(bridge) -> dict[str, str]:
    store = getattr(bridge, "_intercom_tts_messages", None)
    if isinstance(store, dict):
        return store
    created: dict[str, str] = {}
    setattr(bridge, "_intercom_tts_messages", created)
    return created


def _intercom_tts_volume_store(bridge) -> dict[str, int]:
    store = getattr(bridge, "_intercom_tts_volumes", None)
    if isinstance(store, dict):
        return store
    created: dict[str, int] = {}
    setattr(bridge, "_intercom_tts_volumes", created)
    return created
