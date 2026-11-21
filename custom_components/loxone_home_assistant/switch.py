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
    SWITCH_CONTROL_TYPES,
)
from .entity import (
    LoxoneEntity,
    coerce_bool,
    coerce_float,
    first_matching_state_name,
    switch_control_is_on,
)
from .intercom import intercom_mute_state_name
from .light import should_expose_as_light
from .models import LoxoneControl
from .options import option_enabled
from .runtime import entry_bridge


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
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
        LoxoneAutomaticShadingSwitch(bridge, control, state_name)
        for control in bridge.controls
        if control.type == "Jalousie"
        and coerce_bool(control.details.get("isAutomatic")) is not False
        and (state_name := first_matching_state_name(control, ("autoActive",))) is not None
    )
    entities.extend(
        LoxoneAudioBluetoothSwitch(bridge, control, state_name)
        for control in bridge.controls
        if (state_name := _audio_bluetooth_state(control)) is not None
    )
    entities.extend(
        LoxoneIntercomMuteSwitch(bridge, control, mute_state_name)
        for control in bridge.controls
        if (mute_state_name := intercom_mute_state_name(control)) is not None
    )
    async_add_entities(entities)


class LoxoneSwitchEntity(LoxoneEntity, SwitchEntity):
    """Representation of a Loxone switch."""

    def __init__(self, bridge, control) -> None:
        super().__init__(bridge, control)
        if control.type in {"Alarm", "Remote"}:
            # Existing registry entries retain their settings. New installs use
            # the alarm panel or remote platform for these controls.
            self._attr_entity_registry_enabled_default = False

    @property
    def is_on(self) -> bool | None:
        return switch_control_is_on(self.bridge, self.control)

    async def async_turn_on(self, **kwargs) -> None:
        await self._send_switch_command("on")

    async def async_turn_off(self, **kwargs) -> None:
        await self._send_switch_command("off")

    async def _send_switch_command(self, command: str) -> None:
        if self.control.type == "CarCharger":
            command = f"charge/{command}"
        await self.bridge.async_send_action(self.control.uuid_action, command)


class LoxoneAutomaticShadingSwitch(LoxoneEntity, SwitchEntity):
    """Switch the automatic sun-position program of an individual blind."""

    _attr_icon = "mdi:blinds-horizontal"

    def __init__(self, bridge, control, state_name: str) -> None:
        super().__init__(bridge, control, "Automatic shading")
        self._state_name = state_name

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))

    async def async_turn_on(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "auto")

    async def async_turn_off(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "NoAuto")


def _audio_bluetooth_state(control: LoxoneControl) -> str | None:
    if control.type != "AudioZoneV2" or control.is_secured:
        return None
    if coerce_bool(control.details.get("validOutputs")) is False:
        return None
    # The LoxAPP bitmasks flag Bluetooth controlled by logic or disabled in Config.
    for detail, flag in (("parameterConnections", 1 << 1), ("disableFeature", 1 << 2)):
        mask = coerce_float(control.details.get(detail))
        if mask is not None and mask >= 0 and mask.is_integer() and int(mask) & flag:
            return None
    return first_matching_state_name(control, ("bluetooth",))


class LoxoneAudioBluetoothSwitch(LoxoneEntity, SwitchEntity):
    """Enable Bluetooth when the Audio Player allows local control."""

    _attr_icon = "mdi:bluetooth"

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, "Bluetooth")
        self._state_name = state_name

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))

    async def async_turn_on(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "bluetooth/1")

    async def async_turn_off(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "bluetooth/0")


class LoxoneIntercomMuteSwitch(LoxoneEntity, SwitchEntity):
    """Control the doorbell mute state reported by a Gen 2 Intercom."""

    _attr_icon = "mdi:bell-off-outline"

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, "Doorbell Mute")
        self._set_intercom_entity_name("switch", "intercom_doorbell_mute")
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

