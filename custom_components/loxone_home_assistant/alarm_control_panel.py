"""Expose Loxone burglar alarms as Home Assistant alarm panels."""

from __future__ import annotations

from time import time

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import LoxoneEntity, coerce_bool, coerce_float
from .runtime import entry_bridge


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    async_add_entities(
        LoxoneAlarmPanel(bridge, control)
        for control in bridge.controls
        if control.type == "Alarm"
    )


class LoxoneAlarmPanel(LoxoneEntity, AlarmControlPanelEntity):
    """Show and control the arming and escalation state of a burglar alarm."""

    _attr_code_arm_required = False

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        if self.control.is_secured:
            return AlarmControlPanelEntityFeature(0)
        return (
            AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_AWAY
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        level = coerce_float(self.state_value("level"))
        if level is not None and level > 0:
            return AlarmControlPanelState.TRIGGERED
        armed = coerce_bool(self.state_value("armed"))
        if armed is None:
            return None
        if armed:
            return (
                AlarmControlPanelState.ARMED_HOME
                if coerce_bool(self.state_value("disabledMove"))
                else AlarmControlPanelState.ARMED_AWAY
            )

        armed_at = coerce_float(self.state_value("armedAt"))
        if armed_at is not None:
            arming = armed_at > time()
        else:
            arming = (coerce_float(self.state_value("armedDelay")) or 0) > 0
        return AlarmControlPanelState.ARMING if arming else AlarmControlPanelState.DISARMED

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._async_send("on/0")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._async_send("delayedon/1")

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._async_send("off")

    async def _async_send(self, command: str) -> None:
        if self.control.is_secured:
            raise ServiceValidationError("This Loxone alarm requires a visualization password.")
        await self.bridge.async_send_action(self.control.uuid_action, command)
