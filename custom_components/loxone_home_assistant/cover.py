"""Cover platform for Loxone."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COVER_CONTROL_TYPES
from .entity import (
    LoxoneEntity,
    coerce_bool,
    coerce_float,
    first_matching_state_name,
    number_value_state_name,
)
from .models import LoxoneControl
from .runtime import entry_bridge
from .versioning import parse_miniserver_version

_OPEN_COMMANDS = {
    "Gate": "open",
    "Jalousie": "FullUp",
    "UpDownLeftRight": "UpOn",
    "Window": "fullopen",
}
_CLOSE_COMMANDS = {
    "Gate": "close",
    "Jalousie": "FullDown",
    "UpDownLeftRight": "DownOn",
    "Window": "fullclose",
}
_POSITION_COMMANDS = {
    "Jalousie": "manualPosition",
    "Window": "moveToPosition",
}
_DIRECTION_STATES = {"Gate": "active", "Window": "direction"}
_JALOUSIE_ANIMATIONS = {
    0: CoverDeviceClass.BLIND,
    1: CoverDeviceClass.SHUTTER,
    2: CoverDeviceClass.CURTAIN,
    4: CoverDeviceClass.CURTAIN,
    5: CoverDeviceClass.CURTAIN,
}

NON_ALNUM_LABEL_RE = re.compile(r"[^a-z0-9]+")
CURTAIN_HINTS = {
    "curtain",
    "curtains",
    "drape",
    "drapes",
    "drapery",
    "firana",
    "firanki",
    "zaslona",
    "zaslony",
}
BLIND_HINTS = {
    "blind",
    "blinds",
    "jalousie",
    "jaluzja",
    "jaluzje",
    "roleta",
    "rolety",
    "roller",
    "shutter",
    "shutters",
}
CURTAIN_HINT_SUBSTRINGS = {
    "curtain",
    "drape",
    "firan",
    "zaslon",
}
BLIND_HINT_SUBSTRINGS = {
    "blind",
    "jalous",
    "jaluz",
    "rolet",
    "roller",
    "shutter",
    "lamell",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    entities = [
        LoxoneCoverEntity(bridge, control)
        for control in bridge.controls
        if control.type in COVER_CONTROL_TYPES
        and not (
            control.type == "UpDownLeftRight" and number_value_state_name(control)
        )
    ]
    async_add_entities(entities)


class LoxoneCoverEntity(LoxoneEntity, CoverEntity):
    """Representation of a Loxone blind or shutter."""

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control)
        self._supports_tilt = _supports_jalousie_tilt(control)
        self._version = parse_miniserver_version(getattr(bridge, "software_version", None))

    @property
    def device_class(self) -> CoverDeviceClass | None:
        match self.control.type:
            case "Gate":
                animation = coerce_float(self.control.details.get("animation"))
                return CoverDeviceClass.GARAGE if animation == 0 else CoverDeviceClass.GATE
            case "Window":
                return CoverDeviceClass.WINDOW
            case "Jalousie":
                return _detect_jalousie_device_class(self.control)
            case _:
                return None

    @property
    def supported_features(self) -> CoverEntityFeature:
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if self._can_stop():
            features |= CoverEntityFeature.STOP
        if self.control.type in _POSITION_COMMANDS:
            features |= CoverEntityFeature.SET_POSITION
        if self._supports_tilt:
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.STOP_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
        return features

    @property
    def current_cover_position(self) -> int | None:
        return _reported_position(
            self.state_value("position"),
            inverted=self.control.type == "Jalousie",
        )

    @property
    def current_cover_tilt_position(self) -> int | None:
        if not self._supports_tilt:
            return None
        return _reported_position(
            self.state_value("shadePosition"),
            inverted=True,
        )

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        return position == 0 if position is not None else None

    @property
    def is_opening(self) -> bool | None:
        return self._movement_state("up", 1)

    @property
    def is_closing(self) -> bool | None:
        return self._movement_state("down", -1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        inverted = self.control.type == "Jalousie"
        target = _reported_position(self.state_value("targetPosition"), inverted=inverted)
        if target is not None:
            attrs["target_position"] = target
        if self._supports_tilt:
            target_tilt = _reported_position(self.state_value("targetPositionLamelle"), inverted=True)
            if target_tilt is not None:
                attrs["target_tilt_position"] = target_tilt
        return attrs

    def _movement_state(self, jalousie_state: str, direction: int) -> bool | None:
        if self.control.type == "Jalousie":
            return coerce_bool(self.state_value(jalousie_state))
        state_name = _DIRECTION_STATES.get(self.control.type)
        if state_name is None:
            return None
        value = coerce_float(self.state_value(state_name))
        return value == direction if value is not None else None

    async def async_open_cover(self, **kwargs) -> None:
        command = _OPEN_COMMANDS[self.control.type]
        if self._can_reverse_moving_gate() and self.is_closing is True:
            command = "forceOpen"
        await self._send_command(command)

    async def async_close_cover(self, **kwargs) -> None:
        command = _CLOSE_COMMANDS[self.control.type]
        if self._can_reverse_moving_gate() and self.is_opening is True:
            command = "forceClose"
        await self._send_command(command)

    def _can_reverse_moving_gate(self) -> bool:
        return (
            self.control.type == "Gate"
            and self._version is not None
            and self._version[:2] >= (12, 1)
        )

    async def async_stop_cover(self, **kwargs) -> None:
        if not self._can_stop():
            return
        if self.control.type == "UpDownLeftRight" or (
            self.control.type == "Jalousie" and self._has_legacy_stop_commands()
        ):
            await self._send_command("UpOff")
            await self._send_command("DownOff")
            return
        await self._send_command("stop")

    def _has_legacy_stop_commands(self) -> bool:
        return self._version is not None and self._version[:2] < (11, 3)

    def _can_stop(self) -> bool:
        # The Window stop command was added in Miniserver 11.3.
        return self.control.type != "Window" or not self._has_legacy_stop_commands()

    async def async_set_cover_position(self, **kwargs) -> None:
        command = _POSITION_COMMANDS.get(self.control.type)
        if command is None:
            return
        target = _clamp_percent(kwargs["position"])
        if self.control.type == "Jalousie":
            target = 100 - target
        await self._send_command(f"{command}/{target}")

    async def async_open_cover_tilt(self, **kwargs) -> None:
        await self.async_set_cover_tilt_position(tilt_position=100)

    async def async_close_cover_tilt(self, **kwargs) -> None:
        await self.async_set_cover_tilt_position(tilt_position=0)

    async def async_stop_cover_tilt(self, **kwargs) -> None:
        if self._supports_tilt:
            await self.async_stop_cover()

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        if not self._supports_tilt:
            return
        target = _clamp_percent(kwargs["tilt_position"])
        await self._send_command(f"manualLamelle/{100 - target}")

    async def _send_command(self, command: str) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, command)


def _supports_jalousie_tilt(control: LoxoneControl) -> bool:
    if control.type != "Jalousie":
        return False
    animation = coerce_float(control.details.get("animation"))
    if animation is not None:
        return animation == 0
    return first_matching_state_name(control, ("shadePosition", "targetPositionLamelle")) is not None


def _detect_jalousie_device_class(control: LoxoneControl) -> CoverDeviceClass:
    return (
        _device_class_from_candidates((control.name, control.display_name, *control.path))
        or _device_class_from_candidates((control.icon,))
        or _JALOUSIE_ANIMATIONS.get(coerce_float(control.details.get("animation")))
        or _device_class_from_candidates((control.room_name, control.category_name))
        or (CoverDeviceClass.BLIND if _supports_jalousie_tilt(control) else CoverDeviceClass.CURTAIN)
    )


def _normalize_cover_label(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(
        char for char in normalized.casefold() if not unicodedata.combining(char)
    )
    return " ".join(NON_ALNUM_LABEL_RE.sub(" ", ascii_only).split())


def _device_class_from_candidates(candidates: tuple[str | None, ...]) -> CoverDeviceClass | None:
    normalized_candidates = {
        normalized
        for value in candidates
        if (normalized := _normalize_cover_label(value))
    }
    curtain_match = any(
        _matches_hint(value, value.replace(" ", ""), CURTAIN_HINTS, CURTAIN_HINT_SUBSTRINGS)
        for value in normalized_candidates
    )
    blind_match = any(
        _matches_hint(value, value.replace(" ", ""), BLIND_HINTS, BLIND_HINT_SUBSTRINGS)
        for value in normalized_candidates
    )
    if curtain_match and not blind_match:
        return CoverDeviceClass.CURTAIN
    if blind_match and not curtain_match:
        return CoverDeviceClass.BLIND
    return None


def _matches_hint(
    normalized_value: str,
    compact_value: str,
    hints: set[str],
    substrings: set[str],
) -> bool:
    words = set(normalized_value.split())
    if words & hints:
        return True
    return any(fragment in compact_value for fragment in substrings)


def _clamp_percent(value: int | float) -> int:
    return max(0, min(100, round(float(value))))


def _reported_position(value: object, *, inverted: bool) -> int | None:
    position = coerce_float(value)
    if position is None or not math.isfinite(position):
        return None
    if 0 <= position <= 1:
        position *= 100
    return _clamp_percent(100 - position if inverted else position)
