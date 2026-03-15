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
        OPEN_TILT = 16
        CLOSE_TILT = 32
        STOP_TILT = 64
        SET_TILT_POSITION = 128

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
    """Verify Gate, UpDownLeftRight and Jalousie mappings."""

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

    async def test_jalousie_with_lamella_states_exposes_tilt_support(self) -> None:
        control = LoxoneControl(
            uuid="jalousie-uuid",
            uuid_action="jalousie-action",
            name="Zaluzje",
            type="Jalousie",
            states={
                "position": "state-position",
                "shadePosition": "state-shade-position",
                "targetPositionLamelle": "state-target-lamella",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-position": 30,
                "state-shade-position": 25,
            },
        )
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "blind")
        self.assertEqual(entity.current_cover_position, 70)
        self.assertEqual(entity.current_cover_tilt_position, 75)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.SET_POSITION)
        self.assertTrue(
            entity.supported_features
            & cover_module.CoverEntityFeature.SET_TILT_POSITION
        )

        await entity.async_open_cover_tilt()
        await entity.async_close_cover_tilt()
        await entity.async_stop_cover_tilt()
        await entity.async_set_cover_tilt_position(tilt_position=20)

        self.assertEqual(
            bridge.commands,
            [
                ("jalousie-action", "manualLamelle/0"),
                ("jalousie-action", "manualLamelle/100"),
                ("jalousie-action", "stop"),
                ("jalousie-action", "manualLamelle/80"),
            ],
        )

    async def test_jalousie_icon_curtain_overrides_animation_based_blind_guess(self) -> None:
        control = LoxoneControl(
            uuid="curtain-icon-uuid",
            uuid_action="curtain-icon-action",
            name="Zacienienie",
            type="Jalousie",
            states={"position": "state-position"},
            details={"animation": 0},
            icon="IconsFilled/curtain-open.svg",
        )
        bridge = _FakeBridge([control], {"state-position": 50})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "curtain")

    async def test_jalousie_icon_blind_detects_blind(self) -> None:
        control = LoxoneControl(
            uuid="blind-icon-uuid",
            uuid_action="blind-icon-action",
            name="Zacienienie",
            type="Jalousie",
            states={"position": "state-position"},
            details={"animation": 1},
            icon="IconsFilled/roller-shutter-1.svg",
        )
        bridge = _FakeBridge([control], {"state-position": 50})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "blind")

    async def test_jalousie_with_animation_zero_enables_tilt_commands(self) -> None:
        control = LoxoneControl(
            uuid="jalousie-details-uuid",
            uuid_action="jalousie-details-action",
            name="Zaluzje Taras",
            type="Jalousie",
            states={"position": "state-position"},
            details={"animation": 0},
        )
        bridge = _FakeBridge([control], {"state-position": 0})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertTrue(
            entity.supported_features
            & cover_module.CoverEntityFeature.SET_TILT_POSITION
        )
        self.assertIsNone(entity.current_cover_tilt_position)

        await entity.async_set_cover_tilt_position(tilt_position=40)

        self.assertEqual(
            bridge.commands,
            [("jalousie-details-action", "manualLamelle/60")],
        )

    async def test_jalousie_named_zacienienie_defaults_to_curtain(self) -> None:
        control = LoxoneControl(
            uuid="shade-uuid",
            uuid_action="shade-action",
            name="Zacienienie Taras",
            type="Jalousie",
            states={"position": "state-position"},
            details={"animation": 1},
        )
        bridge = _FakeBridge([control], {"state-position": 20})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "curtain")

    async def test_jalousie_animation_zero_without_hints_defaults_to_curtain(self) -> None:
        control = LoxoneControl(
            uuid="animation-zero-uuid",
            uuid_action="animation-zero-action",
            name="Oslona",
            type="Jalousie",
            states={"position": "state-position"},
            details={"animation": 0},
        )
        bridge = _FakeBridge([control], {"state-position": 20})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "curtain")

    async def test_jalousie_named_roleta_detects_blind(self) -> None:
        control = LoxoneControl(
            uuid="roller-uuid",
            uuid_action="roller-action",
            name="Roleta Salon",
            type="Jalousie",
            states={"position": "state-position"},
            details={"animation": 1},
        )
        bridge = _FakeBridge([control], {"state-position": 20})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "blind")

    async def test_window_is_supported_and_uses_target_position_state(self) -> None:
        control = LoxoneControl(
            uuid="window-uuid",
            uuid_action="window-action",
            name="Okno",
            type="Window",
            states={"targetPosition": "state-target-position"},
        )
        bridge = _FakeBridge([control], {"state-target-position": 73})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.current_cover_position, 73)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.OPEN)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.CLOSE)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.STOP)
        self.assertFalse(
            entity.supported_features & cover_module.CoverEntityFeature.SET_TILT_POSITION
        )

    async def test_cover_setup_includes_window_controls(self) -> None:
        window = LoxoneControl(
            uuid="window-uuid",
            uuid_action="window-action",
            name="Okno",
            type="Window",
            states={"position": "state-position"},
        )
        bridge = _FakeBridge([window], {})
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await cover_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "window-action")


if __name__ == "__main__":
    unittest.main()
