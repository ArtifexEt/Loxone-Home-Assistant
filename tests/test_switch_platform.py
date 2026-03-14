"""Tests for Loxone switch/light platform split."""

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

    switch = sys.modules["homeassistant.components.switch"]

    class SwitchEntity:
        pass

    switch.SwitchEntity = SwitchEntity

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

    def async_get(_hass):
        return object()

    def async_entries_for_config_entry(_registry, _entry_id):
        return []

    device_registry.DeviceInfo = DeviceInfo
    device_registry.async_get = async_get
    device_registry.async_entries_for_config_entry = async_entries_for_config_entry
    sys.modules["homeassistant.helpers.device_registry"] = device_registry

    entity = types.ModuleType("homeassistant.helpers.entity")

    class Entity:
        pass

    entity.Entity = Entity
    sys.modules["homeassistant.helpers.entity"] = entity


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls):
        self.controls = controls

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, _control, _state_name):
        return None

    def async_send_action(self, _uuid_action, _command):
        raise NotImplementedError


class _FakeConfigEntry:
    def __init__(self, entry_id: str, options: dict) -> None:
        self.entry_id = entry_id
        self.options = options


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
switch_module = load_integration_module("custom_components.loxone_home_assistant.switch")
LoxoneControl = models.LoxoneControl


class SwitchPlatformSplitTests(unittest.IsolatedAsyncioTestCase):
    """Verify child on/off lights are not duplicated as switch entities."""

    async def test_switch_platform_skips_controller_child_switches_when_light_export_enabled(self) -> None:
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
            name="Lampa nocna",
            type="Switch",
            states={"active": "state-child-active"},
            parent_uuid_action="ctrl-action",
        )
        top_level_switch = LoxoneControl(
            uuid="switch-uuid",
            uuid_action="switch-action",
            name="Pompa",
            type="Switch",
            states={"active": "state-switch-active"},
        )
        bridge = _FakeBridge([controller, child_switch, top_level_switch])
        entry = _FakeConfigEntry(
            "entry-1",
            {const.CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS: True},
        )
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []

        await switch_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "switch-action")

    async def test_switch_platform_keeps_controller_child_switches_when_light_export_disabled(self) -> None:
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
            name="Lampa nocna",
            type="Switch",
            states={"active": "state-child-active"},
            parent_uuid_action="ctrl-action",
        )
        bridge = _FakeBridge([controller, child_switch])
        entry = _FakeConfigEntry(
            "entry-1",
            {const.CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS: False},
        )
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities = []

        await switch_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "child-switch-action")


if __name__ == "__main__":
    unittest.main()
