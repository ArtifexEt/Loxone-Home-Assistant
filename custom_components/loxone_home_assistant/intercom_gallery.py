"""Serve the Intercom photo gallery and protect its archive with camera permissions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView, StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity import control_entity_unique_id
from .intercom_archive import (
    GEN2_ARCHIVE_SELECTION_PREFIX,
    IntercomArchiveUnavailableError,
    async_gen2_archive_entries,
    async_gen2_archive_image,
    async_gen2_archive_thumbnail,
    is_gen2_archive_control,
)
from .runtime import bridges_by_entry_id

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .bridge import LoxoneBridge
    from .models import LoxoneControl

INTERCOM_GALLERY_RESOURCE_URL = f"/{DOMAIN}/intercom-gallery.js"
INTERCOM_HISTORY_PANEL_PATH = "loxone-intercom-history"
_PANEL_RESOURCE_URL = f"/{DOMAIN}/intercom-history-panel.js"
_GALLERY_URL = f"/api/{DOMAIN}/intercom_gallery/{{entity_id}}"
_HISTORY_DEVICE_URL = f"/api/{DOMAIN}/intercom_history_device/{{device_id}}"
_DATA_VIEW_REGISTERED = "intercom_gallery_view_registered"
_DEVICE_ID = re.compile(r"[0-9a-f]{32}")
_PAGE_SIZE = 18
_PAGE_NUMBER = re.compile(r"[1-9][0-9]{0,8}")
_PHOTO_ID = re.compile(rf"{re.escape(GEN2_ARCHIVE_SELECTION_PREFIX)}[1-9][0-9]{{0,11}}")
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


async def async_setup_intercom_gallery(hass: HomeAssistant) -> None:
    """Register the packaged card and the authenticated archive API once."""
    domain_data = hass.data[DOMAIN]
    if domain_data.get(_DATA_VIEW_REGISTERED):
        return

    frontend = Path(__file__).with_name("frontend")
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(INTERCOM_GALLERY_RESOURCE_URL, str(frontend / "intercom-gallery.js"), cache_headers=False),
            StaticPathConfig(_PANEL_RESOURCE_URL, str(frontend / "intercom-history-panel.js"), cache_headers=False),
        ]
    )
    hass.http.register_view(IntercomGalleryView(hass))
    hass.http.register_view(IntercomHistoryDeviceView(hass))
    await async_register_panel(
        hass,
        frontend_url_path=INTERCOM_HISTORY_PANEL_PATH,
        webcomponent_name="loxone-intercom-history-panel",
        module_url=_PANEL_RESOURCE_URL,
    )
    domain_data[_DATA_VIEW_REGISTERED] = True


def async_add_intercom_history_device_link(
    hass: HomeAssistant,
    registry_entry: er.RegistryEntry | None,
    bridge: LoxoneBridge,
    control: LoxoneControl,
) -> None:
    """Link a Video camera's device to its gallery through Home Assistant."""
    if not hass.data.get(DOMAIN, {}).get(_DATA_VIEW_REGISTERED) or registry_entry is None:
        return
    device_id = registry_entry.device_id
    if not device_id or _DEVICE_ID.fullmatch(device_id) is None:
        return

    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return

    url = f"homeassistant://{INTERCOM_HISTORY_PANEL_PATH}/{device_id}"
    if is_gen2_archive_control(bridge, control):
        if device.configuration_url != url:
            registry.async_update_device(device_id, configuration_url=url)
    elif device.configuration_url == url:
        registry.async_update_device(device_id, configuration_url=None)


class IntercomGalleryView(HomeAssistantView):
    """List photos or serve a JPEG for an authorized Home Assistant camera."""

    url = _GALLERY_URL
    extra_urls = [f"{_GALLERY_URL}/{{photo_id}}"]
    name = f"api:{DOMAIN}:intercom-gallery"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(
        self, request: web.Request, entity_id: str, photo_id: str | None = None
    ) -> web.Response:
        """Resolve the camera before requesting any archive data."""
        user = request.get(KEY_HASS_USER)
        if user is None:
            raise web.HTTPUnauthorized(headers=_PRIVATE_HEADERS)
        if not user.permissions.check_entity(entity_id, POLICY_READ):
            raise web.HTTPForbidden(headers=_PRIVATE_HEADERS)
        bridge, control = _archive_camera(self._hass, er.async_get(self._hass).async_get(entity_id))

        if photo_id is None:
            page_value = request.query.get("page", "1")
            if _PAGE_NUMBER.fullmatch(page_value) is None:
                raise web.HTTPBadRequest(headers=_PRIVATE_HEADERS)
            try:
                entries = await async_gen2_archive_entries(bridge, control, require_fresh=True)
            except IntercomArchiveUnavailableError:
                raise web.HTTPServiceUnavailable(headers=_PRIVATE_HEADERS) from None
            last_page = max(1, (len(entries) + _PAGE_SIZE - 1) // _PAGE_SIZE)
            page = min(int(page_value), last_page)
            start = (page - 1) * _PAGE_SIZE
            return self.json(
                {
                    "items": [
                        {"id": entry.selection_value, "timestamp": entry.timestamp}
                        for entry in entries[start : start + _PAGE_SIZE]
                    ],
                    "page": page,
                    "page_size": _PAGE_SIZE,
                    "total": len(entries),
                    "language": getattr(bridge, "intercom_language", None),
                },
                headers=_PRIVATE_HEADERS,
            )

        if _PHOTO_ID.fullmatch(photo_id) is None:
            raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)
        thumbnail = request.query.get("thumbnail", "0")
        if thumbnail not in {"0", "1"}:
            raise web.HTTPBadRequest(headers=_PRIVATE_HEADERS)
        fetch_photo = async_gen2_archive_thumbnail if thumbnail == "1" else async_gen2_archive_image
        photo = await fetch_photo(bridge, control, photo_id)
        if photo is None:
            raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)
        return web.Response(body=photo, content_type="image/jpeg", headers=_PRIVATE_HEADERS)


class IntercomHistoryDeviceView(HomeAssistantView):
    """Resolve a Home Assistant device link to a readable Video entity."""

    url = _HISTORY_DEVICE_URL
    name = f"api:{DOMAIN}:intercom-history-device"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request, device_id: str) -> web.Response:
        """Resolve current entity IDs, including cameras renamed by the user."""
        user = request.get(KEY_HASS_USER)
        if user is None:
            raise web.HTTPUnauthorized(headers=_PRIVATE_HEADERS)
        if _DEVICE_ID.fullmatch(device_id) is None:
            raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)

        registry = er.async_get(self._hass)
        unavailable = False
        for entry in er.async_entries_for_device(registry, device_id):
            if entry.domain != "camera" or entry.platform != DOMAIN:
                continue
            if not user.permissions.check_entity(entry.entity_id, POLICY_READ):
                continue
            try:
                bridge, _control = _archive_camera(self._hass, entry)
            except web.HTTPNotFound:
                continue
            except web.HTTPServiceUnavailable:
                unavailable = True
                continue
            return self.json(
                {"entity_id": entry.entity_id, "language": getattr(bridge, "intercom_language", None)},
                headers=_PRIVATE_HEADERS,
            )

        if unavailable:
            raise web.HTTPServiceUnavailable(headers=_PRIVATE_HEADERS)
        raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)


def _archive_camera(hass: HomeAssistant, entry: er.RegistryEntry | None) -> tuple[LoxoneBridge, LoxoneControl]:
    """Bind the registry camera to its original control in its loaded entry."""
    if (
        entry is None
        or entry.domain != "camera"
        or entry.platform != DOMAIN
        or entry.disabled
        or entry.config_entry_id is None
    ):
        raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)

    bridge = bridges_by_entry_id(hass).get(entry.config_entry_id)
    if bridge is None:
        raise web.HTTPServiceUnavailable(headers=_PRIVATE_HEADERS)
    for control in bridge.controls:
        if is_gen2_archive_control(bridge, control) and entry.unique_id == control_entity_unique_id(
            bridge.serial, control.uuid_action, "Video"
        ):
            return bridge, control
    raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)
