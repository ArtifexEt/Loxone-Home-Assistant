"""Tests for Loxone Intercom history sensors."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
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
        "climate": "climate",
        "cover": "cover",
        "light": "light",
        "number": "number",
        "select": "select",
        "sensor": "sensor",
        "switch": "switch",
        "text": "text",
    }.items():
        module = types.ModuleType(f"homeassistant.components.{module_name}")
        module.DOMAIN = domain
        sys.modules[f"homeassistant.components.{module_name}"] = module

    sensor = sys.modules["homeassistant.components.sensor"]

    class SensorEntity:
        pass

    class SensorStateClass:
        MEASUREMENT = "measurement"
        TOTAL_INCREASING = "total_increasing"

    class SensorDeviceClass:
        TIMESTAMP = "timestamp"
        ENERGY = "energy"
        POWER = "power"

    sensor.SensorEntity = SensorEntity
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorDeviceClass = SensorDeviceClass

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
    status = 200

    def __init__(self, payload) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type=None):
        del content_type
        return self._payload


class _FakeSession:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}
        self.requests = []

    def get(self, url: str, auth=None, *, allow_redirects=True):
        self.requests.append((url, auth, allow_redirects))
        return _FakeResponse(self.responses[url])


class _FakeBridge:
    serial = "1234567890"
    available = True
    username = "user"
    password = "pass"
    intercom_username = None
    intercom_password = None
    host = "mini.local"
    port = 443
    use_tls = True
    _ws_path_prefix = ""

    def __init__(self, controls, values, session: _FakeSession | None = None) -> None:
        self.controls = controls
        self.controls_by_action = {control.uuid_action: control for control in controls}
        self._values = values
        self._session = session

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

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
        return f"https://{self.host}{path}"


class _FakeConfigEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.options = {}


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
sensor_module = load_integration_module("custom_components.loxone_home_assistant.sensor")
media_module = load_integration_module("custom_components.loxone_home_assistant.intercom_media")
LoxoneControl = models.LoxoneControl


class IntercomSensorTests(unittest.IsolatedAsyncioTestCase):
    """Verify `lastBellEvents` based history handling."""

    async def test_device_status_does_not_expose_metadata_or_preset_text(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Intercom",
            type="IntercomV2",
            states={
                "address": "state-address",
                "answers": "state-answers",
                "deviceState": "state-device",
            },
        )
        values = {
            "state-address": "192.0.2.1",
            "state-answers": ["Preset reply"],
            "state-device": 1.0,
        }
        bridge = _FakeBridge([control], values)
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []
        await sensor_module.async_setup_entry(hass, entry, entities.extend)
        self.assertEqual(len(entities), 1)
        status = entities[0]
        self.assertEqual(status.relevant_state_uuids(), ("state-device",))
        self.assertEqual(status.native_value, "online")
        self.assertNotIn("192.0.2.1", str(status.extra_state_attributes))
        self.assertNotIn("Preset reply", str(status.extra_state_attributes))
        for value, expected in (
            (0, "unknown"),
            (2, "rebooting"),
            ("3", "initializing"),
            (2.5, None),
            (4, None),
            (None, None),
        ):
            with self.subTest(value=value):
                values["state-device"] = value
                self.assertEqual(status.native_value, expected)

    async def test_setup_adds_gen2_archive_sensor_without_legacy_history_metadata(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Sample Intercom",
            type="IntercomV2",
            states={"bell": "state-bell"},
            details={"deviceUuid": "00000000-0000-4000-8000-000000000001"},
        )
        bridge = _FakeBridge([control], {"state-bell": False})
        entry = _FakeConfigEntry("entry-1")
        entities = []
        await sensor_module.async_setup_entry(
            _FakeHass(entry.entry_id, bridge, const.DOMAIN), entry, entities.extend
        )
        history_sensor, = entities
        self.assertIsInstance(history_sensor, sensor_module.LoxoneIntercomHistorySensor)
        self.assertEqual(history_sensor.relevant_state_uuids(), ["state-bell"])

        timestamp = 1_773_491_445
        archive = AsyncMock(return_value=(types.SimpleNamespace(
            timestamp=timestamp, selection_value=f"gen2:{timestamp}",
        ),))
        with patch.dict(
            sensor_module.async_intercom_history_entries.__globals__,
            {"async_gen2_archive_entries": archive},
        ):
            await history_sensor.async_update()

        archive.assert_awaited_once_with(bridge, control)
        self.assertEqual(
            history_sensor.native_value, datetime.fromtimestamp(timestamp, timezone.utc)
        )
        attrs = history_sensor.extra_state_attributes
        self.assertEqual(attrs["event_count"], 1)
        self.assertEqual(attrs["history_timestamps"], [str(timestamp)])
        self.assertNotIn("gen2:", repr(attrs))

    async def test_setup_adds_intercom_history_sensor_from_last_bell_events(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={"lastBellEvents": "state-events"},
        )
        bridge = _FakeBridge(
            [control],
            {"state-events": "20260313090000|20260314112000"},
            session=_FakeSession(),
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        history_entity = entities[0]

        await history_entity.async_update()
        attrs = history_entity.extra_state_attributes

        self.assertEqual(attrs["event_count"], 2)
        self.assertNotIn("latest_event_image_url", attrs)
        self.assertNotIn("recent_image_urls", attrs)
        self.assertEqual(
            await self._history_image_urls(history_entity),
            [
                "https://mini.local/camimage/intercom-action/20260314112000",
                "https://mini.local/camimage/intercom-action/20260313090000",
            ],
        )
        self.assertEqual(
            attrs["history_timestamps"],
            ["20260314112000", "20260313090000"],
        )
        self.assertIsNotNone(history_entity.native_value)

    async def test_history_sensor_reads_last_bell_events_from_video_settings_state(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "videoSettingsExtern": "state-video-settings",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-video-settings": {
                    "lastBellEvents": "20260314100000|20260314123045",
                },
            },
            session=_FakeSession(),
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        history_entity = entities[0]
        await history_entity.async_update()

        attrs = history_entity.extra_state_attributes
        self.assertEqual(attrs["event_count"], 2)
        self.assertEqual(
            (await self._history_image_urls(history_entity))[0],
            "https://mini.local/camimage/intercom-action/20260314123045",
        )

    async def test_history_sensor_falls_back_to_history_date_when_list_is_missing(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "historyDate": "state-history-date",
            },
        )
        bridge = _FakeBridge(
            [control],
            {"state-history-date": "20260314123045"},
            session=_FakeSession(),
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        history_entity = entities[0]
        await history_entity.async_update()

        attrs = history_entity.extra_state_attributes
        self.assertEqual(attrs["event_count"], 0)
        self.assertIsNotNone(history_entity.native_value)

    async def test_history_sensor_uses_event_history_url_payload(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={},
            details={"videoInfo": {"eventHistoryUrl": "/history/intercom.json"}},
        )
        bridge = _FakeBridge(
            [control],
            {},
            session=_FakeSession(
                {
                    "https://mini.local/history/intercom.json": {
                        "events": [
                            {
                                "timestamp": "20260313090000",
                                "imageUrl": "/camimage/intercom-action/20260313090000",
                            },
                            {
                                "timestamp": "20260314123045",
                                "imageUrl": "/camimage/intercom-action/20260314123045",
                            },
                        ]
                    }
                }
            ),
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        history_entity = entities[0]
        await history_entity.async_update()

        attrs = history_entity.extra_state_attributes
        self.assertEqual(attrs["event_count"], 2)
        self.assertEqual(
            (await self._history_image_urls(history_entity))[0],
            "https://mini.local/camimage/intercom-action/20260314123045",
        )
        self.assertEqual(
            attrs["history_timestamps"],
            ["20260314123045", "20260313090000"],
        )

    async def test_history_sensor_accepts_image_path_payload_keys(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Gate",
            type="IntercomV2",
            states={},
            details={
                "videoInfo": {
                    "eventHistoryUrl": "/history/intercom.json",
                    "host": "192.0.2.50",
                }
            },
        )
        bridge = _FakeBridge(
            [control],
            {},
            session=_FakeSession(
                {
                    "https://192.0.2.50/history/intercom.json": {
                        "events": [
                            {
                                "timestamp": "20260314123045",
                                "imagePath": "/jpg/history/20260314123045.jpg",
                            },
                            {
                                "timestamp": "20260313090000",
                                "thumbPath": "jpg/history/thumb-20260313090000.jpg",
                            },
                        ]
                    }
                }
            ),
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        history_entity = entities[0]
        await history_entity.async_update()

        attrs = history_entity.extra_state_attributes
        self.assertEqual(attrs["event_count"], 2)
        self.assertEqual(
            (await self._history_image_urls(history_entity))[0],
            "https://192.0.2.50/jpg/history/20260314123045.jpg",
        )
        self.assertEqual(
            await self._history_image_urls(history_entity),
            [
                "https://192.0.2.50/jpg/history/20260314123045.jpg",
                "https://192.0.2.50/jpg/history/thumb-20260313090000.jpg",
            ],
        )

    async def test_history_sensor_accepts_path_payload_key(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Gate",
            type="IntercomV2",
            states={},
            details={
                "videoInfo": {
                    "eventHistoryUrl": "/history/intercom.json",
                    "host": "192.0.2.50",
                }
            },
        )
        bridge = _FakeBridge(
            [control],
            {},
            session=_FakeSession(
                {
                    "https://192.0.2.50/history/intercom.json": {
                        "events": [
                            {
                                "timestamp": "20260312083000",
                                "path": "jpg/history/20260312083000.jpg",
                            }
                        ]
                    }
                }
            ),
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        history_entity = entities[0]
        await history_entity.async_update()

        attrs = history_entity.extra_state_attributes
        self.assertEqual(attrs["event_count"], 1)
        self.assertEqual(
            (await self._history_image_urls(history_entity))[0],
            "https://192.0.2.50/jpg/history/20260312083000.jpg",
        )
        self.assertEqual(attrs["history_timestamps"], ["20260312083000"])

    async def test_history_sensor_keeps_proxy_prefix_for_root_relative_images(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Gate",
            type="IntercomV2",
            states={},
            details={
                "videoInfo": {
                    "eventHistoryUrl": "/proxy/device-uuid/history/intercom.json",
                    "host": "192.0.2.50",
                }
            },
        )
        bridge = _FakeBridge(
            [control],
            {},
            session=_FakeSession(
                {
                    "https://mini.local/proxy/device-uuid/history/intercom.json": {
                        "events": [
                            {
                                "timestamp": "20260314123045",
                                "thumbPath": "/images/history/20260314123045_thumb.jpg",
                            }
                        ]
                    }
                }
            ),
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        history_entity = entities[0]
        await history_entity.async_update()

        self.assertEqual(
            await self._history_image_urls(history_entity),
            ["https://mini.local/proxy/device-uuid/images/history/20260314123045_thumb.jpg"],
        )

    async def test_history_uses_proxied_images_and_ignores_unrelated_urls(self) -> None:
        origin = "https://mini.local"
        archive = f"{origin}/proxy/intercom/history.json"
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Doorbell",
            type="IntercomV2",
            details={"eventHistoryUrl": archive},
        )
        session = _FakeSession({
            archive: {"events": [
                {"timestamp": "20260314123100", "imagePath": "https://other.example/photo.jpg"},
                {"timestamp": "20260314123045", "imagePath": "/images/photo.jpg?signature=example"},
            ]}
        })
        bridge = _FakeBridge([control], {}, session)
        entity = sensor_module.LoxoneIntercomHistorySensor(bridge, control, None)

        await entity.async_update()

        self.assertEqual(entity.extra_state_attributes["event_count"], 1)
        self.assertNotIn("example", repr(entity.extra_state_attributes))
        self.assertEqual(
            await self._history_image_urls(entity),
            [f"{origin}/proxy/intercom/images/photo.jpg?signature=example"],
        )
        self.assertTrue(all(not allow_redirects for _, _, allow_redirects in session.requests))

    async def test_history_reads_declared_secured_details(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Doorbell",
            type="IntercomV2",
            details={"securedDetails": True},
        )
        bridge = _FakeBridge([control], {}, _FakeSession())
        calls = []

        async def get_details(uuid, command):
            calls.append((uuid, command))
            return {"value": '{"videoInfo":{"lastBellEvents":"20260314123045"}}'}

        bridge.async_send_action = get_details
        entity = sensor_module.LoxoneIntercomHistorySensor(bridge, control, None)
        self.assertTrue(media_module.has_intercom_history_detail(control))

        await entity.async_update()
        await entity.async_update()

        self.assertEqual(entity.extra_state_attributes["event_count"], 1)
        self.assertEqual(entity.extra_state_attributes["history_timestamps"], ["20260314123045"])
        self.assertEqual(calls, [(control.uuid_action, "securedDetails")])

    def test_secured_video_credentials_are_bound_to_the_declared_camera_origin(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Doorbell",
            type="Intercom",
        )
        bridge = _FakeBridge([control], {})
        secured_details = {
            "videoInfo": {
                "user": "camera-user",
                "pass": "camera-password",
                "streamUrl": "http://camera.local:8080/live.mjpg",
            }
        }

        def auth_for(url):
            return media_module.intercom_request_auth(
                bridge, url, control=control, secured_details=secured_details
            )

        auth = auth_for("http://camera.local:8080/preview.jpg")
        self.assertEqual(auth.login, "camera-user")
        self.assertEqual(auth.password, "camera-password")
        self.assertIsNone(auth_for("https://camera.local:8080/preview.jpg"))
        self.assertIsNone(auth_for("http://camera.local/preview.jpg"))
        self.assertIsNone(auth_for("http://different.example:8080/preview.jpg"))
        self.assertEqual(auth_for("https://mini.local/preview.jpg").login, bridge.username)

        bridge.intercom_username = "configured-user"
        bridge.intercom_password = "configured-password"
        auth = auth_for("http://camera.local:8080/preview.jpg")
        self.assertEqual(auth.login, "configured-user")

    async def _history_image_urls(self, entity):
        entries = await media_module.async_intercom_history_entries(
            entity.bridge, entity.control, state_value_getter=entity.state_value
        )
        return [entry.image_url for entry in entries]

    async def test_intercom_system_schema_webpage_is_disabled_by_default(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="IntercomV2",
            states={},
        )
        webpage = LoxoneControl(
            uuid="webpage-uuid",
            uuid_action="webpage-action",
            name="Schemat systemu Dzwonek",
            type="Webpage",
            states={},
            details={"url": "/apps/intercom/schema"},
            parent_uuid_action="intercom-action",
            path=("Dzwonek", "Schemat systemu Dzwonek"),
        )
        bridge = _FakeBridge([intercom, webpage], {}, session=None)
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        webpage_entities = [
            entity
            for entity in entities
            if isinstance(entity, sensor_module.LoxoneWebpageSensor)
        ]
        self.assertEqual(len(webpage_entities), 1)
        self.assertFalse(webpage_entities[0]._attr_entity_registry_enabled_default)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
