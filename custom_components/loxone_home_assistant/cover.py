"""Cover platform for Loxone."""

from __future__ import annotations

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COVER_CONTROL_TYPES, DOMAIN
from .entity import LoxoneEntity, coerce_float


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = hass.data[DOMAIN]["bridges"][entry.entry_id]
    entities = [
        LoxoneCoverEntity(bridge, control)
        for control in bridge.controls
        if control.type in COVER_CONTROL_TYPES
        and not _is_updownleftright_analog(control)
    ]
    async_add_entities(entities)


class LoxoneCoverEntity(LoxoneEntity, CoverEntity):
    """Representation of a Loxone blind or shutter."""

    @property
    def supported_features(self) -> CoverEntityFeature:
        if self.control.type == "Jalousie":
            return (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
                | CoverEntityFeature.SET_POSITION
            )
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )

    @property
    def current_cover_position(self) -> int | None:
        position = coerce_float(self.first_state_value("position", "targetPosition"))
        if position is None:
            return None
        if self.control.type == "Jalousie":
            return max(0, min(100, round(100 - position)))
        if 0 <= position <= 1:
            return max(0, min(100, round(position * 100)))
        return max(0, min(100, round(position)))

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    async def async_open_cover(self, **kwargs) -> None:
        if self.control.type == "Gate":
            await self.bridge.async_send_action(self.control.uuid_action, "open")
            return
        if self.control.type == "UpDownLeftRight":
            await self.bridge.async_send_action(self.control.uuid_action, "UpOn")
            return
        await self.bridge.async_send_action(self.control.uuid_action, "FullUp")

    async def async_close_cover(self, **kwargs) -> None:
        if self.control.type == "Gate":
            await self.bridge.async_send_action(self.control.uuid_action, "close")
            return
        if self.control.type == "UpDownLeftRight":
            await self.bridge.async_send_action(self.control.uuid_action, "DownOn")
            return
        await self.bridge.async_send_action(self.control.uuid_action, "FullDown")

    async def async_stop_cover(self, **kwargs) -> None:
        if self.control.type == "UpDownLeftRight":
            await self.bridge.async_send_action(self.control.uuid_action, "UpOff")
            await self.bridge.async_send_action(self.control.uuid_action, "DownOff")
            return
        try:
            await self.bridge.async_send_action(self.control.uuid_action, "stop")
        except Exception as err:
            from .bridge import LoxoneConnectionError

            if not isinstance(err, LoxoneConnectionError):
                raise
            # Compatibility fallback for controllers exposing directional stop commands only.
            await self.bridge.async_send_action(self.control.uuid_action, "UpOff")
            await self.bridge.async_send_action(self.control.uuid_action, "DownOff")

    async def async_set_cover_position(self, **kwargs) -> None:
        if self.control.type != "Jalousie":
            return
        target = kwargs["position"]
        await self.bridge.async_send_action(
            self.control.uuid_action,
            f"manualPosition/{100 - int(target)}",
        )


def _is_updownleftright_analog(control) -> bool:
    if control.type != "UpDownLeftRight":
        return False
    normalized_states = {state_name.strip().casefold() for state_name in control.states}
    return any(state_name in normalized_states for state_name in ("value", "position", "actual"))
