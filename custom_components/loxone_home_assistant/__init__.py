"""Home Assistant entry point for the Loxone integration."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote

from aiohttp import BasicAuth, ClientError, web
import voluptuous as vol
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

try:
    from homeassistant.core import SupportsResponse
except ImportError:  # pragma: no cover - compatibility for lightweight test stubs
    SupportsResponse = None  # type: ignore[assignment]

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
from .icons import decode_icon_key, normalize_icon_path
from .intercom import (
    build_intercom_tts_command,
    is_intercom_control,
    resolve_intercom_command,
)
from .intercom_stream_proxy import (
    INTERCOM_STREAM_PROXY_URL,
    clear_intercom_stream_targets,
    intercom_stream_target,
)
from .models import LoxoneControl
from .runtime import bridges_by_entry_id, remove_entry_bridge, set_entry_bridge
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
DATA_INTERCOM_STREAM_VIEW_REGISTERED = "intercom_stream_view_registered"


def _command_service_register_kwargs() -> dict[str, Any]:
    """Return kwargs enabling optional service responses when supported by HA."""
    if SupportsResponse is None:
        return {}
    return {"supports_response": SupportsResponse.OPTIONAL}


def _service_validation_exception(message: str) -> Exception:
    """Build a validation error compatible with multiple HA core versions."""
    try:
        return ServiceValidationError(message)
    except TypeError:
        return HomeAssistantError(message)


class LoxoneIconProxyView(HomeAssistantView):
    """Serve Loxone icons through authenticated HA endpoint."""

    url = f"/api/{DOMAIN}/icon/{{serial}}/{{icon_key}}"
    name = f"api:{DOMAIN}:icon"
    # Keep icon URLs usable in plain <img> contexts where HA auth headers/cookies
    # may not be attached. Path validation in `decode_icon_key` limits fetch scope
    # to safe relative Miniserver icon paths only.
    requires_auth = False

    async def get(self, request: web.Request, serial: str, icon_key: str) -> web.Response:
        hass = getattr(self, "hass", None) or request.app.get("hass")
        if hass is None:
            _LOGGER.error("Icon proxy request received without Home Assistant context.")
            return web.Response(status=500)

        bridge = _resolve_bridge_for_serial(hass, serial)
        if bridge is None:
            return web.Response(status=404)

        icon_path = decode_icon_key(icon_key)
        if icon_path is None:
            return web.Response(status=400)

        cached = _cached_icon_payload(hass, bridge.serial, icon_path)
        if cached is not None:
            return _icon_web_response(cached)

        payload = await _async_fetch_icon_payload(bridge, icon_path)
        if payload is None:
            return web.Response(status=404)

        _store_cached_icon_payload(hass, bridge.serial, icon_path, payload)
        return _icon_web_response(payload)


class LoxoneIntercomStreamProxyView(HomeAssistantView):
    """Proxy one Intercom MJPEG stream through Home Assistant."""

    url = INTERCOM_STREAM_PROXY_URL
    name = f"api:{DOMAIN}:intercom_stream"
    requires_auth = False

    async def get(self, request: web.Request, serial: str, uuid_action: str) -> web.StreamResponse:
        hass = getattr(self, "hass", None) or request.app.get("hass")
        if hass is None:
            _LOGGER.error("Intercom stream proxy request received without Home Assistant context.")
            return web.Response(status=500)

        bridge = _resolve_bridge_for_serial(hass, serial)
        if bridge is None:
            return web.Response(status=404)

        control = bridge.control_for_uuid_action(uuid_action)
        if control is None or not is_intercom_control(control):
            return web.Response(status=404)

        target = intercom_stream_target(bridge, uuid_action)
        if target is None:
            return web.Response(status=404)

        session = getattr(bridge, "_session", None)
        if session is None:
            return web.Response(status=503)

        username = target["username"]
        password = target["password"]
        request_auth = BasicAuth(username, password) if username is not None else None
        target_url = target["target_url"]

        try:
            async with session.get(target_url, auth=request_auth) as upstream:
                if upstream.status != 200:
                    return web.Response(status=upstream.status)

                content_type = upstream.headers.get("Content-Type", "multipart/x-mixed-replace")
                if ";" in content_type:
                    content_type = content_type.split(";", 1)[0].strip()
                response = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": content_type or "multipart/x-mixed-replace",
                        "Cache-Control": "no-store",
                    },
                )
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(16 * 1024):
                    if not chunk:
                        continue
                    await response.write(chunk)
                await response.write_eof()
                return response
        except asyncio.CancelledError:
            raise
        except ClientError as err:
            _LOGGER.debug(
                "Intercom stream proxy request failed for serial=%s uuid_action=%s (%s)",
                bridge.serial,
                uuid_action,
                err,
            )
            return web.Response(status=502)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Intercom stream proxy request errored for serial=%s uuid_action=%s (%s)",
                bridge.serial,
                uuid_action,
                err,
            )
            return web.Response(status=500)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration namespace."""
    del config
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})
    hass.data[DOMAIN].setdefault(DATA_ICON_CACHE, {})
    hass.data[DOMAIN].setdefault(DATA_INTERCOM_STREAM_VIEW_REGISTERED, False)
    if not hass.data[DOMAIN].get(DATA_ICON_VIEW_REGISTERED):
        hass.http.register_view(LoxoneIconProxyView())
        hass.data[DOMAIN][DATA_ICON_VIEW_REGISTERED] = True
    if not hass.data[DOMAIN].get(DATA_INTERCOM_STREAM_VIEW_REGISTERED):
        hass.http.register_view(LoxoneIntercomStreamProxyView())
        hass.data[DOMAIN][DATA_INTERCOM_STREAM_VIEW_REGISTERED] = True

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_COMMAND,
            _make_send_command_handler(hass),
            schema=SEND_COMMAND_SCHEMA,
            **_command_service_register_kwargs(),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_RAW_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_RAW_COMMAND,
            _make_send_raw_command_handler(hass),
            schema=SEND_RAW_COMMAND_SCHEMA,
            **_command_service_register_kwargs(),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_TTS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_TTS,
            _make_send_tts_handler(hass),
            schema=SEND_TTS_SCHEMA,
            **_command_service_register_kwargs(),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CALL_INTERCOM_FUNCTION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CALL_INTERCOM_FUNCTION,
            _make_call_intercom_function_handler(hass),
            schema=CALL_INTERCOM_FUNCTION_SCHEMA,
            **_command_service_register_kwargs(),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CALL_INTERCOM_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CALL_INTERCOM_COMMAND,
            _make_call_intercom_command_handler(hass),
            schema=CALL_INTERCOM_COMMAND_SCHEMA,
            **_command_service_register_kwargs(),
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_BRIDGES, {})

    merged_data = {**entry.data, **entry.options}
    bridge = LoxoneBridge(hass, merged_data)
    await bridge.async_initialize()
    if getattr(bridge, "use_loxone_icons", True):
        hass.async_create_task(_async_prefetch_icons(hass, bridge))

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
        clear_intercom_stream_targets(bridge)
        _clear_cached_bridge_icons(hass, bridge.serial)
        await bridge.async_shutdown()

    return unload_ok


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
        raise _service_validation_exception("TTS message cannot be empty.")

    volume = call.data.get(SERVICE_ATTR_VOLUME)
    encoded_message = quote(message, safe="")
    target_control = bridge.control_for_uuid_action(uuid_action)

    if target_control is not None and is_intercom_control(target_control):
        command = build_intercom_tts_command(message)
    else:
        command = (
            f"tts/{encoded_message}"
            if volume is None
            else f"tts/{encoded_message}/{int(volume)}"
        )

    if command is None:
        raise _service_validation_exception("TTS message cannot be empty.")

    return await bridge.async_send_action(uuid_action, command)


async def _async_handle_call_intercom_function(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    bridge = _resolve_bridge(hass, call)
    intercom_uuid_action = call.data[SERVICE_ATTR_UUID_ACTION]
    selector = str(call.data[SERVICE_ATTR_FUNCTION]).strip()
    if not selector:
        raise _service_validation_exception("Intercom function selector cannot be empty.")

    intercom_control = bridge.control_for_uuid_action(intercom_uuid_action)
    if intercom_control is None:
        raise _service_validation_exception(
            f"Unknown intercom uuid_action: {intercom_uuid_action}"
        )
    if not is_intercom_control(intercom_control):
        raise _service_validation_exception(
            f"Control {intercom_uuid_action} is not recognized as an intercom."
        )

    available_controls = [
        control
        for control in bridge.controls
        if control.parent_uuid_action == intercom_control.uuid_action
    ]
    if not available_controls:
        raise _service_validation_exception(
            f"Intercom {intercom_uuid_action} has no mapped child functions."
        )

    selected_control = _select_intercom_function_control(
        available_controls,
        selector,
    )
    if selected_control is None:
        available_labels = ", ".join(control.name for control in available_controls)
        raise _service_validation_exception(
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
        raise _service_validation_exception(
            f"Unknown intercom uuid_action: {intercom_uuid_action}"
        )
    if not is_intercom_control(intercom_control):
        raise _service_validation_exception(
            f"Control {intercom_uuid_action} is not recognized as an intercom."
        )

    try:
        resolved_command = resolve_intercom_command(
            str(call.data[SERVICE_ATTR_COMMAND]),
            call.data.get(SERVICE_ATTR_ARGUMENTS),
        )
    except ValueError as err:
        raise _service_validation_exception(str(err)) from err

    return await bridge.async_send_action(intercom_control.uuid_action, resolved_command)


def _make_send_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> dict[str, Any]:
        return await _async_handle_send_command(hass, call)

    return handler


def _make_send_raw_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> dict[str, Any]:
        return await _async_handle_send_raw_command(hass, call)

    return handler


def _make_send_tts_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> dict[str, Any]:
        return await _async_handle_send_tts(hass, call)

    return handler


def _make_call_intercom_function_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> dict[str, Any]:
        return await _async_handle_call_intercom_function(hass, call)

    return handler


def _make_call_intercom_command_handler(hass: HomeAssistant):
    async def handler(call: ServiceCall) -> dict[str, Any]:
        return await _async_handle_call_intercom_command(hass, call)

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
            raise _service_validation_exception(
                f"Unknown or unloaded Loxone entry_id: {entry_id}"
            )
        return bridge

    if len(bridges) == 1:
        return next(iter(bridges.values()))
    if not bridges:
        raise _service_validation_exception(
            "No configured Loxone entries are currently loaded."
        )

    raise _service_validation_exception(
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


def _resolve_bridge_for_serial(hass: HomeAssistant, serial: str) -> LoxoneBridge | None:
    normalized = str(serial).strip()
    if not normalized:
        return None
    for bridge in bridges_by_entry_id(hass).values():
        if str(bridge.serial).strip() == normalized:
            return bridge
    return None


def _icon_cache(hass: HomeAssistant) -> dict[str, dict[str, tuple[str, bytes]]]:
    hass.data.setdefault(DOMAIN, {})
    domain_data = hass.data[DOMAIN]
    domain_data.setdefault(DATA_ICON_CACHE, {})
    return domain_data[DATA_ICON_CACHE]


def _cached_icon_payload(
    hass: HomeAssistant, serial: str, icon_path: str
) -> tuple[str, bytes] | None:
    serial_cache = _icon_cache(hass).get(str(serial))
    if not isinstance(serial_cache, dict):
        return None
    payload = serial_cache.get(icon_path)
    if not isinstance(payload, tuple) or len(payload) != 2:
        return None
    content_type, body = payload
    if not isinstance(content_type, str) or not isinstance(body, bytes):
        return None
    return content_type, body


def _store_cached_icon_payload(
    hass: HomeAssistant, serial: str, icon_path: str, payload: tuple[str, bytes]
) -> None:
    cache = _icon_cache(hass)
    serial_key = str(serial)
    serial_cache = cache.setdefault(serial_key, {})
    serial_cache[icon_path] = payload


def _clear_cached_bridge_icons(hass: HomeAssistant, serial: str) -> None:
    _icon_cache(hass).pop(str(serial), None)


def _icon_web_response(payload: tuple[str, bytes]) -> web.Response:
    content_type, body = payload
    return web.Response(body=body, content_type=content_type or "image/svg+xml")


async def _async_fetch_icon_payload(
    bridge: LoxoneBridge, icon_path: str
) -> tuple[str, bytes] | None:
    icon_url = bridge.resolve_http_url(icon_path)
    if icon_url is None:
        return None
    session = getattr(bridge, "_session", None)
    if session is None:
        return None

    auth = BasicAuth(bridge.username, bridge.password)
    for request_auth in (auth, None):
        try:
            async with session.get(icon_url, auth=request_auth) as response:
                if response.status == 401 and request_auth is not None:
                    continue
                if response.status != 200:
                    return None
                body = await response.read()
                if not body:
                    return None
                content_type = response.headers.get("Content-Type", "image/svg+xml")
                if ";" in content_type:
                    content_type = content_type.split(";", 1)[0].strip()
                return content_type or "image/svg+xml", body
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
    icon_paths = {
        icon_path
        for control in bridge.controls
        if (icon_path := normalize_icon_path(control.icon))
    }
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
