"""Tests for Loxone meter sensor behavior."""

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


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
sensor_module = load_integration_module("custom_components.loxone_home_assistant.sensor")
LoxoneControl = models.LoxoneControl
LoxoneMeterSensor = sensor_module.LoxoneMeterSensor
LoxoneWebpageSensor = sensor_module.LoxoneWebpageSensor


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, values):
        self._values = values

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))


class MeterSensorTests(unittest.TestCase):
    """Verify meter sensors expose correct HA metadata."""

    def _meter(self, details: dict | None = None) -> LoxoneControl:
        return LoxoneControl(
            uuid="meter-uuid",
            uuid_action="meter-action",
            name="Energia",
            type="Meter",
            states={
                "actual": "state-actual",
                "total": "state-total",
            },
            details=details or {},
        )

    def test_total_is_energy_total_increasing(self) -> None:
        control = self._meter({"format": "%.3f kWh", "actualFormat": "%.1f W"})
        bridge = _FakeBridge({"state-total": "123.45", "state-actual": "78.9"})
        entity = LoxoneMeterSensor(bridge, control, "total")

        self.assertEqual(entity.native_value, 123.45)
        self.assertEqual(entity.native_unit_of_measurement, "kWh")
        self.assertEqual(entity.device_class, sensor_module.SensorDeviceClass.ENERGY)
        self.assertEqual(entity.state_class, sensor_module.SensorStateClass.TOTAL_INCREASING)

    def test_actual_prefers_actual_format_and_power_class(self) -> None:
        control = self._meter({"format": "%.3f kWh", "actualFormat": "%.1f W"})
        bridge = _FakeBridge({"state-total": "123.45", "state-actual": "78.9"})
        entity = LoxoneMeterSensor(bridge, control, "actual")

        self.assertEqual(entity.native_value, 78.9)
        self.assertEqual(entity.native_unit_of_measurement, "W")
        self.assertEqual(entity.device_class, sensor_module.SensorDeviceClass.POWER)
        self.assertEqual(entity.state_class, sensor_module.SensorStateClass.MEASUREMENT)

    def test_actual_falls_back_to_format_when_actual_format_missing(self) -> None:
        control = self._meter({"format": "%.3f kWh"})
        bridge = _FakeBridge({"state-total": "10.0", "state-actual": "1.5"})
        entity = LoxoneMeterSensor(bridge, control, "actual")

        self.assertEqual(entity.native_unit_of_measurement, "kWh")
        self.assertEqual(entity.device_class, sensor_module.SensorDeviceClass.ENERGY)

    def test_webpage_sensor_exposes_url_from_details(self) -> None:
        control = LoxoneControl(
            uuid="webpage-uuid",
            uuid_action="webpage-action",
            name="Kamera",
            type="Webpage",
            states={},
            details={"url": "https://example.local/cam"},
        )
        bridge = _FakeBridge({})
        entity = LoxoneWebpageSensor(bridge, control)

        self.assertEqual(entity.native_value, "https://example.local/cam")


if __name__ == "__main__":
    unittest.main()
