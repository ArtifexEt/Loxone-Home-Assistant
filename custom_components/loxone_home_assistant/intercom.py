"""Shared Intercom helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
_HISTORY_HINTS = ("lastbell", "history", "event", "snapshot")


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
