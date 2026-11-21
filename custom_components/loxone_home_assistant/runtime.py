"""Access Miniserver connections owned by Home Assistant config entries."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    from .bridge import LoxoneBridge


def entry_bridge(entry: ConfigEntry) -> LoxoneBridge:
    """Return the bridge assigned before setting up the entity platforms."""
    return cast("LoxoneBridge", entry.runtime_data)


def bridges_by_entry_id(hass: HomeAssistant) -> dict[str, LoxoneBridge]:
    """Return bridges available to integration services and HTTP views."""
    return {
        entry.entry_id: entry_bridge(entry)
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
    }
