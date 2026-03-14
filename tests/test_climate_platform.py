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
        PRESET_MODE = 2

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

    def __init__(self, values, controls=None, operating_modes=None):
        self._values = values
        self.controls = controls or []
        self.operating_modes = operating_modes or {}
        self.commands: list[tuple[str, str]] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    async def async_send_action(self, _uuid_action, _command):
        self.commands.append((_uuid_action, _command))


class _FakeConfigEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


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

    async def test_climate_exposes_operating_modes_as_preset_options(self) -> None:
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
                "state-operating-mode": 1,
            },
            operating_modes={
                "0": "Auto",
                "1": "Comfort",
                "2": "Eco",
            },
        )
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual(entity.preset_modes, ["Auto", "Comfort", "Eco"])
        self.assertEqual(entity.preset_mode, "Comfort")

        await entity.async_set_preset_mode("Eco")

        self.assertEqual(bridge.commands, [("climate-action", "setOperatingMode/2")])

    async def test_climate_uses_operating_modes_from_control_details(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Biuro",
            type="IRoomControllerV2",
            states={
                "tempTarget": "state-target",
                "operatingMode": "state-operating-mode",
            },
            details={
                "operatingModes": [
                    {"id": 1, "name": "Comfort"},
                    {"id": 3, "name": "Sleep"},
                ]
            },
        )
        bridge = _FakeBridge(
            {
                "state-target": 22,
                "state-operating-mode": "3",
            }
        )
        entity = LoxoneClimateEntity(bridge, control)

        self.assertEqual(entity.preset_modes, ["Comfort", "Sleep"])
        self.assertEqual(entity.preset_mode, "Sleep")

    async def test_ac_control_uses_set_mode_for_operating_mode_changes(self) -> None:
        control = LoxoneControl(
            uuid="ac-uuid",
            uuid_action="ac-action",
            name="Salon AC",
            type="ACControl",
            states={
                "targetTemperature": "state-target",
                "mode": "state-mode",
            },
            details={
                "operatingModes": [
                    {"id": 0, "name": "Cool"},
                    {"id": 1, "name": "Heat"},
                ]
            },
        )
        bridge = _FakeBridge(
            {
                "state-target": 24,
                "state-mode": 0,
            }
        )
        entity = LoxoneClimateEntity(bridge, control)

        await entity.async_set_preset_mode("Heat")

        self.assertEqual(bridge.commands, [("ac-action", "setMode/1")])


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
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, "loxone_home_assistant")
        entities: list = []

        await climate_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "room-action")


if __name__ == "__main__":
    unittest.main()
