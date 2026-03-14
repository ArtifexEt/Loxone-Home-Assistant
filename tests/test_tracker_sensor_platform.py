"""Tests for Tracker mapping on the sensor platform."""

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


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls, values):
        self.controls = controls
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


class TrackerSensorPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify Tracker controls map to log/event sensors."""

    async def test_tracker_creates_event_sensors_for_non_boolean_states(self) -> None:
        control = LoxoneControl(
            uuid="tracker-uuid",
            uuid_action="tracker-action",
            name="Access Tracker",
            type="Tracker",
            states={
                "active": "state-active",
                "entries": "state-entries",
                "lastMessage": "state-last-message",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-active": 1,
                "state-entries": "2026-03-14 10:00 Door opened",
                "state-last-message": "Door opened",
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

        self.assertEqual(len(entities), 2)
        for entity in entities:
            self.assertIsInstance(entity, sensor_module.LoxoneEventStateSensor)
            self.assertIsNotNone(entity.native_value)

    async def test_tracker_falls_back_to_named_sensor_when_only_boolean_state_exists(self) -> None:
        control = LoxoneControl(
            uuid="tracker-uuid",
            uuid_action="tracker-action",
            name="Boolean Tracker",
            type="Tracker",
            states={
                "active": "state-active",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-active": 1,
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

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertIsInstance(entity, sensor_module.LoxoneNamedStateSensor)
        self.assertNotIsInstance(entity, sensor_module.LoxoneEventStateSensor)
        self.assertEqual(entity.native_value, 1.0)


if __name__ == "__main__":
    unittest.main()
