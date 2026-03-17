"""Text platform for Loxone."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
try:
    from homeassistant.components.text import TextMode
except ImportError:  # pragma: no cover - compatibility for lightweight test stubs
    TextMode = None  # type: ignore[assignment]
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import INTERCOM_CONTROL_TYPES, MEDIA_PLAYER_CONTROL_TYPES, TEXT_CONTROL_TYPES
from .entity import LoxoneEntity, normalize_state_name
from .intercom import (
    intercom_tts_message,
    set_intercom_tts_message,
)
from .media_player import (
    audio_tts_message,
    set_audio_tts_message,
)
from .runtime import entry_bridge
INTERCOM_TTS_CONTROL_TYPES_NORMALIZED = {
    normalize_state_name(value) for value in INTERCOM_CONTROL_TYPES
}
AUDIO_TTS_CONTROL_TYPES_NORMALIZED = {
    normalize_state_name(value) for value in MEDIA_PLAYER_CONTROL_TYPES
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities: list[TextEntity] = [
        LoxoneTextEntity(bridge, control)
        for control in bridge.controls
        if control.type in TEXT_CONTROL_TYPES
    ]
    entities.extend(
        LoxoneIntercomTtsMessageEntity(bridge, control)
        for control in bridge.controls
        if _supports_intercom_tts_controls(control)
    )
    entities.extend(
        LoxoneAudioTtsMessageEntity(bridge, control)
        for control in bridge.controls
        if _supports_audio_tts_controls(control)
    )
    async_add_entities(entities)


class LoxoneTextEntity(LoxoneEntity, TextEntity):
    """Representation of a writable text Loxone control."""

    @property
    def native_value(self) -> str:
        value = self.first_state_value("text", "value")
        return "" if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, value)


class LoxoneIntercomTtsMessageEntity(LoxoneEntity, TextEntity):
    """Text helper storing Intercom TTS message."""

    _attr_icon = "mdi:message-text"
    _attr_native_max = 120
    if TextMode is not None:
        _attr_mode = TextMode.TEXT

    def __init__(self, bridge, control) -> None:
        super().__init__(bridge, control, "TTS Message")

    @property
    def native_value(self) -> str:
        return intercom_tts_message(self.bridge, self.control.uuid_action)

    async def async_set_value(self, value: str) -> None:
        set_intercom_tts_message(self.bridge, self.control.uuid_action, value)
        self.async_write_ha_state()


class LoxoneAudioTtsMessageEntity(LoxoneEntity, TextEntity):
    """Text helper storing AudioZone TTS message."""

    _attr_icon = "mdi:message-text"
    _attr_native_max = 120
    if TextMode is not None:
        _attr_mode = TextMode.TEXT

    def __init__(self, bridge, control) -> None:
        super().__init__(bridge, control, "TTS Message")

    @property
    def native_value(self) -> str:
        return audio_tts_message(self.bridge, self.control)

    async def async_set_value(self, value: str) -> None:
        set_audio_tts_message(self.bridge, self.control, value)
        self.async_write_ha_state()


def _supports_intercom_tts_controls(control) -> bool:
    return normalize_state_name(control.type) in INTERCOM_TTS_CONTROL_TYPES_NORMALIZED


def _supports_audio_tts_controls(control) -> bool:
    return normalize_state_name(control.type) in AUDIO_TTS_CONTROL_TYPES_NORMALIZED
