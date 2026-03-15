"""Button platform for Loxone."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import LoxoneConnectionError
from .const import BUTTON_CONTROL_TYPES, INTERCOM_CONTROL_TYPES
from .entity import LoxoneEntity, miniserver_device_info, normalize_state_name
from .intercom import (
    build_intercom_tts_command,
    intercom_tts_message,
    intercom_tts_volume,
    is_intercom_control,
    resolve_intercom_command,
)
from .models import LoxoneControl
from .runtime import entry_bridge

INTERCOM_GEN2_BUTTON_SPECS = (
    ("Answer Call", "answer", None, "mdi:phone-check"),
    ("Mute Microphone", "mute", ("1",), "mdi:microphone-off"),
    ("Unmute Microphone", "mute", ("0",), "mdi:microphone"),
)
INTERCOM_TTS_CONTROL_TYPES_NORMALIZED = {
    normalize_state_name(value) for value in INTERCOM_CONTROL_TYPES
}


def _build_control_buttons(bridge) -> list[ButtonEntity]:
    return [
        LoxoneButtonEntity(bridge, control)
        for control in bridge.controls
        if control.type in BUTTON_CONTROL_TYPES
    ]


def _build_intercom_command_buttons(bridge) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = []
    for control in bridge.controls:
        if not _is_intercom_gen2_control(control):
            continue
        entities.extend(
            LoxoneIntercomCommandButton(
                bridge,
                control,
                name_suffix,
                command_name,
                command_args,
                icon,
            )
            for name_suffix, command_name, command_args, icon in INTERCOM_GEN2_BUTTON_SPECS
        )
    return entities


def _build_intercom_tts_buttons(bridge) -> list[ButtonEntity]:
    return [
        LoxoneIntercomTtsButton(bridge, control)
        for control in bridge.controls
        if _supports_intercom_tts_controls(control)
    ]


def _build_hub_action_buttons(bridge) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = [LoxoneHubRestartButton(bridge)]
    if callable(getattr(bridge, "async_refresh_system_stats", None)):
        entities.append(LoxoneHubRefreshSystemStatsButton(bridge))
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities: list[ButtonEntity] = []
    entities.extend(_build_control_buttons(bridge))
    entities.extend(_build_intercom_command_buttons(bridge))
    entities.extend(_build_intercom_tts_buttons(bridge))
    entities.extend(_build_hub_action_buttons(bridge))
    async_add_entities(entities)


class LoxoneButtonEntity(LoxoneEntity, ButtonEntity):
    """Representation of a Loxone momentary push button."""

    async def async_press(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "pulse")


class LoxoneIntercomCommandButton(LoxoneEntity, ButtonEntity):
    """Action button for a predefined Intercom Gen2 command."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        suffix: str,
        command: str,
        arguments: Sequence[str] | None,
        icon: str,
    ) -> None:
        super().__init__(bridge, control, suffix)
        self._command = resolve_intercom_command(command, list(arguments or []))
        self._attr_icon = icon

    async def async_press(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, self._command)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs["intercom_command"] = self._command
        return attrs


class LoxoneIntercomTtsButton(LoxoneEntity, ButtonEntity):
    """Action button sending configured TTS message to Intercom."""

    _attr_icon = "mdi:bullhorn"

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "Send TTS")

    async def async_press(self) -> None:
        message = intercom_tts_message(self.bridge, self.control.uuid_action)
        volume = intercom_tts_volume(self.bridge, self.control.uuid_action)
        command = build_intercom_tts_command(message, volume)
        if command is None:
            return
        await self.bridge.async_send_action(self.control.uuid_action, command)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs["intercom_tts_message"] = intercom_tts_message(
            self.bridge,
            self.control.uuid_action,
        )
        attrs["intercom_tts_volume"] = intercom_tts_volume(
            self.bridge,
            self.control.uuid_action,
        )
        return attrs


class LoxoneHubActionButton(ButtonEntity):
    """Base class for Miniserver-level action buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, bridge, unique_id_suffix: str) -> None:
        self.bridge = bridge
        self._attr_unique_id = f"{bridge.serial}-{unique_id_suffix}"

    @property
    def available(self) -> bool:
        return self.bridge.available

    @property
    def device_info(self):
        return miniserver_device_info(self.bridge)


class LoxoneHubRestartButton(LoxoneHubActionButton):
    """Hub action button for restarting the Miniserver."""

    _attr_name = "Restart Miniserver"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, bridge) -> None:
        super().__init__(bridge, "restart-miniserver")

    async def async_press(self) -> None:
        try:
            await self.bridge.async_send_raw_command("jdev/sys/reboot")
        except LoxoneConnectionError:
            # Reboot can close the websocket before command acknowledgment arrives.
            return


class LoxoneHubRefreshSystemStatsButton(LoxoneHubActionButton):
    """Hub action button for refreshing Miniserver system diagnostics."""

    _attr_name = "Refresh System Stats"
    _attr_icon = "mdi:chart-line"

    def __init__(self, bridge) -> None:
        super().__init__(bridge, "refresh-system-stats")

    async def async_press(self) -> None:
        await self.bridge.async_refresh_system_stats(force=True)


def _is_intercom_gen2_control(control: LoxoneControl) -> bool:
    if not is_intercom_control(control):
        return False

    normalized_type = normalize_state_name(control.type)
    if "v2" in normalized_type:
        return True

    normalized_states = {
        normalize_state_name(state_name) for state_name in control.states
    }
    return "trustaddress" in normalized_states


def _supports_intercom_tts_controls(control: LoxoneControl) -> bool:
    return normalize_state_name(control.type) in INTERCOM_TTS_CONTROL_TYPES_NORMALIZED
