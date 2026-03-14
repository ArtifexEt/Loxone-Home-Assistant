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
    ]
    async_add_entities(entities)


class LoxoneCoverEntity(LoxoneEntity, CoverEntity):
    """Representation of a Loxone blind or shutter."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    @property
    def current_cover_position(self) -> int | None:
        position = coerce_float(self.first_state_value("position", "targetPosition"))
        if position is None:
            return None
        return max(0, min(100, round(100 - position)))

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    async def async_open_cover(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "FullUp")

    async def async_close_cover(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "FullDown")

    async def async_stop_cover(self, **kwargs) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "stop")

    async def async_set_cover_position(self, **kwargs) -> None:
        target = kwargs["position"]
        await self.bridge.async_send_action(
            self.control.uuid_action,
            f"manualPosition/{100 - int(target)}",
        )
