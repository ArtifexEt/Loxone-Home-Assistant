"""Tests for extended Loxone climate behavior."""

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

    climate = sys.modules["homeassistant.components.climate"]

    class ClimateEntity:
        pass

    class ClimateEntityFeature:
        TARGET_TEMPERATURE = 1

    class HVACMode:
        AUTO = "auto"

    climate.ClimateEntity = ClimateEntity
    climate.ClimateEntityFeature = ClimateEntityFeature
    climate.HVACMode = HVACMode

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

    class UnitOfTemperature:
        CELSIUS = "°C"
        FAHRENHEIT = "°F"

    const.Platform = Platform
    const.UnitOfTemperature = UnitOfTemperature
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

    def __init__(self, values):
        self._values = values

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    async def async_send_action(self, _uuid_action, _command):
        return None


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
climate_module = load_integration_module("custom_components.loxone_home_assistant.climate")
LoxoneControl = models.LoxoneControl
LoxoneClimateEntity = climate_module.LoxoneClimateEntity


class ClimatePlatformTests(unittest.TestCase):
    """Verify climate entity exposes extended environmental data."""

    def test_climate_exposes_humidity_co2_and_air_quality(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Salon",
            type="IRoomControllerV2",
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "humidityActual": "state-humidity",
                "co2": "state-co2",
                "airQuality": "state-air",
            },
            details={
                "minTemp": 8,
                "maxTemp": 30,
                "stepTemp": 0.1,
                "format": "%.1f °F",
            },
        )
        bridge = _FakeBridge(
            {
                "state-temp-actual": "21.5",
                "state-temp-target": "22.0",
                "state-humidity": "48",
                "state-co2": "720",
                "state-air": "42",
            }
        )
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual(entity.current_temperature, 21.5)
        self.assertEqual(entity.target_temperature, 22.0)
        self.assertEqual(entity.current_humidity, 48.0)
        self.assertEqual(entity.min_temp, 8.0)
        self.assertEqual(entity.max_temp, 30.0)
        self.assertEqual(entity.target_temperature_step, 0.1)
        self.assertEqual(
            entity.temperature_unit,
            climate_module.UnitOfTemperature.FAHRENHEIT,
        )
        self.assertEqual(entity.extra_state_attributes["co2"], 720.0)
        self.assertEqual(entity.extra_state_attributes["air_quality"], 42.0)

    def test_climate_matches_state_names_even_with_case_and_separator_variants(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Biuro",
            type="IRoomControllerV2",
            states={
                "Temp-Actual": "state-temp-actual",
                "Target_Temperature": "state-temp-target",
                "HumidityActual": "state-humidity",
            },
        )
        bridge = _FakeBridge(
            {
                "state-temp-actual": 20.2,
                "state-temp-target": 23.1,
                "state-humidity": 40,
            }
        )
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual(entity.current_temperature, 20.2)
        self.assertEqual(entity.target_temperature, 23.1)
        self.assertEqual(entity.current_humidity, 40.0)


if __name__ == "__main__":
    unittest.main()
