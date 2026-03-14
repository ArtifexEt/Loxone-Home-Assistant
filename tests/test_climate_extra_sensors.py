"""Tests for additional climate state sensors."""

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
        HUMIDITY = "humidity"
        CO2 = "co2"
        AQI = "aqi"

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


class ClimateExtraSensorTests(unittest.IsolatedAsyncioTestCase):
    """Verify climate controls expose additional state sensors."""

    async def test_setup_creates_climate_state_sensors_except_temperatures(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Salon",
            type="IRoomControllerV2",
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "humidity": "state-humidity",
                "co2": "state-co2",
                "airQuality": "state-air",
                "operatingMode": "state-mode",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 21.5,
                "state-temp-target": 22.0,
                "state-humidity": 45,
                "state-co2": 760,
                "state-air": 32,
                "state-mode": "comfort",
            },
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 4)
        by_state = {entity._state_name: entity for entity in entities}
        self.assertEqual(set(by_state), {"humidity", "co2", "airQuality", "operatingMode"})

        humidity_sensor = by_state["humidity"]
        co2_sensor = by_state["co2"]
        air_sensor = by_state["airQuality"]
        mode_sensor = by_state["operatingMode"]

        self.assertEqual(humidity_sensor.native_value, 45.0)
        self.assertEqual(
            humidity_sensor.device_class,
            sensor_module.SensorDeviceClass.HUMIDITY,
        )
        self.assertEqual(humidity_sensor.native_unit_of_measurement, "%")

        self.assertEqual(co2_sensor.native_value, 760.0)
        self.assertEqual(
            co2_sensor.device_class,
            sensor_module.SensorDeviceClass.CO2,
        )
        self.assertEqual(co2_sensor.native_unit_of_measurement, "ppm")

        self.assertEqual(
            air_sensor.device_class,
            sensor_module.SensorDeviceClass.AQI,
        )
        self.assertEqual(air_sensor.native_value, 32.0)
        self.assertEqual(mode_sensor.native_value, "comfort")


if __name__ == "__main__":
    unittest.main()
