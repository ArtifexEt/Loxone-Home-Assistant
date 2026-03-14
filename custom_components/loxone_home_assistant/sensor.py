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
    SENSOR_CONTROL_TYPES,
)
from .entity import (
    LoxoneEntity,
    control_primary_state,
    coerce_float,
    first_matching_state_name,
    infer_unit,
    normalize_state_name,
    state_is_boolean,
)
from .intercom import (
    intercom_history_state_name,
    is_intercom_control,
    is_intercom_system_schema_webpage,
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
CLIMATE_OVERRIDE_ENTRIES_STATE_CANDIDATES = ("overrideEntries",)
LOCK_STATE_CANDIDATES = ("jLocked", "isLocked")
INTERCOM_HISTORY_DETAIL_PATHS = (
    "lastBellEvents",
    "securedDetails.lastBellEvents",
)
INTERCOM_EVENT_COLLECTION_PATHS = (
    "events",
    "items",
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
    "liveImageUrl",
)
INTERCOM_EVENT_TIMESTAMP_KEY_CANDIDATES = (
    "timestamp",
    "time",
    "date",
    "created",
    "eventTime",
    "lastBellTimestamp",
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
METER_ACTUAL_STATE_KEYS = {
    normalize_state_name(value) for value in METER_ACTUAL_STATE_CANDIDATES
}
METER_TOTAL_STATE_KEYS = {
    normalize_state_name(value) for value in METER_TOTAL_STATE_CANDIDATES
}
STATE_HISTORY_SEPARATOR = "|"
STATE_DETAIL_SEPARATOR = "\x14"
STATE_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]+")


def _normalized_unit(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower()


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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities: list[SensorEntity] = []

    for control in bridge.controls:
        if control.type == "Webpage":
            entities.append(
                LoxoneWebpageSensor(
                    bridge,
                    control,
                    enabled_default=not is_intercom_system_schema_webpage(
                        control,
                        bridge.controls_by_action,
                    ),
                )
            )
            continue

        if is_intercom_control(control):
            history_state_name = intercom_history_state_name(control)
            if history_state_name is None:
                history_state_name = first_matching_state_name(
                    control,
                    INTERCOM_HISTORY_STATE_CANDIDATES,
                )
            if history_state_name is not None or _has_intercom_history_detail(control):
                entities.append(
                    LoxoneIntercomHistorySensor(
                        bridge,
                        control,
                        history_state_name,
                    )
                )
            continue

        if control.type == "Meter":
            entities.extend(_build_meter_sensors(bridge, control))
            continue

        if control.type in POWER_SUPPLY_CONTROL_TYPES:
            battery_state = _find_power_supply_battery_state(control)
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

            remaining_time_state = _find_power_supply_remaining_time_state(control)
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

            if battery_state is None and remaining_time_state is None:
                for state_name in control.states:
                    if state_is_boolean(control, state_name):
                        continue
                    entities.append(
                        LoxonePowerSupplySensor(
                            bridge,
                            control,
                            state_name,
                            state_name,
                            "",
                        )
                    )
                    break
            continue

        if control.type in CLIMATE_CONTROL_TYPES:
            entities.extend(_build_climate_state_sensors(bridge, control))
            continue

        if control.type in SENSOR_CONTROL_TYPES:
            entities.append(LoxonePrimarySensor(bridge, control))
            continue

        if control.type in HANDLED_CONTROL_TYPES:
            continue

        for state_name in control.states:
            if state_is_boolean(control, state_name):
                continue
            entities.append(LoxoneDiagnosticSensor(bridge, control, state_name))

    async_add_entities(entities)


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
    for state_name in control.states:
        if state_name in excluded_states:
            continue
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
    normalized = normalize_state_name(state_name)
    if normalized in HUMIDITY_STATE_KEYS:
        return "Humidity"
    if normalized in CO2_STATE_KEYS:
        return "CO2"
    if normalized in AIR_QUALITY_STATE_KEYS:
        return "Air Quality"

    humanized = CAMEL_CASE_SPLIT_RE.sub(" ", state_name)
    humanized = humanized.replace("_", " ").replace("-", " ").strip()
    if not humanized:
        return state_name
    return humanized[0].upper() + humanized[1:]


class LoxonePrimarySensor(LoxoneEntity, SensorEntity):
    """Primary read-only sensor for one Loxone control."""

    @property
    def native_value(self) -> Any:
        state_name, _ = control_primary_state(self.control)
        if state_name is None:
            return None
        return _sensor_native_value(self.state_value(state_name))

    @property
    def native_unit_of_measurement(self) -> str | None:
        state_name, _ = control_primary_state(self.control)
        return infer_unit(self.control, state_name)


class LoxoneMeterSensor(LoxoneEntity, SensorEntity):
    """Specialized meter sensor."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        meter_kind: str | None = None,
    ) -> None:
        self._state_name = state_name
        self._meter_kind = meter_kind or _meter_state_kind_from_name(state_name)
        entity_suffix = self._meter_kind if meter_kind is not None else state_name
        super().__init__(bridge, control, entity_suffix)

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

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


class LoxonePowerSupplySensor(LoxoneEntity, SensorEntity):
    """PowerSupply-related sensor."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str,
        kind: str,
    ) -> None:
        super().__init__(bridge, control, suffix)
        self._state_name = state_name
        self._kind = kind

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

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
        return (
            SensorStateClass.MEASUREMENT
            if coerce_float(self.state_value(self._state_name)) is not None
            else None
        )


class LoxoneClimateOverrideEntriesSensor(LoxoneEntity, SensorEntity):
    """Count and expose active climate override entries."""

    _attr_icon = "mdi:playlist-edit"

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
    ) -> None:
        super().__init__(bridge, control, "Override Entries")
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

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


class LoxoneClimateStateSensor(LoxoneEntity, SensorEntity):
    """Additional sensor for climate-related states beyond temperature."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str,
    ) -> None:
        super().__init__(bridge, control, suffix)
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

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
        return infer_unit(self.control, self._state_name)

    @property
    def device_class(self) -> SensorDeviceClass | None:
        normalized = normalize_state_name(self._state_name)
        if normalized in HUMIDITY_STATE_KEYS:
            return getattr(SensorDeviceClass, "HUMIDITY", None)
        if normalized in CO2_STATE_KEYS:
            return getattr(SensorDeviceClass, "CO2", None)
        if normalized == normalize_state_name("voc"):
            return getattr(SensorDeviceClass, "VOLATILE_ORGANIC_COMPOUNDS", None)
        if normalized in AIR_QUALITY_STATE_KEYS:
            return getattr(SensorDeviceClass, "AQI", None)
        return None

    @property
    def state_class(self) -> SensorStateClass | None:
        return (
            SensorStateClass.MEASUREMENT
            if coerce_float(self.state_value(self._state_name)) is not None
            else None
        )


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
        self._events_url: str | None = None
        self._latest_event_time: datetime | None = None
        self._latest_event_image_url: str | None = None
        self._event_count = 0
        self._recent_image_urls: list[str] = []

    @property
    def should_poll(self) -> bool:
        return True

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._history_state_name or "")
        return [state_uuid] if state_uuid else []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()

    def _handle_bridge_update(self) -> None:
        self.async_schedule_update_ha_state(True)

    async def async_update(self) -> None:
        events_url = _resolve_intercom_history_url(
            self.bridge,
            self.control,
            self._history_state_name,
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

        normalized_events = _extract_intercom_events(payload, self.bridge)
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


class LoxoneDiagnosticSensor(LoxoneEntity, SensorEntity):
    """Disabled-by-default raw state sensor for unsupported control types."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name)
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

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
        return infer_unit(self.control, self._state_name)


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
) -> str | None:
    from_state = (
        bridge.control_state(control, history_state_name)
        if history_state_name is not None
        else None
    )
    resolved_state = bridge.resolve_http_url(_coerce_text(from_state))
    if resolved_state is not None:
        return resolved_state
    return _resolve_control_detail_url(bridge, control, INTERCOM_HISTORY_DETAIL_PATHS)


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


def _extract_intercom_events(payload: Any, bridge) -> list[dict[str, Any]]:
    events = _find_event_mappings(payload)
    normalized: list[dict[str, Any]] = []
    for event in events:
        timestamp = _event_timestamp(event)
        image_url = _event_image_url(event, bridge)
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


def _event_image_url(event: Mapping[str, Any], bridge) -> str | None:
    image_value = _first_mapping_value(event, INTERCOM_EVENT_IMAGE_KEY_CANDIDATES)
    return bridge.resolve_http_url(_coerce_text(image_value))


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


def _resolve_control_detail_url(
    bridge,
    control: LoxoneControl,
    detail_paths: tuple[str, ...],
) -> str | None:
    return _resolve_detail_url(bridge, control.details, detail_paths)


def _resolve_detail_url(
    bridge,
    details: Mapping[str, Any],
    detail_paths: tuple[str, ...],
) -> str | None:
    for path in detail_paths:
        raw_value = _nested_detail_value(details, path)
        resolved = bridge.resolve_http_url(_coerce_text(raw_value))
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
