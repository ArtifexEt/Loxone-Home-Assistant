"""Switch platform for Loxone."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    DOMAIN,
    SWITCH_CONTROL_TYPES,
)
from .entity import LoxoneEntity, coerce_bool
from .light import should_expose_as_light


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = hass.data[DOMAIN]["bridges"][entry.entry_id]
    expose_controller_children = _option_enabled(
        entry.options.get(CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS),
        DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    )
    entities = [
        LoxoneSwitchEntity(bridge, control)
        for control in bridge.controls
        if control.type in SWITCH_CONTROL_TYPES
        and not should_expose_as_light(bridge, control, expose_controller_children)
    ]
    async_add_entities(entities)


class LoxoneSwitchEntity(LoxoneEntity, SwitchEntity):
    """Representation of a Loxone switch."""

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.first_state_value("active", "value", "position"))

    async def async_turn_on(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "on")

    async def async_turn_off(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "off")


def _option_enabled(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
    return default
