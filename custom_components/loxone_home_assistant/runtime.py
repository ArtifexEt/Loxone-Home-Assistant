"""Runtime bridge helpers for config entries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_BRIDGES, DOMAIN

if TYPE_CHECKING:
    from .bridge import LoxoneBridge


def entry_bridge(hass: HomeAssistant, entry: ConfigEntry) -> LoxoneBridge:
    """Resolve loaded bridge for one config entry."""
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is not None:
        return cast("LoxoneBridge", runtime_data)
    return cast("LoxoneBridge", hass.data[DOMAIN][DATA_BRIDGES][entry.entry_id])


def set_entry_bridge(
    hass: HomeAssistant, entry: ConfigEntry, bridge: LoxoneBridge
) -> None:
    """Store bridge in HA runtime containers."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})
    hass.data[DOMAIN][DATA_BRIDGES][entry.entry_id] = bridge
    # Modern HA runtime storage (quality-scale recommendation).
    setattr(entry, "runtime_data", bridge)


def remove_entry_bridge(
    hass: HomeAssistant, entry: ConfigEntry
) -> LoxoneBridge | None:
    """Remove bridge from runtime containers and return it."""
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is not None:
        setattr(entry, "runtime_data", None)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})
        hass.data[DOMAIN][DATA_BRIDGES].pop(entry.entry_id, None)
        return cast("LoxoneBridge", runtime_data)

    bridges = (
        hass.data.get(DOMAIN, {}).get(DATA_BRIDGES, {})
        if isinstance(hass.data.get(DOMAIN, {}), dict)
        else {}
    )
    bridge = bridges.pop(entry.entry_id, None)
    return cast("LoxoneBridge | None", bridge)


def bridges_by_entry_id(hass: HomeAssistant) -> dict[str, LoxoneBridge]:
    """Return loaded bridges keyed by config entry id."""
    data = hass.data.get(DOMAIN)
    if not isinstance(data, dict):
        return {}

    raw = data.get(DATA_BRIDGES)
    if not isinstance(raw, dict):
        return {}

    result: dict[str, LoxoneBridge] = {}
    for entry_id, bridge in raw.items():
        if isinstance(entry_id, str):
            result[entry_id] = cast("LoxoneBridge", bridge)
    return result


def runtime_bridge(entry: ConfigEntry) -> LoxoneBridge | None:
    """Return typed runtime bridge from a config entry when available."""
    value: Any = getattr(entry, "runtime_data", None)
    return cast("LoxoneBridge | None", value)
