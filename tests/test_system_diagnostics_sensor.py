"""Tests for Miniserver system diagnostics sensors."""

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
    miniserver_name = "Loxone Miniserver"
    server_model = "Miniserver"
    software_version = "16.0.0"

    def __init__(self, values):
        self.controls = []
        self._values = values

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def system_stat_state_uuid(self, metric_key: str) -> str:
        return f"sysdiag-{metric_key}"

    def system_stat_value(self, metric_key: str):
        return self._values.get(self.system_stat_state_uuid(metric_key))

    def system_stat_command(self, metric_key: str):
        return f"dev/sys/{metric_key}"


class _FakeConfigEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


_install_homeassistant_stubs()
const = load_integration_module("custom_components.loxone_home_assistant.const")
sensor_module = load_integration_module("custom_components.loxone_home_assistant.sensor")


class SystemDiagnosticsSensorTests(unittest.IsolatedAsyncioTestCase):
    """Verify Miniserver diagnostics are exposed as sensors."""

    async def test_setup_adds_system_diagnostic_sensors_with_expected_values(self) -> None:
        bridge = _FakeBridge(
            {
                "sysdiag-numtasks": "17",
                "sysdiag-cpu": "23.4",
                "sysdiag-heap": "61.2",
                "sysdiag-ints": "12456",
            }
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 4)
        by_metric = {
            entity.extra_state_attributes["metric_key"]: entity
            for entity in entities
        }

        numtasks = by_metric["numtasks"]
        cpu = by_metric["cpu"]
        heap = by_metric["heap"]
        interrupts = by_metric["ints"]

        self.assertEqual(numtasks.native_value, 17)
        self.assertIsNone(numtasks.native_unit_of_measurement)
        self.assertEqual(numtasks.state_class, sensor_module.SensorStateClass.MEASUREMENT)

        self.assertEqual(cpu.native_value, 23.4)
        self.assertEqual(cpu.native_unit_of_measurement, "%")
        self.assertEqual(cpu.state_class, sensor_module.SensorStateClass.MEASUREMENT)

        self.assertEqual(heap.native_value, 61.2)
        self.assertEqual(heap.native_unit_of_measurement, "%")
        self.assertEqual(heap.state_class, sensor_module.SensorStateClass.MEASUREMENT)

        self.assertEqual(interrupts.native_value, 12456)
        self.assertIsNone(interrupts.native_unit_of_measurement)
        self.assertEqual(
            interrupts.state_class,
            sensor_module.SensorStateClass.TOTAL_INCREASING,
        )


if __name__ == "__main__":
    unittest.main()
