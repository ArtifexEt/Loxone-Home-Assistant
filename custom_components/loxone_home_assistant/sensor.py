"""Sensor platform for Loxone."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CLIMATE_AIR_QUALITY_STATE_CANDIDATES,
    CLIMATE_CO2_STATE_CANDIDATES,
    CLIMATE_CONTROL_TYPES,
    CLIMATE_CURRENT_TEMPERATURE_STATE_CANDIDATES,
    CLIMATE_HUMIDITY_STATE_CANDIDATES,
    CLIMATE_TARGET_TEMPERATURE_STATE_CANDIDATES,
    DOMAIN,
    HANDLED_CONTROL_TYPES,
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
from .models import LoxoneControl

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
POWER_SUPPLY_KIND_BATTERY = "battery_level"
POWER_SUPPLY_KIND_REMAINING_TIME = "remaining_time"

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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = hass.data[DOMAIN]["bridges"][entry.entry_id]
    entities: list[SensorEntity] = []

    for control in bridge.controls:
        if control.type == "Webpage":
            entities.append(LoxoneWebpageSensor(bridge, control))
            continue

        if control.type == "Meter":
            entities.append(LoxoneMeterSensor(bridge, control, "actual"))
            if control.state_uuid("total"):
                entities.append(LoxoneMeterSensor(bridge, control, "total"))
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


def _build_climate_state_sensors(bridge, control: LoxoneControl) -> list["LoxoneClimateStateSensor"]:
    current_temp_state = first_matching_state_name(
        control, CLIMATE_CURRENT_TEMPERATURE_STATE_CANDIDATES
    )
    target_temp_state = first_matching_state_name(
        control, CLIMATE_TARGET_TEMPERATURE_STATE_CANDIDATES
    )
    excluded_states = {
        state_name for state_name in (current_temp_state, target_temp_state) if state_name
    }

    entities: list[LoxoneClimateStateSensor] = []
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
        value = self.state_value(state_name)
        numeric = coerce_float(value)
        return numeric if numeric is not None else value

    @property
    def native_unit_of_measurement(self) -> str | None:
        state_name, _ = control_primary_state(self.control)
        return infer_unit(self.control, state_name)


class LoxoneMeterSensor(LoxoneEntity, SensorEntity):
    """Specialized meter sensor."""

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name)
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def native_value(self) -> float | None:
        return coerce_float(self.state_value(self._state_name))

    @property
    def native_unit_of_measurement(self) -> str | None:
        return infer_unit(self.control, self._state_name)

    @property
    def device_class(self) -> SensorDeviceClass | None:
        unit = self.native_unit_of_measurement
        if self._state_name == "total":
            return SensorDeviceClass.ENERGY if _is_energy_unit(unit) else None
        if _is_power_unit(unit):
            return SensorDeviceClass.POWER
        if _is_energy_unit(unit):
            return SensorDeviceClass.ENERGY
        return None

    @property
    def state_class(self) -> SensorStateClass | None:
        return SensorStateClass.TOTAL_INCREASING if self._state_name == "total" else SensorStateClass.MEASUREMENT


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
        value = self.state_value(self._state_name)
        numeric = coerce_float(value)
        return numeric if numeric is not None else value

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
        numeric = coerce_float(value)
        return numeric if numeric is not None else value

    @property
    def native_unit_of_measurement(self) -> str | None:
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
        numeric = coerce_float(value)
        return numeric if numeric is not None else value

    @property
    def native_unit_of_measurement(self) -> str | None:
        return infer_unit(self.control, self._state_name)


class LoxoneWebpageSensor(LoxoneEntity, SensorEntity):
    """Read-only sensor exposing Webpage control target URL."""

    @property
    def native_value(self) -> Any:
        url = self.control.details.get("url")
        return str(url).strip() if url is not None else None
