"""Coverage for Gen 2 Intercom activity discovery and private photo handling."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import sys
import unittest
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from tests._loader import load_integration_module

archive = load_integration_module("custom_components.loxone_home_assistant.intercom_archive")
signaling = import_module("custom_components.loxone_home_assistant.intercom_signaling")

DEVICE_UUID = "01234567-89ab-cdef-0123456789abcdef"
NEWER = 1_800_000_100
OLDER = 1_800_000_000
JPEG = b"\xff\xd8synthetic JPEG payload\xff\xd9"


def _test_jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.synthetic-signature"


class Gen2ArchiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.states = {"bell": 0, "lastBellUnix": OLDER}
        self.bridge = SimpleNamespace(
            use_tls=True, token="synthetic-token", control_state=lambda _, name: self.states[name]
        )
        self.control = archive.LoxoneControl(
            uuid="test-intercom", uuid_action="test-action", name="Intercom",
            type="IntercomV2", details={"deviceUuid": DEVICE_UUID},
        )
        self.photo_path = f"/snapshots/example/bell/{NEWER}.jpg?signature=test"
        self.thumb_path = f"/snapshots/example/bell/{NEWER}-thumb.jpg?signature=test"
        self.session = SimpleNamespace(
            async_request=AsyncMock(return_value=[
                {"date": OLDER, "imagePath": "snapshots/older.jpg"},
                {"date": NEWER, "imagePath": self.photo_path, "thumbPath": self.thumb_path},
                {"date": NEWER, "imagePath": "duplicate.jpg"},
                {"date": True, "imagePath": "invalid.jpg"},
                {"date": "1800000099", "error": {"invalid": True}, "imagePath": "partial.jpg"},
                {"date": "1800000098", "error": 1, "imagePath": "incomplete.jpg"},
                {"date": "1800000097", "thumbPath": "no-full-image.jpg"},
            ]),
            async_get_archive_photo=AsyncMock(return_value=JPEG),
            async_close=AsyncMock(),
        )
        connection = patch.object(
            archive.IntercomSignaling, "async_connect", AsyncMock(return_value=self.session)
        )
        self.connect = self.enterContext(connection)

    async def test_refresh_is_shared_and_returns_only_sorted_timestamps(self) -> None:
        first, second = await asyncio.gather(*(
            archive.async_gen2_archive_entries(self.bridge, self.control) for _ in range(2)
        ))

        self.assertEqual(first, second)
        self.assertEqual([entry.timestamp for entry in first], [NEWER, OLDER])
        self.assertEqual(first[0].selection_value, f"gen2:{NEWER}")
        self.assertNotIn("signature", repr(first))
        self.connect.assert_awaited_once_with(self.bridge, DEVICE_UUID)
        self.session.async_request.assert_awaited_once_with("getLastActivities", [0, 100, 2])
        self.session.async_close.assert_awaited_once()

        self.states["bell"] = 1
        await archive.async_gen2_archive_entries(self.bridge, self.control)
        self.assertEqual(self.connect.await_count, 2)

    async def test_invalid_params_uses_the_legacy_list_format(self) -> None:
        self.session.async_request.side_effect = [
            signaling.IntercomSignalingError("Invalid parameters", code=-32602),
            [{"date": OLDER, "imagePath": "snapshots/older.jpg"}],
        ]
        entries = await archive.async_gen2_archive_entries(self.bridge, self.control)

        self.assertEqual([entry.timestamp for entry in entries], [OLDER])
        self.session.async_request.assert_awaited_with("getLastActivities", [0, 100])

    async def test_live_photo_fetch_uses_new_authenticated_session_and_bounded_cache(self) -> None:
        entries = await archive.async_gen2_archive_entries(self.bridge, self.control)
        for _ in range(2):
            photo = await archive.async_gen2_archive_image(
                self.bridge, self.control, entries[0].selection_value
            )
            self.assertEqual(photo, JPEG)

        self.assertEqual(self.connect.await_count, 2)
        self.assertEqual(self.session.async_close.await_count, 2)
        self.session.async_get_archive_photo.assert_awaited_once_with(self.photo_path)
        self.assertIsNone(await archive.async_gen2_archive_image(
            self.bridge, self.control, "gen2:https://example.invalid/test.jpg"
        ))

    async def test_unsupported_and_unauthenticated_controls_do_not_open_a_socket(self) -> None:
        self.bridge.token = None
        self.assertTrue(archive.is_gen2_archive_control(self.bridge, self.control))
        self.assertEqual(await archive.async_gen2_archive_entries(self.bridge, self.control), ())
        self.bridge._remote_serial = "test"
        self.assertFalse(archive.is_gen2_archive_control(self.bridge, self.control))
        self.connect.assert_not_awaited()

    async def test_modern_photo_uses_permission_zero_jwt_from_token_challenge(self) -> None:
        token = _test_jwt({"msSerial": "000000000001", "tokenRights": 0})
        key = "a3ff0061"
        self.bridge.client_uuid = "00000000-0000-0000-0000-000000000001"
        self.bridge.username = "Dummy User"
        self.bridge.async_send_raw_command = AsyncMock(side_effect=[
            {"value": key},
            {"value": {"token": token, "tokenRights": 0, "validUntil": 900_000_000}},
        ])

        for timestamp in (NEWER, OLDER):
            self.assertEqual(
                await archive.async_gen2_archive_image(self.bridge, self.control, f"gen2:{timestamp}"), JPEG
            )

        self.bridge.async_send_raw_command.assert_any_await("jdev/sys/getkey")
        proof = hmac.digest(bytes.fromhex(key), b"synthetic-token", "sha256").hex()
        command = self.bridge.async_send_raw_command.await_args_list[1].args[0]
        self.assertEqual(self.bridge.async_send_raw_command.await_count, 2)
        self.assertRegex(command, rf"^jdev/sys/getjwt/{proof}/Dummy%20User/0/"
                         r"[0-9a-f]{8}(-[0-9a-f]{4}){2}-[0-9a-f]{16}/"
                         r"Home%20Assistant%20Intercom\?tokenHash=true$")
        self.session.async_get_archive_photo.assert_has_awaits([
            call(self.photo_path, auth_token=token), call("snapshots/older.jpg", auth_token=token),
        ])
        self.assertNotIn(token, repr(self.bridge._intercom_archive_access))

    async def test_rejected_modern_token_uses_the_existing_intercom_challenge(self) -> None:
        self.bridge.client_uuid = "test-client"
        self.bridge.username = "test user"
        self.bridge.async_send_raw_command = AsyncMock(side_effect=[
            {"value": "00000000"},
            {"value": {"token": _test_jwt({"tokenRights": 0})}},
        ])
        self.assertEqual(
            await archive.async_gen2_archive_image(self.bridge, self.control, f"gen2:{NEWER}"), JPEG
        )
        self.session.async_get_archive_photo.assert_awaited_once_with(self.photo_path)

    async def test_thumbnails_share_metadata_and_token_and_prefer_native_previews(self) -> None:
        token = _test_jwt({"msSerial": "000000000001", "tokenRights": 0})
        self.bridge.client_uuid = "test-client"
        self.bridge.username = "test user"
        self.bridge.async_send_raw_command = AsyncMock(side_effect=[
            {"value": "00000000"},
            {"value": {"token": token}},
        ])
        download = self.enterContext(patch.object(archive, "async_get_scoped_archive_photo", AsyncMock(return_value=JPEG)))

        self.assertEqual(await asyncio.gather(*(
            archive.async_gen2_archive_thumbnail(self.bridge, self.control, f"gen2:{timestamp}")
            for timestamp in (NEWER, OLDER)
        )), [JPEG, JPEG])

        self.connect.assert_awaited_once()
        self.assertEqual(self.bridge.async_send_raw_command.await_count, 2)
        self.session.async_get_archive_photo.assert_not_awaited()
        download.assert_has_awaits([
            call(self.bridge, DEVICE_UUID, self.thumb_path, token),
            call(self.bridge, DEVICE_UUID, "snapshots/older.jpg", token),
        ], any_order=True)
        self.assertNotIn("signature", repr(archive._archive_cache(self.bridge, self.control)))

    async def test_missing_scoped_preview_falls_back_to_the_full_photo_without_signaling(self) -> None:
        token = _test_jwt({"msSerial": "000000000001", "tokenRights": 0})
        self.enterContext(patch.object(archive, "_async_archive_token", AsyncMock(return_value=token)))
        download = self.enterContext(patch.object(
            archive, "async_get_scoped_archive_photo", AsyncMock(side_effect=[None, JPEG])
        ))

        self.assertEqual(await archive.async_gen2_archive_thumbnail(
            self.bridge, self.control, f"gen2:{NEWER}"
        ), JPEG)

        self.connect.assert_awaited_once()
        download.assert_has_awaits([
            call(self.bridge, DEVICE_UUID, self.thumb_path, token),
            call(self.bridge, DEVICE_UUID, self.photo_path, token),
        ])

    async def test_legacy_thumbnails_use_a_new_session_for_challenge_authentication(self) -> None:
        self.session.async_get_archive_photo.side_effect = [None, JPEG]

        self.assertEqual(await archive.async_gen2_archive_thumbnail(
            self.bridge, self.control, f"gen2:{NEWER}"
        ), JPEG)

        self.assertEqual(self.connect.await_count, 2)
        self.assertEqual(self.session.async_close.await_count, 2)
        self.session.async_get_archive_photo.assert_has_awaits([call(self.thumb_path), call(self.photo_path)])

    async def test_invalid_and_unknown_thumbnail_ids_never_download_a_photo(self) -> None:
        download = self.enterContext(patch.object(archive, "async_get_scoped_archive_photo", AsyncMock()))
        self.assertIsNone(await archive.async_gen2_archive_thumbnail(
            self.bridge, self.control, "gen2:https://example.invalid/test.jpg"
        ))
        self.connect.assert_not_awaited()
        self.assertIsNone(await archive.async_gen2_archive_thumbnail(self.bridge, self.control, "gen2:1"))
        self.connect.assert_awaited_once()
        download.assert_not_awaited()
        self.session.async_get_archive_photo.assert_not_awaited()

    async def test_does_not_accept_a_token_with_broad_rights(self) -> None:
        self.assertIsNone(archive._archive_token_payload(_test_jwt({
            "msSerial": "000000000001", "tokenRights": 4,
        })))


class ArchiveDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.calls = []
        self.tls = object()
        self.content_type = "image/jpeg"
        self.photo = JPEG
        self.status = 200
        self.session = signaling.IntercomSignaling(
            SimpleNamespace(get=self._get),
            SimpleNamespace(),
            f"wss://example.invalid:8443/base/proxy/{DEVICE_UUID}/",
            self.tls,
        )
        self.session._photo_token = "synthetic photo token"

    def _get(self, url, **kwargs):
        from contextlib import asynccontextmanager

        self.calls.append((str(url), kwargs))

        async def chunks(_):
            yield self.photo

        @asynccontextmanager
        async def response():
            yield SimpleNamespace(
                status=self.status, content_type=self.content_type, content_length=None,
                content=SimpleNamespace(iter_chunked=chunks),
            )

        return response()

    async def test_keeps_signed_query_and_tls_scope(self) -> None:
        jpeg = await self.session.async_get_archive_photo(
            f"/snapshots/example/bell/{OLDER}?signature=%2f%2B&autht=old&version=1"
        )

        self.assertEqual(jpeg, JPEG)
        self.assertEqual(self.calls, [(
            f"https://example.invalid:8443/base/proxy/{DEVICE_UUID}/snapshots/example/bell/{OLDER}"
            "?signature=%2f%2B&version=1&autht=synthetic+photo+token",
            {"ssl": self.tls, "allow_redirects": False},
        )])

    async def test_explicit_photo_token_replaces_the_session_challenge(self) -> None:
        token = _test_jwt({"msSerial": "000000000001", "tokenRights": 0})
        self.status = 401
        with self.assertLogs(signaling.__name__, "DEBUG") as logs:
            self.assertIsNone(await self.session.async_get_archive_photo("photo.jpg", auth_token=token))

        self.assertEqual(self.calls, [(
            f"https://example.invalid:8443/base/proxy/{DEVICE_UUID}/photo.jpg?autht={token}",
            {"ssl": self.tls, "allow_redirects": False},
        )])
        self.assertIn("HTTP status 401", logs.output[0])
        self.assertNotIn(token, logs.output[0])
        self.assertNotIn("example.invalid", logs.output[0])

    async def test_scoped_photo_keeps_local_origin_and_pins_the_authenticated_certificate(self) -> None:
        digest = bytes(range(32))
        bridge = SimpleNamespace(
            hass=object(), host="example.invalid", port=8443, verify_ssl=False,
            resolve_http_url=lambda path: f"https://example.invalid:8443/base{path}",
            miniserver_tls_fingerprint=lambda: digest,
        )
        get_client = Mock(return_value=SimpleNamespace(get=self._get))
        fingerprint = Mock(return_value=self.tls)
        with patch.dict(sys.modules, {
            "aiohttp": SimpleNamespace(ClientError=type("ClientError", (Exception,), {}), Fingerprint=fingerprint),
            "homeassistant.helpers.aiohttp_client": SimpleNamespace(async_get_clientsession=get_client),
        }):
            self.assertEqual(await signaling.async_get_scoped_archive_photo(
                bridge, DEVICE_UUID, "photo.jpg", "synthetic-photo-token"
            ), JPEG)

        get_client.assert_called_once_with(bridge.hass, verify_ssl=False)
        fingerprint.assert_called_once_with(digest)
        [(url, request)] = self.calls
        self.assertEqual(url, f"https://example.invalid:8443/base/proxy/{DEVICE_UUID}/photo.jpg?autht=synthetic-photo-token")
        self.assertEqual(request, {"ssl": self.tls, "allow_redirects": False})

    async def test_external_and_traversal_paths_are_rejected(self) -> None:
        for path in (
            "//other.invalid/photo.jpg",
            "https://other.invalid/photo.jpg",
            "snapshots/../../photo.jpg",
            "snapshots/%252e%252e/photo.jpg",
            "snapshots\\example\\photo.jpg",
            "snapshots/photo.jpg#other",
            "/dev/sys/reboot",
        ):
            with self.subTest(path=path):
                self.assertIsNone(await self.session.async_get_archive_photo(path))
        self.assertEqual(self.calls, [])

    async def test_redirects_and_invalid_media_are_rejected(self) -> None:
        self.status = 302
        self.assertIsNone(await self.session.async_get_archive_photo("photo.jpg"))
        self.status = 200
        self.content_type = "text/html"
        self.assertIsNone(await self.session.async_get_archive_photo("photo.jpg"))
        self.content_type = "image/jpeg"
        self.photo = b"not a JPEG"
        self.assertIsNone(await self.session.async_get_archive_photo("photo.jpg"))
        self.photo = JPEG * 3
        with patch.object(signaling, "_MAX_PHOTO_BYTES", len(JPEG) * 2):
            self.assertIsNone(await self.session.async_get_archive_photo("photo.jpg"))
