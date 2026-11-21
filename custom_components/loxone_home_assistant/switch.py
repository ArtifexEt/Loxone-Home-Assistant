"""Switch platform for Loxone."""

from __future__ import annotations

from collections.abc import Iterable

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
from .intercom import intercom_mute_state_name
from .light import should_expose_as_light
from .models import LoxoneControl
from .options import option_enabled
from .runtime import entry_bridge


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entry_data = getattr(entry, "data", {}) or {}
    expose_controller_children = option_enabled(
        entry.options.get(
            CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS,
            entry_data.get(CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS),
        ),
        DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    )
    entities: list[SwitchEntity] = [
        LoxoneSwitchEntity(bridge, control)
        for control in bridge.controls
        if control.type in SWITCH_CONTROL_TYPES
        and not should_expose_as_light(bridge, control, expose_controller_children)
    ]
    entities.extend(
        LoxoneIntercomMuteSwitch(bridge, control, mute_state_name)
        for control in bridge.controls
        if (mute_state_name := intercom_mute_state_name(control)) is not None
    )
    async_add_entities(entities)


class LoxoneSwitchEntity(LoxoneEntity, SwitchEntity):
    """Representation of a Loxone switch."""

    @property
    def is_on(self) -> bool | None:
        for state_name in (
            "active",
            "value",
            "position",
            "armed",
            "charging",
            "power",
            "isEnabled",
        ):
            value = coerce_bool(self.state_value(state_name))
            if value is not None:
                return value
        for state_uuid in self.control.states.values():
            value = coerce_bool(self.bridge.state_value(state_uuid))
            if value is not None:
                return value
        return None

    async def async_turn_on(self, **kwargs) -> None:
        if self.control.type == "CarCharger":
            await self.bridge.async_send_action(self.control.uuid_action, "charge/on")
            return
        await self.bridge.async_send_action(self.control.uuid_action, "on")

    async def async_turn_off(self, **kwargs) -> None:
        if self.control.type == "CarCharger":
            await self.bridge.async_send_action(self.control.uuid_action, "charge/off")
            return
        await self.bridge.async_send_action(self.control.uuid_action, "off")


class LoxoneIntercomMuteSwitch(LoxoneEntity, SwitchEntity):
    """Control the doorbell mute state reported by a Gen 2 Intercom."""

    _attr_icon = "mdi:bell-off-outline"

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, "Doorbell Mute")
        self._state_name = state_name

    def relevant_state_uuids(self) -> Iterable[str]:
        return (self.control.states[self._state_name],)

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))

    async def async_turn_on(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "mute/1")

    async def async_turn_off(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "mute/0")

