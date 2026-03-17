"""Number platform for Loxone."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import INTERCOM_CONTROL_TYPES, MEDIA_PLAYER_CONTROL_TYPES, NUMBER_CONTROL_TYPES
from .entity import LoxoneEntity, coerce_float, first_numeric_state_name, normalize_state_name
from .intercom import (
    intercom_tts_volume,
    set_intercom_tts_volume,
)
from .media_player import (
    audio_tts_volume,
    set_audio_tts_volume,
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
    entities: list[NumberEntity] = [
        LoxoneNumberEntity(bridge, control)
        for control in bridge.controls
        if control.type in NUMBER_CONTROL_TYPES
        and _supports_number_control(control)
    ]
    entities.extend(
        LoxoneIntercomTtsVolumeEntity(bridge, control)
        for control in bridge.controls
        if _supports_intercom_tts_controls(control)
    )
    entities.extend(
        LoxoneAudioTtsVolumeEntity(bridge, control)
        for control in bridge.controls
        if _supports_audio_tts_controls(control)
    )
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


class LoxoneIntercomTtsVolumeEntity(LoxoneEntity, NumberEntity):
    """Number helper storing Intercom TTS volume."""

    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0

    def __init__(self, bridge, control) -> None:
        super().__init__(bridge, control, "TTS Volume")

    @property
    def native_value(self) -> float:
        return float(intercom_tts_volume(self.bridge, self.control.uuid_action))

    async def async_set_native_value(self, value: float) -> None:
        set_intercom_tts_volume(self.bridge, self.control.uuid_action, value)
        self.async_write_ha_state()


class LoxoneAudioTtsVolumeEntity(LoxoneEntity, NumberEntity):
    """Number helper storing AudioZone TTS volume."""

    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0

    def __init__(self, bridge, control) -> None:
        super().__init__(bridge, control, "TTS Volume")

    @property
    def native_value(self) -> float:
        return float(audio_tts_volume(self.bridge, self.control))

    async def async_set_native_value(self, value: float) -> None:
        set_audio_tts_volume(self.bridge, self.control, value)
        self.async_write_ha_state()


def _supports_number_control(control) -> bool:
    if control.type == "UpDownLeftRight":
        normalized_states = {state_name.strip().casefold() for state_name in control.states}
        return any(state_name in normalized_states for state_name in ("value", "position", "actual"))
    return first_numeric_state_name(control) is not None


def _supports_intercom_tts_controls(control) -> bool:
    return normalize_state_name(control.type) in INTERCOM_TTS_CONTROL_TYPES_NORMALIZED


def _supports_audio_tts_controls(control) -> bool:
    return normalize_state_name(control.type) in AUDIO_TTS_CONTROL_TYPES_NORMALIZED
