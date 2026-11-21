"""Tests for the Loxone Intercom camera implementation."""

from __future__ import annotations

import sys
import types
import unittest
from enum import IntFlag
from unittest.mock import AsyncMock, patch

from tests._loader import load_integration_module


def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

    for module_name, domain in {
        "binary_sensor": "binary_sensor",
        "button": "button",
        "camera": "camera",
        "climate": "climate",
        "cover": "cover",
        "light": "light",
        "media_player": "media_player",
        "number": "number",
        "select": "select",
        "sensor": "sensor",
        "switch": "switch",
        "text": "text",
    }.items():
        module = types.ModuleType(f"homeassistant.components.{module_name}")
        module.DOMAIN = domain
        sys.modules[f"homeassistant.components.{module_name}"] = module

    camera = sys.modules["homeassistant.components.camera"]

    class Camera:
        def __init__(self) -> None:
            self._webrtc_provider = None

        async def async_refresh_providers(self, write_state: bool = False) -> None:
            del write_state

    class CameraEntityFeature(IntFlag):
        STREAM = 1 << 0

    camera.Camera = Camera
    camera.CameraEntityFeature = CameraEntityFeature
    camera.SUPPORT_STREAM = 2

    const = types.ModuleType("homeassistant.const")

    class Platform:
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        CLIMATE = "climate"
        COVER = "cover"
        LIGHT = "light"
        NUMBER = "number"
        SELECT = "select"
        SENSOR = "sensor"
        SWITCH = "switch"
        TEXT = "text"

    const.Platform = Platform
    sys.modules["homeassistant.const"] = const

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        pass

    config_entries.ConfigEntry = ConfigEntry
    sys.modules["homeassistant.config_entries"] = config_entries

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers

    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform

    device_registry = types.ModuleType("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        pass

    device_registry.DeviceInfo = DeviceInfo
    sys.modules["homeassistant.helpers.device_registry"] = device_registry

    entity = types.ModuleType("homeassistant.helpers.entity")

    class Entity:
        pass

    entity.Entity = Entity
    sys.modules["homeassistant.helpers.entity"] = entity


class _FakeResponse:
    def __init__(self, status: int, payload: bytes, content_type: str | None = "image/jpeg") -> None:
        self.status = status
        self._payload = payload
        self.content = _FakeStreamContent(payload)
        self.headers = {}
        if content_type is not None:
            self.headers["Content-Type"] = content_type

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def read(self) -> bytes:
        return self._payload


class _FakeStreamContent:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def iter_chunked(self, chunk_size: int):
        for index in range(0, len(self._payload), chunk_size):
            yield self._payload[index : index + chunk_size]


class _FakeSession:
    def __init__(
        self,
        responses: dict[tuple[str, bool], tuple[int, bytes] | tuple[int, bytes, str | None]]
        | None = None,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, bool]] = []
        self.auth_logins: list[str | None] = []
        self.allow_redirects: list[bool] = []

    def get(self, url: str, auth=None, *, allow_redirects=True):
        has_auth = auth is not None
        self.calls.append((url, has_auth))
        self.auth_logins.append(getattr(auth, "login", None) if has_auth else None)
        self.allow_redirects.append(allow_redirects)
        response_data = self.responses.get((url, has_auth), (200, b"default-image", "image/jpeg"))
        if len(response_data) == 2:
            status, payload = response_data
            content_type = "image/jpeg"
        else:
            status, payload, content_type = response_data
        return _FakeResponse(status, payload, content_type)


class _FakeBridge:
    serial = "1234567890"
    available = True
    username = "user"
    password = "pass"
    intercom_username = None
    intercom_password = None

    def __init__(
        self,
        controls,
        values,
        session: _FakeSession,
        secured_details_by_uuid_action: dict | None = None,
        host: str = "mini.local",
        port: int = 443,
        use_tls: bool = True,
        ws_path_prefix: str = "",
    ) -> None:
        self.controls = controls
        self._values = values
        self._session = session
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self._ws_path_prefix = ws_path_prefix
        self._secured_details_by_uuid_action = secured_details_by_uuid_action or {}
        self.action_calls: list[tuple[str, str]] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    def resolve_http_url(self, value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        path = raw if raw.startswith("/") else f"/{raw}"
        prefix = self._ws_path_prefix.rstrip("/")
        if prefix and path != prefix and not path.startswith(f"{prefix}/"):
            path = f"{prefix}{path}"
        scheme = "https" if self.use_tls else "http"
        default_port = 443 if self.use_tls else 80
        port_part = "" if self.port == default_port else f":{self.port}"
        return f"{scheme}://{self.host}{port_part}{path}"

    async def async_send_action(self, uuid_action: str, command: str):
        self.action_calls.append((uuid_action, command))
        if command == "securedDetails":
            return {
                "value": self._secured_details_by_uuid_action.get(uuid_action, {}),
            }
        return {"value": None}


class _FakeConfigEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
camera_module = load_integration_module("custom_components.loxone_home_assistant.camera")
media_module = load_integration_module("custom_components.loxone_home_assistant.intercom_media")
LoxoneControl = models.LoxoneControl
LoxoneIntercomCameraEntity = camera_module.LoxoneIntercomCameraEntity


class CameraPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify direct MJPEG camera handling."""

    async def test_camera_disables_ha_stream_source(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={"videoInfo": {"streamUrl": "/dev/stream.mjpg"}},
        )
        bridge = _FakeBridge([control], {}, _FakeSession())
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertEqual(entity._attr_supported_features, camera_module.CameraEntityFeature(0))
        self.assertNotIn(
            camera_module.CameraEntityFeature.STREAM,
            entity._attr_supported_features,
        )
        self.assertIsNone(await entity.stream_source())

    async def test_setup_adds_only_intercom_cameras(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={"videoInfo": {"streamUrl": "/dev/stream.mjpg"}},
        )
        switch = LoxoneControl(
            uuid="switch-uuid",
            uuid_action="switch-action",
            name="Pompa",
            type="Switch",
            states={"active": "state-switch"},
        )
        empty_intercom = LoxoneControl(
            uuid="empty-intercom-uuid",
            uuid_action="empty-intercom-action",
            name="Pusty interkom",
            type="Intercom",
            states={},
            details={},
        )
        bridge = _FakeBridge([intercom, empty_intercom, switch], {}, _FakeSession())
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await camera_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual([entity.control.uuid_action for entity in entities], ["intercom-action"])

    async def test_setup_keeps_history_only_intercom_camera(self) -> None:
        history_only = LoxoneControl(
            uuid="door-controller-uuid",
            uuid_action="door-controller-action",
            name="Front Door",
            type="DoorController",
            states={"lastBellEvents": "state-events"},
            details={},
        )
        bridge = _FakeBridge([history_only], {"state-events": "20260314123045"}, _FakeSession())
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await camera_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual([entity.control.uuid_action for entity in entities], ["door-controller-action"])

    async def test_camera_resolves_miniserver_relative_urls(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={
                "videoInfo": {
                    "streamUrl": "/dev/video.mjpg",
                    "liveImageUrl": "/dev/live.jpg",
                }
            },
        )
        expected_stream_url = "https://mini.local/ABCD1234/dev/video.mjpg"
        expected_snapshot_url = "https://mini.local/ABCD1234/dev/live.jpg"
        session = _FakeSession(
            {
                (expected_snapshot_url, True): (200, b"image-bytes"),
            }
        )
        bridge = _FakeBridge(
            [control],
            {},
            session,
            ws_path_prefix="/ABCD1234",
        )
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertEqual(entity._stream_url(), expected_stream_url)
        self.assertNotIn("stream_url", entity.extra_state_attributes)
        self.assertNotIn("snapshot_url", entity.extra_state_attributes)

        image = await entity.async_camera_image()

        self.assertEqual(image, b"image-bytes")
        self.assertEqual(session.calls, [(expected_snapshot_url, True)])

    async def test_camera_resolves_live_urls_from_intercom_address(self) -> None:
        control = LoxoneControl(
            uuid="intercom-v2-uuid",
            uuid_action="intercom-v2-action",
            name="Brama",
            type="IntercomV2",
            states={
                "videoSettingsExtern": "state-video-settings",
                "address": "state-address",
            },
            details={},
        )
        expected_stream_url = "https://198.51.100.70/rest/stream.mjpg"
        expected_snapshot_url = "https://198.51.100.70/rest/live.jpg"
        session = _FakeSession(
            {
                (expected_snapshot_url, False): (200, b"external-live"),
            }
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-address": "198.51.100.70",
                "state-video-settings": {
                    "streamUrl": "/rest/stream.mjpg",
                    "alertImage": "/rest/live.jpg",
                },
            },
            session,
        )
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertEqual(entity._stream_url(), expected_stream_url)
        self.assertEqual(entity._snapshot_url(), expected_snapshot_url)
        self.assertEqual(await entity.async_camera_image(), b"external-live")

    async def test_camera_extracts_first_jpeg_frame_from_mjpeg_stream(self) -> None:
        control = LoxoneControl(
            uuid="intercom-v2-uuid",
            uuid_action="intercom-v2-action",
            name="Brama",
            type="IntercomV2",
            states={},
            details={"videoInfo": {"streamUrl": "/dev/stream.mjpg"}},
        )
        expected_stream_url = "https://mini.local/dev/stream.mjpg"
        frame = b"\xff\xd8\xff\xe0JPEG-FRAME\xff\xd9"
        mjpeg_payload = (
            b"--frameboundary\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame
            + b"\r\n--frameboundary--\r\n"
        )
        session = _FakeSession(
            {
                (
                    expected_stream_url,
                    True,
                ): (200, mjpeg_payload, "multipart/x-mixed-replace; boundary=frameboundary"),
            }
        )
        bridge = _FakeBridge([control], {}, session)
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertEqual(await entity.async_camera_image(), frame)

    async def test_camera_falls_back_to_stream_when_snapshot_endpoint_fails(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={
                "videoInfo": {
                    "streamUrl": "/dev/stream.mjpg",
                    "liveImageUrl": "/dev/live.jpg",
                }
            },
        )
        expected_snapshot_url = "https://mini.local/dev/live.jpg"
        expected_stream_url = "https://mini.local/dev/stream.mjpg"
        frame = b"\xff\xd8\xff\xe0JPEG-FRAME\xff\xd9"
        mjpeg_payload = (
            b"--frameboundary\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame
            + b"\r\n--frameboundary--\r\n"
        )
        session = _FakeSession(
            {
                (expected_snapshot_url, True): (404, b"", "image/jpeg"),
                (
                    expected_stream_url,
                    True,
                ): (200, mjpeg_payload, "multipart/x-mixed-replace; boundary=frameboundary"),
            }
        )
        bridge = _FakeBridge([control], {}, session)
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertEqual(await entity.async_camera_image(), frame)
        self.assertEqual(
            session.calls,
            [
                (expected_snapshot_url, True),
                (expected_stream_url, True),
            ],
        )

    async def test_camera_uses_selected_history_timestamp_for_preview(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="IntercomV2",
            states={"lastBellEvents": "state-events"},
            details={},
        )
        expected_history_url = "https://mini.local/camimage/intercom-action/20260314123045"
        session = _FakeSession(
            {
                (expected_history_url, True): (200, b"history-image"),
            }
        )
        bridge = _FakeBridge(
            [control],
            {"state-events": "20260314123045|20260314100000"},
            session,
        )
        media_module.set_intercom_selected_history_timestamp(
            bridge,
            "intercom-action",
            "20260314123045",
        )
        entity = LoxoneIntercomCameraEntity(bridge, control)

        image = await entity.async_camera_image()

        self.assertEqual(image, b"history-image")
        self.assertEqual(session.calls, [(expected_history_url, True)])
        self.assertNotIn("selected_history_image_url", entity.extra_state_attributes)

    async def test_history_selection_returns_still_response_from_live_endpoint(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="IntercomV2",
            states={"lastBellEvents": "state-events"},
            details={},
        )
        expected_history_url = "https://mini.local/camimage/intercom-action/20260314123045"
        session = _FakeSession(
            {
                (expected_history_url, True): (200, b"history-image"),
            }
        )
        bridge = _FakeBridge(
            [control],
            {"state-events": "20260314123045"},
            session,
        )
        media_module.set_intercom_selected_history_timestamp(
            bridge,
            "intercom-action",
            "20260314123045",
        )
        entity = LoxoneIntercomCameraEntity(bridge, control)

        response = await entity.handle_async_mjpeg_stream(request=None)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"history-image")
        self.assertEqual(response.content_type, "image/jpeg")

    async def test_native_archive_selection_uses_private_photo_client(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Doorbell",
            type="IntercomV2",
            details={"deviceUuid": "12345678-1234-1234-1234567890123456"},
        )
        session = _FakeSession()
        bridge = _FakeBridge([control], {}, session)
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []
        await camera_module.async_setup_entry(hass, entry, entities.extend)
        self.assertEqual(len(entities), 1)

        selection = "gen2:1773451234"
        media_module.set_intercom_selected_history_timestamp(bridge, control.uuid_action, selection)
        with patch.object(
            camera_module, "async_gen2_archive_image",
            AsyncMock(side_effect=(b"\xff\xd8archive-photo\xff\xd9", None)),
        ) as archive_image:
            response = await entities[0].handle_async_mjpeg_stream(request=None)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body, b"\xff\xd8archive-photo\xff\xd9")
            self.assertIsNone(await entities[0].async_camera_image())
            archive_image.assert_awaited_with(bridge, control, selection)
        self.assertEqual(session.calls, [])

    async def test_camera_uses_secured_details_for_media_urls(self) -> None:
        control = LoxoneControl(
            uuid="door-controller-uuid",
            uuid_action="door-controller-action",
            name="Intercom Front",
            type="DoorController",
            states={"lastBellEvents": "state-events"},
            details={"securedDetails": True},
        )
        expected_snapshot_url = "https://mini.local/dev/secured-alert.jpg"
        bridge = _FakeBridge(
            [control],
            {"state-events": "20260314123045"},
            _FakeSession({(expected_snapshot_url, True): (200, b"secured-alert")}),
            secured_details_by_uuid_action={
                "door-controller-action": {
                    "videoInfo": {
                        "streamUrl": "/dev/secured-stream.mjpg",
                        "alertImage": "/dev/secured-alert.jpg",
                    }
                }
            },
        )
        entity = LoxoneIntercomCameraEntity(bridge, control)

        image = await entity.async_camera_image()

        self.assertEqual(image, b"secured-alert")
        self.assertEqual(
            bridge.action_calls,
            [("door-controller-action", "securedDetails")],
        )
        self.assertNotIn("snapshot_url", entity.extra_state_attributes)

    async def test_camera_retries_secured_details_after_connection_failure(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="Intercom",
            details={"securedDetails": True},
        )
        bridge = _FakeBridge([control], {}, _FakeSession())
        responses = iter((None, {"value": {"videoInfo": {"alertImage": "/dev/alert.jpg"}}}))

        async def send_action(_uuid_action, _command):
            response = next(responses)
            if response is None:
                raise TimeoutError
            return response

        bridge.async_send_action = send_action
        self.assertTrue(camera_module._is_intercom_camera_control(control))
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertIsNone(await entity.async_camera_image())
        self.assertEqual(await entity.async_camera_image(), b"default-image")

    async def test_camera_rejects_html_and_keeps_history_out_of_live_cache(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="IntercomV2",
            details={"videoInfo": {"alertImage": "/dev/live.jpg"}},
        )
        live_url = "https://mini.local/dev/live.jpg"
        history_path = "https://mini.local/camimage/intercom-action/"
        session = _FakeSession(
            {
                (live_url, True): (200, b"live-image"),
                (history_path + "20260314123045", True): (200, b"history-image"),
                (history_path + "20260314123100", True): (200, b"<html>Login</html>", "text/html"),
            }
        )
        bridge = _FakeBridge([control], {}, session)
        entity = LoxoneIntercomCameraEntity(bridge, control)
        self.assertEqual(await entity.async_camera_image(), b"live-image")

        media_module.set_intercom_selected_history_timestamp(bridge, control.uuid_action, "20260314123045")
        self.assertEqual(await entity.async_camera_image(), b"history-image")

        media_module.set_intercom_selected_history_timestamp(bridge, control.uuid_action, "20260314123100")
        self.assertIsNone(await entity.async_camera_image())

        media_module.set_intercom_selected_history_timestamp(bridge, control.uuid_action, None)
        session.responses[(live_url, True)] = (503, b"")
        self.assertEqual(await entity.async_camera_image(), b"live-image")
        response = await entity.handle_async_mjpeg_stream(request=None)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"live-image")

    async def test_native_frame_fallback_does_not_replace_a_selected_history_photo(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="IntercomV2",
        )
        historical_url = "https://mini.local/camimage/intercom-action/20260314123045"
        session = _FakeSession({(historical_url, True): (404, b"")})
        bridge = _FakeBridge([control], {}, session)
        captured = []

        async def snapshot():
            captured.append(True)
            return b"live-frame"

        entity = LoxoneIntercomCameraEntity(bridge, control, native_snapshot=snapshot)
        self.assertEqual(await entity.async_camera_image(), b"live-frame")

        media_module.set_intercom_selected_history_timestamp(
            bridge, control.uuid_action, "20260314123045"
        )
        self.assertIsNone(await entity.async_camera_image())
        self.assertEqual(captured, [True])

    async def test_live_stream_uses_dedicated_intercom_credentials(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={"videoInfo": {"streamUrl": "http://192.0.2.10/mjpg/video.mjpg"}},
        )
        session = _FakeSession(
            {
                ("http://192.0.2.10/mjpg/video.mjpg", True): (
                    200,
                    b"\xff\xd8\xff\xe0frame\xff\xd9",
                    "multipart/x-mixed-replace",
                ),
            }
        )
        bridge = _FakeBridge([control], {}, session)
        bridge.intercom_username = "camuser"
        bridge.intercom_password = "campass"
        entity = LoxoneIntercomCameraEntity(bridge, control)

        await entity.async_camera_image()

        self.assertEqual(session.auth_logins, ["camuser"])

    async def test_live_stream_never_sends_miniserver_credentials_to_camera(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={"videoInfo": {"streamUrl": "http://192.0.2.10/mjpg/video.mjpg"}},
        )
        session = _FakeSession(
            {
                ("http://192.0.2.10/mjpg/video.mjpg", False): (
                    200,
                    b"\xff\xd8\xff\xe0frame\xff\xd9",
                    "multipart/x-mixed-replace",
                ),
            }
        )
        bridge = _FakeBridge([control], {}, session)
        entity = LoxoneIntercomCameraEntity(bridge, control)

        await entity.async_camera_image()

        self.assertEqual(session.auth_logins, [None])

    async def test_snapshot_uses_dedicated_credentials_on_alternate_miniserver_port(self) -> None:
        url = "https://mini.local:8443/snapshot.jpg"
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={"videoInfo": {"liveImageUrl": url}},
        )
        session = _FakeSession({(url, True): (200, b"snapshot"), (url, False): (401, b"")})
        bridge = _FakeBridge([control], {}, session)
        bridge.intercom_username = "camuser"
        bridge.intercom_password = "campass"
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertEqual(await entity.async_camera_image(), b"snapshot")
        self.assertEqual(session.auth_logins, ["camuser"])

    def test_alternate_miniserver_port_requires_both_camera_credentials(self) -> None:
        bridge = _FakeBridge([], {}, _FakeSession())
        url = "https://mini.local:8443/snapshot.jpg"

        for username, password in ((None, None), ("camuser", None), (None, "campass")):
            with self.subTest(username=username, password=password):
                bridge.intercom_username = username
                bridge.intercom_password = password
                self.assertIsNone(media_module.intercom_request_auth(bridge, url))

    async def test_proxy_history_urls_use_miniserver_credentials(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={},
        )
        bridge = _FakeBridge([control], {}, _FakeSession())
        bridge.intercom_username = "camuser"
        bridge.intercom_password = "campass"
        auth = media_module.intercom_request_auth(
            bridge, "https://mini.local/proxy/device-uuid/images/1.jpg"
        )

        self.assertEqual(getattr(auth, "login", None), "user")
        self.assertEqual(getattr(auth, "password", None), "pass")
        for url in (
            "https://camera.local/proxy/device-uuid/images/1.jpg",
            "https://mini.local:8443/proxy/device-uuid/images/1.jpg",
            "http://mini.local:443/proxy/device-uuid/images/1.jpg",
        ):
            with self.subTest(url=url):
                self.assertIsNone(media_module.intercom_request_auth(bridge, url))

    async def test_gen1_proxy_snapshot_uses_no_auth_and_does_not_follow_redirects(self) -> None:
        url = "http://mini.local:7778/proxy/device-uuid/images/1.jpg"
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={"videoInfo": {"liveImageUrl": url}},
        )
        session = _FakeSession({(url, False): (302, b"redirect")})
        bridge = _FakeBridge([control], {}, session, port=7778, use_tls=False)
        bridge.intercom_username = "camuser"
        bridge.intercom_password = "campass"
        entity = LoxoneIntercomCameraEntity(bridge, control)

        self.assertIsNone(await entity.async_camera_image())
        self.assertEqual(session.calls, [(url, False)])
        self.assertEqual(session.allow_redirects, [False])

    async def test_gen1_direct_snapshot_uses_only_dedicated_credentials(self) -> None:
        camera_url = "http://camera.local/snapshot.jpg"
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="Intercom",
            states={},
            details={"videoInfo": {"liveImageUrl": "http://user:pass@camera.local/snapshot.jpg"}},
        )
        session = _FakeSession()
        bridge = _FakeBridge([control], {}, session, port=7778, use_tls=False)
        bridge.intercom_username = "camuser"
        entity = LoxoneIntercomCameraEntity(bridge, control)

        await entity.async_camera_image()
        bridge.intercom_password = "campass"
        await entity.async_camera_image()

        self.assertEqual(session.calls, [(camera_url, False), (camera_url, True)])
        self.assertEqual(session.auth_logins, [None, "camuser"])
        self.assertEqual(session.allow_redirects, [False, False])


if __name__ == "__main__":
    unittest.main()
