"""Button platform for Loxone."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import LoxoneConnectionError
from .const import BUTTON_CONTROL_TYPES, INTERCOM_CONTROL_TYPES, MEDIA_PLAYER_CONTROL_TYPES
from .entity import LoxoneEntity, miniserver_device_info, normalize_state_name
from .intercom import (
    build_intercom_tts_command,
    intercom_mute_state_name,
    intercom_tts_message,
    intercom_tts_volume,
    is_intercom_gen2_control,
    resolve_intercom_command,
)
from .media_player import (
    audio_tts_message,
    audio_tts_volume,
    build_audio_tts_command,
    resolve_audio_tts_target_uuid_action,
)
from .models import LoxoneControl
from .runtime import entry_bridge

INTERCOM_BUTTON_SPECS = (
    ("Answer Call", "intercom_stop_ringing", "answer", None, "mdi:bell-cancel-outline"),
)
INTERCOM_GEN2_MUTE_BUTTON_SPECS = (
    ("Mute Microphone", "intercom_mute_doorbell", "mute", ("1",), "mdi:bell-off-outline"),
    ("Unmute Microphone", "intercom_unmute_doorbell", "mute", ("0",), "mdi:bell-outline"),
)
INTERCOM_TTS_CONTROL_TYPES_NORMALIZED = {
    normalize_state_name(value) for value in INTERCOM_CONTROL_TYPES
}
AUDIO_TTS_CONTROL_TYPES_NORMALIZED = {
    normalize_state_name(value) for value in MEDIA_PLAYER_CONTROL_TYPES
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
        gen2 = is_intercom_gen2_control(control)
        if not gen2 and normalize_state_name(control.type) not in INTERCOM_TTS_CONTROL_TYPES_NORMALIZED:
            continue
        specs = (
            INTERCOM_BUTTON_SPECS + INTERCOM_GEN2_MUTE_BUTTON_SPECS
            if gen2
            else INTERCOM_BUTTON_SPECS
        )
        has_mute_switch = intercom_mute_state_name(control) is not None
        entities.extend(
            LoxoneIntercomCommandButton(
                bridge,
                control,
                legacy_suffix,
                translation_key,
                command_name,
                command_args,
                icon,
                enabled_by_default=command_name != "mute" or not has_mute_switch,
            )
            for (
                legacy_suffix,
                translation_key,
                command_name,
                command_args,
                icon,
            ) in specs
        )
    return entities


def _build_intercom_tts_buttons(bridge) -> list[ButtonEntity]:
    return [
        LoxoneIntercomTtsButton(bridge, control)
        for control in bridge.controls
        if _supports_intercom_tts_controls(control)
    ]


def _build_audio_tts_buttons(bridge) -> list[ButtonEntity]:
    return [
        LoxoneAudioTtsButton(bridge, control)
        for control in bridge.controls
        if _supports_audio_tts_controls(control)
    ]


def _build_hub_action_buttons(bridge, entry: ConfigEntry) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = [
        LoxoneHubRestartButton(bridge),
        LoxoneHubRefreshEntitiesButton(bridge, entry),
    ]
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
    entities.extend(_build_audio_tts_buttons(bridge))
    entities.extend(_build_hub_action_buttons(bridge, entry))
    async_add_entities(entities)


class LoxoneButtonEntity(LoxoneEntity, ButtonEntity):
    """Representation of a Loxone momentary push button."""

    async def async_press(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "pulse")


class LoxoneIntercomCommandButton(LoxoneEntity, ButtonEntity):
    """Action button for a predefined Intercom command."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        legacy_suffix: str,
        translation_key: str,
        command: str,
        arguments: Sequence[str] | None,
        icon: str,
        *,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(bridge, control, legacy_suffix)
        self._set_intercom_entity_name("button", translation_key)
        self._command = resolve_intercom_command(command, list(arguments or []))
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = enabled_by_default

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
        self._set_intercom_entity_name("button", "intercom_send_tts")

    async def async_press(self) -> None:
        message = intercom_tts_message(self.bridge, self.control.uuid_action)
        command = build_intercom_tts_command(message)
        if command is None:
            return
        await self.bridge.async_send_action(self.control.uuid_action, command)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs["intercom_tts_volume"] = intercom_tts_volume(
            self.bridge,
            self.control.uuid_action,
        )
        return attrs


class LoxoneAudioTtsButton(LoxoneEntity, ButtonEntity):
    """Action button sending configured TTS message to AudioZone targets."""

    _attr_icon = "mdi:bullhorn"

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "Send TTS")

    async def async_press(self) -> None:
        message = audio_tts_message(self.bridge, self.control)
        volume = audio_tts_volume(self.bridge, self.control)
        command = build_audio_tts_command(message, volume)
        if command is None:
            return
        target_uuid_action = resolve_audio_tts_target_uuid_action(
            self.bridge,
            self.control,
        )
        await self.bridge.async_send_action(target_uuid_action, command)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs["audio_tts_message"] = audio_tts_message(
            self.bridge,
            self.control,
        )
        attrs["audio_tts_volume"] = audio_tts_volume(
            self.bridge,
            self.control,
        )
        attrs["audio_tts_target_uuid_action"] = resolve_audio_tts_target_uuid_action(
            self.bridge,
            self.control,
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


class LoxoneHubRefreshEntitiesButton(LoxoneHubActionButton):
    """Re-import the Miniserver project through a Home Assistant entry reload."""

    _attr_translation_key = "refresh_entities"
    _attr_icon = "mdi:sync"

    def __init__(self, bridge, entry: ConfigEntry) -> None:
        super().__init__(bridge, "refresh-entities")
        self._entry = entry

    async def async_press(self) -> None:
        if self._entry.state is ConfigEntryState.LOADED and not self._entry.setup_lock.locked():
            self.hass.config_entries.async_schedule_reload(self._entry.entry_id)


class LoxoneHubRefreshSystemStatsButton(LoxoneHubActionButton):
    """Hub action button for refreshing Miniserver system diagnostics."""

    _attr_name = "Refresh System Stats"
    _attr_icon = "mdi:chart-line"

    def __init__(self, bridge) -> None:
        super().__init__(bridge, "refresh-system-stats")

    async def async_press(self) -> None:
        await self.bridge.async_refresh_system_stats(force=True)


def _supports_intercom_tts_controls(control: LoxoneControl) -> bool:
    return normalize_state_name(control.type) in INTERCOM_TTS_CONTROL_TYPES_NORMALIZED


def _supports_audio_tts_controls(control: LoxoneControl) -> bool:
    return normalize_state_name(control.type) in AUDIO_TTS_CONTROL_TYPES_NORMALIZED
