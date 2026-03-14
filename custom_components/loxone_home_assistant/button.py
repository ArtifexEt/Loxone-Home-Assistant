"""Button platform for Loxone."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import LoxoneConnectionError
from .const import BUTTON_CONTROL_TYPES
from .entity import LoxoneEntity, miniserver_device_info
from .runtime import entry_bridge


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = [
        LoxoneButtonEntity(bridge, control)
        for control in bridge.controls
        if control.type in BUTTON_CONTROL_TYPES
    ]
    entities.append(LoxoneHubRestartButton(bridge))
    async_add_entities(entities)


class LoxoneButtonEntity(LoxoneEntity, ButtonEntity):
    """Representation of a Loxone momentary push button."""

    async def async_press(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "pulse")


class LoxoneHubRestartButton(ButtonEntity):
    """Hub action button for restarting the Miniserver."""

    _attr_has_entity_name = True
    _attr_name = "Restart Miniserver"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart-alert"

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._attr_unique_id = f"{bridge.serial}-restart-miniserver"

    @property
    def available(self) -> bool:
        return self.bridge.available

    @property
    def device_info(self):
        return miniserver_device_info(self.bridge)

    async def async_press(self) -> None:
        try:
            await self.bridge.async_send_raw_command("jdev/sys/reboot")
        except LoxoneConnectionError:
            # Reboot can close the websocket before command acknowledgment arrives.
            return
