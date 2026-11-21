"""Tests for additional sensor control type mappings."""

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
        BATTERY = "battery"
        DURATION = "duration"
        ENERGY = "energy"
        HUMIDITY = "humidity"
        POWER = "power"
        TEMPERATURE = "temperature"
        TIMESTAMP = "timestamp"

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


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
sensor_module = load_integration_module("custom_components.loxone_home_assistant.sensor")
LoxoneControl = models.LoxoneControl


class AdditionalSensorMappingsTests(unittest.IsolatedAsyncioTestCase):
    """Verify additional Loxone control types map to dedicated sensors."""

    async def test_ircv2_daytimer_maps_to_primary_sensor(self) -> None:
        control = LoxoneControl(
            uuid="daytimer-uuid",
            uuid_action="daytimer-action",
            name="Heating and Cooling",
            type="IRCV2Daytimer",
            details={"format": "%.1f°"},
            states={
                "entriesAndDefaultValue": "state-entries",
                "mode": "state-mode",
                "modeList": "state-mode-list",
                "value": "state-value",
                "needsActivation": "state-needs-activation",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-entries": [],
                "state-mode": 1,
                "state-mode-list": "[]",
                "state-value": 21.5,
                "state-needs-activation": 0,
            },
        )
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertIsInstance(entity, sensor_module.LoxonePrimarySensor)
        self.assertEqual(entity.native_value, 21.5)
        self.assertEqual(entity.native_unit_of_measurement, "°")

    async def test_window_monitor_exposes_all_count_states_without_duplicate_primary(self) -> None:
        control = LoxoneControl(
            uuid="window-monitor-uuid",
            uuid_action="window-monitor-action",
            name="Window Monitor",
            type="WindowMonitor",
            states={
                "numOpen": "state-num-open",
                "numClosed": "state-num-closed",
                "numTilted": "state-num-tilted",
                "numOffline": "state-num-offline",
                "numLocked": "state-num-locked",
                "numUnlocked": "state-num-unlocked",
                "windowStates": "state-window-states",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-num-open": 2,
                "state-num-closed": 5,
                "state-num-tilted": 1,
                "state-num-offline": 0,
                "state-num-locked": 3,
                "state-num-unlocked": 4,
                "state-window-states": "4,17,2",
            },
        )
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 6)
        entity = entities[0]
        self.assertIsInstance(entity, sensor_module.LoxonePrimarySensor)
        self.assertEqual(entity.native_value, 2.0)
        self.assertIsNone(entity.native_unit_of_measurement)
        self.assertEqual(
            {entity._attr_unique_id.rsplit(":", 1)[-1]: entity.native_value for entity in entities[1:]},
            {"closed": 5, "tilted": 1, "offline": 0, "locked": 3, "unlocked": 4},
        )

    async def test_fronius_preserves_signed_power_and_types_cumulative_energy(self) -> None:
        control = LoxoneControl(
            uuid="fronius", uuid_action="fronius-action", name="Solar", type="Fronius",
            states={name: name for name in ("mode", "prodCurr", "gridCurr", "prodTotal", "stateOfCharge")},
        )
        bridge = _FakeBridge(
            [control], {"mode": 15, "prodCurr": 3.5, "gridCurr": -1.2, "prodTotal": 400.0, "stateOfCharge": 76}
        )
        result = []
        await sensor_module.async_setup_entry(
            types.SimpleNamespace(data={}), types.SimpleNamespace(runtime_data=bridge), result.extend
        )
        by_id = {entity._attr_unique_id: entity for entity in result}
        self.assertEqual(by_id["1234567890:fronius-action"].native_value, 3.5)
        grid = by_id["1234567890:fronius-action:grid_power"]
        self.assertEqual((grid.native_value, grid._attr_native_unit_of_measurement), (-1.2, "kW"))
        production = by_id["1234567890:fronius-action:production_total"]
        self.assertEqual(production._attr_state_class, "total_increasing")
        self.assertEqual(production._attr_device_class, "energy")
        charge = by_id["1234567890:fronius-action:battery_level"]
        self.assertEqual((charge.native_value, charge._attr_device_class), (76, "battery"))

    async def test_hourcounter_uses_seconds_even_when_config_displays_hours(self) -> None:
        control = LoxoneControl(
            uuid="counter", uuid_action="counter-action", name="Pump", type="Hourcounter",
            states={name: name for name in ("active", "total", "remaining", "stateUnit")},
        )
        bridge = _FakeBridge(
            [control], {"active": 1, "total": 12_000, "remaining": 3_600, "stateUnit": 2}
        )
        entities = sensor_module._build_control_sensor_entities(bridge, control)
        runtime, remaining = entities
        self.assertEqual((runtime.native_value, runtime.native_unit_of_measurement), (12_000, "s"))
        self.assertEqual(runtime.state_class, "total_increasing")
        self.assertEqual((remaining.native_value, remaining._attr_native_unit_of_measurement), (3600, "s"))

    def test_virtual_text_keeps_leading_zeros_and_previous_entity_id(self) -> None:
        control = LoxoneControl(
            uuid="text", uuid_action="text-action", name="Code", type="InfoOnlyText",
            details={"format": "%.2f kW"}, states={"text": "text-state"},
        )
        bridge = _FakeBridge([control], {"text-state": "007"})
        [entity] = sensor_module._build_control_sensor_entities(bridge, control)
        self.assertEqual(entity.native_value, "007")
        self.assertIsNone(entity.native_unit_of_measurement)
        self.assertEqual(entity._attr_unique_id, "1234567890:text-action:text")

    def test_ventilation_lock_keeps_setting_read_only_and_exposes_air_measurements(self) -> None:
        states = ("speed", "humidityIndoor", "airQualityIndoor", "presenceMin", "absenceMin", "overwriteUntil")
        control = LoxoneControl(
            uuid="vent", uuid_action="vent-action", name="Ventilation", type="Ventilation",
            details={"hasPresenceMin": True}, states={name: name for name in states},
        )
        bridge = _FakeBridge(
            [control],
            {"speed": 25, "humidityIndoor": 41, "airQualityIndoor": 720, "overwriteUntil": 1_700_000_000},
        )
        entities = sensor_module._build_control_sensor_entities(bridge, control)
        by_state = {entity._state_name: entity for entity in entities}
        self.assertNotIn("absenceMin", by_state)
        self.assertEqual(by_state["presenceMin"]._attr_native_unit_of_measurement, "%")
        self.assertEqual(by_state["airQualityIndoor"]._attr_native_unit_of_measurement, "ppm")
        self.assertEqual(by_state["overwriteUntil"].native_value.isoformat(), "2023-11-14T22:13:20+00:00")

    def test_energy_flow_monitor_maps_published_node_to_configured_name_and_units(self) -> None:
        control = LoxoneControl(
            uuid="flow", uuid_action="flow-action", name="Energy flow", type="EnergyFlowMonitor",
            details={"actualFormat": "%.3f kW", "nodes": [
                {"title": "House load", "actualEfmState": "actual0", "nodes": [{"title": "Child"}]},
                {"title": "Not published", "actualEfmState": "actual1"},
            ]},
            states={"actual0": "load", "Gpwr": "grid"},
        )
        bridge = _FakeBridge([control], {"load": 1.72, "grid": -0.56})
        entities = sensor_module._build_control_sensor_entities(bridge, control)
        by_state = {entity._state_name: entity for entity in entities}
        self.assertNotIn("actual1", by_state)
        self.assertEqual(by_state["actual0"]._attr_name, "Energy flow House load")
        self.assertEqual(by_state["actual0"]._attr_native_unit_of_measurement, "kW")
        self.assertEqual(by_state["Gpwr"].native_value, -0.56)
        self.assertEqual(by_state["Gpwr"]._attr_device_class, "power")

    async def test_info_only_analog_energy_primary_sensor_is_total_increasing(self) -> None:
        control = LoxoneControl(
            uuid="energy-uuid",
            uuid_action="energy-action",
            name="Grid Import",
            type="InfoOnlyAnalog",
            details={"format": "%.3f kWh"},
            states={
                "active": "state-enabled",
                "value": "state-energy",
            },
        )
        bridge = _FakeBridge([control], {"state-enabled": 1, "state-energy": 1254.8})
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertIsInstance(entity, sensor_module.LoxonePrimarySensor)
        self.assertEqual(entity.native_value, 1254.8)
        self.assertEqual(entity.native_unit_of_measurement, "kWh")
        self.assertEqual(entity.device_class, sensor_module.SensorDeviceClass.ENERGY)
        self.assertEqual(
            entity.state_class,
            sensor_module.SensorStateClass.TOTAL_INCREASING,
        )

    async def test_handled_cover_control_does_not_create_access_state_sensors(self) -> None:
        control = LoxoneControl(
            uuid="cover-uuid",
            uuid_action="cover-action",
            name="Zaluzja",
            type="Jalousie",
            states={
                "position": "state-position",
                "deviceState": "state-device-state",
                "autoState": "state-auto-state",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-position": 0.5,
                "state-device-state": 0,
                "state-auto-state": 1,
            },
        )
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(entities, [])

    async def test_primary_sensor_actual_energy_state_is_measurement(self) -> None:
        control = LoxoneControl(
            uuid="energy-actual-uuid",
            uuid_action="energy-actual-action",
            name="Grid Actual",
            type="InfoOnlyAnalog",
            details={"actualFormat": "%.2f kWh"},
            states={
                "actual": "state-actual-energy",
            },
        )
        bridge = _FakeBridge([control], {"state-actual-energy": 2.4})
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities: list = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.native_unit_of_measurement, "kWh")
        # HA only allows cumulative state classes for its ENERGY device class.
        self.assertIsNone(entity.device_class)
        self.assertEqual(
            entity.state_class,
            sensor_module.SensorStateClass.MEASUREMENT,
        )


if __name__ == "__main__":
    unittest.main()
