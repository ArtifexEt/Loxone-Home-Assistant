"""Tests for Loxone Intercom camera preview support."""

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
        pass

    camera.Camera = Camera

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
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def read(self) -> bytes:
        return self._payload


class _FakeSession:
    def __init__(self, responses: dict[tuple[str, bool], tuple[int, bytes]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, bool]] = []

    def get(self, url: str, auth=None):
        has_auth = auth is not None
        self.calls.append((url, has_auth))
        status, payload = self.responses.get((url, has_auth), (200, b"default-image"))
        return _FakeResponse(status, payload)


class _FakeBridge:
    serial = "1234567890"
    available = True
    username = "user"
    password = "pass"

    def __init__(
        self,
        controls,
        values,
        session: _FakeSession,
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
LoxoneControl = models.LoxoneControl
LoxoneIntercomCameraEntity = camera_module.LoxoneIntercomCameraEntity


class CameraPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify Intercom camera preview mapping."""

    async def test_setup_adds_camera_entities_for_intercom_controls(self) -> None:
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
        session = _FakeSession()
        bridge = _FakeBridge([intercom, switch], {}, session)
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await camera_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "intercom-action")

    async def test_stream_and_snapshot_urls_resolve_with_prefix(self) -> None:
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
                },
                "lastBellEvents": "/dev/events.json",
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

        self.assertEqual(await entity.stream_source(), expected_stream_url)
        self.assertEqual(entity.extra_state_attributes["snapshot_url"], expected_snapshot_url)
        self.assertEqual(
            entity.extra_state_attributes["last_bell_events_url"],
            "https://mini.local/ABCD1234/dev/events.json",
        )

        image = await entity.async_camera_image()

        self.assertEqual(image, b"image-bytes")
        self.assertEqual(session.calls, [(expected_snapshot_url, True)])

    async def test_intercom_v2_uses_alert_image_state_for_preview(self) -> None:
        control = LoxoneControl(
            uuid="intercom-v2-uuid",
            uuid_action="intercom-v2-action",
            name="Brama",
            type="IntercomV2",
            states={"alertImage": "state-alert-image"},
            details={},
        )
        expected_image_url = "https://mini.local/dev/alert.jpg"
        session = _FakeSession(
            {
                (expected_image_url, True): (200, b"alert-image"),
            }
        )
        bridge = _FakeBridge(
            [control],
            {"state-alert-image": "/dev/alert.jpg"},
            session,
        )
        entity = LoxoneIntercomCameraEntity(bridge, control)

        image = await entity.async_camera_image()

        self.assertEqual(image, b"alert-image")
        self.assertEqual(session.calls, [(expected_image_url, True)])

    async def test_camera_setup_accepts_variant_type_and_case_insensitive_details(self) -> None:
        control = LoxoneControl(
            uuid="door-station-uuid",
            uuid_action="door-station-action",
            name="Bramofon",
            type="DoorStationV2",
            states={},
            details={
                "VideoInfo": {
                    "StreamURL": "/dev/variant-stream.mjpg",
                    "LiveImageURL": "/dev/variant-live.jpg",
                },
            },
        )
        session = _FakeSession()
        bridge = _FakeBridge([control], {}, session)
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await camera_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(
            await entity.stream_source(),
            "https://mini.local/dev/variant-stream.mjpg",
        )
        self.assertEqual(
            entity.extra_state_attributes["snapshot_url"],
            "https://mini.local/dev/variant-live.jpg",
        )


if __name__ == "__main__":
    unittest.main()
