"""Tests for Loxone cover mappings."""

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

    class CoverDeviceClass:
        BLIND = "blind"
        CURTAIN = "curtain"
        GARAGE = "garage"
        GATE = "gate"
        SHUTTER = "shutter"
        WINDOW = "window"

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
    cover.CoverDeviceClass = CoverDeviceClass
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
    def __init__(self, bridge: _FakeBridge) -> None:
        self.runtime_data = bridge


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

    async def test_moving_gate_reverses_direction_on_supported_miniserver(self) -> None:
        control = LoxoneControl(
            uuid="gate-uuid", uuid_action="gate-action", name="Garage",
            type="Gate", details={"animation": 0}, states={"active": "moving"},
        )
        values = {"moving": -1}
        bridge = _FakeBridge([control], values)
        bridge.software_version = "17.0.0.0"
        gate = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(gate.device_class, "garage")
        await gate.async_open_cover()
        values["moving"] = 1
        await gate.async_close_cover()
        self.assertEqual(
            bridge.commands, [("gate-action", "forceOpen"), ("gate-action", "forceClose")]
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
        entry = _FakeConfigEntry(bridge)
        entities: list = []

        await cover_module.async_setup_entry(
            object(),
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

    async def test_jalousie_with_shutter_animation_does_not_offer_tilt(self) -> None:
        control = LoxoneControl(
            uuid="shade-uuid",
            uuid_action="shade-action",
            name="Zacienienie Taras",
            type="Jalousie",
            states={"position": "state-position", "shadePosition": "state-shade"},
            details={"animation": 1},
        )
        bridge = _FakeBridge([control], {"state-position": 20})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "shutter")
        self.assertIsNone(entity.current_cover_tilt_position)
        self.assertFalse(
            entity.supported_features & cover_module.CoverEntityFeature.SET_TILT_POSITION
        )

    async def test_jalousie_animation_zero_without_hints_defaults_to_blind(self) -> None:
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

        self.assertEqual(entity.device_class, "blind")

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

    async def test_explicit_name_wins_over_conflicting_icon_hint(self) -> None:
        control = LoxoneControl(
            uuid="curtain-name-uuid",
            uuid_action="curtain-name-action",
            name="Zaslony Salon",
            type="Jalousie",
            states={"position": "state-position"},
            details={"animation": 1},
            icon="IconsFilled/roller-shutter-1.svg",
        )
        bridge = _FakeBridge([control], {"state-position": 40})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertEqual(entity.device_class, "curtain")

    async def test_window_uses_direction_and_window_commands(self) -> None:
        control = LoxoneControl(
            uuid="window-uuid",
            uuid_action="window-action",
            name="Okno",
            type="Window",
            states={"targetPosition": "state-target-position", "direction": "state-direction"},
        )
        bridge = _FakeBridge([control], {"state-target-position": 0.73, "state-direction": -1})
        entity = cover_module.LoxoneCoverEntity(bridge, control)

        self.assertIsNone(entity.current_cover_position)
        self.assertEqual(entity.extra_state_attributes["target_position"], 73)
        self.assertEqual(entity.device_class, "window")
        self.assertFalse(entity.is_opening)
        self.assertTrue(entity.is_closing)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.OPEN)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.CLOSE)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.STOP)
        self.assertTrue(entity.supported_features & cover_module.CoverEntityFeature.SET_POSITION)
        self.assertFalse(
            entity.supported_features & cover_module.CoverEntityFeature.SET_TILT_POSITION
        )
        await entity.async_open_cover()
        await entity.async_close_cover()
        await entity.async_set_cover_position(position=35)
        await entity.async_stop_cover()
        self.assertEqual(
            bridge.commands,
            [
                ("window-action", "fullopen"),
                ("window-action", "fullclose"),
                ("window-action", "moveToPosition/35"),
                ("window-action", "stop"),
            ],
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
        entry = _FakeConfigEntry(bridge)
        entities: list = []

        await cover_module.async_setup_entry(
            object(),
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "window-action")


if __name__ == "__main__":
    unittest.main()
