"""Tests for Loxone doorbell binary sensor behavior."""

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

    binary_sensor.BinarySensorEntity = BinarySensorEntity

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

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

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
binary_sensor_module = load_integration_module(
    "custom_components.loxone_home_assistant.binary_sensor"
)
LoxoneControl = models.LoxoneControl


class DoorbellBinarySensorTests(unittest.IsolatedAsyncioTestCase):
    """Verify doorbell states are exposed as first-class binary sensors."""

    async def test_setup_adds_doorbell_for_handled_switch_control(self) -> None:
        switch_control = LoxoneControl(
            uuid="switch-uuid",
            uuid_action="switch-action",
            name="Wejscie",
            type="Switch",
            states={
                "active": "state-active",
                "bell": "state-bell",
            },
        )
        bridge = _FakeBridge(
            [switch_control],
            {
                "state-active": 0,
                "state-bell": 1,
            },
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertIsInstance(
            entities[0], binary_sensor_module.LoxoneDoorbellBinaryEntity
        )
        self.assertTrue(entities[0].is_on)

    async def test_setup_avoids_diagnostic_binary_for_supported_intercom(self) -> None:
        unsupported_control = LoxoneControl(
            uuid="unknown-uuid",
            uuid_action="unknown-action",
            name="Wideodomofon",
            type="Intercom",
            states={
                "bell": "state-bell",
                "alarm": "state-alarm",
            },
        )
        bridge = _FakeBridge(
            [unsupported_control],
            {
                "state-bell": 0,
                "state-alarm": 1,
            },
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertTrue(
            any(
                isinstance(entity, binary_sensor_module.LoxoneDoorbellBinaryEntity)
                for entity in entities
            )
        )

    async def test_setup_adds_intercom_proximity_and_call_binary_entities(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "bell": "state-bell",
                "proximity": "state-proximity",
                "callActive": "state-call",
            },
        )
        bridge = _FakeBridge(
            [intercom],
            {
                "state-bell": 0,
                "state-proximity": 1,
                "state-call": 1,
            },
        )
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        role_by_name = {
            entity._attr_name: entity  # noqa: SLF001 - test-only inspection
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneIntercomStateBinaryEntity)
        }
        self.assertIn("Furtka Proximity", role_by_name)
        self.assertIn("Furtka Call", role_by_name)
        self.assertTrue(role_by_name["Furtka Proximity"].is_on)
        self.assertTrue(role_by_name["Furtka Call"].is_on)


if __name__ == "__main__":
    unittest.main()
