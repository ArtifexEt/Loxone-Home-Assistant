"""Tests for Intercom-specific sensor support."""

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
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self, content_type=None):
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


class _FakeSession:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, auth=None):
        del auth
        self.calls.append(url)
        payload = self.responses.get(url, [])
        return _FakeResponse(200, payload)


class _FakeBridge:
    serial = "1234567890"
    available = True
    username = "user"
    password = "pass"
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
LoxoneControl = models.LoxoneControl


class IntercomSensorTests(unittest.IsolatedAsyncioTestCase):
    """Verify intercom history and schema mapping."""

    async def test_setup_adds_intercom_history_sensor_with_latest_image(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={"lastBellEvents": "state-events"},
        )
        events_url = "https://mini.local/dev/events.json"
        session = _FakeSession(
            {
                events_url: [
                    {
                        "timestamp": "2026-03-14T11:20:00Z",
                        "imageUrl": "/dev/history/latest.jpg",
                    },
                    {
                        "timestamp": "2026-03-13T09:00:00Z",
                        "imageUrl": "/dev/history/old.jpg",
                    },
                ]
            }
        )
        bridge = _FakeBridge([control], {"state-events": "/dev/events.json"}, session=session)
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
        self.assertIsInstance(history_entity, sensor_module.LoxoneIntercomHistorySensor)

        await history_entity.async_update()
        attrs = history_entity.extra_state_attributes
        self.assertEqual(attrs["history_events_url"], events_url)
        self.assertEqual(
            attrs["latest_event_image_url"],
            "https://mini.local/dev/history/latest.jpg",
        )
        self.assertEqual(
            attrs["recent_image_urls"],
            [
                "https://mini.local/dev/history/latest.jpg",
                "https://mini.local/dev/history/old.jpg",
            ],
        )
        self.assertIsNotNone(history_entity.native_value)

    async def test_history_sensor_accepts_answers_state_payload_without_fetch(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "answers": "state-answers",
                "address": "state-address",
            },
        )
        payload = [
            {
                "timestamp": "2026-03-14T12:30:00Z",
                "imageUrl": "/api/intercom/latest.jpg",
            },
            {
                "timestamp": "2026-03-14T10:00:00Z",
                "imageUrl": "/api/intercom/older.jpg",
            },
        ]
        session = _FakeSession({})
        bridge = _FakeBridge(
            [control],
            {
                "state-answers": payload,
                "state-address": "198.51.100.70",
            },
            session=session,
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
        self.assertEqual(
            attrs["latest_event_image_url"],
            "https://198.51.100.70/api/intercom/latest.jpg",
        )
        self.assertEqual(
            attrs["recent_image_urls"],
            [
                "https://198.51.100.70/api/intercom/latest.jpg",
                "https://198.51.100.70/api/intercom/older.jpg",
            ],
        )
        self.assertNotIn("history_events_url", attrs)
        self.assertEqual(session.calls, [])

    async def test_history_sensor_resolves_event_url_from_video_settings_state(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "videoSettingsExtern": "state-video-settings",
                "address": "state-address",
            },
        )
        events_url = "https://198.51.100.70/rest/events.json"
        session = _FakeSession(
            {
                events_url: [
                    {
                        "timestamp": "2026-03-14T12:30:00Z",
                        "imageUrl": "/rest/latest.jpg",
                    }
                ]
            }
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-address": "198.51.100.70",
                "state-video-settings": {
                    "streamUrl": "/rest/stream.mjpg",
                    "alertImage": "/rest/live.jpg",
                    "lastBellEvents": "/rest/events.json",
                },
            },
            session=session,
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
        self.assertEqual(attrs["history_events_url"], events_url)
        self.assertEqual(
            attrs["latest_event_image_url"],
            "https://198.51.100.70/rest/latest.jpg",
        )
        self.assertEqual(session.calls, [events_url])

    async def test_history_sensor_resolves_event_url_from_video_settings_list_payload(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "videoSettingsExtern": "state-video-settings",
                "address": "state-address",
            },
        )
        events_url = "https://198.51.100.70/rest/events.json"
        session = _FakeSession(
            {
                events_url: [
                    {
                        "timestamp": "2026-03-14T12:30:00Z",
                        "imageUrl": "/rest/latest.jpg",
                    }
                ]
            }
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-address": "198.51.100.70",
                "state-video-settings": [
                    {"name": "streamUrl", "value": "/rest/stream.mjpg"},
                    {"name": "alertImage", "value": "/rest/live.jpg"},
                    {"name": "lastBellEvents", "value": "/rest/events.json"},
                ],
            },
            session=session,
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
        self.assertEqual(attrs["history_events_url"], events_url)
        self.assertEqual(
            attrs["latest_event_image_url"],
            "https://198.51.100.70/rest/latest.jpg",
        )
        self.assertEqual(session.calls, [events_url])

    async def test_history_sensor_falls_back_to_history_date_state_when_events_missing(self) -> None:
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
            {
                "state-history-date": "20260314123045",
            },
            session=_FakeSession({}),
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
        self.assertEqual(
            history_entity.native_value.isoformat(),
            "2026-03-14T12:30:45+00:00",
        )

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
