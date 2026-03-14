"""Climate platform for Loxone."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CLIMATE_AIR_QUALITY_STATE_CANDIDATES,
    CLIMATE_CO2_STATE_CANDIDATES,
    CLIMATE_CONTROL_TYPES,
    CLIMATE_CURRENT_TEMPERATURE_STATE_CANDIDATES,
    CLIMATE_HUMIDITY_STATE_CANDIDATES,
    CLIMATE_TARGET_TEMPERATURE_STATE_CANDIDATES,
)
from .entity import LoxoneEntity, coerce_float, first_matching_state_name, infer_unit
from .runtime import entry_bridge

TEMPERATURE_COMMAND_BY_CONTROL_TYPE = {
    "PoolController": "targetTemp",
    "Sauna": "temp",
    "ACControl": "setTarget",
    "AcControl": "setTarget",
}
DEFAULT_TEMPERATURE_COMMAND = "setComfortTemperature"
OPERATING_MODE_COMMAND_BY_CONTROL_TYPE = {
    "ACControl": "setMode",
    "AcControl": "setMode",
}
DEFAULT_OPERATING_MODE_COMMAND = "setOperatingMode"
OPERATING_MODE_STATE_CANDIDATES = ("operatingMode", "mode")
OPERATING_MODE_LIST_STATE_CANDIDATES = (
    "operatingModes",
    "operatingModeList",
    "modeList",
    "modes",
)
TARGET_TEMPERATURE_FEATURE = getattr(ClimateEntityFeature, "TARGET_TEMPERATURE", 0)
PRESET_MODE_FEATURE = getattr(ClimateEntityFeature, "PRESET_MODE", 0)


def _coerce_first_float(values: tuple[object, ...], default: float) -> float:
    for value in values:
        numeric = coerce_float(value)
        if numeric is not None:
            return numeric
    return default


def _temperature_command(control_type: str, temperature: float | int) -> str:
    command_name = TEMPERATURE_COMMAND_BY_CONTROL_TYPE.get(
        control_type, DEFAULT_TEMPERATURE_COMMAND
    )
    return f"{command_name}/{temperature}"


def _operating_mode_command(control_type: str, mode_id: str) -> str:
    command_name = OPERATING_MODE_COMMAND_BY_CONTROL_TYPE.get(
        control_type, DEFAULT_OPERATING_MODE_COMMAND
    )
    return f"{command_name}/{mode_id}"


def _coerce_mode_id(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    try:
        numeric = float(normalized.replace(",", "."))
    except ValueError:
        return normalized
    if numeric.is_integer():
        return str(int(numeric))
    return normalized


def _coerce_mode_name(value: Any, fallback_mode_id: str) -> str:
    mode_name = _first_non_empty_mode_name(value)
    return mode_name if mode_name is not None else f"Mode {fallback_mode_id}"


def _first_non_empty_mode_name(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, Mapping):
        return None

    for key in ("name", "title", "text", "description"):
        nested = value.get(key)
        nested_name = _first_non_empty_mode_name(nested)
        if nested_name is not None:
            return nested_name
    return None


def _mode_sort_key(mode_id: str) -> tuple[int, int | float | str]:
    try:
        numeric = float(mode_id)
    except ValueError:
        return (1, mode_id.casefold())
    if numeric.is_integer():
        return (0, int(numeric))
    return (0, numeric)


def _extract_operating_modes(
    raw: Any,
    *,
    fallback_labels: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    fallback = fallback_labels or {}
    parsed: list[tuple[str, str]] = []

    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            parsed_raw = json.loads(stripped.replace("'", '"'))
        except json.JSONDecodeError:
            return []
        return _extract_operating_modes(parsed_raw, fallback_labels=fallback)

    if isinstance(raw, Mapping):
        for raw_mode_id, raw_mode in sorted(
            raw.items(), key=lambda item: _mode_sort_key(str(item[0]))
        ):
            mode_id = _coerce_mode_id(raw_mode_id)
            if mode_id is None:
                if not isinstance(raw_mode, Mapping):
                    continue
                mode_id = _coerce_mode_id(
                    raw_mode.get("id", raw_mode.get("modeId", raw_mode.get("mode")))
                )
            if mode_id is None:
                continue
            mode_name = fallback.get(mode_id)
            if mode_name is None:
                mode_name = _coerce_mode_name(raw_mode, mode_id)
            parsed.append((mode_id, mode_name))
    elif isinstance(raw, list):
        next_fallback_mode_id = 0
        for item in raw:
            mode_id: str | None = None
            mode_name: str | None = None

            if isinstance(item, Mapping):
                mode_id = _coerce_mode_id(
                    item.get("id", item.get("modeId", item.get("mode")))
                )
                if mode_id is not None:
                    mode_name = fallback.get(mode_id)
                    if mode_name is None:
                        mode_name = _coerce_mode_name(item, mode_id)
            else:
                mode_id = _coerce_mode_id(item)
                if mode_id is not None:
                    mode_name = fallback.get(mode_id)
                    if mode_name is None and isinstance(item, str):
                        mode_name = item.strip() or None

            if mode_id is None:
                mode_id = str(next_fallback_mode_id)
                next_fallback_mode_id += 1
            if not mode_name:
                mode_name = fallback.get(mode_id) or f"Mode {mode_id}"
            parsed.append((mode_id, mode_name))

    deduplicated: dict[str, str] = {}
    for mode_id, mode_name in parsed:
        deduplicated.setdefault(mode_id, mode_name)
    return list(deduplicated.items())


def _build_operating_mode_option_maps(
    modes: list[tuple[str, str]],
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    options: list[str] = []
    option_to_mode_id: dict[str, str] = {}
    mode_id_to_option: dict[str, str] = {}
    seen_options: set[str] = set()

    for mode_id, mode_name in modes:
        option = mode_name
        if option in seen_options:
            option = f"{mode_name} ({mode_id})"
        seen_options.add(option)
        options.append(option)
        option_to_mode_id[option] = mode_id
        mode_id_to_option[mode_id] = option

    return options, option_to_mode_id, mode_id_to_option


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = [
        LoxoneClimateEntity(bridge, control)
        for control in bridge.controls
        if control.type in CLIMATE_CONTROL_TYPES
    ]
    async_add_entities(entities)


class LoxoneClimateEntity(LoxoneEntity, ClimateEntity):
    """Representation of a Loxone room controller."""

    _attr_hvac_modes = [HVACMode.AUTO]
    _attr_hvac_mode = HVACMode.AUTO

    def __init__(self, bridge, control) -> None:
        super().__init__(bridge, control)
        self._current_temp_state_name = first_matching_state_name(
            control, CLIMATE_CURRENT_TEMPERATURE_STATE_CANDIDATES
        )
        self._target_temp_state_name = first_matching_state_name(
            control, CLIMATE_TARGET_TEMPERATURE_STATE_CANDIDATES
        )
        self._humidity_state_name = first_matching_state_name(
            control, CLIMATE_HUMIDITY_STATE_CANDIDATES
        )
        self._co2_state_name = first_matching_state_name(
            control, CLIMATE_CO2_STATE_CANDIDATES
        )
        self._air_quality_state_name = first_matching_state_name(
            control, CLIMATE_AIR_QUALITY_STATE_CANDIDATES
        )
        self._operating_mode_state_name = first_matching_state_name(
            control, OPERATING_MODE_STATE_CANDIDATES
        )
        self._operating_mode_list_state_name = first_matching_state_name(
            control, OPERATING_MODE_LIST_STATE_CANDIDATES
        )
        self._known_operating_modes = self._build_known_operating_modes()

    def _state_float(self, state_name: str | None) -> float | None:
        if state_name is None:
            return None
        return coerce_float(self.state_value(state_name))

    def _first_state_float(self, *state_names: str) -> float | None:
        return coerce_float(self.first_state_value(*state_names))

    @property
    def supported_features(self) -> set[Any]:
        features: set[Any] = set()
        if TARGET_TEMPERATURE_FEATURE:
            features.add(TARGET_TEMPERATURE_FEATURE)
        if PRESET_MODE_FEATURE and self.preset_modes:
            features.add(PRESET_MODE_FEATURE)
        return features

    @property
    def current_temperature(self) -> float | None:
        value = self._state_float(self._current_temp_state_name)
        if value is not None:
            return value
        return self._first_state_float("tempActual", "temperature")

    @property
    def target_temperature(self) -> float | None:
        value = self._state_float(self._target_temp_state_name)
        if value is not None:
            return value
        return self._first_state_float("tempTarget", "comfortTemperature", "setpoint")

    @property
    def current_humidity(self) -> float | None:
        return self._state_float(self._humidity_state_name)

    @property
    def min_temp(self) -> float:
        return _coerce_first_float(
            (
                self.control.details.get("min"),
                self.control.details.get("minTemp"),
            ),
            default=5.0,
        )

    @property
    def max_temp(self) -> float:
        return _coerce_first_float(
            (
                self.control.details.get("max"),
                self.control.details.get("maxTemp"),
            ),
            default=35.0,
        )

    @property
    def target_temperature_step(self) -> float:
        return _coerce_first_float(
            (
                self.control.details.get("step"),
                self.control.details.get("stepTemp"),
            ),
            default=0.5,
        )

    @property
    def temperature_unit(self) -> str:
        unit_state_name = (
            self._target_temp_state_name or self._current_temp_state_name or "tempTarget"
        )
        unit = infer_unit(self.control, unit_state_name)
        return (
            UnitOfTemperature.FAHRENHEIT
            if unit == "°F"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def preset_modes(self) -> list[str] | None:
        options, _, _ = self._current_operating_mode_maps()
        return options or None

    @property
    def preset_mode(self) -> str | None:
        _, _, mode_id_to_option = self._current_operating_mode_maps()
        if not mode_id_to_option:
            return None

        raw_mode = self._state_value(self._operating_mode_state_name)
        mode_id = _coerce_mode_id(raw_mode)
        if mode_id is not None:
            return mode_id_to_option.get(mode_id)

        if isinstance(raw_mode, str):
            stripped = raw_mode.strip()
            if stripped in mode_id_to_option.values():
                return stripped
            lowered = stripped.casefold()
            for option in mode_id_to_option.values():
                if option.casefold() == lowered:
                    return option
        return None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        if self._co2_state_name:
            co2_value = coerce_float(self.state_value(self._co2_state_name))
            if co2_value is not None:
                attrs["co2"] = co2_value
        if self._air_quality_state_name:
            air_value = self.state_value(self._air_quality_state_name)
            numeric = coerce_float(air_value)
            attrs["air_quality"] = numeric if numeric is not None else air_value
        return attrs

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        command = _temperature_command(self.control.type, temperature)
        await self.bridge.async_send_action(
            self.control.uuid_action, command
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        _, option_to_mode_id, _ = self._current_operating_mode_maps()
        mode_id = option_to_mode_id.get(preset_mode)
        if mode_id is None:
            return

        command = _operating_mode_command(self.control.type, mode_id)
        await self.bridge.async_send_action(self.control.uuid_action, command)

    def _state_value(self, state_name: str | None) -> Any:
        if state_name is None:
            return None
        return self.state_value(state_name)

    def _build_known_operating_modes(self) -> list[tuple[str, str]]:
        global_modes = _extract_operating_modes(getattr(self.bridge, "operating_modes", {}))
        global_labels = {mode_id: mode_name for mode_id, mode_name in global_modes}

        for details_key in ("operatingModes", "operatingModeList", "modeList"):
            details_modes = _extract_operating_modes(
                self.control.details.get(details_key),
                fallback_labels=global_labels,
            )
            if details_modes:
                return details_modes

        return global_modes

    def _current_operating_mode_maps(
        self,
    ) -> tuple[list[str], dict[str, str], dict[str, str]]:
        known_labels = {mode_id: mode_name for mode_id, mode_name in self._known_operating_modes}
        dynamic_modes = _extract_operating_modes(
            self._state_value(self._operating_mode_list_state_name),
            fallback_labels=known_labels,
        )
        modes = dynamic_modes or self._known_operating_modes
        return _build_operating_mode_option_maps(modes)
