"""Test authorization and pagination for the Intercom gallery API."""

from __future__ import annotations

import json
import sys
import unittest
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests._loader import load_integration_module
from tests.test_camera_platform import LoxoneControl, _install_homeassistant_stubs


def _load_gallery():
    with patch.dict(sys.modules):
        _install_homeassistant_stubs()
        aiohttp = sys.modules.get("aiohttp")
        if aiohttp is not None and not getattr(aiohttp, "__path__", None):
            del sys.modules["aiohttp"]
        web = import_module("aiohttp.web")
        sys.modules["homeassistant.auth.permissions.const"] = SimpleNamespace(POLICY_READ="read")
        sys.modules["homeassistant.components.http"] = SimpleNamespace(
            KEY_HASS_USER="hass_user",
            HomeAssistantView=type("HomeAssistantView", (), {"json": staticmethod(web.json_response)}),
            StaticPathConfig=Mock(),
        )
        sys.modules["homeassistant.helpers.entity_registry"] = SimpleNamespace(async_get=Mock())
        return load_integration_module("custom_components.loxone_home_assistant.intercom_gallery")


gallery = _load_gallery()
archive = import_module("custom_components.loxone_home_assistant.intercom_archive")
CAMERA_ID = "camera.test_intercom_video"
ENTRY_ID = "test-config-entry"
LATEST = 1_800_000_018


class IntercomGalleryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.control = LoxoneControl(
            uuid="test-intercom", uuid_action="test-action", name="Intercom",
            type="IntercomV2", details={"deviceUuid": "test-device"},
        )
        self.bridge = SimpleNamespace(serial="test-server", controls=[self.control], use_tls=True)
        self.entry = SimpleNamespace(
            domain="camera", platform=gallery.DOMAIN, config_entry_id=ENTRY_ID,
            disabled=False, unique_id="test-server:test-action:video",
        )
        self.registry = {CAMERA_ID: self.entry}
        self.enterContext(patch.object(gallery.er, "async_get", return_value=SimpleNamespace(
            async_get=self.registry.get,
        )))
        self.hass = SimpleNamespace(
            data={gallery.DOMAIN: {"bridges": {ENTRY_ID: self.bridge}}},
            http=SimpleNamespace(async_register_static_paths=AsyncMock(), register_view=Mock()),
        )
        self.read = Mock(return_value=True)
        self.entries = self.enterContext(patch.object(
            gallery, "async_gen2_archive_entries", new_callable=AsyncMock,
        ))
        self.image = self.enterContext(patch.object(
            gallery, "async_gen2_archive_image", new_callable=AsyncMock,
        ))
        self.thumbnail = self.enterContext(patch.object(
            gallery, "async_gen2_archive_thumbnail", new_callable=AsyncMock,
        ))
        self.view = gallery.IntercomGalleryView(self.hass)

    def _request(self, query: dict[str, str] | None = None, *, authenticated: bool = True):
        request = Mock(query=query or {})
        request.get.return_value = (
            SimpleNamespace(permissions=SimpleNamespace(check_entity=self.read))
            if authenticated else None
        )
        return request

    async def test_metadata_pages_return_only_ids_and_timestamps(self) -> None:
        self.entries.return_value = tuple(
            archive.IntercomArchiveEntry(timestamp) for timestamp in range(LATEST, LATEST - 19, -1)
        )
        first = await self.view.get(self._request(), CAMERA_ID)
        last = await self.view.get(self._request({"page": "9"}), CAMERA_ID)
        first_page = json.loads(first.body)
        self.assertEqual((first_page["page"], first_page["page_size"], first_page["total"]), (1, 18, 19))
        self.assertEqual(len(first_page["items"]), 18)
        self.assertEqual(first_page["items"][0], {"id": f"gen2:{LATEST}", "timestamp": LATEST})
        self.assertEqual(json.loads(last.body), {
            "items": [{"id": f"gen2:{LATEST - 18}", "timestamp": LATEST - 18}],
            "page": 2, "page_size": 18, "total": 19,
        })
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        self.read.assert_called_with(CAMERA_ID, gallery.POLICY_READ)
        self.entries.assert_awaited_with(self.bridge, self.control)

    async def test_missing_and_denied_authentication_does_not_reach_archive(self) -> None:
        self.assertTrue(self.view.requires_auth)
        with self.assertRaises(gallery.web.HTTPUnauthorized):
            await self.view.get(self._request(authenticated=False), CAMERA_ID)
        self.read.return_value = False
        with self.assertRaises(gallery.web.HTTPForbidden):
            await self.view.get(self._request({"thumbnail": "1"}), CAMERA_ID, f"gen2:{LATEST}")
        self.entries.assert_not_awaited()
        self.image.assert_not_awaited()
        self.thumbnail.assert_not_awaited()

    async def test_requires_the_loaded_registered_archive_camera(self) -> None:
        for change in (
            {"domain": "sensor"}, {"platform": "other_integration"}, {"disabled": True},
            {"config_entry_id": "unloaded-entry"}, {"unique_id": "test-server:test-action:live"},
            {"unique_id": "other-server:test-action:video"},
        ):
            with self.subTest(change=change), patch.multiple(self.entry, **change):
                with self.assertRaises(gallery.web.HTTPNotFound):
                    await self.view.get(self._request(), CAMERA_ID)
        self.entries.assert_not_awaited()

    async def test_full_and_thumbnail_routes_validate_id_and_serve_jpeg(self) -> None:
        photo_id = f"gen2:{LATEST}"
        self.image.return_value = b"\xff\xd8full JPEG\xff\xd9"
        self.thumbnail.return_value = b"\xff\xd8thumbnail JPEG\xff\xd9"
        for query, handler in ((None, self.image), ({"thumbnail": "1"}, self.thumbnail)):
            response = await self.view.get(self._request(query), CAMERA_ID, photo_id)
            handler.assert_awaited_once_with(self.bridge, self.control, photo_id)
            self.assertEqual(response.body, handler.return_value)
            self.assertEqual(response.content_type, "image/jpeg")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with self.assertRaises(gallery.web.HTTPNotFound):
            await self.view.get(self._request(), CAMERA_ID, "https://example.invalid/photo.jpg")
        with self.assertRaises(gallery.web.HTTPBadRequest):
            await self.view.get(self._request({"page": "0"}), CAMERA_ID)
        self.entries.assert_not_awaited()
        self.image.assert_awaited_once()
        self.thumbnail.assert_awaited_once()

    async def test_registers_one_view_and_one_packaged_script(self) -> None:
        await gallery.async_setup_intercom_gallery(self.hass)
        await gallery.async_setup_intercom_gallery(self.hass)
        self.hass.http.async_register_static_paths.assert_awaited_once()
        self.hass.http.register_view.assert_called_once()
        url, filepath = gallery.StaticPathConfig.call_args.args
        self.assertEqual(url, "/loxone_home_assistant/intercom-gallery.js")
        self.assertTrue(filepath.endswith("/frontend/intercom-gallery.js"))
