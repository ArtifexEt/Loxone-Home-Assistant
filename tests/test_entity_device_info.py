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

    @staticmethod
    def resolve_http_url(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        path = normalized if normalized.startswith("/") else f"/{normalized}"
        return f"https://mini.local{path}"


class _FakeMiniserverEntity(LoxoneEntity):
    def uses_miniserver_device(self) -> bool:
        return True


class _FakeBridgeB:
    serial = "B0987654321"
    miniserver_name = "Dom B"
    server_model = "Miniserver"
    software_version = "14.6.8.22"
    available = True

    @staticmethod
    def resolve_http_url(value: str | None) -> str | None:
        return _FakeBridge.resolve_http_url(value)


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

    def test_entity_exposes_loxone_icon_as_entity_picture(self) -> None:
        control = LoxoneControl(
            uuid="icon-uuid",
            uuid_action="icon-action",
            name="Noc",
            type="Switch",
            icon="IconsFilled/night-mode.svg",
        )

        icon_entity = LoxoneEntity(_FakeBridge(), control)

        self.assertEqual(
            icon_entity._attr_entity_picture,
            "https://mini.local/IconsFilled/night-mode.svg",
        )
        self.assertEqual(
            icon_entity.extra_state_attributes["loxone_icon"],
            "IconsFilled/night-mode.svg",
        )
        self.assertEqual(
            icon_entity.extra_state_attributes["loxone_icon_url"],
            "https://mini.local/IconsFilled/night-mode.svg",
        )


class InferUnitTests(unittest.TestCase):
    """Verify unit inference from format strings and state names."""

    @staticmethod
    def _control(*, details: dict | None = None) -> LoxoneControl:
        return LoxoneControl(
            uuid="sensor-uuid",
            uuid_action="sensor-action",
            name="Sensor",
            type="InfoOnlyAnalog",
            states={"value": "state-value"},
            details=details or {},
        )

    def test_infer_unit_handles_compact_percent_format(self) -> None:
        control = self._control(details={"format": "%.1f%%"})

        self.assertEqual(entity.infer_unit(control, "value"), "%")

    def test_infer_unit_handles_spaced_percent_format(self) -> None:
        control = self._control(details={"format": "%.1f %%"})

        self.assertEqual(entity.infer_unit(control, "value"), "%")

    def test_infer_unit_uses_case_insensitive_state_fallback(self) -> None:
        control = LoxoneControl(
            uuid="sensor-uuid",
            uuid_action="sensor-action",
            name="Sensor",
            type="InfoOnlyAnalog",
            states={"BatteryLevel": "state-battery"},
            details={},
        )

        self.assertEqual(entity.infer_unit(control, "BatteryLevel"), "%")

    def test_infer_unit_prefers_fixed_humidity_and_co2_units_over_generic_degree_format(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Climate",
            type="IRoomControllerV2",
            states={
                "humidity": "state-humidity",
                "co2": "state-co2",
            },
            details={"format": "%.1f °"},
        )

        self.assertEqual(entity.infer_unit(control, "humidity"), "%")
        self.assertEqual(entity.infer_unit(control, "co2"), "ppm")

    def test_infer_unit_keeps_temperature_format_override(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Climate",
            type="IRoomControllerV2",
            states={"tempActual": "state-temp"},
            details={"format": "%.1f °F"},
        )

        self.assertEqual(entity.infer_unit(control, "tempActual"), "°F")


if __name__ == "__main__":
    unittest.main()
