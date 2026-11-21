"""Home Assistant entry point for the Loxone integration."""

from __future__ import annotations

import asyncio
import logging
import re
from functools import partial
from typing import Any

import voluptuous as vol
from aiohttp import BasicAuth, ClientError, web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.translation import async_get_translations

from .bridge import (
    LoxoneAuthenticationError,
    LoxoneBridge,
    LoxoneConnectionError,
    LoxoneUnsupportedError,
)
from .connection_config import merge_connection_config
from .const import (
    DOMAIN,
    MANUFACTURER,
    MEDIA_PLAYER_CONTROL_TYPES,
    PLATFORMS,
    SERVICE_ATTR_ARGUMENTS,
    SERVICE_ATTR_COMMAND,
    SERVICE_ATTR_ENTRY_ID,
    SERVICE_ATTR_FUNCTION,
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
from .icons import decode_icon_key, icon_content_type
from .intercom import (
    build_intercom_tts_command,
    is_intercom_control,
    resolve_intercom_command,
)
from .intercom_gallery import async_setup_intercom_gallery
from .media_player import async_send_audio_tts, build_audio_tts_command
from .models import LoxoneControl
from .runtime import bridges_by_entry_id, entry_bridge
from .server_model import DEFAULT_SERVER_MODEL

_LOGGER = logging.getLogger(__name__)
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
DATA_ICON_CACHE = "icon_cache"
DATA_ICON_VIEW_REGISTERED = "icon_view_registered"


class LoxoneIconProxyView(HomeAssistantView):
    """Serve configured Loxone icons in dashboard image elements."""

    url = f"/api/{DOMAIN}/icon/{{serial}}/{{icon_key}}"
    name = f"api:{DOMAIN}:icon"
    # Dashboard image elements may load without attaching HA credentials.
    requires_auth = False

    async def get(self, request: web.Request, serial: str, icon_key: str) -> web.Response:
        hass = request.app[KEY_HASS]
        bridge = _resolve_bridge_for_serial(hass, serial)
        if bridge is None or not bridge.use_loxone_icons:
            return web.Response(status=404)

        icon_path = decode_icon_key(icon_key)
        if icon_path is None:
            return web.Response(status=400)
        if icon_path not in bridge.icon_paths:
            return web.Response(status=404)

        cached = _cached_icon_payload(hass, bridge.serial, icon_path)
        if cached is not None:
            return _icon_web_response(cached)

        payload = await _async_fetch_icon_payload(bridge, icon_path)
        if payload is None:
            return web.Response(status=404)

        _store_cached_icon_payload(hass, bridge.serial, icon_path, payload)
        return _icon_web_response(payload)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration namespace."""
    del config
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get(DATA_ICON_VIEW_REGISTERED):
        hass.http.register_view(LoxoneIconProxyView())
        domain_data[DATA_ICON_VIEW_REGISTERED] = True
    await async_setup_intercom_gallery(hass)

    services = (
        (SERVICE_SEND_COMMAND, _async_handle_send_command, SEND_COMMAND_SCHEMA),
        (SERVICE_SEND_RAW_COMMAND, _async_handle_send_raw_command, SEND_RAW_COMMAND_SCHEMA),
        (SERVICE_SEND_TTS, _async_handle_send_tts, SEND_TTS_SCHEMA),
        (
            SERVICE_CALL_INTERCOM_FUNCTION,
            _async_handle_call_intercom_function,
            CALL_INTERCOM_FUNCTION_SCHEMA,
        ),
        (
            SERVICE_CALL_INTERCOM_COMMAND,
            _async_handle_call_intercom_command,
            CALL_INTERCOM_COMMAND_SCHEMA,
        ),
    )
    for service, handler, schema in services:
        if hass.services.has_service(DOMAIN, service):
            continue
        hass.services.async_register(
            DOMAIN,
            service,
            partial(handler, hass),
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    bridge = None
    try:
        bridge = LoxoneBridge(hass, merge_connection_config(entry.data, entry.options))
        await bridge.async_initialize()
        if bridge.intercom_language is not None:
            bridge.intercom_entity_translations = await async_get_translations(
                hass, bridge.intercom_language, "entity", {DOMAIN}
            )
        bridge.miniserver_device_id = _async_register_miniserver_device(hass, entry, bridge)

        entry.runtime_data = bridge
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        data = {**entry.data, **bridge.export_runtime_data()}
        if bridge.serial != entry.unique_id or data != entry.data:
            hass.config_entries.async_update_entry(
                entry,
                unique_id=bridge.serial,
                data=data,
            )
    except (Exception, asyncio.CancelledError) as err:
        if bridge is not None:
            if getattr(entry, "runtime_data", None) is bridge:
                del entry.runtime_data
            await bridge.async_shutdown()
        if isinstance(err, LoxoneConnectionError):
            raise ConfigEntryNotReady(str(err)) from err
        if isinstance(err, LoxoneAuthenticationError):
            raise ConfigEntryError(
                "Loxone rejected the configured credentials. Update them in the integration options."
            ) from err
        if isinstance(err, LoxoneUnsupportedError):
            raise ConfigEntryError(str(err)) from err
        raise

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    @callback
    def _schedule_structure_reload() -> None:
        if entry.state is not ConfigEntryState.LOADED or entry.setup_lock.locked():
            return
        _LOGGER.info("Loxone project changed. Reloading %s.", entry.title)
        hass.config_entries.async_schedule_reload(entry.entry_id)

    bridge.start_structure_monitor(_schedule_structure_reload)
    if bridge.use_loxone_icons:
        entry.async_create_background_task(
            hass, _async_prefetch_icons(hass, bridge), "loxone_prefetch_icons"
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    bridge = entry_bridge(entry)
    await bridge.async_shutdown()
    _clear_cached_bridge_icons(hass, bridge.serial)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry after options changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_handle_send_command(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    bridge = _resolve_bridge(hass, call)
    return await bridge.async_send_action(
        call.data[SERVICE_ATTR_UUID_ACTION],
        call.data[SERVICE_ATTR_COMMAND],
    )


async def _async_handle_send_raw_command(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    bridge = _resolve_bridge(hass, call)
    return await bridge.async_send_raw_command(call.data[SERVICE_ATTR_COMMAND])


async def _async_handle_send_tts(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    bridge = _resolve_bridge(hass, call)
    uuid_action = call.data[SERVICE_ATTR_UUID_ACTION]
    message = str(call.data[SERVICE_ATTR_MESSAGE]).strip()
    if not message:
        raise ServiceValidationError("TTS message cannot be empty.")

    volume = call.data.get(SERVICE_ATTR_VOLUME)
    target_control = bridge.control_for_uuid_action(uuid_action)

    if target_control is not None and target_control.type in MEDIA_PLAYER_CONTROL_TYPES:
        return await async_send_audio_tts(bridge, target_control, message, volume)

    command = (
        build_intercom_tts_command(message)
        if target_control is not None and is_intercom_control(target_control)
        else build_audio_tts_command(message, volume)
    )
    if command is None:
        raise ServiceValidationError("TTS message cannot be empty.")

    return await bridge.async_send_action(uuid_action, command)


async def _async_handle_call_intercom_function(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    bridge = _resolve_bridge(hass, call)
    intercom_uuid_action = call.data[SERVICE_ATTR_UUID_ACTION]
    selector = str(call.data[SERVICE_ATTR_FUNCTION]).strip()
    if not selector:
        raise ServiceValidationError("Intercom function selector cannot be empty.")

    intercom_control = bridge.control_for_uuid_action(intercom_uuid_action)
    if intercom_control is None:
        raise ServiceValidationError(
            f"Unknown intercom uuid_action: {intercom_uuid_action}"
        )
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

    response = await bridge.async_send_action(selected_control.uuid_action, "pulse")
    return {
        **response,
        "resolved_uuid_action": selected_control.uuid_action,
        "resolved_function_name": selected_control.name,
    }


async def _async_handle_call_intercom_command(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    bridge = _resolve_bridge(hass, call)
    intercom_uuid_action = call.data[SERVICE_ATTR_UUID_ACTION]
    intercom_control = bridge.control_for_uuid_action(intercom_uuid_action)
    if intercom_control is None:
        raise ServiceValidationError(
            f"Unknown intercom uuid_action: {intercom_uuid_action}"
        )
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

    return await bridge.async_send_action(intercom_control.uuid_action, resolved_command)


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
            raise ServiceValidationError(
                f"Unknown or unloaded Loxone entry_id: {entry_id}"
            )
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


def _async_register_miniserver_device(
    hass: HomeAssistant, entry: ConfigEntry, bridge: LoxoneBridge
) -> str:
    """Return the registry ID of the registered Miniserver hub."""
    model = str(getattr(bridge, "server_model", DEFAULT_SERVER_MODEL)).strip()
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={miniserver_device_identifier(bridge.serial)},
        manufacturer=MANUFACTURER,
        model=model or DEFAULT_SERVER_MODEL,
        name=bridge.miniserver_name,
        sw_version=bridge.software_version,
        serial_number=bridge.serial,
    )
    return device.id


def _resolve_bridge_for_serial(hass: HomeAssistant, serial: str) -> LoxoneBridge | None:
    normalized = str(serial).strip()
    if not normalized:
        return None
    for bridge in bridges_by_entry_id(hass).values():
        if str(bridge.serial).strip() == normalized:
            return bridge
    return None


def _icon_cache(hass: HomeAssistant) -> dict[str, dict[str, tuple[str, bytes]]]:
    return hass.data[DOMAIN].setdefault(DATA_ICON_CACHE, {})


def _cached_icon_payload(
    hass: HomeAssistant, serial: str, icon_path: str
) -> tuple[str, bytes] | None:
    return _icon_cache(hass).get(serial, {}).get(icon_path)


def _store_cached_icon_payload(
    hass: HomeAssistant, serial: str, icon_path: str, payload: tuple[str, bytes]
) -> None:
    _icon_cache(hass).setdefault(serial, {})[icon_path] = payload


def _clear_cached_bridge_icons(hass: HomeAssistant, serial: str) -> None:
    _icon_cache(hass).pop(serial, None)


def _icon_web_response(payload: tuple[str, bytes]) -> web.Response:
    content_type, body = payload
    return web.Response(
        body=body,
        content_type=content_type,
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _async_fetch_icon_payload(
    bridge: LoxoneBridge, icon_path: str
) -> tuple[str, bytes] | None:
    icon_url = bridge.resolve_http_url(icon_path)
    if icon_url is None:
        return None
    session = getattr(bridge, "_session", None)
    if session is None:
        return None

    auth_options = (BasicAuth(bridge.username, bridge.password), None) if bridge.use_tls else (None,)
    for request_auth in auth_options:
        try:
            async with session.get(icon_url, auth=request_auth, allow_redirects=False) as response:
                if response.status == 401 and request_auth is not None:
                    continue
                if response.status != 200:
                    return None
                content_type = icon_content_type(response.headers.get("Content-Type"), icon_path)
                if content_type is None:
                    return None
                body = await response.read()
                if not body:
                    return None
                return content_type, body
        except (ClientError, RuntimeError) as err:
            _LOGGER.debug(
                "Loxone icon fetch failed for serial=%s path=%s (%s)",
                bridge.serial,
                icon_path,
                err,
            )
            if request_auth is None:
                return None
    return None


async def _async_prefetch_icons(hass: HomeAssistant, bridge: LoxoneBridge) -> None:
    icon_paths = bridge.icon_paths
    if not icon_paths:
        return

    fetched = 0
    for icon_path in sorted(icon_paths):
        if _cached_icon_payload(hass, bridge.serial, icon_path) is not None:
            continue
        payload = await _async_fetch_icon_payload(bridge, icon_path)
        if payload is None:
            continue
        _store_cached_icon_payload(hass, bridge.serial, icon_path, payload)
        fetched += 1

    _LOGGER.debug(
        "Loxone icon prefetch finished for serial=%s cached=%s requested=%s",
        bridge.serial,
        fetched,
        len(icon_paths),
    )
