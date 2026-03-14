"""Tests for Loxone light status behavior."""

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

    light = sys.modules["homeassistant.components.light"]
    light.ATTR_BRIGHTNESS = "brightness"
    light.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    light.ATTR_HS_COLOR = "hs_color"

    class ColorMode:
        HS = "hs"
        COLOR_TEMP = "color_temp"
        BRIGHTNESS = "brightness"
        ONOFF = "onoff"

    class LightEntity:
        pass

    light.ColorMode = ColorMode
    light.LightEntity = LightEntity

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

    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")

    def async_get(_hass):
        return object()

    def async_entries_for_config_entry(_registry, _entry_id):
        return []

    entity_registry.async_get = async_get
    entity_registry.async_entries_for_config_entry = async_entries_for_config_entry
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry

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
models = load_integration_module("custom_components.loxone_home_assistant.models")
light_module = load_integration_module("custom_components.loxone_home_assistant.light")
LoxoneControl = models.LoxoneControl
LoxoneLightEntity = light_module.LoxoneLightEntity


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls, values):
        self.controls = controls
        self._controls_by_action = {control.uuid_action: control for control in controls}
        self._values = values

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    def control_for_uuid_action(self, uuid_action):
        return self._controls_by_action.get(uuid_action)


class LightStatusTests(unittest.TestCase):
    """Verify grouped light status handling."""

    def test_controller_treats_active_switch_child_as_on(self) -> None:
        controller = LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
        )
        child_switch = LoxoneControl(
            uuid="child-uuid",
            uuid_action="child-action",
            name="Lampa",
            type="Switch",
            states={"active": "state-child-active"},
            parent_uuid_action="ctrl-action",
        )
        bridge = _FakeBridge(
            [controller, child_switch],
            {
                "state-moods": "0",
                "state-child-active": 1,
            },
        )

        entity = LoxoneLightEntity(bridge, controller)

        self.assertTrue(entity.is_on)
        self.assertIn("state-child-active", entity.relevant_state_uuids())

    def test_controller_detects_child_with_uuid_action_prefix(self) -> None:
        controller = LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
        )
        child_dimmer = LoxoneControl(
            uuid="child-dimmer-uuid",
            uuid_action="ctrl-action/1",
            name="Sufit",
            type="Dimmer",
            states={"position": "state-child-position"},
        )
        bridge = _FakeBridge(
            [controller, child_dimmer],
            {
                "state-moods": "0",
                "state-child-position": 65,
            },
        )

        entity = LoxoneLightEntity(bridge, controller)

        self.assertTrue(entity.is_on)
        self.assertIn("state-child-position", entity.relevant_state_uuids())
        self.assertTrue(light_module._is_child_light_control(bridge, child_dimmer))

    def test_child_switch_can_be_exposed_as_light_only_when_enabled(self) -> None:
        controller = LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
        )
        child_switch = LoxoneControl(
            uuid="child-switch-uuid",
            uuid_action="child-switch-action",
            name="Nocna",
            type="Switch",
            states={"active": "state-child-active"},
            parent_uuid_action="ctrl-action",
        )
        top_level_switch = LoxoneControl(
            uuid="top-switch-uuid",
            uuid_action="top-switch-action",
            name="Gniazdko",
            type="Switch",
            states={"active": "state-top-active"},
        )
        bridge = _FakeBridge([controller, child_switch, top_level_switch], {})

        self.assertTrue(light_module.should_expose_as_light(bridge, child_switch, True))
        self.assertFalse(light_module.should_expose_as_light(bridge, child_switch, False))
        self.assertFalse(light_module.should_expose_as_light(bridge, top_level_switch, True))

    def test_child_dimmer_respects_child_light_option(self) -> None:
        controller = LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
        )
        child_dimmer = LoxoneControl(
            uuid="child-dimmer-uuid",
            uuid_action="child-dimmer-action",
            name="Sufit",
            type="Dimmer",
            states={"position": "state-child-position"},
            parent_uuid_action="ctrl-action",
        )
        bridge = _FakeBridge([controller, child_dimmer], {})

        self.assertTrue(light_module.should_expose_as_light(bridge, child_dimmer, True))
        self.assertFalse(light_module.should_expose_as_light(bridge, child_dimmer, False))

    def test_controller_detects_child_referenced_in_details(self) -> None:
        controller = LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
            details={"masterValue": "child-action"},
        )
        child_dimmer = LoxoneControl(
            uuid="child-dimmer-uuid",
            uuid_action="child-action",
            name="Sufit",
            type="Dimmer",
            states={"position": "state-child-position"},
        )
        bridge = _FakeBridge(
            [controller, child_dimmer],
            {
                "state-moods": "0",
                "state-child-position": 40,
            },
        )

        entity = LoxoneLightEntity(bridge, controller)

        self.assertTrue(entity.is_on)
        self.assertIn("state-child-position", entity.relevant_state_uuids())
        self.assertTrue(light_module._is_child_light_control(bridge, child_dimmer))


if __name__ == "__main__":
    unittest.main()
