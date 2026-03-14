"""Text platform for Loxone."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TEXT_CONTROL_TYPES
from .entity import LoxoneEntity
from .runtime import entry_bridge


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = [
        LoxoneTextEntity(bridge, control)
        for control in bridge.controls
        if control.type in TEXT_CONTROL_TYPES
    ]
    async_add_entities(entities)


class LoxoneTextEntity(LoxoneEntity, TextEntity):
    """Representation of a writable text Loxone control."""

    @property
    def native_value(self) -> str:
        value = self.first_state_value("text", "value")
        return "" if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, value)
