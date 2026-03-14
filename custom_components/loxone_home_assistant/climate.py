"""Climate platform for Loxone."""

from __future__ import annotations

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
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

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

    @property
    def current_temperature(self) -> float | None:
        if self._current_temp_state_name:
            return coerce_float(self.state_value(self._current_temp_state_name))
        return coerce_float(self.first_state_value("tempActual", "temperature"))

    @property
    def target_temperature(self) -> float | None:
        if self._target_temp_state_name:
            return coerce_float(self.state_value(self._target_temp_state_name))
        return coerce_float(
            self.first_state_value("tempTarget", "comfortTemperature", "setpoint")
        )

    @property
    def current_humidity(self) -> float | None:
        if self._humidity_state_name is None:
            return None
        return coerce_float(self.state_value(self._humidity_state_name))

    @property
    def min_temp(self) -> float:
        return (
            coerce_float(self.control.details.get("min"))
            or coerce_float(self.control.details.get("minTemp"))
            or 5.0
        )

    @property
    def max_temp(self) -> float:
        return (
            coerce_float(self.control.details.get("max"))
            or coerce_float(self.control.details.get("maxTemp"))
            or 35.0
        )

    @property
    def target_temperature_step(self) -> float:
        return (
            coerce_float(self.control.details.get("step"))
            or coerce_float(self.control.details.get("stepTemp"))
            or 0.5
        )

    @property
    def temperature_unit(self) -> str:
        unit_state_name = self._target_temp_state_name or self._current_temp_state_name or "tempTarget"
        unit = infer_unit(self.control, unit_state_name)
        return UnitOfTemperature.FAHRENHEIT if unit == "°F" else UnitOfTemperature.CELSIUS

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
        if self.control.type == "PoolController":
            command = f"targetTemp/{temperature}"
        elif self.control.type == "Sauna":
            command = f"temp/{temperature}"
        else:
            command = f"setComfortTemperature/{temperature}"
        await self.bridge.async_send_action(
            self.control.uuid_action, command
        )
