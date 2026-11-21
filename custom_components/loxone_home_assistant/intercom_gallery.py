"""Serve the Intercom photo gallery and protect its archive with camera permissions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView, StaticPathConfig
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity import control_entity_unique_id
from .intercom_archive import (
    GEN2_ARCHIVE_SELECTION_PREFIX,
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
_GALLERY_URL = f"/api/{DOMAIN}/intercom_gallery/{{entity_id}}"
_DATA_VIEW_REGISTERED = "intercom_gallery_view_registered"
_PAGE_SIZE = 18
_PAGE_NUMBER = re.compile(r"[1-9][0-9]{0,8}")
_PHOTO_ID = re.compile(rf"{re.escape(GEN2_ARCHIVE_SELECTION_PREFIX)}[1-9][0-9]{{0,11}}")
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


async def async_setup_intercom_gallery(hass: HomeAssistant) -> None:
    """Register the packaged card and the authenticated archive API once."""
    domain_data = hass.data[DOMAIN]
    if domain_data.get(_DATA_VIEW_REGISTERED):
        return

    script = Path(__file__).with_name("frontend") / "intercom-gallery.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(INTERCOM_GALLERY_RESOURCE_URL, str(script), cache_headers=False)]
    )
    hass.http.register_view(IntercomGalleryView(hass))
    domain_data[_DATA_VIEW_REGISTERED] = True


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
        bridge, control = self._archive_camera(entity_id)

        if photo_id is None:
            page_value = request.query.get("page", "1")
            if _PAGE_NUMBER.fullmatch(page_value) is None:
                raise web.HTTPBadRequest(headers=_PRIVATE_HEADERS)
            entries = await async_gen2_archive_entries(bridge, control)
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

    def _archive_camera(self, entity_id: str) -> tuple[LoxoneBridge, LoxoneControl]:
        """Bind the registry camera to its original control in its loaded entry."""
        entry = er.async_get(self._hass).async_get(entity_id)
        if (
            entry is None
            or entry.domain != "camera"
            or entry.platform != DOMAIN
            or entry.disabled
            or entry.config_entry_id is None
        ):
            raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)

        bridge = bridges_by_entry_id(self._hass).get(entry.config_entry_id)
        if bridge is not None:
            for control in bridge.controls:
                if is_gen2_archive_control(bridge, control) and entry.unique_id == control_entity_unique_id(
                    bridge.serial, control.uuid_action, "Video"
                ):
                    return bridge, control
        raise web.HTTPNotFound(headers=_PRIVATE_HEADERS)
