"""Tests for extended Loxone climate behavior."""

from __future__ import annotations

import sys
import types
import unittest
from enum import IntFlag

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

    class ClimateEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        TARGET_HUMIDITY = 4
        FAN_MODE = 8
        PRESET_MODE = 16
        SWING_MODE = 32
        TURN_OFF = 128
        TURN_ON = 256

    class HVACMode:
        AUTO = "auto"
        OFF = "off"
        HEAT = "heat"
        COOL = "cool"
        DRY = "dry"
        FAN_ONLY = "fan_only"

    class HVACAction:
        OFF = "off"
        HEATING = "heating"
        COOLING = "cooling"
        IDLE = "idle"

    climate.ClimateEntity = ClimateEntity
    climate.ClimateEntityFeature = ClimateEntityFeature
    climate.HVACMode = HVACMode
    climate.HVACAction = HVACAction

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

    def __init__(self, values, controls=None):
        self._values = values
        self.controls = controls or []
        self.commands: list[tuple[str, str]] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    async def async_send_action(self, _uuid_action, _command):
        self.commands.append((_uuid_action, _command))


class _FakeConfigEntry:
    def __init__(self, bridge: _FakeBridge) -> None:
        self.runtime_data = bridge


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
        self.assertTrue(
            entity.supported_features
            & climate_module.ClimateEntityFeature.TARGET_TEMPERATURE,
        )
        self.assertFalse(
            entity.supported_features
            & climate_module.ClimateEntityFeature.PRESET_MODE,
        )

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


class ClimateCommandMappingTests(unittest.IsolatedAsyncioTestCase):
    """Verify control-specific temperature commands."""

    async def test_pool_controller_uses_target_temp_command(self) -> None:
        control = LoxoneControl(
            uuid="pool-uuid",
            uuid_action="pool-action",
            name="Basen",
            type="PoolController",
            states={"tempTarget": "state-target"},
        )
        bridge = _FakeBridge({"state-target": 27})
        entity = LoxoneClimateEntity(bridge, control)

        await entity.async_set_temperature(temperature=28)

        self.assertEqual(bridge.commands, [("pool-action", "targetTemp/28")])

    async def test_sauna_uses_temp_command(self) -> None:
        control = LoxoneControl(
            uuid="sauna-uuid",
            uuid_action="sauna-action",
            name="Sauna",
            type="Sauna",
            states={"tempTarget": "state-target"},
        )
        bridge = _FakeBridge({"state-target": 70})
        entity = LoxoneClimateEntity(bridge, control)

        await entity.async_set_temperature(temperature=75)

        self.assertEqual(bridge.commands, [("sauna-action", "temp/75")])

    async def test_air_condition_uses_set_target_command(self) -> None:
        control = LoxoneControl(
            uuid="ac-uuid",
            uuid_action="ac-action",
            name="Salon AC",
            type="ACControl",
            states={"targetTemperature": "state-target"},
        )
        bridge = _FakeBridge({"state-target": 24})
        entity = LoxoneClimateEntity(bridge, control)

        await entity.async_set_temperature(temperature=23)

        self.assertEqual(bridge.commands, [("ac-action", "setTarget/23")])

    async def test_air_condition_accepts_legacy_accontrol_type_name(self) -> None:
        control = LoxoneControl(
            uuid="ac-uuid",
            uuid_action="ac-action",
            name="Salon AC",
            type="AcControl",
            states={"targetTemperature": "state-target"},
        )
        bridge = _FakeBridge({"state-target": 24})
        entity = LoxoneClimateEntity(bridge, control)

        await entity.async_set_temperature(temperature=22.5)

        self.assertEqual(bridge.commands, [("ac-action", "setTarget/22.5")])

    async def test_room_controller_v2_uses_its_own_mode_ids(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Salon",
            type="IRoomControllerV2",
            states={
                "tempTarget": "state-target",
                "operatingMode": "state-operating-mode",
            },
        )
        bridge = _FakeBridge(
            {
                "state-target": 22,
                "state-operating-mode": 2,
            },
        )
        bridge.operating_modes = {"1": "Holiday", "2": "Comfort"}
        entity = LoxoneClimateEntity(bridge, control)

        self.assertIn("Automatic cooling", entity.preset_modes)
        self.assertNotIn("Holiday", entity.preset_modes)
        self.assertEqual(entity.preset_mode, "Automatic cooling")
        self.assertTrue(
            entity.supported_features
            & climate_module.ClimateEntityFeature.PRESET_MODE,
        )

        await entity.async_set_preset_mode("Off")

        self.assertEqual(bridge.commands, [("climate-action", "setOperatingMode/-1")])

    async def test_room_controller_v1_uses_legacy_mode_and_temperature_commands(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Biuro",
            type="IRoomController",
            states={
                "tempTarget": "state-target",
                "mode": "state-operating-mode",
            },
        )
        bridge = _FakeBridge(
            {
                "state-target": 22,
                "state-operating-mode": 2,
            }
        )
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual(entity.preset_mode, "Automatic")
        await entity.async_set_preset_mode("Manual cooling")
        await entity.async_set_temperature(temperature=22)
        self.assertEqual(
            bridge.commands,
            [("climate-action", "mode/6"), ("climate-action", "settemp/2/22")],
        )

    async def test_ac_control_uses_set_mode_for_operating_mode_changes(self) -> None:
        control = LoxoneControl(
            uuid="ac-uuid",
            uuid_action="ac-action",
            name="Salon AC",
            type="ACControl",
            states={
                "targetTemperature": "state-target",
                "mode": "state-mode",
                "operatingmodes": "state-modes",
            },
        )
        bridge = _FakeBridge(
            {
                "state-target": 24,
                "state-mode": 1,
                "state-modes": """["Night's cool", "Heat"]""",
            }
        )
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual(entity.preset_mode, "Night's cool")
        await entity.async_set_preset_mode("Heat")

        self.assertEqual(bridge.commands, [("ac-action", "setMode/2")])

    async def test_ac_control_maps_native_modes_by_id_and_uses_live_limits(self) -> None:
        control = LoxoneControl(
            uuid="ac-uuid",
            uuid_action="ac-action",
            name="Salon AC",
            type="ACControl",
            states={
                "status": "powered-off",
                "mode": "cold",
                "operatingmodes": "mode-names",
                "targetTemperature": "target",
                "minTemp": "min",
                "maxTemp": "max",
                "fan": "fan",
                "defaultFan": "default-fan",
                "fanspeeds": "fan-names",
                "ventMode": "direction",
                "airflows": "direction-names",
            },
        )
        bridge = _FakeBridge({
            "powered-off": 0,
            "cold": 3,
            "mode-names": '["Automatyczny", null, "Chłodno"]',
            "target": 21.5,
            "min": 16,
            "max": 28,
            "fan": 2,
            "default-fan": 0,
            "fan-names": '["Off", "Auto", "Silent"]',
            "direction": 2,
            "direction-names": '["Up", "Down"]',
        })
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual(entity.hvac_modes, ["off", "auto", "cool"])
        self.assertEqual(entity.hvac_mode, "off")
        self.assertEqual(entity.preset_mode, "Chłodno")
        self.assertEqual(entity.fan_mode, "Off")
        self.assertEqual(entity.swing_mode, "Down")
        self.assertEqual((entity.min_temp, entity.max_temp, entity.target_temperature), (16, 28, 21.5))
        self.assertTrue(entity.supported_features & climate_module.ClimateEntityFeature.FAN_MODE)
        self.assertTrue(entity.supported_features & climate_module.ClimateEntityFeature.SWING_MODE)

        await entity.async_set_hvac_mode("cool")
        await entity.async_set_fan_mode("Silent")
        await entity.async_set_swing_mode("Up")
        self.assertEqual(
            bridge.commands,
            [
                ("ac-action", "setMode/3"),
                ("ac-action", "on"),
                ("ac-action", "setFan/2"),
                ("ac-action", "setAirDir/1"),
            ],
        )

    async def test_sauna_reports_heat_and_uses_manual_temperature_and_humidity(self) -> None:
        control = LoxoneControl(
            uuid="sauna-uuid",
            uuid_action="sauna-action",
            name="Sauna",
            type="Sauna",
            details={"hasVaporizer": True},
            states={
                "active": "active",
                "power": "heating",
                "tempTarget": "temperature",
                "humidityTarget": "humidity",
                "mode": "mode",
                "fan": "fan",
            },
        )
        bridge = _FakeBridge({"active": 1, "heating": 1, "mode": 2, "fan": 0, "humidity": 45})
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual((entity.hvac_mode, entity.hvac_action), ("heat", "heating"))
        self.assertEqual((entity.min_temp, entity.max_temp), (30, 120))
        self.assertEqual(entity.target_humidity, 45)
        self.assertEqual(entity.preset_mode, "Humidity manual")
        await entity.async_set_temperature(temperature=80)
        await entity.async_set_humidity(50)
        await entity.async_set_fan_mode("on")
        self.assertEqual(
            bridge.commands,
            [("sauna-action", "temp/80"), ("sauna-action", "humidity/50"), ("sauna-action", "fanon")],
        )


class ClimateSetupAliasTests(unittest.IsolatedAsyncioTestCase):
    """Verify climate setup accepts known controller type aliases."""

    async def test_setup_includes_room_controller_v2_alias(self) -> None:
        room_controller = LoxoneControl(
            uuid="room-uuid",
            uuid_action="room-action",
            name="Salon",
            type="RoomControllerV2",
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
            },
        )
        bridge = _FakeBridge(
            {
                "state-temp-actual": 21.0,
                "state-temp-target": 22.0,
            },
            controls=[room_controller],
        )
        entry = _FakeConfigEntry(bridge)
        entities: list = []

        await climate_module.async_setup_entry(
            object(),
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "room-action")


if __name__ == "__main__":
    unittest.main()
