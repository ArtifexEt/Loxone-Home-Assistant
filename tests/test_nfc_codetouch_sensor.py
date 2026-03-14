"""Tests for NfcCodeTouch and universal event sensor support."""

from __future__ import annotations

from datetime import datetime, timezone
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
        ILLUMINANCE = "illuminance"
        SOUND_PRESSURE = "sound_pressure"

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


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls, values):
        self.controls = controls
        self.controls_by_action = {control.uuid_action: control for control in controls}
        self._values = values

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))


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


class NfcCodeTouchSensorTests(unittest.IsolatedAsyncioTestCase):
    """Verify NfcCodeTouch mapping and universal events behavior."""

    async def test_setup_adds_nfc_codetouch_states_as_enabled_sensors(self) -> None:
        control = LoxoneControl(
            uuid="nfc-uuid",
            uuid_action="nfc-action",
            name="Front Nfc Touch",
            type="NfcCodeTouch",
            states={
                "CodeDate": "state-code-date",
                "DeviceState": "state-device-state",
                "Events": "state-events",
                "HistoryDate": "state-history-date",
                "JLocked": "state-locked",
                "KeyPadAuthType": "state-auth-type",
                "Lastcode": "state-last-code",
                "Lastid": "state-last-id",
                "Lasttag": "state-last-tag",
                "Lastuser": "state-last-user",
                "NfcLearnResult": "state-learn-result",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-code-date": "20260314112200",
                "state-device-state": "ready",
                "state-events": ["granted", "denied"],
                "state-history-date": "2026-03-13T08:15:00Z",
                "state-locked": {"locked": 2, "reason": "logic"},
                "state-auth-type": "pin+nfc",
                "state-last-code": "****",
                "state-last-id": "42",
                "state-last-tag": "AA:BB:CC:DD",
                "state-last-user": "Jan",
                "state-learn-result": "ok",
            },
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        by_state = {
            entity._state_name: entity
            for entity in entities
            if hasattr(entity, "_state_name")
        }
        self.assertEqual(set(by_state), set(control.states))
        self.assertEqual(
            [entity for entity in entities if isinstance(entity, sensor_module.LoxoneDiagnosticSensor)],
            [],
        )

        events_sensor = by_state["Events"]
        self.assertIsInstance(events_sensor, sensor_module.LoxoneEventStateSensor)
        self.assertEqual(events_sensor.extra_state_attributes["event_state"], "Events")
        self.assertIn("granted", events_sensor.native_value)

        user_sensor = by_state["Lastuser"]
        self.assertIsInstance(user_sensor, sensor_module.LoxoneAccessStateSensor)
        self.assertEqual(user_sensor.extra_state_attributes["access_state"], "Lastuser")

        code_date_sensor = by_state["CodeDate"]
        self.assertEqual(
            code_date_sensor.native_value,
            datetime(2026, 3, 14, 11, 22, tzinfo=timezone.utc),
        )
        self.assertEqual(
            code_date_sensor.device_class,
            sensor_module.SensorDeviceClass.TIMESTAMP,
        )
        self.assertEqual(
            by_state["HistoryDate"].device_class,
            sensor_module.SensorDeviceClass.TIMESTAMP,
        )
        self.assertIn('"locked":2', by_state["JLocked"].native_value)

    async def test_universal_events_state_is_added_for_supported_sensor_control(self) -> None:
        control = LoxoneControl(
            uuid="text-uuid",
            uuid_action="text-action",
            name="Event Text",
            type="TextState",
            states={
                "value": "state-value",
                "Events": "state-events",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-value": "main state",
                "state-events": ["a", "b"],
            },
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        primary_entities = [
            entity for entity in entities if isinstance(entity, sensor_module.LoxonePrimarySensor)
        ]
        event_entities = [
            entity for entity in entities if isinstance(entity, sensor_module.LoxoneEventStateSensor)
        ]
        self.assertEqual(len(primary_entities), 1)
        self.assertEqual(len(event_entities), 1)
        self.assertEqual(event_entities[0].extra_state_attributes["event_state"], "Events")
        self.assertIn("a", event_entities[0].native_value)


if __name__ == "__main__":
    unittest.main()
