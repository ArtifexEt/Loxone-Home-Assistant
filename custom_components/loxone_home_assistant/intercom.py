"""Shared Intercom helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import SplitResult, quote, urlsplit

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
_INTERCOM_GEN2_TYPES = {
    normalize_state_name(value) for value in INTERCOM_CONTROL_TYPES if value.endswith("V2")
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
_HISTORY_HINTS = ("lastbell", "history", "event", "snapshot", "record")
_NON_BOOLEAN_STATE_HINTS = (
    "lastbell",
    "history",
    "timestamp",
    "image",
    "snapshot",
    "volume",
    "duration",
    "settings",
)
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
_ADDRESS_DETAIL_PATHS = (
    "address",
    "trustAddress",
    "ipAddress",
    "deviceAddress",
    "deviceIp",
    "host",
    "hostname",
    "videoInfo.address",
    "videoInfo.trustAddress",
    "videoInfo.ipAddress",
    "videoInfo.deviceAddress",
    "videoInfo.deviceIp",
    "videoInfo.host",
    "videoInfo.hostname",
    "securedDetails.address",
    "securedDetails.trustAddress",
    "securedDetails.ipAddress",
    "securedDetails.deviceAddress",
    "securedDetails.deviceIp",
    "securedDetails.host",
    "securedDetails.hostname",
    "securedDetails.videoInfo.address",
    "securedDetails.videoInfo.trustAddress",
    "securedDetails.videoInfo.ipAddress",
    "securedDetails.videoInfo.deviceAddress",
    "securedDetails.videoInfo.deviceIp",
    "securedDetails.videoInfo.host",
    "securedDetails.videoInfo.hostname",
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_INTERCOM_COMMAND_SPECS: dict[str, tuple[int, int | None, tuple[int, ...]]] = {
    "answer": (0, 0, ()),
    "playTts": (1, 1, (0,)),
    "mute": (1, 1, (0,)),
    "setAnswers": (1, None, ()),
    "setallvideosettings": (4, 4, (0, 1, 2, 3)),
    "setvideosettings": (3, 3, (0, 1, 2)),
    "setframerate": (2, 2, (0, 1)),
    "setresolution": (2, 2, (0, 1)),
    "getnumberbellimages": (0, 0, ()),
    "setnumberbellimages": (1, 1, (0,)),
}
_INTERCOM_COMMAND_ALIASES = {
    **{command.casefold(): command for command in _INTERCOM_COMMAND_SPECS},
    "accept": "answer",
    "answercall": "answer",
    "pickup": "answer",
    "tts": "playTts",
    "mutemic": "mute",
    "microphonemute": "mute",
    "unmute": "mute",
    "unmutemic": "mute",
    "microphoneunmute": "mute",
    "setanswer": "setAnswers",
    "setallanswer": "setAnswers",
}
_INTERCOM_COMMAND_ALIAS_DEFAULT_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "mutemic": ("1",),
    "microphonemute": ("1",),
    "unmute": ("0",),
    "unmutemic": ("0",),
    "microphoneunmute": ("0",),
}
INTERCOM_DEFAULT_TTS_MESSAGE = ""


def is_intercom_control(control: LoxoneControl) -> bool:
    """Return True when a control looks like an intercom/door station."""
    normalized_type = normalize_state_name(control.type)
    normalized_name = normalize_state_name(control.name)
    if any(
        hint in normalized_type or hint in normalized_name
        for hint in INTERCOM_TYPE_HINTS_NORMALIZED
    ):
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
        intercom_detail_value(control.details, detail_path) is not None
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

    resolved_arguments = _coerce_intercom_arguments(
        canonical_command, arguments
    ) or _INTERCOM_COMMAND_ALIAS_DEFAULT_ARGUMENTS.get(normalized_command, ())

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
        try:
            int(resolved_arguments[index], 10)
        except ValueError as err:
            raise ValueError(
                f"Argument {index + 1} for '{canonical_command}' must be an integer."
            ) from err

    if (
        canonical_command in {"mute", "setvideosettings", "setframerate", "setresolution"}
        and int(resolved_arguments[0], 10) not in {0, 1}
    ):
        raise ValueError(f"Argument 1 for '{canonical_command}' must be 0 or 1.")

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
        if command in {
            "setAnswers",
            "setallvideosettings",
            "setvideosettings",
            "setframerate",
            "setresolution",
        }:
            arguments = raw.split("/")
        else:
            arguments = (raw,)
    elif not isinstance(arguments, Sequence):
        arguments = (arguments,)

    cleaned = [str(value).strip() if value is not None else "" for value in arguments]
    if command != "setAnswers":
        return [value for value in cleaned if value]
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    if "" in cleaned:
        raise ValueError("Command 'setAnswers' cannot skip an answer index.")
    return cleaned


def intercom_doorbell_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(
        control, DOORBELL_STATE_CANDIDATES, _DOORBELL_HINTS, _NON_BOOLEAN_STATE_HINTS
    )


def intercom_proximity_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(
        control,
        INTERCOM_PROXIMITY_STATE_CANDIDATES,
        _PROXIMITY_HINTS,
        _NON_BOOLEAN_STATE_HINTS,
    )


def intercom_call_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(
        control, INTERCOM_CALL_STATE_CANDIDATES, _CALL_HINTS, _NON_BOOLEAN_STATE_HINTS
    )


def intercom_light_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(
        control, INTERCOM_LIGHT_STATE_CANDIDATES, _LIGHT_HINTS, _NON_BOOLEAN_STATE_HINTS
    )


def intercom_history_state_name(control: LoxoneControl) -> str | None:
    return _first_state_with_fallback(
        control, (*INTERCOM_HISTORY_STATE_CANDIDATES, "eventHistoryUrl"), _HISTORY_HINTS
    )


def is_intercom_gen2_control(control: LoxoneControl) -> bool:
    """Identify Gen 2 Intercom blocks by type or firmware-specific state."""
    return normalize_state_name(control.type) in _INTERCOM_GEN2_TYPES or (
        is_intercom_control(control)
        and first_matching_state_name(control, ("trustAddress",)) is not None
    )


def intercom_mute_state_name(control: LoxoneControl) -> str | None:
    """Find the Gen 2 doorbell mute state."""
    if not is_intercom_gen2_control(control):
        return None
    return first_matching_state_name(control, ("muted",))


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
    if not isinstance(value, str) or not (text := value.strip()):
        return None

    parts = split_intercom_url(text)
    if parts is None:
        return None
    if parts.scheme or parts.netloc:
        return _resolve_absolute_media_url(bridge, parts)

    if not _looks_like_urlish_relative(text):
        return None

    if parts.path.lstrip("/").startswith(("camimage/", "proxy/")):
        return bridge.resolve_http_url(text)

    intercom_base_url = _intercom_base_url(
        bridge,
        control,
        address_value=address_value,
    )
    if intercom_base_url is not None:
        return f"{intercom_base_url}/{text.lstrip('/')}"

    return bridge.resolve_http_url(text)


def _resolve_absolute_media_url(bridge, parts: SplitResult) -> str | None:
    if parts.scheme not in {"", "http", "https"} or not parts.hostname:
        return None
    if parts.hostname == "remoteconnect":
        if not bridge.use_tls:
            return None
        return bridge.resolve_http_url(parts._replace(scheme="", netloc="").geturl())
    if parts.hostname == "clouddns":
        host = str(bridge.host).strip("[]")
        host = f"[{host}]" if ":" in host else host
        port = f":{parts.port}" if parts.port is not None else ""
        parts = parts._replace(netloc=f"{host}{port}")
    return bridge.resolve_http_url(parts.geturl())


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
            roles.setdefault(state_name, role)
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

    url = _coerce_text(intercom_mapping_value(control.details, "url"))
    if url is not None and any(
        hint in url.casefold() for hint in ("intercom", "door", "doorbell", "dzwonek")
    ):
        return True

    return False


def _first_state_with_fallback(
    control: LoxoneControl,
    candidates: tuple[str, ...],
    keyword_hints: tuple[str, ...],
    excluded_hints: tuple[str, ...] = (),
) -> str | None:
    direct_match = first_matching_state_name(control, candidates)
    if direct_match is not None:
        return direct_match

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if any(hint in normalized for hint in excluded_hints):
            continue
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

    for detail_path in _ADDRESS_DETAIL_PATHS:
        from_details = _base_url_from_value(
            intercom_detail_value(control.details, detail_path),
            default_scheme=default_scheme,
        )
        if from_details is not None:
            return from_details

    return None


def _base_url_from_value(value: Any, *, default_scheme: str) -> str | None:
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
            nested = intercom_mapping_value(value, key)
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

    explicit = "://" in raw or raw.startswith("//")
    if raw.startswith("//"):
        raw = f"{default_scheme}:{raw}"
    elif not explicit:
        raw = f"{default_scheme}://{raw}"

    parts = split_intercom_url(raw)
    if parts is None or parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    if not explicit and not _is_likely_host(parts.hostname):
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{host}{port}"


def _is_likely_host(value: str) -> bool:
    return value == "localhost" or ":" in value or "." in value


def split_intercom_url(value: str) -> SplitResult | None:
    """Split a firmware-provided URL, validating its optional port."""
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        return None
    return parts


def _looks_like_urlish_relative(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        return True

    normalized = stripped.casefold()
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


def intercom_detail_value(details: Any, path: str) -> Any:
    """Read a dotted path in a firmware details object."""
    current: Any = details
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = intercom_mapping_value(current, part)
    return current


def intercom_mapping_value(mapping: Mapping[str, Any], key: str) -> Any:
    """Read a field across variants in spelling and case."""
    if key in mapping:
        return mapping[key]

    wanted = normalize_state_name(key)
    for current_key, value in mapping.items():
        if isinstance(current_key, str) and normalize_state_name(current_key) == wanted:
            return value
    return None


def _coerce_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def intercom_tts_message(bridge, uuid_action: str) -> str:
    store = _intercom_tts_message_store(bridge)
    value = store.get(uuid_action, INTERCOM_DEFAULT_TTS_MESSAGE)
    return str(value)


def set_intercom_tts_message(bridge, uuid_action: str, value: Any) -> str:
    text = str(value or "")
    _intercom_tts_message_store(bridge)[uuid_action] = text
    return text


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
