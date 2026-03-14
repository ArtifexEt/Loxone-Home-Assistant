"""Tests for Loxone entity-to-device mapping."""

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

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers

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
models = load_integration_module("custom_components.loxone_home_assistant.models")
entity = load_integration_module("custom_components.loxone_home_assistant.entity")
LoxoneControl = models.LoxoneControl
LoxoneEntity = entity.LoxoneEntity
miniserver_device_identifier = entity.miniserver_device_identifier
control_device_identifier = entity.control_device_identifier
control_entity_unique_id = entity.control_entity_unique_id
miniserver_device_info = entity.miniserver_device_info


class _FakeBridge:
    serial = "1234567890"
    miniserver_name = "Dom"
    server_model = "Miniserver"
    software_version = "14.6.8.22"
    available = True


class _FakeMiniserverEntity(LoxoneEntity):
    def uses_miniserver_device(self) -> bool:
        return True


class _FakeBridgeB:
    serial = "B0987654321"
    miniserver_name = "Dom B"
    server_model = "Miniserver"
    software_version = "14.6.8.22"
    available = True


class EntityDeviceInfoTests(unittest.TestCase):
    """Verify entity-device registry mapping."""

    def test_control_entities_use_own_device_with_miniserver_parent(self) -> None:
        control = LoxoneControl(
            uuid="thermo-uuid",
            uuid_action="thermo-action",
            name="Termostat",
            type="IRoomControllerV2",
            room_name="Salon",
        )

        info = LoxoneEntity(_FakeBridge(), control).device_info

        self.assertEqual(
            info["identifiers"],
            {control_device_identifier("1234567890", "thermo-action")},
        )
        self.assertEqual(
            info["via_device"],
            miniserver_device_identifier("1234567890"),
        )
        self.assertEqual(info["manufacturer"], "Loxone")
        self.assertEqual(info["model"], "IRoomControllerV2")
        self.assertEqual(info["name"], "Termostat")
        self.assertEqual(info["suggested_area"], "Salon")

    def test_miniserver_device_info_is_stable(self) -> None:
        info = miniserver_device_info(_FakeBridge())

        self.assertEqual(
            info["identifiers"],
            {miniserver_device_identifier("1234567890")},
        )
        self.assertEqual(info["name"], "Dom")
        self.assertEqual(info["model"], "Miniserver")
        self.assertEqual(info["sw_version"], "14.6.8.22")

    def test_miniserver_device_info_uses_server_model(self) -> None:
        bridge = _FakeBridge()
        bridge.server_model = "Miniserver Compact"
        info = miniserver_device_info(bridge)
        self.assertEqual(info["model"], "Miniserver Compact")

    def test_entity_can_opt_in_to_miniserver_device(self) -> None:
        control = LoxoneControl(
            uuid="system-uuid",
            uuid_action="system-action",
            name="System",
            type="System",
        )

        info = _FakeMiniserverEntity(_FakeBridge(), control).device_info

        self.assertEqual(
            info["identifiers"],
            {miniserver_device_identifier("1234567890")},
        )

    def test_control_entity_unique_id_includes_miniserver_serial(self) -> None:
        self.assertEqual(
            control_entity_unique_id("1234567890", "thermo-action"),
            "1234567890:thermo-action",
        )
        self.assertEqual(
            control_entity_unique_id("1234567890", "thermo-action", "total value"),
            "1234567890:thermo-action:total_value",
        )

    def test_same_uuid_action_on_two_miniservers_has_distinct_entity_unique_ids(self) -> None:
        control = LoxoneControl(
            uuid="same-uuid",
            uuid_action="same-action",
            name="Control",
            type="Switch",
        )
        entity_a = LoxoneEntity(_FakeBridge(), control)
        entity_b = LoxoneEntity(_FakeBridgeB(), control)

        self.assertNotEqual(entity_a._attr_unique_id, entity_b._attr_unique_id)


if __name__ == "__main__":
    unittest.main()
