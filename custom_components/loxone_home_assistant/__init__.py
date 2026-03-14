"""Home Assistant entry point for the Loxone integration."""

from __future__ import annotations

import re
from urllib.parse import quote

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
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
    SERVICE_ATTR_FUNCTION,
    SERVICE_ATTR_ARGUMENTS,
    SERVICE_ATTR_MESSAGE,
    SERVICE_ATTR_UUID_ACTION,
    SERVICE_ATTR_VOLUME,
    SERVICE_CALL_INTERCOM_COMMAND,
    SERVICE_CALL_INTERCOM_FUNCTION,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_RAW_COMMAND,
    SERVICE_SEND_TTS,
)
from .entity import miniserver_device_identifier
from .intercom import is_intercom_control, resolve_intercom_command
from .models import LoxoneControl
from .runtime import bridges_by_entry_id, remove_entry_bridge, set_entry_bridge
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

SEND_TTS_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTRY_ID): cv.string,
        vol.Required(SERVICE_ATTR_UUID_ACTION): cv.string,
        vol.Required(SERVICE_ATTR_MESSAGE): cv.string,
        vol.Optional(SERVICE_ATTR_VOLUME): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    }
)
CALL_INTERCOM_FUNCTION_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTRY_ID): cv.string,
        vol.Required(SERVICE_ATTR_UUID_ACTION): cv.string,
        vol.Required(SERVICE_ATTR_FUNCTION): cv.string,
    }
)
CALL_INTERCOM_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTRY_ID): cv.string,
        vol.Required(SERVICE_ATTR_UUID_ACTION): cv.string,
        vol.Required(SERVICE_ATTR_COMMAND): cv.string,
        vol.Optional(SERVICE_ATTR_ARGUMENTS): vol.Any(
            cv.string,
            vol.All(cv.ensure_list, [vol.Coerce(str)]),
        ),
    }
)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration namespace."""
    del config
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})

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

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_TTS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_TTS,
            _make_send_tts_handler(hass),
            schema=SEND_TTS_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CALL_INTERCOM_FUNCTION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CALL_INTERCOM_FUNCTION,
            _make_call_intercom_function_handler(hass),
            schema=CALL_INTERCOM_FUNCTION_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CALL_INTERCOM_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CALL_INTERCOM_COMMAND,
            _make_call_intercom_command_handler(hass),
            schema=CALL_INTERCOM_COMMAND_SCHEMA,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})

    merged_data = {**entry.data, **entry.options}
    bridge = LoxoneBridge(hass, merged_data)
    await bridge.async_initialize()

    _async_register_miniserver_device(hass, entry, bridge)

    set_entry_bridge(hass, entry, bridge)
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

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    bridge = remove_entry_bridge(hass, entry)
    if bridge is not None:
        await bridge.async_shutdown()

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


async def _async_handle_send_tts(hass: HomeAssistant, call: ServiceCall) -> None:
    bridge = _resolve_bridge(hass, call)
    message = str(call.data[SERVICE_ATTR_MESSAGE]).strip()
    if not message:
        raise ServiceValidationError("TTS message cannot be empty.")

    volume = call.data.get(SERVICE_ATTR_VOLUME)
    encoded_message = quote(message, safe="")
    command = (
        f"tts/{encoded_message}"
        if volume is None
        else f"tts/{encoded_message}/{int(volume)}"
    )
    await bridge.async_send_action(call.data[SERVICE_ATTR_UUID_ACTION], command)


async def _async_handle_call_intercom_function(hass: HomeAssistant, call: ServiceCall) -> None:
    bridge = _resolve_bridge(hass, call)
    intercom_uuid_action = call.data[SERVICE_ATTR_UUID_ACTION]
    selector = str(call.data[SERVICE_ATTR_FUNCTION]).strip()
    if not selector:
        raise ServiceValidationError("Intercom function selector cannot be empty.")

    intercom_control = bridge.control_for_uuid_action(intercom_uuid_action)
    if intercom_control is None:
        raise ServiceValidationError(f"Unknown intercom uuid_action: {intercom_uuid_action}")
    if not is_intercom_control(intercom_control):
        raise ServiceValidationError(
            f"Control {intercom_uuid_action} is not recognized as an intercom."
        )

    available_controls = [
        control
        for control in bridge.controls
        if control.parent_uuid_action == intercom_control.uuid_action
    ]
    if not available_controls:
        raise ServiceValidationError(
            f"Intercom {intercom_uuid_action} has no mapped child functions."
        )

    selected_control = _select_intercom_function_control(
        available_controls,
        selector,
    )
    if selected_control is None:
        available_labels = ", ".join(control.name for control in available_controls)
        raise ServiceValidationError(
            f"Unknown intercom function '{selector}'. Available: {available_labels}."
        )

    await bridge.async_send_action(selected_control.uuid_action, "pulse")


async def _async_handle_call_intercom_command(hass: HomeAssistant, call: ServiceCall) -> None:
    bridge = _resolve_bridge(hass, call)
    intercom_uuid_action = call.data[SERVICE_ATTR_UUID_ACTION]
    intercom_control = bridge.control_for_uuid_action(intercom_uuid_action)
    if intercom_control is None:
        raise ServiceValidationError(f"Unknown intercom uuid_action: {intercom_uuid_action}")
    if not is_intercom_control(intercom_control):
        raise ServiceValidationError(
            f"Control {intercom_uuid_action} is not recognized as an intercom."
        )

    try:
        resolved_command = resolve_intercom_command(
            str(call.data[SERVICE_ATTR_COMMAND]),
            call.data.get(SERVICE_ATTR_ARGUMENTS),
        )
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    await bridge.async_send_action(intercom_control.uuid_action, resolved_command)


def _make_send_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> None:
        await _async_handle_send_command(hass, call)

    return handler


def _make_send_raw_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> None:
        await _async_handle_send_raw_command(hass, call)

    return handler


def _make_send_tts_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> None:
        await _async_handle_send_tts(hass, call)

    return handler


def _make_call_intercom_function_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> None:
        await _async_handle_call_intercom_function(hass, call)

    return handler


def _make_call_intercom_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> None:
        await _async_handle_call_intercom_command(hass, call)

    return handler


def _select_intercom_function_control(
    controls: list[LoxoneControl],
    selector: str,
) -> LoxoneControl | None:
    raw_selector = selector.strip()
    folded_selector = raw_selector.casefold()
    normalized_selector = _normalize_function_selector(raw_selector)

    for control in controls:
        if control.uuid_action.casefold() == folded_selector:
            return control

    if raw_selector.isdigit():
        index = int(raw_selector)
        for control in controls:
            if control.uuid_action.endswith(f"/{index}"):
                return control
        if 1 <= index <= len(controls):
            return controls[index - 1]

    exact_matches = [
        control
        for control in controls
        if _normalize_function_selector(control.name) == normalized_selector
        or _normalize_function_selector(control.display_name) == normalized_selector
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return sorted(exact_matches, key=lambda control: control.uuid_action)[0]

    partial_matches = [
        control
        for control in controls
        if normalized_selector
        and (
            normalized_selector in _normalize_function_selector(control.name)
            or normalized_selector in _normalize_function_selector(control.display_name)
        )
    ]
    if len(partial_matches) == 1:
        return partial_matches[0]

    return None


def _normalize_function_selector(value: str) -> str:
    return NON_ALNUM_RE.sub("", value.casefold())


def _resolve_bridge(hass: HomeAssistant, call: ServiceCall) -> LoxoneBridge:
    bridges = bridges_by_entry_id(hass)
    entry_id = call.data.get(SERVICE_ATTR_ENTRY_ID)

    if entry_id:
        bridge = bridges.get(entry_id)
        if bridge is None:
            raise ServiceValidationError(f"Unknown or unloaded Loxone entry_id: {entry_id}")
        return bridge

    if len(bridges) == 1:
        return next(iter(bridges.values()))
    if not bridges:
        raise ServiceValidationError(
            "No configured Loxone entries are currently loaded."
        )

    raise ServiceValidationError(
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
