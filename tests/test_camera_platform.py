"""Tests for the Loxone Intercom camera implementation."""

from __future__ import annotations

import sys
import types
import unittest

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

    camera.Camera = Camera
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

    def get(self, url: str, auth=None):
        has_auth = auth is not None
        self.calls.append((url, has_auth))
        self.auth_logins.append(getattr(auth, "login", None) if has_auth else None)
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

        self.assertEqual(entity._attr_supported_features, 0)
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
        bridge = _FakeBridge([intercom, switch], {}, _FakeSession())
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await camera_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual([entity.control.uuid_action for entity in entities], ["intercom-action"])

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

        self.assertEqual(entity.extra_state_attributes["stream_url"], expected_stream_url)
        self.assertEqual(entity.extra_state_attributes["snapshot_url"], expected_snapshot_url)

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
                (expected_snapshot_url, True): (200, b"external-live"),
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

        self.assertEqual(entity.extra_state_attributes["stream_url"], expected_stream_url)
        self.assertEqual(entity.extra_state_attributes["snapshot_url"], expected_snapshot_url)
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
        self.assertEqual(
            entity.extra_state_attributes["selected_history_image_url"],
            expected_history_url,
        )

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

    async def test_camera_uses_secured_details_for_media_urls(self) -> None:
        control = LoxoneControl(
            uuid="door-controller-uuid",
            uuid_action="door-controller-action",
            name="Intercom Front",
            type="DoorController",
            states={"lastBellEvents": "state-events"},
            details={},
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
        self.assertEqual(
            entity.extra_state_attributes["snapshot_url"],
            expected_snapshot_url,
        )

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


if __name__ == "__main__":
    unittest.main()
