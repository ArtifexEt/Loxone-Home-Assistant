"""Climate platform for Loxone."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
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
from .entity import LoxoneEntity, coerce_bool, coerce_float, first_matching_state_name, infer_unit
from .protocol import deserialize_value
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
    "IRoomController": "mode",
    "RoomController": "mode",
    "PoolController": "operatingMode",
    "Sauna": "mode",
}
DEFAULT_OPERATING_MODE_COMMAND = "setOperatingMode"
_ROOM_CONTROLLER_V1_MODES = {
    "0": "Automatic",
    "3": "Automatic heating",
    "4": "Automatic cooling",
    "5": "Manual heating",
    "6": "Manual cooling",
}
_ROOM_CONTROLLER_V2_MODES = {
    "0": "Automatic",
    "1": "Automatic heating",
    "2": "Automatic cooling",
    "3": "Manual",
    "4": "Manual heating",
    "5": "Manual cooling",
    "-1": "Off",
}
_ROOM_CONTROLLER_MODES = {
    "IRoomController": _ROOM_CONTROLLER_V1_MODES,
    "RoomController": _ROOM_CONTROLLER_V1_MODES,
    "IRoomControllerV2": _ROOM_CONTROLLER_V2_MODES,
    "RoomControllerV2": _ROOM_CONTROLLER_V2_MODES,
}
_AC_CONTROL_TYPES = {"ACControl", "AcControl"}
_IRC_V1_TYPES = {"IRoomController", "RoomController"}
_IRC_V2_TYPES = {"IRoomControllerV2", "RoomControllerV2"}
_IRC_V1_HVAC_MODES = {"0": HVACMode.AUTO, "3": HVACMode.HEAT, "4": HVACMode.COOL}
_IRC_V2_HVAC_MODES = {
    "0": HVACMode.AUTO,
    "1": HVACMode.HEAT,
    "2": HVACMode.COOL,
    "-1": HVACMode.OFF,
}
_POOL_HVAC_MODES = {
    "1": HVACMode.AUTO,
    "2": HVACMode.HEAT,
    "3": HVACMode.COOL,
    "0": HVACMode.OFF,
}
_POOL_OPERATING_MODES = {"0": "Out of order", "1": "Automatic", "2": "Service"}
_SAUNA_MODES = {
    "0": "Off",
    "1": "Finnish manual",
    "2": "Humidity manual",
    "3": "Finnish automatic",
    "4": "Herbal sauna",
    "5": "Soft steam bath",
    "6": "Warm air bath",
}
_AC_HVAC_MODES = {
    "1": HVACMode.AUTO,
    "2": HVACMode.HEAT,
    "3": HVACMode.COOL,
    "4": HVACMode.DRY,
    "5": HVACMode.FAN_ONLY,
}
_SAUNA_FAN_MODES = {"on": "fanon", "off": "fanoff"}
OPERATING_MODE_STATE_CANDIDATES = ("operatingMode", "mode")
OPERATING_MODE_LIST_STATE_CANDIDATES = (
    "operatingModes",
    "operatingModeList",
    "modeList",
    "modes",
)


def _coerce_first_float(values: tuple[object, ...], default: float) -> float:
    for value in values:
        numeric = coerce_float(value)
        if numeric is not None and math.isfinite(numeric):
            return numeric
    return default


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


def _extract_operating_modes(raw: Any, *, first_mode_id: int) -> dict[str, str]:
    payload = deserialize_value(raw)
    if isinstance(payload, Mapping):
        entries = payload.items()
    elif isinstance(payload, list):
        entries = enumerate(payload, start=first_mode_id)
    else:
        return {}

    modes: dict[str, str] = {}
    for key, entry in entries:
        mode_id = _coerce_mode_id(key)
        if entry is None or entry == "":
            continue
        if isinstance(entry, Mapping):
            if entry.get("enabled") is False:
                continue
            mode_id = _coerce_mode_id(
                entry.get("id", entry.get("modeId", entry.get("mode")))
            ) or mode_id
        if mode_id is not None:
            label = _first_non_empty_mode_name(entry) or f"Mode {mode_id}"
            modes.setdefault(mode_id, label)
    return modes


def _mode_ids_by_option(modes: Mapping[str, str]) -> dict[str, str]:
    mode_ids: dict[str, str] = {}

    for mode_id, mode_name in modes.items():
        option = mode_name
        while option in mode_ids:
            option = f"{option} ({mode_id})"
        mode_ids[option] = mode_id

    return mode_ids


def _mode_label(modes: Mapping[str, str], value: Any) -> str | None:
    mode_id = _coerce_mode_id(value)
    if mode_id is None:
        return None
    return next(
        (
            option for option, code in modes.items()
            if code == mode_id or option.casefold() == mode_id.casefold()
        ),
        None,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    entities = [
        LoxoneClimateEntity(bridge, control)
        for control in bridge.controls
        if control.type in CLIMATE_CONTROL_TYPES
    ]
    async_add_entities(entities)


class LoxoneClimateEntity(LoxoneEntity, ClimateEntity):
    """Representation of a Loxone room controller."""

    _enable_turn_on_off_backwards_compatibility = False

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
        mode_states = (
            ("currentOpMode", *OPERATING_MODE_STATE_CANDIDATES)
            if control.type == "PoolController"
            else OPERATING_MODE_STATE_CANDIDATES
        )
        self._operating_mode_state_name = first_matching_state_name(control, mode_states)
        self._operating_mode_list_state_name = first_matching_state_name(
            control, OPERATING_MODE_LIST_STATE_CANDIDATES
        )
        # ACControl.mode is 1-based for its array of configured mode labels.
        self._first_mode_id = 1 if control.type in _AC_CONTROL_TYPES else 0
        self._known_operating_modes = self._build_known_operating_modes()
        self._linked_ac_controls = self._resolve_linked_ac_controls()

    def _resolve_linked_ac_controls(self) -> tuple:
        if self.control.type not in _IRC_V2_TYPES:
            return ()
        references = self.control.details.get("linkedAcControls")
        if not isinstance(references, list) or not references:
            return ()
        controls = tuple(
            self.bridge.control_for_uuid_action(reference)
            for reference in references
            if isinstance(reference, str)
        )
        if len(controls) != len(references) or not all(
            control is not None and control.type in _AC_CONTROL_TYPES for control in controls
        ):
            return ()
        return controls

    def relevant_state_uuids(self):
        uuids = set(super().relevant_state_uuids())
        for control in self._linked_ac_controls:
            uuids.update(
                state_uuid
                for name in ("fanspeeds", "airflows")
                if (state_uuid := control.state_uuid(name)) is not None
            )
        return uuids

    @property
    def hvac_modes(self) -> list[HVACMode]:
        if self.control.type in _AC_CONTROL_TYPES:
            return [HVACMode.OFF, *self._ac_native_modes()]
        if self.control.type == "Sauna":
            return [HVACMode.OFF, HVACMode.HEAT]
        return list(self._hvac_mode_ids())

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self.control.type in _AC_CONTROL_TYPES:
            power = coerce_bool(self.state_value("status"))
            if power is False:
                return HVACMode.OFF
            if power is None and self.control.state_uuid("status") is not None:
                return None
            return _AC_HVAC_MODES.get(self._read_mode_id())
        if self.control.type == "Sauna":
            active = coerce_bool(self.state_value("active"))
            if active is None:
                return None
            return HVACMode.HEAT if active else HVACMode.OFF
        if self.control.type == "PoolController":
            mode = _coerce_mode_id(self.state_value("currentTempMode"))
            return _POOL_HVAC_MODES.get({"4": "2", "5": "3"}.get(mode, mode))
        mode = self._read_mode_id()
        if self.control.type in _IRC_V1_TYPES:
            return _IRC_V1_HVAC_MODES.get({"1": "0", "2": "0", "5": "3", "6": "4"}.get(mode, mode))
        return _IRC_V2_HVAC_MODES.get({"3": "0", "4": "1", "5": "2"}.get(mode, mode))

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode == HVACMode.OFF or (
            self.control.type in _IRC_V1_TYPES and coerce_bool(self.state_value("stop")) is True
        ):
            return HVACAction.OFF
        if self.control.type == "Sauna":
            power = coerce_bool(self.state_value("power"))
            return {True: HVACAction.HEATING, False: HVACAction.IDLE}.get(power)
        if self.control.type in _IRC_V2_TYPES:
            return {-1: HVACAction.COOLING, 1: HVACAction.HEATING}.get(self._state_float("prepareState"))
        return None

    def _read_mode_id(self) -> str | None:
        return _coerce_mode_id(self._state_value(self._operating_mode_state_name))

    def _hvac_mode_ids(self) -> dict[HVACMode, str]:
        control_type = self.control.type
        heat = cool = True
        if control_type == "PoolController":
            modes = _POOL_HVAC_MODES
            heat = coerce_bool(self.control.details.get("hasHeating")) is not False
            cool = coerce_bool(self.control.details.get("hasCooling")) is not False
        elif control_type in _IRC_V1_TYPES:
            modes = _IRC_V1_HVAC_MODES
            restricted = _coerce_mode_id(self.control.details.get("restrictedToMode"))
            heat, cool = restricted != "1", restricted != "2"
        else:
            modes = _IRC_V2_HVAC_MODES
            capabilities = _coerce_mode_id(self.control.details.get("possibleCapabilities"))
            if capabilities is not None and capabilities.isdigit():
                bits = int(capabilities)
                heat, cool = bool(bits & 1), bool(bits & (2 | 32))
        return {
            mode: code
            for code, mode in modes.items()
            if (mode != HVACMode.HEAT or heat) and (mode != HVACMode.COOL or cool)
        }

    def _ac_native_modes(self) -> dict[HVACMode, str]:
        configured_ids = set(self._current_mode_ids_by_option().values())
        configured_ids.add(self._read_mode_id())
        return {
            mode: code for code, mode in _AC_HVAC_MODES.items() if code in configured_ids
        }

    def _state_float(self, state_name: str | None) -> float | None:
        if state_name is None:
            return None
        return coerce_float(self.state_value(state_name))

    def _first_state_float(self, *state_names: str) -> float | None:
        return coerce_float(self.first_state_value(*state_names))

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature(0)
        if self._target_temp_state_name is not None:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        if self.fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self.swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        if self._supports_target_humidity():
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        if HVACMode.OFF in self.hvac_modes:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        return features

    @property
    def native_current_temperature(self) -> float | None:
        value = self._state_float(self._current_temp_state_name)
        if value is not None:
            return value
        return self._first_state_float("tempActual", "temperature")

    @property
    def native_target_temperature(self) -> float | None:
        value = self._state_float(self._target_temp_state_name)
        if value is not None:
            return value
        return self._first_state_float("tempTarget", "comfortTemperature", "setpoint")

    @property
    def current_humidity(self) -> float | None:
        return self._state_float(self._humidity_state_name)

    @property
    def target_humidity(self) -> float | None:
        return self._state_float("humidityTarget") if self._supports_target_humidity() else None

    def _supports_target_humidity(self) -> bool:
        return (
            self.control.type == "Sauna"
            and bool(self.control.state_uuid("humidityTarget"))
            and coerce_bool(self.control.details.get("hasVaporizer")) is not False
        )

    @property
    def min_temp(self) -> float:
        return _coerce_first_float(
            (
                self.state_value("minTemp"),
                self.control.details.get("min"),
                self.control.details.get("minTemp"),
            ),
            default=self._default_temperature_limits()[0],
        )

    @property
    def max_temp(self) -> float:
        return _coerce_first_float(
            (
                self.state_value("maxTemp"),
                self.control.details.get("max"),
                self.control.details.get("maxTemp"),
            ),
            default=self._default_temperature_limits()[1],
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
    def native_temperature_unit(self) -> str:
        unit_state_name = (
            self._target_temp_state_name or self._current_temp_state_name or "tempTarget"
        )
        unit = infer_unit(self.control, unit_state_name)
        return (
            UnitOfTemperature.FAHRENHEIT
            if unit == "°F"
            else UnitOfTemperature.CELSIUS
        )

    def _default_temperature_limits(self) -> tuple[float, float]:
        if self.control.type == "Sauna":
            limits = (30.0, 120.0)
        elif self.control.type in _AC_CONTROL_TYPES:
            limits = (1.0, 40.0)
        else:
            limits = (5.0, 35.0)
        if self.native_temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return limits[0] * 9 / 5 + 32, limits[1] * 9 / 5 + 32
        return limits

    # Climate entities used the non-native names until Home Assistant 2026.11.
    if not hasattr(ClimateEntity, "native_temperature_unit"):
        temperature_unit = native_temperature_unit
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature

    def _ac_options(self, state_name: str, details_key: str) -> dict[str, str]:
        if self.control.type in _AC_CONTROL_TYPES:
            controls = (self.control,)
        else:
            controls = self._linked_ac_controls
        option_sets = []
        for control in controls:
            raw = self.bridge.control_state(control, state_name)
            if raw is None:
                raw = control.details.get(details_key, control.details.get(state_name))
            payload = deserialize_value(raw)
            first_id = 1
            if state_name == "fanspeeds" and isinstance(payload, list) and payload:
                if len(payload) == 8 or _first_non_empty_mode_name(payload[0]) == "Off":
                    first_id = 0
            option_sets.append(_extract_operating_modes(payload, first_mode_id=first_id))
        if not option_sets:
            return {}
        agreed_modes = {
            code: name
            for code, name in option_sets[0].items()
            if all(other.get(code) == name for other in option_sets[1:])
        }
        return _mode_ids_by_option(agreed_modes)

    def _fan_options(self) -> dict[str, str]:
        if self.control.type in _IRC_V2_TYPES and self.control.state_uuid("fan") is None:
            return {}
        return self._ac_options("fanspeeds", "fanspeed")

    def _swing_options(self) -> dict[str, str]:
        if self.control.type in _IRC_V2_TYPES and self.control.state_uuid("ventMode") is None:
            return {}
        return self._ac_options("airflows", "airflow")

    @property
    def fan_modes(self) -> list[str] | None:
        if self.control.type == "Sauna" and self.control.state_uuid("fan"):
            return list(_SAUNA_FAN_MODES)
        return list(self._fan_options()) or None

    @property
    def fan_mode(self) -> str | None:
        if self.control.type == "Sauna":
            active = coerce_bool(self.state_value("fan"))
            return {True: "on", False: "off"}.get(active)
        raw = self.state_value("fan")
        if self.control.type in _AC_CONTROL_TYPES and self.hvac_mode == HVACMode.OFF:
            default_fan = self.state_value("defaultFan")
            raw = default_fan if default_fan is not None else raw
        return _mode_label(self._fan_options(), raw)

    @property
    def swing_modes(self) -> list[str] | None:
        return list(self._swing_options()) or None

    @property
    def swing_mode(self) -> str | None:
        return _mode_label(self._swing_options(), self.state_value("ventMode"))

    @property
    def preset_modes(self) -> list[str] | None:
        options = list(self._current_mode_ids_by_option())
        return options or None

    @property
    def preset_mode(self) -> str | None:
        mode = self._read_mode_id()
        if self.control.type in _IRC_V1_TYPES and mode in {"1", "2"}:
            mode = "0"
        return _mode_label(self._current_mode_ids_by_option(), mode)

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
        hvac_mode = kwargs.get("hvac_mode")
        if hvac_mode is not None and hvac_mode != self.hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
        temperature = coerce_float(kwargs.get("temperature"))
        if temperature is None or not math.isfinite(temperature):
            return
        command = self._temperature_command(hvac_mode)
        await self._send_command(f"{command}/{temperature:g}")

    def _temperature_command(self, hvac_mode: HVACMode | None) -> str:
        control_type = self.control.type
        mode_id = self._read_mode_id()
        if hvac_mode is not None:
            selected_mode = self._hvac_mode_ids().get(hvac_mode)
            if selected_mode is not None:
                mode_id = selected_mode
        if control_type in _IRC_V1_TYPES:
            if mode_id in {"5", "6"}:
                return "settemp/7"
            return "settemp/2" if mode_id in {"2", "4"} else "settemp/1"
        if control_type in _IRC_V2_TYPES:
            if mode_id in {"3", "4", "5"}:
                return "setManualTemperature"
            if (
                mode_id == "2"
                and coerce_bool(self.control.details.get("SingleComfortTemperature")) is not True
                and self.control.state_uuid("comfortTemperatureCool")
            ):
                return "setComfortTemperatureCool"
        return TEMPERATURE_COMMAND_BY_CONTROL_TYPE.get(control_type, DEFAULT_TEMPERATURE_COMMAND)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self.hvac_modes:
            return
        control_type = self.control.type
        if control_type == "Sauna":
            await self._send_command("off" if hvac_mode == HVACMode.OFF else "on")
            return
        if control_type in _AC_CONTROL_TYPES:
            if hvac_mode == HVACMode.OFF:
                await self._send_command("off")
                return
            powered_off = self.hvac_mode == HVACMode.OFF
            mode_id = self._ac_native_modes().get(hvac_mode)
            if mode_id is not None:
                await self._send_command(f"setMode/{mode_id}")
            if powered_off or mode_id is None:
                await self._send_command("on")
            return
        mode_id = self._hvac_mode_ids()[hvac_mode]
        command = (
            f"tempMode/{mode_id}"
            if control_type == "PoolController"
            else _operating_mode_command(control_type, mode_id)
        )
        await self._send_command(command)

    async def async_turn_on(self) -> None:
        if self.control.type in _AC_CONTROL_TYPES or self.control.type == "Sauna":
            await self._send_command("on")
        elif HVACMode.AUTO in self.hvac_modes:
            await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if self.control.type == "Sauna":
            command = _SAUNA_FAN_MODES.get(fan_mode)
        else:
            mode_id = self._fan_options().get(fan_mode)
            command = f"setFan/{mode_id}" if mode_id is not None else None
        if command is not None:
            await self._send_command(command)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        mode_id = self._swing_options().get(swing_mode)
        if mode_id is not None:
            await self._send_command(f"setAirDir/{mode_id}")

    async def async_set_humidity(self, humidity: int) -> None:
        if self._supports_target_humidity():
            await self._send_command(f"humidity/{humidity}")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        mode_id = self._current_mode_ids_by_option().get(preset_mode)
        if mode_id is None:
            return

        await self._send_command(_operating_mode_command(self.control.type, mode_id))

    async def _send_command(self, command: str) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, command)

    def _state_value(self, state_name: str | None) -> Any:
        if state_name is None:
            return None
        return self.state_value(state_name)

    def _build_known_operating_modes(self) -> dict[str, str]:
        if room_modes := _ROOM_CONTROLLER_MODES.get(self.control.type):
            # Room controller mode IDs are independent of the Miniserver's
            # schedule IDs and the room controller's comfort/eco timer modes.
            return room_modes
        if self.control.type == "PoolController":
            return _POOL_OPERATING_MODES
        if self.control.type == "Sauna":
            return _SAUNA_MODES if coerce_bool(self.control.details.get("hasVaporizer")) is not False else {}
        for details_key in OPERATING_MODE_LIST_STATE_CANDIDATES:
            details_modes = _extract_operating_modes(
                self.control.details.get(details_key),
                first_mode_id=self._first_mode_id,
            )
            if details_modes:
                return details_modes

        return {}

    def _current_mode_ids_by_option(self) -> dict[str, str]:
        if self._operating_mode_state_name is None:
            return {}
        if self.control.type not in _AC_CONTROL_TYPES:
            return _mode_ids_by_option(self._known_operating_modes)
        dynamic_modes = _extract_operating_modes(
            self._state_value(self._operating_mode_list_state_name),
            first_mode_id=self._first_mode_id,
        )
        return _mode_ids_by_option(dynamic_modes or self._known_operating_modes)
