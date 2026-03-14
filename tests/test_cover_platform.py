"""Tests for extended Loxone cover mappings (Gate + UpDownLeftRight)."""

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

    cover = sys.modules["homeassistant.components.cover"]

    class CoverEntity:
        pass

    class CoverEntityFeature:
        OPEN = 1
        CLOSE = 2
        STOP = 4
        SET_POSITION = 8

    cover.CoverEntity = CoverEntity
    cover.CoverEntityFeature = CoverEntityFeature

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
        self.commands: list[tuple[str, str]] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    async def async_send_action(self, uuid_action, command):
        self.commands.append((uuid_action, command))


class _FakeConfigEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
cover_module = load_integration_module("custom_components.loxone_home_assistant.cover")
LoxoneControl = models.LoxoneControl


class CoverPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify Gate and UpDownLeftRight mappings."""

    async def test_gate_commands_use_open_close_stop(self) -> None:
        control = LoxoneControl(
            uuid="gate-uuid",
            uuid_action="gate-action",
            name="Brama",
            type="Gate",
            states={"position": "state-position"},
        )
        bridge = _FakeBridge([control], {"state-position": 0})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        await entity.async_open_cover()
        await entity.async_close_cover()
        await entity.async_stop_cover()

        self.assertEqual(
            bridge.commands,
            [("gate-action", "open"), ("gate-action", "close"), ("gate-action", "stop")],
        )

    async def test_updownleftright_digital_maps_to_cover_commands(self) -> None:
        control = LoxoneControl(
            uuid="udlr-uuid",
            uuid_action="udlr-action",
            name="Roleta",
            type="UpDownLeftRight",
            states={"active": "state-active"},
        )
        bridge = _FakeBridge([control], {"state-active": False})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        await entity.async_open_cover()
        await entity.async_close_cover()
        await entity.async_stop_cover()

        self.assertEqual(
            bridge.commands,
            [
                ("udlr-action", "UpOn"),
                ("udlr-action", "DownOn"),
                ("udlr-action", "UpOff"),
                ("udlr-action", "DownOff"),
            ],
        )

    async def test_updownleftright_analog_is_excluded_from_cover_setup(self) -> None:
        analog = LoxoneControl(
            uuid="udlr-analog-uuid",
            uuid_action="udlr-analog-action",
            name="Pozycja",
            type="UpDownLeftRight",
            states={"value": "state-value"},
        )
        gate = LoxoneControl(
            uuid="gate-uuid",
            uuid_action="gate-action",
            name="Brama",
            type="Gate",
            states={"position": "state-position"},
        )
        bridge = _FakeBridge([analog, gate], {})
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await cover_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "gate-action")


if __name__ == "__main__":
    unittest.main()
