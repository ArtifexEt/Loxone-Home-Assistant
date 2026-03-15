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

    async def test_climate_state_sensors_use_canonical_units_for_indexed_humidity_and_co2(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-indexed-uuid",
            uuid_action="climate-indexed-action",
            name="Inteligentny Regulator Pomieszczeniowy",
            type="IRoomControllerV2",
            details={"format": "%.1f°"},
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "humidity_5": "state-humidity",
                "co2_6": "state-co2",
                "operatingMode": "state-mode",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 21.5,
                "state-temp-target": 22.0,
                "state-humidity": 47,
                "state-co2": 820,
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

        by_state = {entity._state_name: entity for entity in entities}
        self.assertEqual(set(by_state), {"humidity_5", "co2_6", "operatingMode"})

        humidity_sensor = by_state["humidity_5"]
        co2_sensor = by_state["co2_6"]
        mode_sensor = by_state["operatingMode"]

        self.assertEqual(humidity_sensor.native_value, 47.0)
        self.assertEqual(
            humidity_sensor.device_class,
            sensor_module.SensorDeviceClass.HUMIDITY,
        )
        self.assertEqual(humidity_sensor.native_unit_of_measurement, "%")
        self.assertEqual(
            humidity_sensor.state_class,
            sensor_module.SensorStateClass.MEASUREMENT,
        )

        self.assertEqual(co2_sensor.native_value, 820.0)
        self.assertEqual(
            co2_sensor.device_class,
            sensor_module.SensorDeviceClass.CO2,
        )
        self.assertEqual(co2_sensor.native_unit_of_measurement, "ppm")
        self.assertEqual(
            co2_sensor.state_class,
            sensor_module.SensorStateClass.MEASUREMENT,
        )

        self.assertEqual(mode_sensor.native_value, "comfort")
        self.assertIsNone(mode_sensor.native_unit_of_measurement)

    async def test_climate_state_sensors_keep_only_last_tagged_humidity_and_co2_state(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-tagged-uuid",
            uuid_action="climate-tagged-action",
            name="Inteligentny Regulator",
            type="IRoomControllerV2",
            details={"format": "%.1f°"},
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "humidity_5": "state-humidity-5",
                "humidity_6": "state-humidity-6",
                "humidity_7": "state-humidity-7",
                "co2_6": "state-co2-6",
                "co2_7": "state-co2-7",
                "operatingMode": "state-mode",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 21.5,
                "state-temp-target": 22.0,
                "state-humidity-5": 40,
                "state-humidity-6": 44,
                "state-humidity-7": 48,
                "state-co2-6": 760,
                "state-co2-7": 810,
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

        by_state = {entity._state_name: entity for entity in entities}
        self.assertEqual(set(by_state), {"humidity_7", "co2_7", "operatingMode"})
        self.assertEqual(by_state["humidity_7"].native_value, 48.0)
        self.assertEqual(by_state["humidity_7"].native_unit_of_measurement, "%")
        self.assertEqual(by_state["co2_7"].native_value, 810.0)
        self.assertEqual(by_state["co2_7"].native_unit_of_measurement, "ppm")

    async def test_climate_state_sensors_normalize_indexed_temperature_units(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-indexed-temp-uuid",
            uuid_action="climate-indexed-temp-action",
            name="Inteligentny Regulator Pomieszczeniowy",
            type="IRoomControllerV2",
            details={"format": "%.1f°"},
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "actual_outdoor_temp_7": "state-outdoor",
                "comfort_temperature_offset_7": "state-comfort-offset",
                "excess_energy_temp_offset_7": "state-energy-offset",
                "average_outdoor_temp_7": "state-average-outdoor",
                "humidity_7": "state-humidity",
                "operatingMode": "state-mode",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 21.5,
                "state-temp-target": 22.0,
                "state-outdoor": 4.0,
                "state-comfort-offset": 0.5,
                "state-energy-offset": 1.0,
                "state-average-outdoor": 3.4,
                "state-humidity": 48,
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

        by_state = {entity._state_name: entity for entity in entities}
        self.assertEqual(
            set(by_state),
            {
                "actual_outdoor_temp_7",
                "comfort_temperature_offset_7",
                "excess_energy_temp_offset_7",
                "average_outdoor_temp_7",
                "humidity_7",
                "operatingMode",
            },
        )
        self.assertEqual(by_state["humidity_7"].native_unit_of_measurement, "%")
        self.assertIsNone(by_state["operatingMode"].native_unit_of_measurement)

        for state_name in (
            "actual_outdoor_temp_7",
            "comfort_temperature_offset_7",
            "excess_energy_temp_offset_7",
            "average_outdoor_temp_7",
        ):
            self.assertEqual(by_state[state_name].native_unit_of_measurement, "°C")
            self.assertEqual(
                by_state[state_name].state_class,
                sensor_module.SensorStateClass.MEASUREMENT,
            )

    async def test_setup_creates_air_condition_state_sensors_except_temperatures(self) -> None:
        climate_control = LoxoneControl(
            uuid="ac-uuid",
            uuid_action="ac-action",
            name="Salon AC",
            type="ACControl",
            details={"format": "%.1f°"},
            states={
                "temperature": "state-temp-actual",
                "targetTemperature": "state-temp-target",
                "status": "state-status",
                "mode": "state-mode",
                "fan": "state-fan",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 23.5,
                "state-temp-target": 21.0,
                "state-status": 1,
                "state-mode": 2,
                "state-fan": 3,
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

        by_state = {entity._state_name: entity for entity in entities}
        self.assertEqual(set(by_state), {"status", "mode", "fan"})
        self.assertEqual(by_state["status"].native_value, 1.0)
        self.assertEqual(by_state["mode"].native_value, 2.0)
        self.assertEqual(by_state["fan"].native_value, 3.0)
        self.assertIsNone(by_state["status"].native_unit_of_measurement)
        self.assertIsNone(by_state["mode"].native_unit_of_measurement)
        self.assertIsNone(by_state["fan"].native_unit_of_measurement)
        self.assertIsNone(by_state["status"].state_class)
        self.assertIsNone(by_state["mode"].state_class)
        self.assertIsNone(by_state["fan"].state_class)

    async def test_climate_metadata_states_do_not_report_degree_units_or_statistics(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Inteligentny Regulator",
            type="IRoomControllerV2",
            details={"format": "%.1f°"},
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "temperatureBoundaryInfo": "state-boundary",
                "capabilities": "state-capabilities",
                "fan": "state-fan",
                "ventMode": "state-vent-mode",
                "openWindow": "state-open-window",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 21.5,
                "state-temp-target": 22.0,
                "state-boundary": 1,
                "state-capabilities": 2,
                "state-fan": 3,
                "state-vent-mode": 4,
                "state-open-window": 0,
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

        by_state = {entity._state_name: entity for entity in entities}
        self.assertEqual(
            set(by_state),
            {
                "temperatureBoundaryInfo",
                "capabilities",
                "fan",
                "ventMode",
                "openWindow",
            },
        )
        for state_name in by_state:
            self.assertIsNone(by_state[state_name].native_unit_of_measurement)
            self.assertIsNone(by_state[state_name].state_class)

    async def test_setup_creates_override_entries_sensor_and_skips_lock_state(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Sypialnia",
            type="IRoomControllerV2",
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "jLocked": "state-j-locked",
                "overrideEntries": "state-overrides",
                "operatingMode": "state-mode",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 20.5,
                "state-temp-target": 21.0,
                "state-j-locked": {"locked": 1, "reason": "Visualization"},
                "state-overrides": [
                    {"start": 1710000000, "end": 0, "reason": 3, "source": "APP"},
                    {"start": 1710000600, "end": 1710004200, "reason": 2, "source": "Window"},
                ],
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

        override_entities = [
            entity
            for entity in entities
            if isinstance(entity, sensor_module.LoxoneClimateOverrideEntriesSensor)
        ]
        self.assertEqual(len(override_entities), 1)
        override_entity = override_entities[0]
        self.assertEqual(override_entity.native_value, 2)
        self.assertTrue(override_entity.extra_state_attributes["override_active"])
        self.assertEqual(
            override_entity.extra_state_attributes["override_reason_codes"],
            [3, 2],
        )
        self.assertEqual(
            override_entity.extra_state_attributes["override_sources"],
            ["APP", "Window"],
        )

        by_state = {
            entity._state_name: entity
            for entity in entities
            if hasattr(entity, "_state_name")
        }
        self.assertNotIn("jLocked", by_state)
        self.assertIn("operatingMode", by_state)

    async def test_override_entries_sensor_accepts_json_string_payload(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Gabinet",
            type="IRoomControllerV2",
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "overrideEntries": "state-overrides",
                "operatingMode": "state-mode",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 20.0,
                "state-temp-target": 21.0,
                "state-overrides": (
                    '{"start":542719409,"end":542723220,"reason":1,"isTimer":false,"source":"null"}'
                ),
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

        override_entities = [
            entity
            for entity in entities
            if isinstance(entity, sensor_module.LoxoneClimateOverrideEntriesSensor)
        ]
        self.assertEqual(len(override_entities), 1)
        override_entity = override_entities[0]
        self.assertEqual(override_entity.native_value, 1)
        self.assertTrue(override_entity.extra_state_attributes["override_active"])
        self.assertEqual(
            override_entity.extra_state_attributes["override_reason_codes"],
            [1],
        )

    async def test_setup_detects_override_entries_state_with_extended_name(self) -> None:
        climate_control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Pokoj Dziecka",
            type="IRoomControllerV2",
            states={
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
                "overrideEntriesCurrent": "state-overrides",
                "operatingMode": "state-mode",
            },
        )
        bridge = _FakeBridge(
            [climate_control],
            {
                "state-temp-actual": 20.5,
                "state-temp-target": 22.0,
                "state-overrides": '{"start":1,"end":0,"reason":2}',
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

        override_entities = [
            entity
            for entity in entities
            if isinstance(entity, sensor_module.LoxoneClimateOverrideEntriesSensor)
        ]
        self.assertEqual(len(override_entities), 1)
        self.assertEqual(override_entities[0]._state_name, "overrideEntriesCurrent")
        self.assertEqual(override_entities[0].native_value, 1)

        climate_state_entities = [
            entity
            for entity in entities
            if isinstance(entity, sensor_module.LoxoneClimateStateSensor)
        ]
        climate_state_names = {entity._state_name for entity in climate_state_entities}
        self.assertNotIn("overrideEntriesCurrent", climate_state_names)


if __name__ == "__main__":
    unittest.main()
