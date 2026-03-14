"""Tests for Loxone LightController mood select behavior."""

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

    select = sys.modules["homeassistant.components.select"]

    class SelectEntity:
        pass

    select.SelectEntity = SelectEntity

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
select_module = load_integration_module("custom_components.loxone_home_assistant.select")
LoxoneControl = models.LoxoneControl
LoxoneMoodSelectEntity = select_module.LoxoneMoodSelectEntity
LoxoneRadioOutputSelectEntity = select_module.LoxoneRadioOutputSelectEntity


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls, values):
        self.controls = controls
        self._controls_by_action = {control.uuid_action: control for control in controls}
        self._values = values
        self.commands: list[tuple[str, str]] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    def control_for_uuid_action(self, uuid_action):
        return self._controls_by_action.get(uuid_action)

    async def async_send_action(self, uuid_action, command):
        self.commands.append((uuid_action, command))


class MoodSelectTests(unittest.IsolatedAsyncioTestCase):
    """Verify mood select behavior."""

    def _controller(self) -> LoxoneControl:
        return LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
            details={
                "moodList": [
                    {"id": 12, "name": "Reading"},
                    {"id": 55, "name": "Evening"},
                ]
            },
        )

    def test_current_option_resolves_first_non_off_mood(self) -> None:
        controller = self._controller()
        bridge = _FakeBridge([controller], {"state-moods": "[0, 778, 55]"})
        entity = LoxoneMoodSelectEntity(bridge, controller)

        self.assertEqual(entity.current_option, "Evening")
        self.assertEqual(entity._attr_options[0], "Off")

    async def test_select_option_sends_change_to_command(self) -> None:
        controller = self._controller()
        bridge = _FakeBridge([controller], {"state-moods": "[12]"})
        entity = LoxoneMoodSelectEntity(bridge, controller)

        await entity.async_select_option("Reading")

        self.assertEqual(bridge.commands, [("ctrl-action", "changeTo/12")])

    async def test_radio_select_maps_outputs_and_reset_command(self) -> None:
        radio = LoxoneControl(
            uuid="radio-uuid",
            uuid_action="radio-action",
            name="Radio",
            type="Radio",
            states={"activeOutput": "state-output"},
            details={"outputs": {"0": "allOff", "1": "living room", "2": "kitchen"}},
        )
        bridge = _FakeBridge([radio], {"state-output": 2})
        entity = LoxoneRadioOutputSelectEntity(bridge, radio)

        self.assertEqual(entity.current_option, "kitchen")

        await entity.async_select_option("living room")
        await entity.async_select_option("allOff")

        self.assertEqual(
            bridge.commands,
            [("radio-action", "1"), ("radio-action", "reset")],
        )


if __name__ == "__main__":
    unittest.main()
