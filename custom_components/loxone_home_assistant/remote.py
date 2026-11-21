"""Home Assistant remote entities for Loxone media controllers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from itertools import chain, repeat
from typing import Any

from homeassistant.components.remote import (
    ATTR_ACTIVITY,
    ATTR_DELAY_SECS,
    ATTR_HOLD_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    DEFAULT_HOLD_SECS,
    DEFAULT_NUM_REPEATS,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import LoxoneEntity, coerce_bool, coerce_float, unique_option_labels
from .models import LoxoneControl
from .runtime import entry_bridge

_HOLD_COMMANDS = {
    "volplus", "volminus", "prgplus", "prgminus",
    "dirup", "dirdown", "dirleft", "dirright",
}
_REMOTE_COMMANDS = {
    "on", "off", "reset", "mute", "play", "pause", "stop", "rewind",
    "previous", "next", "forward", "menu", "info", "exit", "guide", "return",
    "btnred", "btnblue", "btnyellow", "btngreen", "dirok",
} | {f"{command}off" for command in _HOLD_COMMANDS}
_NUMBER_COMMAND = re.compile(r"num[0-9]|number/[1-9][0-9]*")


def _activities(control: LoxoneControl) -> dict[int, str]:
    mode_list = control.details.get("modeList")
    if not isinstance(mode_list, Mapping):
        return {}
    modes = []
    for key, mode in mode_list.items():
        if not str(key).isdecimal() or not isinstance(mode, Mapping):
            continue
        mode_id = int(key)
        if mode_id == 0:
            continue
        raw_name = mode.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        modes.append((mode_id, name or f"Mode {mode_id}"))
    return unique_option_labels(sorted(modes))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    async_add_entities(
        LoxoneRemote(bridge, control)
        for control in bridge.controls
        if control.type == "Remote" and not control.is_secured
    )


class LoxoneRemote(LoxoneEntity, RemoteEntity):
    """Expose the activity list and keys of a Loxone media controller."""

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control)
        self._activities = _activities(control)
        self._mode_commands = {
            name: f"mode/{mode_id}" for mode_id, name in self._activities.items()
        }
        if self._activities:
            self._attr_activity_list = list(self._mode_commands)
            self._attr_supported_features = RemoteEntityFeature.ACTIVITY
        self._command_lock = asyncio.Lock()

    @property
    def is_on(self) -> bool | None:
        power = coerce_bool(self.state_value("power"))
        if power is not None:
            return power
        mode = coerce_float(self.state_value("mode"))
        return mode > 0 if mode is not None else None

    @property
    def current_activity(self) -> str | None:
        mode = coerce_float(self.state_value("mode"))
        if mode is None or not mode.is_integer():
            return None
        return self._activities.get(int(mode))

    async def async_turn_on(self, **kwargs: Any) -> None:
        activity = kwargs.get(ATTR_ACTIVITY)
        if activity is None:
            await self.async_send_command(("on",))
            return
        if (command := self._mode_commands.get(activity)) is None:
            raise ServiceValidationError(f"Unknown Loxone activity {activity!r}.")
        await self.async_send_command((command,))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_send_command(("reset",))

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        allowed_modes = set(self._mode_commands.values())
        if isinstance(command, str):
            command = (command,)
        commands = tuple(item.strip().casefold() for item in command)
        for item in commands:
            if (
                item in _REMOTE_COMMANDS
                or item in _HOLD_COMMANDS
                or item in allowed_modes
                or _NUMBER_COMMAND.fullmatch(item)
            ):
                continue
            raise ServiceValidationError(f"Unknown Loxone remote command {item!r}.")

        delay = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        hold = kwargs.get(ATTR_HOLD_SECS, DEFAULT_HOLD_SECS)
        sequences = repeat(commands, kwargs.get(ATTR_NUM_REPEATS, DEFAULT_NUM_REPEATS))
        async with self._command_lock:
            for index, item in enumerate(chain.from_iterable(sequences)):
                if index and delay > 0:
                    await asyncio.sleep(delay)
                await self._async_press(item, hold)

    async def _async_press(self, command: str, hold: float) -> None:
        if command not in _HOLD_COMMANDS:
            await self.bridge.async_send_action(self.control.uuid_action, command)
            return
        try:
            await self.bridge.async_send_action(self.control.uuid_action, command)
            if hold > 0:
                await asyncio.sleep(hold)
        finally:
            await self.bridge.async_send_action(self.control.uuid_action, f"{command}off")
