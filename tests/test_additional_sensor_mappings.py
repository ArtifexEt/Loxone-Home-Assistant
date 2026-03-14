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
        self.assertIsInstance(entity, sensor_module.LoxonePrimarySensor)
        self.assertEqual(entity.native_value, 21.5)
        self.assertEqual(entity.native_unit_of_measurement, "°")

    async def test_window_monitor_maps_to_primary_sensor(self) -> None:
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
                "state-window-states": "open,closed,tilted",
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
        self.assertIsInstance(entity, sensor_module.LoxonePrimarySensor)
        self.assertEqual(entity.native_value, 2.0)
        self.assertIsNone(entity.native_unit_of_measurement)

    async def test_info_only_analog_energy_primary_sensor_is_total_increasing(self) -> None:
        control = LoxoneControl(
            uuid="energy-uuid",
            uuid_action="energy-action",
            name="Grid Import",
            type="InfoOnlyAnalog",
            details={"format": "%.3f kWh"},
            states={
                "value": "state-energy",
            },
        )
        bridge = _FakeBridge([control], {"state-energy": 1254.8})
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
        self.assertIsInstance(entity, sensor_module.LoxonePrimarySensor)
        self.assertEqual(entity.native_value, 1254.8)
        self.assertEqual(entity.native_unit_of_measurement, "kWh")
        self.assertEqual(entity.device_class, sensor_module.SensorDeviceClass.ENERGY)
        self.assertEqual(
            entity.state_class,
            sensor_module.SensorStateClass.TOTAL_INCREASING,
        )

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
        self.assertEqual(entity.native_unit_of_measurement, "kWh")
        self.assertEqual(entity.device_class, sensor_module.SensorDeviceClass.ENERGY)
        self.assertEqual(
            entity.state_class,
            sensor_module.SensorStateClass.MEASUREMENT,
        )


if __name__ == "__main__":
    unittest.main()
