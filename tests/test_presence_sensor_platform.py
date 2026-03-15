"""Tests for PresenceDetector analog sensor support."""

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
        ILLUMINANCE = "illuminance"
        SOUND_PRESSURE = "sound_pressure"

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


class PresenceSensorPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify PresenceDetector exposes analog sensor entities."""

    async def test_setup_creates_illuminance_and_sound_sensors(self) -> None:
        control = LoxoneControl(
            uuid="presence-uuid",
            uuid_action="presence-action",
            name="Przedsionek Presence",
            type="PresenceDetector",
            states={
                "active": "state-active",
                "illumination": "state-illumination",
                "noiseLevel": "state-noise",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-active": 1,
                "state-illumination": 124.2,
                "state-noise": 38.4,
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

        self.assertEqual(len(entities), 2)
        by_kind = {entity._kind: entity for entity in entities}
        illuminance_entity = by_kind[sensor_module.PRESENCE_KIND_ILLUMINANCE]
        sound_entity = by_kind[sensor_module.PRESENCE_KIND_SOUND_LEVEL]

        self.assertEqual(illuminance_entity.native_value, 124.2)
        self.assertEqual(illuminance_entity.native_unit_of_measurement, "lx")
        self.assertEqual(
            illuminance_entity.device_class,
            sensor_module.SensorDeviceClass.ILLUMINANCE,
        )
        self.assertEqual(
            illuminance_entity.state_class,
            sensor_module.SensorStateClass.MEASUREMENT,
        )

        self.assertEqual(sound_entity.native_value, 38.4)
        self.assertEqual(sound_entity.native_unit_of_measurement, "dB")
        self.assertEqual(
            sound_entity.device_class,
            sensor_module.SensorDeviceClass.SOUND_PRESSURE,
        )
        self.assertEqual(
            sound_entity.state_class,
            sensor_module.SensorStateClass.MEASUREMENT,
        )

    async def test_setup_handles_presence_detector_type_variants(self) -> None:
        control = LoxoneControl(
            uuid="presence-uuid",
            uuid_action="presence-action",
            name="Korytarz Presence",
            type="PresenceDetectorV2",
            states={
                "active": "state-active",
                "AmbientLight": "state-light",
                "acoustic": "state-acoustic",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-active": 0,
                "state-light": 80,
                "state-acoustic": 28,
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

        self.assertEqual(len(entities), 2)
        by_kind = {entity._kind: entity for entity in entities}
        self.assertIn(sensor_module.PRESENCE_KIND_ILLUMINANCE, by_kind)
        self.assertIn(sensor_module.PRESENCE_KIND_SOUND_LEVEL, by_kind)

    async def test_sound_sensor_falls_back_to_db_when_control_format_is_lux(self) -> None:
        control = LoxoneControl(
            uuid="presence-uuid",
            uuid_action="presence-action",
            name="Salon Presence",
            type="PresenceDetector",
            states={
                "active": "state-active",
                "illuminance": "state-illumination",
                "soundLevel": "state-sound",
            },
            details={"format": "%.1f lux"},
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-active": 1,
                "state-illumination": 250,
                "state-sound": 42,
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

        by_kind = {entity._kind: entity for entity in entities}
        self.assertEqual(
            by_kind[sensor_module.PRESENCE_KIND_ILLUMINANCE].native_unit_of_measurement,
            "lx",
        )
        self.assertEqual(
            by_kind[sensor_module.PRESENCE_KIND_SOUND_LEVEL].native_unit_of_measurement,
            "dB",
        )


if __name__ == "__main__":
    unittest.main()
