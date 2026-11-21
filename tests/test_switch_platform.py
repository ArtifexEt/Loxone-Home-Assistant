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
        self._values = {}
        self.commands: list[tuple[str, str]] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    async def async_send_action(self, uuid_action, command):
        self.commands.append((uuid_action, command))


class _FakeConfigEntry:
    def __init__(self, bridge: _FakeBridge, options: dict) -> None:
        self.runtime_data = bridge
        self.options = options


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
switch_module = load_integration_module("custom_components.loxone_home_assistant.switch")
LoxoneControl = models.LoxoneControl


class SwitchPlatformSplitTests(unittest.IsolatedAsyncioTestCase):
    """Verify child on/off lights are not duplicated as switch entities."""

    async def test_intercom_exposes_stateful_mute_only_when_state_is_published(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Intercom",
            type="IntercomV2",
            states={"Muted": "state-muted"},
        )
        other_intercom = LoxoneControl(
            uuid="other-uuid",
            uuid_action="other-action",
            name="Intercom 2",
            type="IntercomV2",
            states={"bell": "state-bell"},
        )
        bluetooth = LoxoneControl(
            uuid="audio-uuid",
            uuid_action="audio-action",
            name="Audio",
            type="AudioZoneV2",
            states={"bluetooth": "state-bluetooth"},
        )
        blind = LoxoneControl(
            uuid="blind-uuid",
            uuid_action="blind-action",
            name="Blind",
            type="Jalousie",
            details={"isAutomatic": True},
            states={"autoActive": "state-blind"},
        )
        bridge = _FakeBridge([intercom, other_intercom, bluetooth, blind])
        entities = []
        await switch_module.async_setup_entry(object(), _FakeConfigEntry(bridge, {}), entities.extend)

        self.assertCountEqual(
            [entity._attr_unique_id for entity in entities],
            [
                "1234567890:intercom-action:doorbell_mute",
                "1234567890:audio-action:bluetooth",
                "1234567890:blind-action:automatic_shading",
            ],
        )
        mute = next(entity for entity in entities if entity.control is intercom)
        self.assertEqual(mute._attr_unique_id, "1234567890:intercom-action:doorbell_mute")
        self.assertEqual(mute.relevant_state_uuids(), ("state-muted",))
        self.assertIsNone(mute.is_on)
        await mute.async_turn_on()
        bridge._values["state-muted"] = 1
        self.assertTrue(mute.is_on)
        await mute.async_turn_off()
        bridge._values["state-muted"] = 0
        self.assertFalse(mute.is_on)
        self.assertEqual(
            bridge.commands,
            [("intercom-action", "mute/1"), ("intercom-action", "mute/0")],
        )

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
            bridge,
            {const.CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS: True},
        )
        entities = []

        await switch_module.async_setup_entry(
            object(),
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
            bridge,
            {const.CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS: False},
        )
        entities = []

        await switch_module.async_setup_entry(
            object(),
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "child-switch-action")

    async def test_car_charger_switch_uses_charge_commands(self) -> None:
        control = LoxoneControl(
            uuid="charger-uuid",
            uuid_action="charger-action",
            name="Wallbox",
            type="CarCharger",
            states={"charging": "state-charging"},
        )
        bridge = _FakeBridge([control])
        entity = switch_module.LoxoneSwitchEntity(bridge, control)

        await entity.async_turn_on()
        await entity.async_turn_off()

        self.assertEqual(
            bridge.commands,
            [("charger-action", "charge/on"), ("charger-action", "charge/off")],
        )

    async def test_audio_bluetooth_only_maps_controllable_player(self) -> None:
        control = LoxoneControl(
            uuid="audio-uuid",
            uuid_action="audio-action",
            name="Audioserver",
            type="AudioZoneV2",
            states={"bluetooth": "bt"},
        )
        locked = LoxoneControl(
            uuid="locked-audio-uuid",
            uuid_action="locked-audio-action",
            name="Controlled by Loxone logic",
            type="AudioZoneV2",
            states={"bluetooth": "locked-bt"},
            details={"parameterConnections": 1 << 1},
        )
        bridge = _FakeBridge([control, locked])
        bridge._values["bt"] = 1
        entities = []
        await switch_module.async_setup_entry(object(), _FakeConfigEntry(bridge, {}), entities.extend)

        self.assertEqual(len(entities), 1)
        bluetooth = entities[0]
        self.assertIs(bluetooth.is_on, True)
        await bluetooth.async_turn_off()
        bridge._values["bt"] = 0
        self.assertIs(bluetooth.is_on, False)
        await bluetooth.async_turn_on()
        self.assertEqual(bridge.commands, [("audio-action", "bluetooth/0"), ("audio-action", "bluetooth/1")])

    async def test_automatic_shading_switch_reflects_active_mode(self) -> None:
        control = LoxoneControl(
            uuid="blind-uuid",
            uuid_action="blind-action",
            name="Blind",
            type="Jalousie",
            details={"isAutomatic": True},
            states={"autoActive": "program", "autoAllowed": "allowed"},
        )
        bridge = _FakeBridge([control])
        bridge._values.update({"program": 0, "allowed": 1})
        switches = []
        await switch_module.async_setup_entry(object(), _FakeConfigEntry(bridge, {}), switches.extend)

        self.assertEqual(len(switches), 1)
        switch = switches[0]
        self.assertFalse(switch.is_on)
        await switch.async_turn_on()
        await switch.async_turn_off()
        self.assertEqual(
            bridge.commands,
            [("blind-action", "auto"), ("blind-action", "NoAuto")],
        )

    def test_timed_switch_tracks_remaining_delay(self) -> None:
        control = LoxoneControl(
            uuid="timed-uuid",
            uuid_action="timed-action",
            name="Stair light",
            type="TimedSwitch",
            states={"deactivationDelayTotal": "full", "deactivationDelay": "remaining"},
        )
        bridge = _FakeBridge([control])
        entity = switch_module.LoxoneSwitchEntity(bridge, control)
        bridge._values["full"] = 60
        self.assertIsNone(entity.is_on)

        for raw_delay, expected in ((0, False), (23, True), ("-1", True)):
            with self.subTest(raw_delay=raw_delay):
                bridge._values["remaining"] = raw_delay
                self.assertIs(entity.is_on, expected)


if __name__ == "__main__":
    unittest.main()
