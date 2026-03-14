"""Tests for PowerSupply support in sensor and binary_sensor platforms."""

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

    binary_sensor = sys.modules["homeassistant.components.binary_sensor"]

    class BinarySensorEntity:
        pass

    class BinarySensorDeviceClass:
        BATTERY_CHARGING = "battery_charging"

    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass

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
const_module = load_integration_module("custom_components.loxone_home_assistant.const")
models = load_integration_module("custom_components.loxone_home_assistant.models")
sensor_module = load_integration_module("custom_components.loxone_home_assistant.sensor")
binary_sensor_module = load_integration_module(
    "custom_components.loxone_home_assistant.binary_sensor"
)
LoxoneControl = models.LoxoneControl


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls, values):
        self.controls = controls
        self._values = values

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))


class PowerSupplyPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify native PowerSupply entity mapping."""

    @staticmethod
    def _build_control(**kwargs) -> LoxoneControl:
        defaults = dict(
            uuid="power-uuid",
            uuid_action="power-action",
            name="UPS",
            type="PowerSupply",
            states={},
            details={},
        )
        defaults.update(kwargs)
        return LoxoneControl(**defaults)

    async def test_sensor_platform_creates_battery_and_remaining_time_entities(self) -> None:
        control = self._build_control(
            states={
                "batteryLevel": "state-battery",
                "remainingTime": "state-remaining",
                "isCharging": "state-charging",
            },
            details={"remainingTimeFormat": "%.0f min"},
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-battery": 72,
                "state-remaining": 95,
                "state-charging": 1,
            },
        )
        hass = types.SimpleNamespace(
            data={const_module.DOMAIN: {"bridges": {"entry-id": bridge}}}
        )
        entry = types.SimpleNamespace(entry_id="entry-id")

        entities: list = []

        def _add_entities(new_entities):
            entities.extend(new_entities)

        await sensor_module.async_setup_entry(hass, entry, _add_entities)

        self.assertEqual(len(entities), 2)
        by_name = {entity._kind: entity for entity in entities}
        battery_entity = by_name[sensor_module.POWER_SUPPLY_KIND_BATTERY]
        time_entity = by_name[sensor_module.POWER_SUPPLY_KIND_REMAINING_TIME]

        self.assertEqual(battery_entity.native_value, 72.0)
        self.assertEqual(
            battery_entity.device_class, sensor_module.SensorDeviceClass.BATTERY
        )
        self.assertEqual(battery_entity.native_unit_of_measurement, "%")

        self.assertEqual(time_entity.native_value, 95.0)
        self.assertEqual(time_entity.native_unit_of_measurement, "min")
        self.assertEqual(
            time_entity.device_class, sensor_module.SensorDeviceClass.DURATION
        )

    async def test_sensor_platform_supports_battery_state_of_charge_and_supply_time_remaining(self) -> None:
        control = self._build_control(
            states={
                "BatteryStateOfCharge": "state-battery",
                "SupplyTimeRemaining": "state-remaining",
                "isCharging": "state-charging",
            },
            details={"supplyTimeRemainingFormat": "%.1f h"},
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-battery": 88,
                "state-remaining": 3,
                "state-charging": 0,
            },
        )
        hass = types.SimpleNamespace(
            data={const_module.DOMAIN: {"bridges": {"entry-id": bridge}}}
        )
        entry = types.SimpleNamespace(entry_id="entry-id")

        entities: list = []

        def _add_entities(new_entities):
            entities.extend(new_entities)

        await sensor_module.async_setup_entry(hass, entry, _add_entities)

        self.assertEqual(len(entities), 2)
        by_kind = {entity._kind: entity for entity in entities}
        battery_entity = by_kind[sensor_module.POWER_SUPPLY_KIND_BATTERY]
        time_entity = by_kind[sensor_module.POWER_SUPPLY_KIND_REMAINING_TIME]

        self.assertEqual(battery_entity.native_value, 88.0)
        self.assertEqual(battery_entity.native_unit_of_measurement, "%")
        self.assertEqual(
            battery_entity.device_class, sensor_module.SensorDeviceClass.BATTERY
        )

        self.assertEqual(time_entity.native_value, 3.0)
        self.assertEqual(time_entity.native_unit_of_measurement, "h")
        self.assertEqual(
            time_entity.device_class, sensor_module.SensorDeviceClass.DURATION
        )

    async def test_binary_sensor_platform_creates_charging_entity(self) -> None:
        control = self._build_control(
            states={
                "batteryLevel": "state-battery",
                "remainingTime": "state-remaining",
                "isCharging": "state-charging",
            }
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-battery": 50,
                "state-remaining": 80,
                "state-charging": 1,
            },
        )
        hass = types.SimpleNamespace(
            data={const_module.DOMAIN: {"bridges": {"entry-id": bridge}}}
        )
        entry = types.SimpleNamespace(entry_id="entry-id")

        entities: list = []

        def _add_entities(new_entities):
            entities.extend(new_entities)

        await binary_sensor_module.async_setup_entry(hass, entry, _add_entities)

        self.assertEqual(len(entities), 1)
        charging_entity = entities[0]
        self.assertTrue(charging_entity.is_on)
        self.assertEqual(
            charging_entity._attr_device_class,
            binary_sensor_module.BinarySensorDeviceClass.BATTERY_CHARGING,
        )

    async def test_charging_state_is_detected_for_non_standard_name(self) -> None:
        control = self._build_control(
            states={
                "Battery_Level": "state-battery",
                "TimeLeft": "state-left",
                "chargingNow": "state-charging",
            }
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-battery": 50,
                "state-left": 40,
                "state-charging": 0,
            },
        )
        hass = types.SimpleNamespace(
            data={const_module.DOMAIN: {"bridges": {"entry-id": bridge}}}
        )
        entry = types.SimpleNamespace(entry_id="entry-id")

        entities: list = []

        def _add_entities(new_entities):
            entities.extend(new_entities)

        await binary_sensor_module.async_setup_entry(hass, entry, _add_entities)

        self.assertEqual(len(entities), 1)
        self.assertFalse(entities[0].is_on)


if __name__ == "__main__":
    unittest.main()
