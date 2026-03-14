"""Home Assistant entry point for the Loxone integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .bridge import LoxoneBridge
from .const import (
    CONF_SERVER_MODEL,
    CONF_SOFTWARE_VERSION,
    DATA_BRIDGES,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    SERVICE_ATTR_COMMAND,
    SERVICE_ATTR_ENTRY_ID,
    SERVICE_ATTR_UUID_ACTION,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_RAW_COMMAND,
)
from .entity import miniserver_device_identifier
from .server_model import DEFAULT_SERVER_MODEL
SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTRY_ID): cv.string,
        vol.Required(SERVICE_ATTR_UUID_ACTION): cv.string,
        vol.Required(SERVICE_ATTR_COMMAND): cv.string,
    }
)

SEND_RAW_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTRY_ID): cv.string,
        vol.Required(SERVICE_ATTR_COMMAND): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration namespace."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})

    merged_data = {**entry.data, **entry.options}
    bridge = LoxoneBridge(hass, merged_data)
    await bridge.async_initialize()

    _async_register_miniserver_device(hass, entry, bridge)

    hass.data[DOMAIN][DATA_BRIDGES][entry.entry_id] = bridge
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if bridge.serial != entry.unique_id or _runtime_data_changed(entry.data, bridge):
        hass.config_entries.async_update_entry(
            entry,
            unique_id=bridge.serial,
            data={
                **entry.data,
                **bridge.export_runtime_data(),
            },
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_COMMAND,
            _make_send_command_handler(hass),
            schema=SEND_COMMAND_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_RAW_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_RAW_COMMAND,
            _make_send_raw_command_handler(hass),
            schema=SEND_RAW_COMMAND_SCHEMA,
        )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    bridge: LoxoneBridge = hass.data[DOMAIN][DATA_BRIDGES].pop(entry.entry_id)
    await bridge.async_shutdown()

    if not hass.data[DOMAIN][DATA_BRIDGES]:
        for service_name in (SERVICE_SEND_COMMAND, SERVICE_SEND_RAW_COMMAND):
            if hass.services.has_service(DOMAIN, service_name):
                hass.services.async_remove(DOMAIN, service_name)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry after options changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_handle_send_command(hass: HomeAssistant, call: ServiceCall) -> None:
    bridge = _resolve_bridge(hass, call)
    await bridge.async_send_action(
        call.data[SERVICE_ATTR_UUID_ACTION],
        call.data[SERVICE_ATTR_COMMAND],
    )


async def _async_handle_send_raw_command(hass: HomeAssistant, call: ServiceCall) -> None:
    bridge = _resolve_bridge(hass, call)
    await bridge.async_send_raw_command(call.data[SERVICE_ATTR_COMMAND])


def _make_send_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> None:
        await _async_handle_send_command(hass, call)

    return handler


def _make_send_raw_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> None:
        await _async_handle_send_raw_command(hass, call)

    return handler


def _resolve_bridge(hass: HomeAssistant, call: ServiceCall) -> LoxoneBridge:
    bridges: dict[str, LoxoneBridge] = hass.data[DOMAIN][DATA_BRIDGES]
    entry_id = call.data.get(SERVICE_ATTR_ENTRY_ID)

    if entry_id:
        bridge = bridges.get(entry_id)
        if bridge is None:
            raise HomeAssistantError(f"Unknown Loxone entry_id: {entry_id}")
        return bridge

    if len(bridges) == 1:
        return next(iter(bridges.values()))

    raise HomeAssistantError(
        "More than one Loxone entry exists. Please provide entry_id."
    )


def _runtime_data_changed(data: dict, bridge: LoxoneBridge) -> bool:
    return (
        data.get("serial") != bridge.serial
        or data.get("client_uuid") != bridge.client_uuid
        or data.get("token") != bridge.token
        or data.get("token_valid_until") != bridge.token_valid_until
        or data.get("loxapp_version") != bridge.loxapp_version
        or data.get(CONF_SOFTWARE_VERSION) != bridge.software_version
        or data.get(CONF_SERVER_MODEL, DEFAULT_SERVER_MODEL) != bridge.server_model
        or not data.get(CONF_PASSWORD)
    )


def _async_register_miniserver_device(
    hass: HomeAssistant, entry: ConfigEntry, bridge: LoxoneBridge
) -> None:
    """Ensure the Miniserver hub device exists in HA device registry."""
    model = str(getattr(bridge, "server_model", DEFAULT_SERVER_MODEL)).strip()
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={miniserver_device_identifier(bridge.serial)},
        manufacturer=MANUFACTURER,
        model=model or DEFAULT_SERVER_MODEL,
        name=bridge.miniserver_name,
        sw_version=bridge.software_version,
        serial_number=bridge.serial,
    )
