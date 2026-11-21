"""Number platform for Loxone."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import NUMBER_CONTROL_TYPES
from .entity import (
    LoxoneEntity,
    coerce_bool,
    coerce_float,
    first_matching_state_name,
    infer_unit,
    is_audio_tts_control,
    number_value_state_name,
)
from .media_player import (
    AUDIO_ZONE_CONTROL_TYPE,
    MUSIC_SERVER_TYPE,
    audio_tts_volume,
    resolve_media_server,
    set_audio_tts_volume,
)
from .models import LoxoneControl
from .runtime import entry_bridge

_VENTILATION_LIMITS = (
    ("presenceMin", "Presence Minimum", "hasPresenceMin"),
    ("presenceMax", "Presence Maximum", "hasPresenceMax"),
    ("absenceMin", "Absence Minimum", "hasAbsenceMin"),
    ("absenceMax", "Absence Maximum", "hasAbsenceMax"),
)

_MUSIC_SERVER_VOLUMES = {
    "defaultVolume": "Startup Volume",
    "alarmVolume": "Minimum Alarm Volume",
    "bellVolume": "Minimum Doorbell Volume",
    "buzzerVolume": "Minimum Buzzer Volume",
    "ttsVolume": "Minimum Announcement Volume",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    entities: list[NumberEntity] = []
    for control in bridge.controls:
        if control.type not in NUMBER_CONTROL_TYPES:
            continue
        state_name = number_value_state_name(control)
        if state_name is not None:
            entities.append(LoxoneNumberEntity(bridge, control, state_name))
    entities.extend(
        LoxoneAudioTtsVolumeEntity(bridge, control)
        for control in bridge.controls
        if is_audio_tts_control(control)
    )
    for control in bridge.controls:
        if control.type != AUDIO_ZONE_CONTROL_TYPE or control.is_secured:
            continue
        server = resolve_media_server(bridge, control)
        if server is not None and server.server_type not in {None, MUSIC_SERVER_TYPE}:
            continue
        entities.extend(
            LoxoneMusicServerVolumeEntity(bridge, control, state, name, parameter.lower())
            for parameter, name in _MUSIC_SERVER_VOLUMES.items()
            if (state := first_matching_state_name(control, (parameter,))) is not None
        )
    entities.extend(
        LoxoneVentilationLimit(bridge, control, state, label)
        for control in bridge.controls
        if control.type == "Ventilation" and not control.is_secured
        for state, label, lock_flag in _VENTILATION_LIMITS
        if control.state_uuid(state) and not coerce_bool(control.details.get(lock_flag))
    )
    async_add_entities(entities)


class LoxoneNumberEntity(LoxoneEntity, NumberEntity):
    """Representation of a writable numeric Loxone control."""

    def __init__(
        self, bridge, control: LoxoneControl, state_name: str, label: str | None = None
    ) -> None:
        super().__init__(bridge, control, label)
        self._state_name = state_name
        if (unit := infer_unit(control, state_name)) is not None:
            self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float | None:
        return coerce_float(self.state_value(self._state_name))

    @property
    def native_min_value(self) -> float:
        return self._range_value("min", 0.0)

    @property
    def native_max_value(self) -> float:
        return self._range_value("max", 100.0)

    @property
    def native_step(self) -> float:
        return self._range_value("step", 1.0)

    def _range_value(self, key: str, default: float) -> float:
        # ValueSelector publishes its range as states. Sliders and analog
        # directional inputs provide their range in the structure file.
        value = coerce_float(self.state_value(key))
        if value is None:
            value = coerce_float(self.control.details.get(key))
        if value is None or (key == "step" and value <= 0):
            return default
        return value

    async def async_set_native_value(self, value: float) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, str(value))


class LoxoneVentilationLimit(LoxoneNumberEntity):
    """Set one ventilation intensity limit when it is not wired to Loxone logic."""

    _attr_native_unit_of_measurement = "%"

    def __init__(self, bridge, control: LoxoneControl, state: str, label: str) -> None:
        super().__init__(bridge, control, state, label)
        self._command = f"set{state[0].upper()}{state[1:]}"

    @property
    def native_min_value(self) -> float:
        if self._state_name.endswith("Min"):
            return 0.0
        return coerce_float(self.state_value(f"{self._state_name[:-3]}Min")) or 0.0

    @property
    def native_max_value(self) -> float:
        if self._state_name.endswith("Max"):
            return 100.0
        other_limit = coerce_float(self.state_value(f"{self._state_name[:-3]}Max"))
        return 100.0 if other_limit is None else other_limit

    async def async_set_native_value(self, value: float) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, f"{self._command}/{value:g}")


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


class LoxoneMusicServerVolumeEntity(LoxoneEntity, NumberEntity):
    """Configure a volume that the original Music Server reports for a zone."""

    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self, bridge, control: LoxoneControl, state_name: str, label: str, command: str
    ) -> None:
        super().__init__(bridge, control, label)
        self._state_name = state_name
        self._command = command

    @property
    def native_value(self) -> float | None:
        value = coerce_float(self.state_value(self._state_name))
        return value if value is not None and 0.0 <= value <= 100.0 else None

    async def async_set_native_value(self, value: float) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, f"{self._command}/{round(value)}")
