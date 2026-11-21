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
        hass = types.SimpleNamespace(data={})
        entry = types.SimpleNamespace(entry_id="entry-id", runtime_data=bridge)

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
        hass = types.SimpleNamespace(data={})
        entry = types.SimpleNamespace(entry_id="entry-id", runtime_data=bridge)

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
        hass = types.SimpleNamespace(data={})
        entry = types.SimpleNamespace(entry_id="entry-id", runtime_data=bridge)

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
        hass = types.SimpleNamespace(data={})
        entry = types.SimpleNamespace(entry_id="entry-id", runtime_data=bridge)

        entities: list = []

        def _add_entities(new_entities):
            entities.extend(new_entities)

        await binary_sensor_module.async_setup_entry(hass, entry, _add_entities)

        self.assertEqual(len(entities), 1)
        self.assertFalse(entities[0].is_on)

    def test_power_unit_distinguishes_charge_level_deadline_and_power(self) -> None:
        control = self._build_control(
            type="PowerUnit",
            states={
                "batteryStateOfCharge": "battery",
                "supplyTimeRemaining": "deadline",
                "outputPower": "total",
                "CP1": "output-one",
            },
        )
        values = {"battery": 72, "deadline": 1_700_000_000, "total": 0.138, "output-one": 0.064}
        bridge = _FakeBridge([control], values)
        entities = sensor_module._build_control_sensor_entities(bridge, control)
        by_id = {entity._attr_unique_id.rsplit(":", 1)[-1]: entity for entity in entities}
        deadline = by_id["remaining_time"]
        self.assertEqual(deadline._attr_device_class, "timestamp")
        self.assertEqual(deadline.native_value.isoformat(), "2023-11-14T22:13:20+00:00")
        self.assertEqual(by_id["output_1_power"]._attr_native_unit_of_measurement, "kW")
        self.assertEqual(by_id["output_power"].native_value, 0.138)
        self.assertEqual(binary_sensor_module._build_control_binary_entities(bridge, control), [])
        values["deadline"] = 0
        self.assertIsNone(deadline.native_value)

    def test_power_unit_status_and_fuses_use_individual_bits(self) -> None:
        control = self._build_control(
            type="PowerUnit",
            states={"deviceInfo": "status", "fuse": "fuses"},
            details={"supplyMode": 1, "Outputs": ["Main (1)", "", "Fan (3)"]},
        )
        bridge = _FakeBridge([control], {"status": (1 << 9), "fuses": (1 << 2)})
        entities = binary_sensor_module._build_control_binary_entities(bridge, control)
        by_id = {entity._attr_unique_id.rsplit(":", 1)[-1]: entity.is_on for entity in entities}
        self.assertEqual(
            by_id, {"overcurrent": False, "overheating": True, "fuse_1_tripped": False, "fuse_3_tripped": True}
        )

    async def test_text_state_history_is_compacted_to_latest_entry(self) -> None:
        history_value = "|".join(
            (
                "2026-03-14 11:15:11 Wykryto obecność\x14Czujnik obecności Tree (Sypialnia)",
                "2026-03-14 11:22:10 Obecność zakończona\x14Ostatnia obecność: Czujnik obecności Tree (Sypialnia)",
                "2026-03-14 13:39:18 Overrun extended for current session due to quick presence reactivation\x14Czujnik obecności Tree (Sypialnia)",
            )
        )
        self.assertGreater(len(history_value), sensor_module.MAX_LENGTH_STATE_STATE)

        control = self._build_control(
            uuid="presence-uuid",
            uuid_action="presence-action",
            type="TextState",
            states={"value": "state-history"},
        )
        bridge = _FakeBridge([control], {"state-history": history_value})
        hass = types.SimpleNamespace(data={})
        entry = types.SimpleNamespace(entry_id="entry-id", runtime_data=bridge)
        entities: list = []

        await sensor_module.async_setup_entry(hass, entry, lambda new: entities.extend(new))

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(
            entity.native_value,
            (
                "2026-03-14 13:39:18 Overrun extended for current session due to quick "
                "presence reactivation - Czujnik obecności Tree (Sypialnia)"
            ),
        )
        self.assertLessEqual(
            len(entity.native_value),
            sensor_module.MAX_LENGTH_STATE_STATE,
        )


class SensorStateLengthSafetyTests(unittest.TestCase):
    """Verify helper logic keeps sensor text state within HA limits."""

    def test_plain_long_text_is_truncated_to_max_state_length(self) -> None:
        value = "x" * (sensor_module.MAX_LENGTH_STATE_STATE + 100)

        compacted = sensor_module._truncate_state_text(value)

        self.assertEqual(len(compacted), sensor_module.MAX_LENGTH_STATE_STATE)
        self.assertTrue(compacted.endswith("..."))

    def test_short_text_cleans_control_characters(self) -> None:
        value = "2026-03-14 11:15:11 Wykryto obecność\x14Czujnik obecności Tree (WC)"

        compacted = sensor_module._truncate_state_text(value)

        self.assertEqual(
            compacted,
            "2026-03-14 11:15:11 Wykryto obecność - Czujnik obecności Tree (WC)",
        )

    def test_non_string_values_are_serialized_safely(self) -> None:
        value = {
            "events": [
                "Tryb zasilania awaryjnego rozpoczęty",
                "Zasilanie awaryjne zakończone",
            ],
            "count": 2,
        }

        compacted = sensor_module._sensor_native_value(value)

        self.assertIsInstance(compacted, str)
        self.assertIn("\"count\":2", compacted)
        self.assertLessEqual(len(compacted), sensor_module.MAX_LENGTH_STATE_STATE)


if __name__ == "__main__":
    unittest.main()
