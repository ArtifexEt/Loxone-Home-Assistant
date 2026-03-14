"""Number platform for Loxone."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import NUMBER_CONTROL_TYPES
from .entity import LoxoneEntity, coerce_float, first_numeric_state_name
from .runtime import entry_bridge


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = [
        LoxoneNumberEntity(bridge, control)
        for control in bridge.controls
        if control.type in NUMBER_CONTROL_TYPES
        and _supports_number_control(control)
    ]
    async_add_entities(entities)


class LoxoneNumberEntity(LoxoneEntity, NumberEntity):
    """Representation of a writable numeric Loxone control."""

    @property
    def native_value(self) -> float | None:
        state_name = first_numeric_state_name(self.control)
        return coerce_float(self.state_value(state_name) if state_name else None)

    @property
    def native_min_value(self) -> float:
        return coerce_float(self.control.details.get("min")) or 0.0

    @property
    def native_max_value(self) -> float:
        return coerce_float(self.control.details.get("max")) or 100.0

    @property
    def native_step(self) -> float:
        return coerce_float(self.control.details.get("step")) or 1.0

    async def async_set_native_value(self, value: float) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, str(value))


def _supports_number_control(control) -> bool:
    if control.type == "UpDownLeftRight":
        normalized_states = {state_name.strip().casefold() for state_name in control.states}
        return any(state_name in normalized_states for state_name in ("value", "position", "actual"))
    return first_numeric_state_name(control) is not None
