"""Sensor platform for Loxone."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
import re
from typing import Any

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

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
try:
    from homeassistant.const import MAX_LENGTH_STATE_STATE
except ImportError:  # pragma: no cover - fallback for test stubs
    MAX_LENGTH_STATE_STATE = 255

from .const import (
    ACCESS_DENIED_STATE_CANDIDATES,
    ACCESS_GRANTED_STATE_CANDIDATES,
    ACCESS_TYPE_HINTS,
    CLIMATE_AIR_QUALITY_STATE_CANDIDATES,
    CLIMATE_CO2_STATE_CANDIDATES,
    CLIMATE_CONTROL_TYPES,
    CLIMATE_CURRENT_TEMPERATURE_STATE_CANDIDATES,
    CLIMATE_HUMIDITY_STATE_CANDIDATES,
    CLIMATE_TARGET_TEMPERATURE_STATE_CANDIDATES,
    DOMAIN,
    HANDLED_CONTROL_TYPES,
    INTERCOM_HISTORY_STATE_CANDIDATES,
    POWER_SUPPLY_CONTROL_TYPES,
    PRESENCE_ILLUMINANCE_STATE_CANDIDATES,
    PRESENCE_SOUND_STATE_CANDIDATES,
    SENSOR_CONTROL_TYPES,
    TRACKER_CONTROL_TYPES,
)
from .entity import (
    LoxoneEntity,
    control_primary_state,
    coerce_float,
    first_matching_state_name,
    infer_unit,
    miniserver_device_info,
    normalize_state_name,
    state_is_boolean,
)
from .intercom import (
    intercom_address_state_name,
    intercom_history_state_name,
    is_intercom_control,
    is_intercom_system_schema_webpage,
    resolve_intercom_http_url,
)
from .models import LoxoneControl
from .runtime import entry_bridge

CAMEL_CASE_SPLIT_RE = re.compile(r"(?<!^)(?=[A-Z])")
ENERGY_UNITS = {"wh", "kwh", "mwh", "gwh", "j", "kj", "mj", "gj"}
POWER_UNITS = {"w", "kw", "mw", "gw"}
DURATION_UNITS = {"ms", "s", "min", "h", "d"}
POWER_SUPPLY_BATTERY_STATE_CANDIDATES = (
    "batteryLevel",
    "batteryStateOfCharge",
    "stateOfCharge",
    "chargeLevel",
    "batteryPercent",
)
POWER_SUPPLY_REMAINING_TIME_STATE_CANDIDATES = (
    "remainingTime",
    "timeRemaining",
    "supplyTimeRemaining",
    "remainingRuntime",
    "remainingDuration",
    "batteryRuntime",
)
METER_STATE_KIND_ACTUAL = "actual"
METER_STATE_KIND_TOTAL = "total"
METER_ACTUAL_STATE_CANDIDATES = (
    "actual",
    "act",
    "current",
    "currentValue",
    "power",
    "momentaryPower",
    "instantPower",
    "value",
)
METER_TOTAL_STATE_CANDIDATES = (
    "total",
    "sum",
    "counter",
    "energy",
    "consumption",
    "totalEnergy",
    "totalConsumption",
    "energyCounter",
    "consumptionCounter",
)
POWER_SUPPLY_KIND_BATTERY = "battery_level"
POWER_SUPPLY_KIND_REMAINING_TIME = "remaining_time"
PRESENCE_KIND_ILLUMINANCE = "illuminance"
PRESENCE_KIND_SOUND_LEVEL = "sound_level"
CLIMATE_OVERRIDE_ENTRIES_STATE_CANDIDATES = ("overrideEntries",)
LOCK_STATE_CANDIDATES = ("jLocked", "isLocked")
UNIVERSAL_EVENT_STATE_CANDIDATES = (
    "events",
    "event",
    "eventLog",
    "eventHistory",
    "history",
)
ACCESS_INFO_STATE_CANDIDATES = (
    "codeDate",
    "historyDate",
    "deviceState",
    "keyPadAuthType",
    "lastCode",
    "lastId",
    "lastTag",
    "lastUser",
    "nfcLearnResult",
)
TIMESTAMP_STATE_CANDIDATES = (
    "codeDate",
    "historyDate",
    "lastBellTimestamp",
    "lastEventTimestamp",
    "eventTime",
    "timestamp",
)
INTERCOM_HISTORY_DETAIL_PATHS = (
    "lastBellEvents",
    "eventHistoryUrl",
    "securedDetails.lastBellEvents",
    "videoInfo.lastBellEvents",
    "videoInfo.eventHistoryUrl",
    "securedDetails.videoInfo.lastBellEvents",
    "securedDetails.videoInfo.eventHistoryUrl",
    "videoSettings.lastBellEvents",
    "videoSettings.eventHistoryUrl",
)
INTERCOM_DYNAMIC_HISTORY_STATE_CANDIDATES = (
    "videoSettingsIntern",
    "videoSettingsExtern",
    "videoSettings",
    "videoInfo",
    "answers",
    "events",
    "history",
    "lastBellEvents",
)
INTERCOM_DYNAMIC_HISTORY_STATE_HINTS = (
    "history",
    "event",
    "bell",
    "answer",
    "record",
    "video",
    "snapshot",
    "image",
)
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
    "image",
    "alertImage",
    "snapshot",
    "photo",
    "thumb",
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
    "date",
    "datetime",
    "dateTime",
    "createdAt",
    "created",
    "eventTime",
    "lastBellTimestamp",
)
SYSTEM_DIAGNOSTIC_METRICS = (
    {
        "key": "numtasks",
        "name": "System Tasks",
        "unit": None,
        "icon": "mdi:playlist-check",
        "integer": True,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "cpu",
        "name": "CPU Load",
        "unit": "%",
        "icon": "mdi:cpu-64-bit",
        "integer": False,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "heap",
        "name": "Memory Usage",
        "unit": "%",
        "icon": "mdi:memory",
        "integer": False,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "ints",
        "name": "System Interrupts",
        "unit": None,
        "icon": "mdi:pulse",
        "integer": True,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
)

_DURATION_UNIT_ALIASES = {
    "msec": "ms",
    "millisecond": "ms",
    "milliseconds": "ms",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "mins": "min",
    "minute": "min",
    "minutes": "min",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "day": "d",
    "days": "d",
}
HUMIDITY_STATE_KEYS = {
    normalize_state_name(value) for value in CLIMATE_HUMIDITY_STATE_CANDIDATES
}
CO2_STATE_KEYS = {
    normalize_state_name(value) for value in CLIMATE_CO2_STATE_CANDIDATES
}
AIR_QUALITY_STATE_KEYS = {
    normalize_state_name(value) for value in CLIMATE_AIR_QUALITY_STATE_CANDIDATES
}
PRESENCE_ILLUMINANCE_STATE_KEYS = {
    normalize_state_name(value) for value in PRESENCE_ILLUMINANCE_STATE_CANDIDATES
}
PRESENCE_SOUND_STATE_KEYS = {
    normalize_state_name(value) for value in PRESENCE_SOUND_STATE_CANDIDATES
}
PRESENCE_BINARY_STATE_KEYS = {
    normalize_state_name(value)
    for value in (
        "active",
        "presence",
        "motion",
        "alarm",
        "value",
        "isActive",
        "enabled",
    )
}
PRESENCE_ILLUMINANCE_HINTS = ("illum", "bright", "lux", "lumi", "light")
PRESENCE_SOUND_HINTS = ("noise", "sound", "acoust", "loud", "db", "decibel")
CLIMATE_STATE_TRAILING_INDEX_RE = re.compile(r"[_\-\s]+\d+$")
CLIMATE_METADATA_STATE_HINTS = (
    "mode",
    "status",
    "fan",
    "capabil",
    "window",
    "lock",
    "override",
    "boundary",
    "info",
)
CLIMATE_TEMPERATURE_STATE_HINTS = ("temp", "temperature")
METER_ACTUAL_STATE_KEYS = {
    normalize_state_name(value) for value in METER_ACTUAL_STATE_CANDIDATES
}
METER_TOTAL_STATE_KEYS = {
    normalize_state_name(value) for value in METER_TOTAL_STATE_CANDIDATES
}
ACCESS_TYPE_HINT_KEYS = tuple(hint.casefold() for hint in ACCESS_TYPE_HINTS)
UNIVERSAL_EVENT_STATE_KEYS = {
    normalize_state_name(value) for value in UNIVERSAL_EVENT_STATE_CANDIDATES
}
ACCESS_INFO_STATE_KEYS = {
    normalize_state_name(value) for value in ACCESS_INFO_STATE_CANDIDATES
}
TIMESTAMP_STATE_KEYS = {
    normalize_state_name(value) for value in TIMESTAMP_STATE_CANDIDATES
}
STATE_HISTORY_SEPARATOR = "|"
STATE_DETAIL_SEPARATOR = "\x14"
STATE_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]+")


def _normalized_unit(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower()


def _climate_state_key_candidates(state_name: str) -> tuple[str, ...]:
    normalized = normalize_state_name(state_name)
    stripped = CLIMATE_STATE_TRAILING_INDEX_RE.sub("", state_name)
    if stripped == state_name:
        return (normalized,)
    stripped_normalized = normalize_state_name(stripped)
    if stripped_normalized == normalized:
        return (normalized,)
    return (normalized, stripped_normalized)


def _state_name_matches_any_key(state_name: str, keys: set[str]) -> bool:
    return any(candidate in keys for candidate in _climate_state_key_candidates(state_name))


def _is_humidity_state_name(state_name: str) -> bool:
    return _state_name_matches_any_key(state_name, HUMIDITY_STATE_KEYS)


def _is_co2_state_name(state_name: str) -> bool:
    return _state_name_matches_any_key(state_name, CO2_STATE_KEYS)


def _is_air_quality_state_name(state_name: str) -> bool:
    return _state_name_matches_any_key(state_name, AIR_QUALITY_STATE_KEYS)


def _has_climate_state_trailing_index(state_name: str) -> bool:
    return CLIMATE_STATE_TRAILING_INDEX_RE.search(state_name) is not None


def _climate_metric_state_group_key(state_name: str) -> str | None:
    if _is_humidity_state_name(state_name):
        return "humidity"
    if _is_co2_state_name(state_name):
        return "co2"
    if _is_air_quality_state_name(state_name):
        return "air_quality"
    if _state_name_matches_any_key(state_name, {normalize_state_name("voc")}):
        return "voc"
    return None


def _select_climate_state_names(
    control: LoxoneControl, excluded_states: set[str]
) -> list[str]:
    ordered_state_names: list[str] = []
    latest_metric_state: dict[str, str] = {}
    metrics_with_indexed_states: set[str] = set()

    for state_name in control.states:
        if state_name in excluded_states:
            continue
        ordered_state_names.append(state_name)

        metric_key = _climate_metric_state_group_key(state_name)
        if metric_key is None:
            continue
        latest_metric_state[metric_key] = state_name
        if _has_climate_state_trailing_index(state_name):
            metrics_with_indexed_states.add(metric_key)

    selected_state_names: list[str] = []
    for state_name in ordered_state_names:
        metric_key = _climate_metric_state_group_key(state_name)
        if metric_key is None or metric_key not in metrics_with_indexed_states:
            selected_state_names.append(state_name)
            continue
        if latest_metric_state.get(metric_key) == state_name:
            selected_state_names.append(state_name)

    return selected_state_names


def _is_energy_unit(value: str | None) -> bool:
    normalized = _normalized_unit(value)
    return normalized in ENERGY_UNITS if normalized else False


def _is_power_unit(value: str | None) -> bool:
    normalized = _normalized_unit(value)
    return normalized in POWER_UNITS if normalized else False


def _is_duration_unit(value: str | None) -> bool:
    normalized = _normalized_unit(value)
    return normalized in DURATION_UNITS if normalized else False


def _normalize_duration_unit(value: str | None) -> str | None:
    normalized = _normalized_unit(value)
    if normalized is None:
        return None
    if normalized in DURATION_UNITS:
        return normalized
    return _DURATION_UNIT_ALIASES.get(normalized)


def _coerce_int(value: object) -> int | None:
    numeric = coerce_float(value)
    if numeric is None:
        return None
    return int(numeric)


def _truncate_state_text(value: str) -> str:
    cleaned = value.replace(STATE_DETAIL_SEPARATOR, " - ")
    cleaned = STATE_CONTROL_CHAR_RE.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split())

    if len(cleaned) <= MAX_LENGTH_STATE_STATE:
        return cleaned

    entries = [
        entry.strip()
        for entry in cleaned.split(STATE_HISTORY_SEPARATOR)
        if entry and entry.strip()
    ]
    candidate = entries[-1] if entries else cleaned

    if len(candidate) <= MAX_LENGTH_STATE_STATE:
        return candidate
    if MAX_LENGTH_STATE_STATE <= 3:
        return candidate[:MAX_LENGTH_STATE_STATE]
    return f"{candidate[: MAX_LENGTH_STATE_STATE - 3].rstrip()}..."


def _sensor_native_value(value: Any) -> Any:
    numeric = coerce_float(value)
    if numeric is not None:
        return numeric
    if isinstance(value, str):
        return _truncate_state_text(value)
    if isinstance(value, (Mapping, list, tuple, set)):
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            encoded = str(value)
        return _truncate_state_text(encoded)
    return _truncate_state_text(str(value)) if value is not None else None


def _coerce_override_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        raw = value.strip()
        if raw:
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, Mapping):
                return [dict(decoded)]
            if isinstance(decoded, list):
                return [dict(entry) for entry in decoded if isinstance(entry, Mapping)]
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(entry) for entry in value if isinstance(entry, Mapping)]
    return []


def _is_override_entries_state_name(state_name: str) -> bool:
    normalized = normalize_state_name(state_name)
    return "override" in normalized and ("entries" in normalized or "entry" in normalized)


def _find_climate_override_entries_state(control: LoxoneControl) -> str | None:
    state_name = first_matching_state_name(control, CLIMATE_OVERRIDE_ENTRIES_STATE_CANDIDATES)
    if state_name is not None:
        return state_name

    for candidate in control.states:
        if _is_override_entries_state_name(candidate):
            return candidate
    return None


def _find_power_supply_battery_state(control: LoxoneControl) -> str | None:
    state_name = first_matching_state_name(
        control,
        POWER_SUPPLY_BATTERY_STATE_CANDIDATES,
    )
    if state_name is not None:
        return state_name

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if "battery" in normalized and "level" in normalized:
            return state_name
        if "charge" in normalized and "level" in normalized:
            return state_name
        if "state" in normalized and "charge" in normalized:
            return state_name
    return None


def _find_power_supply_remaining_time_state(control: LoxoneControl) -> str | None:
    state_name = first_matching_state_name(
        control,
        POWER_SUPPLY_REMAINING_TIME_STATE_CANDIDATES,
    )
    if state_name is not None:
        return state_name

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if "remaining" in normalized and "time" in normalized:
            return state_name
        if "time" in normalized and "left" in normalized:
            return state_name
        if "runtime" in normalized:
            return state_name
    return None


def _meter_state_kind_from_name(state_name: str) -> str:
    normalized = normalize_state_name(state_name)
    if normalized in METER_TOTAL_STATE_KEYS:
        return METER_STATE_KIND_TOTAL
    return METER_STATE_KIND_ACTUAL


def _score_meter_total_state(normalized_state_name: str) -> int:
    score = 0
    if normalized_state_name in METER_TOTAL_STATE_KEYS:
        score += 100
    if "total" in normalized_state_name:
        score += 50
    if "energy" in normalized_state_name:
        score += 40
    if "consumption" in normalized_state_name:
        score += 30
    if "counter" in normalized_state_name:
        score += 20
    if "current" in normalized_state_name or "actual" in normalized_state_name:
        score -= 35
    if "power" in normalized_state_name:
        score -= 25
    if any(
        period in normalized_state_name
        for period in (
            "day",
            "daily",
            "week",
            "weekly",
            "month",
            "monthly",
            "year",
            "yearly",
            "today",
            "yesterday",
        )
    ):
        score -= 20
    return score


def _score_meter_actual_state(normalized_state_name: str) -> int:
    score = 0
    if normalized_state_name in METER_ACTUAL_STATE_KEYS:
        score += 100
    if "actual" in normalized_state_name:
        score += 45
    if "current" in normalized_state_name:
        score += 40
    if "power" in normalized_state_name:
        score += 35
    if "instant" in normalized_state_name or "moment" in normalized_state_name:
        score += 25
    if "total" in normalized_state_name:
        score -= 45
    if "consumption" in normalized_state_name or "energy" in normalized_state_name:
        score -= 25
    return score


def _pick_best_meter_state(
    control: LoxoneControl,
    score_fn,
    excluded: set[str] | None = None,
) -> str | None:
    excluded = excluded or set()
    best_state: str | None = None
    best_score = 0
    for state_name in control.states:
        if state_name in excluded or state_is_boolean(control, state_name):
            continue
        score = score_fn(normalize_state_name(state_name))
        if score > best_score:
            best_score = score
            best_state = state_name
    return best_state


def _find_meter_total_state(control: LoxoneControl) -> str | None:
    state_name = first_matching_state_name(control, METER_TOTAL_STATE_CANDIDATES)
    if state_name is not None and not state_is_boolean(control, state_name):
        return state_name

    scored_state = _pick_best_meter_state(control, _score_meter_total_state)
    if scored_state is not None:
        return scored_state

    for candidate in control.states:
        if state_is_boolean(control, candidate):
            continue
        unit = infer_unit(control, candidate) or infer_unit(control, METER_STATE_KIND_TOTAL)
        if _is_energy_unit(unit):
            return candidate
    return None


def _is_presence_detector_control(control: LoxoneControl) -> bool:
    return normalize_state_name(control.type).startswith(
        normalize_state_name("PresenceDetector")
    )


def _is_presence_illuminance_state_name(state_name: str) -> bool:
    normalized = normalize_state_name(state_name)
    if normalized in PRESENCE_ILLUMINANCE_STATE_KEYS:
        return True
    return any(fragment in normalized for fragment in PRESENCE_ILLUMINANCE_HINTS)


def _is_presence_sound_state_name(state_name: str) -> bool:
    normalized = normalize_state_name(state_name)
    if normalized in PRESENCE_SOUND_STATE_KEYS:
        return True
    return any(fragment in normalized for fragment in PRESENCE_SOUND_HINTS)


def _find_presence_illuminance_state(
    control: LoxoneControl, excluded: set[str] | None = None
) -> str | None:
    excluded = excluded or set()

    state_name = first_matching_state_name(control, PRESENCE_ILLUMINANCE_STATE_CANDIDATES)
    if (
        state_name is not None
        and state_name not in excluded
        and normalize_state_name(state_name) not in PRESENCE_BINARY_STATE_KEYS
    ):
        return state_name

    for state_name in control.states:
        if (
            state_name in excluded
            or normalize_state_name(state_name) in PRESENCE_BINARY_STATE_KEYS
        ):
            continue
        if _is_presence_illuminance_state_name(state_name):
            return state_name

    for state_name in control.states:
        if (
            state_name in excluded
            or normalize_state_name(state_name) in PRESENCE_BINARY_STATE_KEYS
        ):
            continue
        unit = _normalized_unit(infer_unit(control, state_name))
        if unit in {"lx", "lux"}:
            return state_name
    return None


def _find_presence_sound_state(
    control: LoxoneControl, excluded: set[str] | None = None
) -> str | None:
    excluded = excluded or set()

    state_name = first_matching_state_name(control, PRESENCE_SOUND_STATE_CANDIDATES)
    if (
        state_name is not None
        and state_name not in excluded
        and normalize_state_name(state_name) not in PRESENCE_BINARY_STATE_KEYS
    ):
        return state_name

    for state_name in control.states:
        if (
            state_name in excluded
            or normalize_state_name(state_name) in PRESENCE_BINARY_STATE_KEYS
        ):
            continue
        if _is_presence_sound_state_name(state_name):
            return state_name

    for state_name in control.states:
        if (
            state_name in excluded
            or normalize_state_name(state_name) in PRESENCE_BINARY_STATE_KEYS
        ):
            continue
        unit = _normalized_unit(infer_unit(control, state_name))
        if unit in {"db", "db(a)", "dba"}:
            return state_name
    return None


def _build_presence_detector_sensors(bridge, control: LoxoneControl) -> list[SensorEntity]:
    illuminance_state = _find_presence_illuminance_state(control)
    excluded_states = {state_name for state_name in (illuminance_state,) if state_name}
    sound_state = _find_presence_sound_state(control, excluded=excluded_states)

    entities: list[SensorEntity] = []
    if illuminance_state is not None:
        entities.append(
            LoxonePresenceAnalogSensor(
                bridge,
                control,
                illuminance_state,
                "Illuminance",
                PRESENCE_KIND_ILLUMINANCE,
            )
        )
    if sound_state is not None:
        entities.append(
            LoxonePresenceAnalogSensor(
                bridge,
                control,
                sound_state,
                "Sound Level",
                PRESENCE_KIND_SOUND_LEVEL,
            )
        )
    return entities


def _find_meter_actual_state(control: LoxoneControl, excluded: set[str] | None = None) -> str | None:
    excluded = excluded or set()

    state_name = first_matching_state_name(control, METER_ACTUAL_STATE_CANDIDATES)
    if state_name is not None and state_name not in excluded and not state_is_boolean(control, state_name):
        return state_name

    scored_state = _pick_best_meter_state(
        control,
        _score_meter_actual_state,
        excluded=excluded,
    )
    if scored_state is not None:
        return scored_state

    for candidate in control.states:
        if candidate in excluded or state_is_boolean(control, candidate):
            continue
        return candidate
    return None


def _build_meter_sensors(bridge, control: LoxoneControl) -> list["LoxoneMeterSensor"]:
    total_state = _find_meter_total_state(control)
    excluded_states = {state_name for state_name in (total_state,) if state_name}
    actual_state = _find_meter_actual_state(control, excluded=excluded_states)

    entities: list[LoxoneMeterSensor] = []
    if actual_state is not None:
        entities.append(
            LoxoneMeterSensor(
                bridge,
                control,
                actual_state,
                METER_STATE_KIND_ACTUAL,
            )
        )
    if total_state is not None and total_state != actual_state:
        entities.append(
            LoxoneMeterSensor(
                bridge,
                control,
                total_state,
                METER_STATE_KIND_TOTAL,
            )
        )

    if entities:
        return entities

    for fallback_state in control.states:
        if state_is_boolean(control, fallback_state):
            continue
        fallback_kind = _meter_state_kind_from_name(fallback_state)
        if fallback_kind != METER_STATE_KIND_TOTAL:
            fallback_unit = infer_unit(control, fallback_state) or infer_unit(
                control, METER_STATE_KIND_TOTAL
            )
            if _is_energy_unit(fallback_unit):
                fallback_kind = METER_STATE_KIND_TOTAL
        return [LoxoneMeterSensor(bridge, control, fallback_state, fallback_kind)]
    return []


def _matching_universal_event_states(control: LoxoneControl) -> list[str]:
    event_states: list[str] = []
    for state_name in control.states:
        if state_is_boolean(control, state_name):
            continue
        if normalize_state_name(state_name) in UNIVERSAL_EVENT_STATE_KEYS:
            event_states.append(state_name)
    return event_states


def _build_universal_event_sensors(
    bridge,
    control: LoxoneControl,
) -> tuple[list[SensorEntity], set[str]]:
    entities: list[SensorEntity] = []
    covered_state_names: set[str] = set()
    for state_name in _matching_universal_event_states(control):
        entities.append(LoxoneEventStateSensor(bridge, control, state_name))
        covered_state_names.add(state_name)
    return entities, covered_state_names


def _build_tracker_sensors(
    bridge,
    control: LoxoneControl,
    excluded_state_names: set[str] | None = None,
) -> list[SensorEntity]:
    excluded = excluded_state_names or set()
    entities: list[SensorEntity] = []
    for state_name in control.states:
        if state_name in excluded or state_is_boolean(control, state_name):
            continue
        entities.append(LoxoneEventStateSensor(bridge, control, state_name))

    if entities:
        return entities

    for state_name in control.states:
        if state_name in excluded:
            continue
        return [LoxoneNamedStateSensor(bridge, control, state_name)]

    return []


def _is_access_like_control(control: LoxoneControl) -> bool:
    normalized_type = control.type.casefold()
    normalized_name = control.name.casefold()
    if any(hint in normalized_type or hint in normalized_name for hint in ACCESS_TYPE_HINT_KEYS):
        return True

    if (
        first_matching_state_name(control, ACCESS_GRANTED_STATE_CANDIDATES) is not None
        and first_matching_state_name(control, ACCESS_DENIED_STATE_CANDIDATES) is not None
    ):
        return True

    return any(
        normalize_state_name(state_name) in ACCESS_INFO_STATE_KEYS
        for state_name in control.states
    )


def _build_access_state_sensors(
    bridge,
    control: LoxoneControl,
    excluded_state_names: set[str] | None = None,
) -> list["LoxoneAccessStateSensor"]:
    if not _is_access_like_control(control):
        return []

    excluded = excluded_state_names or set()
    entities: list[LoxoneAccessStateSensor] = []
    for state_name in control.states:
        if state_name in excluded or state_is_boolean(control, state_name):
            continue
        entities.append(LoxoneAccessStateSensor(bridge, control, state_name))
    return entities


def _first_non_boolean_state_name(
    control: LoxoneControl,
    excluded_state_names: set[str] | None = None,
) -> str | None:
    excluded = excluded_state_names or set()
    for state_name in control.states:
        if state_name in excluded:
            continue
        if state_is_boolean(control, state_name):
            continue
        return state_name
    return None


def _build_intercom_sensors(bridge, control: LoxoneControl) -> list[SensorEntity]:
    history_state_name = intercom_history_state_name(control)
    dynamic_history_state_names = _dynamic_intercom_history_state_names(control)
    if history_state_name is None:
        history_state_name = first_matching_state_name(
            control,
            INTERCOM_HISTORY_STATE_CANDIDATES,
        )
    if (
        history_state_name is None
        and not dynamic_history_state_names
        and not _has_intercom_history_detail(control)
    ):
        return []
    return [LoxoneIntercomHistorySensor(bridge, control, history_state_name)]


def _build_power_supply_sensors(bridge, control: LoxoneControl) -> list[SensorEntity]:
    battery_state = _find_power_supply_battery_state(control)
    remaining_time_state = _find_power_supply_remaining_time_state(control)

    entities: list[SensorEntity] = []
    if battery_state is not None:
        entities.append(
            LoxonePowerSupplySensor(
                bridge,
                control,
                battery_state,
                "Battery level",
                POWER_SUPPLY_KIND_BATTERY,
            )
        )

    if remaining_time_state is not None and remaining_time_state != battery_state:
        entities.append(
            LoxonePowerSupplySensor(
                bridge,
                control,
                remaining_time_state,
                "Remaining time",
                POWER_SUPPLY_KIND_REMAINING_TIME,
            )
        )

    if entities:
        return entities

    fallback_state = _first_non_boolean_state_name(control)
    if fallback_state is None:
        return []
    return [
        LoxonePowerSupplySensor(
            bridge,
            control,
            fallback_state,
            fallback_state,
            "",
        )
    ]


def _build_diagnostic_sensors(
    bridge,
    control: LoxoneControl,
    excluded_state_names: set[str] | None = None,
) -> list[SensorEntity]:
    excluded = excluded_state_names or set()
    return [
        LoxoneDiagnosticSensor(bridge, control, state_name)
        for state_name in control.states
        if state_name not in excluded and not state_is_boolean(control, state_name)
    ]


def _build_control_sensor_entities(bridge, control: LoxoneControl) -> list[SensorEntity]:
    if control.type == "Webpage":
        return [
            LoxoneWebpageSensor(
                bridge,
                control,
                enabled_default=not is_intercom_system_schema_webpage(
                    control,
                    bridge.controls_by_action,
                ),
            )
        ]

    if is_intercom_control(control):
        return _build_intercom_sensors(bridge, control)

    entities, covered_state_names = _build_universal_event_sensors(bridge, control)

    if control.type == "Meter":
        entities.extend(_build_meter_sensors(bridge, control))
        return entities

    if control.type in POWER_SUPPLY_CONTROL_TYPES:
        entities.extend(_build_power_supply_sensors(bridge, control))
        return entities

    if control.type in CLIMATE_CONTROL_TYPES:
        entities.extend(_build_climate_state_sensors(bridge, control))
        return entities

    if _is_presence_detector_control(control):
        entities.extend(_build_presence_detector_sensors(bridge, control))
        return entities

    if control.type in TRACKER_CONTROL_TYPES:
        entities.extend(
            _build_tracker_sensors(
                bridge,
                control,
                excluded_state_names=covered_state_names,
            )
        )
        return entities

    if control.type in SENSOR_CONTROL_TYPES:
        entities.append(LoxonePrimarySensor(bridge, control))
        return entities

    access_entities = _build_access_state_sensors(
        bridge,
        control,
        excluded_state_names=covered_state_names,
    )
    if access_entities:
        entities.extend(access_entities)
        return entities

    if control.type in HANDLED_CONTROL_TYPES:
        return entities

    entities.extend(
        _build_diagnostic_sensors(
            bridge,
            control,
            excluded_state_names=covered_state_names,
        )
    )
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities: list[SensorEntity] = []
    if _supports_miniserver_system_diagnostics(bridge):
        entities.extend(_build_miniserver_system_sensors(bridge))

    for control in bridge.controls:
        entities.extend(_build_control_sensor_entities(bridge, control))

    async_add_entities(entities)


def _supports_miniserver_system_diagnostics(bridge) -> bool:
    return (
        callable(getattr(bridge, "system_stat_state_uuid", None))
        and callable(getattr(bridge, "system_stat_value", None))
        and callable(getattr(bridge, "system_stat_command", None))
    )


def _build_miniserver_system_sensors(bridge) -> list[SensorEntity]:
    return [
        LoxoneMiniserverSystemSensor(
            bridge=bridge,
            metric_key=spec["key"],
            name_suffix=spec["name"],
            native_unit=spec["unit"],
            icon=spec["icon"],
            integer_value=spec["integer"],
            state_class=spec["state_class"],
        )
        for spec in SYSTEM_DIAGNOSTIC_METRICS
    ]


def _build_climate_state_sensors(bridge, control: LoxoneControl) -> list[SensorEntity]:
    current_temp_state = first_matching_state_name(
        control, CLIMATE_CURRENT_TEMPERATURE_STATE_CANDIDATES
    )
    target_temp_state = first_matching_state_name(
        control, CLIMATE_TARGET_TEMPERATURE_STATE_CANDIDATES
    )
    override_entries_state = _find_climate_override_entries_state(control)
    lock_state = first_matching_state_name(control, LOCK_STATE_CANDIDATES)
    excluded_states = {
        state_name
        for state_name in (
            current_temp_state,
            target_temp_state,
            override_entries_state,
            lock_state,
        )
        if state_name
    }

    entities: list[SensorEntity] = []
    if override_entries_state is not None:
        entities.append(
            LoxoneClimateOverrideEntriesSensor(bridge, control, override_entries_state)
        )

    used_suffixes: set[str] = set()
    for state_name in _select_climate_state_names(control, excluded_states):
        suffix = _climate_state_suffix(state_name)
        unique_suffix = suffix
        if unique_suffix in used_suffixes:
            unique_suffix = f"{suffix} {state_name}"
        used_suffixes.add(unique_suffix)
        entities.append(
            LoxoneClimateStateSensor(
                bridge,
                control,
                state_name,
                unique_suffix,
            )
        )
    return entities


def _climate_state_suffix(state_name: str) -> str:
    if _is_humidity_state_name(state_name):
        return "Humidity"
    if _is_co2_state_name(state_name):
        return "CO2"
    if _is_air_quality_state_name(state_name):
        return "Air Quality"

    humanized = CAMEL_CASE_SPLIT_RE.sub(" ", state_name)
    humanized = humanized.replace("_", " ").replace("-", " ").strip()
    if not humanized:
        return state_name
    return humanized[0].upper() + humanized[1:]


def _is_climate_metadata_state_name(state_name: str) -> bool:
    normalized = normalize_state_name(state_name)
    return any(fragment in normalized for fragment in CLIMATE_METADATA_STATE_HINTS)


def _is_likely_climate_temperature_state_name(state_name: str) -> bool:
    normalized = normalize_state_name(state_name)
    if not any(fragment in normalized for fragment in CLIMATE_TEMPERATURE_STATE_HINTS):
        return False
    return not _is_climate_metadata_state_name(state_name)


def _measurement_state_class(value: Any) -> SensorStateClass | None:
    return SensorStateClass.MEASUREMENT if coerce_float(value) is not None else None


class LoxoneSingleStateSensor(LoxoneEntity, SensorEntity):
    """Base sensor entity bound to one Loxone state."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str | None = None,
    ) -> None:
        super().__init__(bridge, control, state_name if suffix is None else suffix)
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []


class LoxonePrimarySensor(LoxoneEntity, SensorEntity):
    """Primary read-only sensor for one Loxone control."""

    def _primary_state_name(self) -> str | None:
        state_name, _ = control_primary_state(self.control)
        return state_name

    @property
    def native_value(self) -> Any:
        state_name = self._primary_state_name()
        if state_name is None:
            return None
        return _sensor_native_value(self.state_value(state_name))

    @property
    def native_unit_of_measurement(self) -> str | None:
        state_name = self._primary_state_name()
        if state_name is None:
            return None
        return infer_unit(self.control, state_name)

    @property
    def device_class(self) -> SensorDeviceClass | None:
        unit = self.native_unit_of_measurement
        if _is_energy_unit(unit):
            return SensorDeviceClass.ENERGY
        if _is_power_unit(unit):
            return SensorDeviceClass.POWER
        return None

    @property
    def state_class(self) -> SensorStateClass | None:
        state_name = self._primary_state_name()
        if state_name is None:
            return None

        if coerce_float(self.state_value(state_name)) is None:
            return None

        unit = self.native_unit_of_measurement
        if _is_power_unit(unit):
            return SensorStateClass.MEASUREMENT

        if _is_energy_unit(unit):
            normalized_state_name = normalize_state_name(state_name)
            if any(
                fragment in normalized_state_name
                for fragment in ("actual", "current", "power", "instant", "moment")
            ):
                return SensorStateClass.MEASUREMENT
            return SensorStateClass.TOTAL_INCREASING

        return None


class LoxoneMiniserverSystemSensor(SensorEntity):
    """Hub-level Miniserver diagnostics sensor from `dev/sys/*` commands."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        bridge,
        metric_key: str,
        name_suffix: str,
        native_unit: str | None,
        icon: str,
        integer_value: bool,
        state_class: SensorStateClass | None,
    ) -> None:
        self.bridge = bridge
        self._metric_key = metric_key
        self._state_uuid = bridge.system_stat_state_uuid(metric_key)
        self._native_unit = native_unit
        self._integer_value = integer_value
        self._state_class = state_class
        self._remove_listener = None
        self._attr_name = name_suffix
        self._attr_unique_id = f"{bridge.serial}-system-{metric_key}"
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return self.bridge.available

    @property
    def device_info(self):
        return miniserver_device_info(self.bridge)

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.bridge.add_listener(
            self._handle_bridge_update,
            [self._state_uuid],
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None

    def _handle_bridge_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | float | None:
        value = self.bridge.system_stat_value(self._metric_key)
        numeric = coerce_float(value)
        if numeric is None:
            return None
        if self._integer_value:
            return int(numeric)
        return numeric

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._native_unit

    @property
    def state_class(self) -> SensorStateClass | None:
        return self._state_class

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "metric_key": self._metric_key,
        }
        command = self.bridge.system_stat_command(self._metric_key)
        if command:
            attrs["webservice_command"] = command
        return attrs


class LoxoneMeterSensor(LoxoneSingleStateSensor):
    """Specialized meter sensor."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        meter_kind: str | None = None,
    ) -> None:
        self._meter_kind = meter_kind or _meter_state_kind_from_name(state_name)
        entity_suffix = self._meter_kind if meter_kind is not None else state_name
        super().__init__(bridge, control, state_name, entity_suffix)

    @property
    def native_value(self) -> float | None:
        return coerce_float(self.state_value(self._state_name))

    @property
    def native_unit_of_measurement(self) -> str | None:
        unit = (
            infer_unit(self.control, self._meter_kind)
            if self._meter_kind in {METER_STATE_KIND_ACTUAL, METER_STATE_KIND_TOTAL}
            else None
        )
        return unit or infer_unit(self.control, self._state_name)

    @property
    def device_class(self) -> SensorDeviceClass | None:
        unit = self.native_unit_of_measurement
        if self._meter_kind == METER_STATE_KIND_TOTAL:
            return SensorDeviceClass.ENERGY if _is_energy_unit(unit) else None
        if _is_power_unit(unit):
            return SensorDeviceClass.POWER
        if _is_energy_unit(unit):
            return SensorDeviceClass.ENERGY
        return None

    @property
    def state_class(self) -> SensorStateClass | None:
        return (
            SensorStateClass.TOTAL_INCREASING
            if self._meter_kind == METER_STATE_KIND_TOTAL
            else SensorStateClass.MEASUREMENT
        )


class LoxonePowerSupplySensor(LoxoneSingleStateSensor):
    """PowerSupply-related sensor."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str,
        kind: str,
    ) -> None:
        super().__init__(bridge, control, state_name, suffix)
        self._kind = kind

    @property
    def native_value(self) -> Any:
        return _sensor_native_value(self.state_value(self._state_name))

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self._kind == POWER_SUPPLY_KIND_BATTERY:
            return "%"

        unit = infer_unit(self.control, self._state_name)
        if self._kind == POWER_SUPPLY_KIND_REMAINING_TIME:
            return _normalize_duration_unit(unit) or unit
        return unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self._kind == POWER_SUPPLY_KIND_BATTERY:
            return SensorDeviceClass.BATTERY

        if self._kind == POWER_SUPPLY_KIND_REMAINING_TIME:
            return (
                SensorDeviceClass.DURATION
                if _is_duration_unit(self.native_unit_of_measurement)
                else None
            )
        return None

    @property
    def state_class(self) -> SensorStateClass | None:
        return _measurement_state_class(self.state_value(self._state_name))


class LoxonePresenceAnalogSensor(LoxoneSingleStateSensor):
    """Illuminance and sound-level sensors for PresenceDetector controls."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str,
        kind: str,
    ) -> None:
        super().__init__(bridge, control, state_name, suffix)
        self._kind = kind
        if self._kind == PRESENCE_KIND_ILLUMINANCE:
            self._attr_icon = "mdi:brightness-6"
        elif self._kind == PRESENCE_KIND_SOUND_LEVEL:
            self._attr_icon = "mdi:volume-high"

    @property
    def native_value(self) -> Any:
        return _sensor_native_value(self.state_value(self._state_name))

    @property
    def native_unit_of_measurement(self) -> str | None:
        unit = infer_unit(self.control, self._state_name)
        normalized_unit = _normalized_unit(unit)

        if self._kind == PRESENCE_KIND_ILLUMINANCE:
            if normalized_unit in {"db", "db(a)", "dba"}:
                return "lx"
            if normalized_unit == "lux":
                return "lx"
            return unit or "lx"

        if self._kind == PRESENCE_KIND_SOUND_LEVEL:
            if normalized_unit in {"lx", "lux"}:
                return "dB"
            return unit or "dB"

        return unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self._kind == PRESENCE_KIND_ILLUMINANCE:
            return getattr(SensorDeviceClass, "ILLUMINANCE", None)

        if self._kind == PRESENCE_KIND_SOUND_LEVEL:
            return getattr(
                SensorDeviceClass,
                "SOUND_PRESSURE",
                getattr(SensorDeviceClass, "SOUND_LEVEL", None),
            )
        return None

    @property
    def state_class(self) -> SensorStateClass | None:
        return _measurement_state_class(self.state_value(self._state_name))

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["presence_sensor_state"] = self._state_name
        attrs["presence_sensor_kind"] = self._kind
        return attrs


class LoxoneClimateOverrideEntriesSensor(LoxoneSingleStateSensor):
    """Count and expose active climate override entries."""

    _attr_icon = "mdi:playlist-edit"

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
    ) -> None:
        super().__init__(bridge, control, state_name, "Override Entries")

    @property
    def native_value(self) -> int:
        entries = _coerce_override_entries(self.state_value(self._state_name))
        return len(entries)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        entries = _coerce_override_entries(self.state_value(self._state_name))
        attrs["override_entries_state"] = self._state_name
        attrs["override_active"] = bool(entries)
        attrs["override_entries"] = entries

        reason_codes: list[int] = []
        sources: list[str] = []
        for entry in entries:
            reason = _coerce_int(entry.get("reason"))
            if reason is not None and reason not in reason_codes:
                reason_codes.append(reason)

            source = entry.get("source")
            if isinstance(source, str):
                source = source.strip()
                if source and source not in sources:
                    sources.append(source)

        if reason_codes:
            attrs["override_reason_codes"] = reason_codes
        if sources:
            attrs["override_sources"] = sources
        return attrs


class LoxoneClimateStateSensor(LoxoneSingleStateSensor):
    """Additional sensor for climate-related states beyond temperature."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str,
    ) -> None:
        super().__init__(bridge, control, state_name, suffix)

    @property
    def native_value(self) -> Any:
        value = self.state_value(self._state_name)
        if _is_override_entries_state_name(self._state_name):
            return len(_coerce_override_entries(value))
        return _sensor_native_value(value)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if _is_override_entries_state_name(self._state_name):
            return None
        if _is_climate_metadata_state_name(self._state_name):
            return None
        if _is_humidity_state_name(self._state_name):
            return "%"
        if _is_co2_state_name(self._state_name):
            return "ppm"

        unit = infer_unit(self.control, self._state_name)
        if unit is None:
            return None
        if "°" in unit and not _is_likely_climate_temperature_state_name(self._state_name):
            return None
        return unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if _is_humidity_state_name(self._state_name):
            return getattr(SensorDeviceClass, "HUMIDITY", None)
        if _is_co2_state_name(self._state_name):
            return getattr(SensorDeviceClass, "CO2", None)
        if _state_name_matches_any_key(self._state_name, {normalize_state_name("voc")}):
            return getattr(SensorDeviceClass, "VOLATILE_ORGANIC_COMPOUNDS", None)
        if _is_air_quality_state_name(self._state_name):
            return getattr(SensorDeviceClass, "AQI", None)
        return None

    @property
    def state_class(self) -> SensorStateClass | None:
        if coerce_float(self.state_value(self._state_name)) is None:
            return None

        if _is_humidity_state_name(self._state_name):
            return SensorStateClass.MEASUREMENT
        if _is_co2_state_name(self._state_name):
            return SensorStateClass.MEASUREMENT
        if _is_air_quality_state_name(self._state_name):
            return SensorStateClass.MEASUREMENT
        if _state_name_matches_any_key(self._state_name, {normalize_state_name("voc")}):
            return SensorStateClass.MEASUREMENT
        if _is_climate_metadata_state_name(self._state_name):
            return None
        if self.native_unit_of_measurement is None:
            return None
        return SensorStateClass.MEASUREMENT


class LoxoneIntercomHistorySensor(LoxoneEntity, SensorEntity):
    """Intercom history sensor with latest bell event metadata."""

    _attr_icon = "mdi:image-multiple"
    _attr_device_class = getattr(SensorDeviceClass, "TIMESTAMP", None)

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        history_state_name: str | None,
    ) -> None:
        super().__init__(bridge, control, "History")
        self._history_state_name = history_state_name
        self._dynamic_history_state_names = _dynamic_intercom_history_state_names(control)
        self._address_state_name = intercom_address_state_name(control)
        self._events_url: str | None = None
        self._latest_event_time: datetime | None = None
        self._latest_event_image_url: str | None = None
        self._event_count = 0
        self._recent_image_urls: list[str] = []

    @property
    def should_poll(self) -> bool:
        return True

    def relevant_state_uuids(self):
        uuids: list[str] = []
        for state_name in (
            self._history_state_name,
            self._address_state_name,
            *self._dynamic_history_state_names,
        ):
            if state_name is None:
                continue
            state_uuid = self.control.state_uuid(state_name)
            if state_uuid and state_uuid not in uuids:
                uuids.append(state_uuid)
        return uuids

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()

    def _handle_bridge_update(self) -> None:
        self.async_schedule_update_ha_state(True)

    async def async_update(self) -> None:
        address_value = self._address_state_value()

        for state_name in (
            self._history_state_name,
            *self._dynamic_history_state_names,
        ):
            state_payload = _intercom_history_payload_from_state(
                self.bridge,
                self.control,
                state_name,
            )
            if state_payload is None:
                continue
            if _is_intercom_video_settings_payload(state_payload):
                continue

            normalized_events = _extract_intercom_events(
                state_payload,
                self.bridge,
                self.control,
                address_value,
            )
            if normalized_events:
                self._events_url = None
                self._apply_normalized_events(normalized_events)
                return

        events_url = _resolve_intercom_history_url(
            self.bridge,
            self.control,
            self._history_state_name,
            address_value,
            dynamic_state_names=self._dynamic_history_state_names,
        )
        self._events_url = events_url
        if events_url is None:
            self._latest_event_time = None
            self._latest_event_image_url = None
            self._event_count = 0
            self._recent_image_urls = []
            return

        payload = await _async_fetch_json(
            self.bridge,
            events_url,
        )
        if payload is None:
            return

        normalized_events = _extract_intercom_events(
            payload,
            self.bridge,
            self.control,
            address_value,
        )
        self._apply_normalized_events(normalized_events)

    def _address_state_value(self) -> Any:
        if self._address_state_name is None:
            return None
        return self.bridge.control_state(self.control, self._address_state_name)

    def _apply_normalized_events(self, normalized_events: list[dict[str, Any]]) -> None:
        self._event_count = len(normalized_events)
        self._recent_image_urls = [
            event["image_url"]
            for event in normalized_events
            if isinstance(event.get("image_url"), str)
        ][:10]

        latest_event = next(iter(normalized_events), None)
        self._latest_event_time = (
            latest_event.get("timestamp")
            if isinstance(latest_event, Mapping)
            and isinstance(latest_event.get("timestamp"), datetime)
            else None
        )
        self._latest_event_image_url = (
            latest_event.get("image_url")
            if isinstance(latest_event, Mapping)
            and isinstance(latest_event.get("image_url"), str)
            else None
        )

    @property
    def native_value(self) -> datetime | None:
        return self._latest_event_time

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        if self._history_state_name is not None:
            attrs["history_state"] = self._history_state_name
        if self._events_url is not None:
            attrs["history_events_url"] = self._events_url
        attrs["event_count"] = self._event_count
        if self._latest_event_image_url is not None:
            attrs["latest_event_image_url"] = self._latest_event_image_url
        if self._recent_image_urls:
            attrs["recent_image_urls"] = self._recent_image_urls
        return attrs


class LoxoneNamedStateSensor(LoxoneSingleStateSensor):
    """Read-only sensor exposing one selected control state."""

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name)

    @property
    def native_value(self) -> Any:
        value = self.state_value(self._state_name)
        timestamp_value = _timestamp_state_value(self._state_name, value)
        if timestamp_value is not None:
            return timestamp_value
        if _is_override_entries_state_name(self._state_name):
            return len(_coerce_override_entries(value))
        return _sensor_native_value(value)

    @property
    def native_unit_of_measurement(self) -> str | None:
        value = self.state_value(self._state_name)
        if _timestamp_state_value(self._state_name, value) is not None:
            return None
        if _is_override_entries_state_name(self._state_name):
            return None
        return infer_unit(self.control, self._state_name)

    @property
    def device_class(self) -> SensorDeviceClass | None:
        value = self.state_value(self._state_name)
        if _timestamp_state_value(self._state_name, value) is not None:
            return getattr(SensorDeviceClass, "TIMESTAMP", None)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["state_name"] = self._state_name
        return attrs


class LoxoneEventStateSensor(LoxoneNamedStateSensor):
    """Always-on event/history sensor created for universal event states."""

    _attr_icon = "mdi:history"
    _attr_force_update = True

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["event_state"] = self._state_name
        return attrs


class LoxoneAccessStateSensor(LoxoneNamedStateSensor):
    """Always-on access sensor exposing last-code/user/tag style states."""

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["access_state"] = self._state_name
        return attrs


class LoxoneDiagnosticSensor(LoxoneNamedStateSensor):
    """Disabled-by-default raw state sensor for unsupported control types."""

    _attr_entity_registry_enabled_default = False


class LoxoneWebpageSensor(LoxoneEntity, SensorEntity):
    """Read-only sensor exposing Webpage control target URL."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(bridge, control)
        self._attr_entity_registry_enabled_default = enabled_default

    @property
    def native_value(self) -> Any:
        url = self.control.details.get("url")
        return _truncate_state_text(str(url).strip()) if url is not None else None


def _has_intercom_history_detail(control: LoxoneControl) -> bool:
    return any(
        _nested_detail_value(control.details, path) is not None
        for path in INTERCOM_HISTORY_DETAIL_PATHS
    )


def _resolve_intercom_history_url(
    bridge,
    control: LoxoneControl,
    history_state_name: str | None,
    address_value: Any = None,
    *,
    dynamic_state_names: tuple[str, ...] = (),
) -> str | None:
    from_state = (
        bridge.control_state(control, history_state_name)
        if history_state_name is not None
        else None
    )
    resolved_state = resolve_intercom_http_url(
        bridge,
        control,
        from_state,
        address_value=address_value,
    )
    if resolved_state is not None:
        return resolved_state
    from_details = _resolve_control_detail_url(
        bridge,
        control,
        INTERCOM_HISTORY_DETAIL_PATHS,
        address_value=address_value,
    )
    if from_details is not None:
        return from_details

    from_details_payload = _resolve_url_from_payload_with_key_hints(
        bridge,
        control,
        control.details,
        key_hints=INTERCOM_HISTORY_URL_KEY_HINTS,
        address_value=address_value,
    )
    if from_details_payload is not None:
        return from_details_payload

    return _resolve_url_from_intercom_state_payloads(
        bridge,
        control,
        dynamic_state_names,
        key_hints=INTERCOM_HISTORY_URL_KEY_HINTS,
        address_value=address_value,
    )


def _dynamic_intercom_history_state_names(control: LoxoneControl) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()

    for candidate in INTERCOM_DYNAMIC_HISTORY_STATE_CANDIDATES:
        state_name = first_matching_state_name(control, (candidate,))
        if state_name is None or state_name in seen:
            continue
        names.append(state_name)
        seen.add(state_name)

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if not any(hint in normalized for hint in INTERCOM_DYNAMIC_HISTORY_STATE_HINTS):
            continue
        if state_name in seen:
            continue
        names.append(state_name)
        seen.add(state_name)

    return tuple(names)


def _resolve_url_from_intercom_state_payloads(
    bridge,
    control: LoxoneControl,
    state_names: tuple[str, ...],
    *,
    key_hints: tuple[str, ...],
    address_value: Any = None,
) -> str | None:
    for state_name in state_names:
        state_payload = bridge.control_state(control, state_name)
        resolved = _resolve_url_from_payload_with_key_hints(
            bridge,
            control,
            state_payload,
            key_hints=key_hints,
            address_value=address_value,
        )
        if resolved is not None:
            return resolved
    return None


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


def _is_intercom_video_settings_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False

    normalized_keys = {
        normalize_state_name(str(key))
        for key in payload
        if isinstance(key, str)
    }
    if not normalized_keys:
        return False

    has_stream_or_snapshot_hint = any(
        "stream" in key or "video" in key or "image" in key or "snapshot" in key
        for key in normalized_keys
    )
    has_history_pointer_hint = any(
        "lastbell" in key or "history" in key or "event" in key
        for key in normalized_keys
    )
    has_event_timestamp = _first_mapping_value(
        payload,
        INTERCOM_EVENT_TIMESTAMP_KEY_CANDIDATES,
    ) is not None

    return has_stream_or_snapshot_hint and has_history_pointer_hint and not has_event_timestamp


def _intercom_history_payload_from_state(
    bridge,
    control: LoxoneControl,
    history_state_name: str | None,
) -> Any | None:
    if history_state_name is None:
        return None
    raw_value = bridge.control_state(control, history_state_name)
    if isinstance(raw_value, (Mapping, list)):
        return raw_value
    if not isinstance(raw_value, str):
        return None

    stripped = raw_value.strip()
    if not stripped:
        return None
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


async def _async_fetch_json(bridge, url: str) -> Any | None:
    session = getattr(bridge, "_session", None)
    if session is None:
        return None

    auth = BasicAuth(bridge.username, bridge.password)
    for request_auth in (auth, None):
        try:
            async with session.get(url, auth=request_auth) as response:
                if response.status == 401 and request_auth is not None:
                    continue
                response.raise_for_status()
                try:
                    return await response.json(content_type=None)
                except (TypeError, ValueError):
                    raw = await response.text()
                    return json.loads(raw)
        except (ClientError, TimeoutError, ValueError, json.JSONDecodeError):
            if request_auth is None:
                return None
    return None


def _extract_intercom_events(
    payload: Any,
    bridge,
    control: LoxoneControl,
    address_value: Any = None,
) -> list[dict[str, Any]]:
    events = _find_event_mappings(payload)
    normalized: list[dict[str, Any]] = []
    for event in events:
        timestamp = _event_timestamp(event)
        image_url = _event_image_url(
            event,
            bridge,
            control,
            address_value=address_value,
        )
        if timestamp is None and image_url is None:
            continue
        normalized.append(
            {
                "timestamp": timestamp,
                "image_url": image_url,
            }
        )

    # Prefer newest first when timestamps are available.
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


def _event_image_url(
    event: Mapping[str, Any],
    bridge,
    control: LoxoneControl,
    *,
    address_value: Any = None,
) -> str | None:
    image_value = _first_mapping_value(event, INTERCOM_EVENT_IMAGE_KEY_CANDIDATES)
    return resolve_intercom_http_url(
        bridge,
        control,
        image_value,
        address_value=address_value,
    )


def _event_timestamp(event: Mapping[str, Any]) -> datetime | None:
    timestamp_raw = _first_mapping_value(event, INTERCOM_EVENT_TIMESTAMP_KEY_CANDIDATES)
    return _coerce_datetime(timestamp_raw)


def _coerce_datetime(value: Any) -> datetime | None:
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
            return _coerce_datetime(float(raw))
        iso_candidate = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(iso_candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _timestamp_state_value(state_name: str, value: Any) -> datetime | None:
    normalized = normalize_state_name(state_name)
    if normalized not in TIMESTAMP_STATE_KEYS and not normalized.endswith("timestamp"):
        return None
    return _coerce_datetime(value)


def _resolve_control_detail_url(
    bridge,
    control: LoxoneControl,
    detail_paths: tuple[str, ...],
    *,
    address_value: Any = None,
) -> str | None:
    for path in detail_paths:
        raw_value = _nested_detail_value(control.details, path)
        resolved = resolve_intercom_http_url(
            bridge,
            control,
            raw_value,
            address_value=address_value,
        )
        if resolved is not None:
            return resolved
    return None


def _nested_detail_value(details: Mapping[str, Any], path: str) -> Any:
    current: Any = details
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = _mapping_get_case_insensitive(current, part)
    return current


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
