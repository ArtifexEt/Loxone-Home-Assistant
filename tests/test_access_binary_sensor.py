"""Tests for access-oriented binary sensor support."""

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

    def __init__(self, controls, values=None):
        self.controls = controls
        self._values = values or {}

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
binary_sensor_module = load_integration_module(
    "custom_components.loxone_home_assistant.binary_sensor"
)
LoxoneControl = models.LoxoneControl


class AccessBinarySensorTests(unittest.IsolatedAsyncioTestCase):
    """Verify access granted/denied entities are created correctly."""

    async def test_access_control_type_exposes_granted_and_denied_entities(self) -> None:
        control = LoxoneControl(
            uuid="access-uuid",
            uuid_action="access-action",
            name="Front Door Keypad",
            type="AccessControl",
            states={
                "access": "state-access",
                "wrongCode": "state-wrong",
                "alarm": "state-alarm",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-access": 1,
                "state-wrong": 0,
                "state-alarm": 1,
            },
        )
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        access_entities = [
            entity
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneAccessBinaryEntity)
        ]
        self.assertEqual(len(access_entities), 2)
        by_result = {
            entity.extra_state_attributes["access_result"]: entity
            for entity in access_entities
        }
        self.assertTrue(by_result["granted"].is_on)
        self.assertFalse(by_result["denied"].is_on)
        self.assertEqual(
            by_result["granted"].extra_state_attributes["access_state"], "access"
        )
        self.assertEqual(
            by_result["denied"].extra_state_attributes["access_state"], "wrongCode"
        )

        diagnostic_entities = [
            entity
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneDiagnosticBinaryEntity)
        ]
        self.assertEqual(len(diagnostic_entities), 1)
        self.assertEqual(diagnostic_entities[0]._state_name, "alarm")

    async def test_matching_granted_denied_pair_enables_access_support_for_unknown_type(self) -> None:
        control = LoxoneControl(
            uuid="custom-uuid",
            uuid_action="custom-action",
            name="Front Entry",
            type="CustomAccessBlock",
            states={
                "granted": "state-granted",
                "denied": "state-denied",
            },
        )
        bridge = _FakeBridge([control], {"state-granted": 0, "state-denied": 1})
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        access_entities = [
            entity
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneAccessBinaryEntity)
        ]
        self.assertEqual(len(access_entities), 2)

    async def test_single_access_like_state_is_ignored_for_regular_non_access_control(self) -> None:
        control = LoxoneControl(
            uuid="switch-uuid",
            uuid_action="switch-action",
            name="Pump",
            type="Switch",
            states={"granted": "state-granted"},
        )
        bridge = _FakeBridge([control], {"state-granted": 1})
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        access_entities = [
            entity
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneAccessBinaryEntity)
        ]
        self.assertEqual(access_entities, [])

    async def test_climate_control_does_not_expose_j_locked_binary_sensor(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Salon",
            type="IRoomControllerV2",
            states={
                "jLocked": "state-j-locked",
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-j-locked": {"locked": 2, "reason": "Central lock"},
                "state-temp-actual": 21.5,
                "state-temp-target": 22.0,
            },
        )
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        lock_entities = [
            entity
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneControlLockBinaryEntity)
        ]
        self.assertEqual(lock_entities, [])

    async def test_climate_control_falls_back_to_is_locked_when_present(self) -> None:
        control = LoxoneControl(
            uuid="climate-uuid",
            uuid_action="climate-action",
            name="Gabinet",
            type="IRoomController",
            states={
                "isLocked": "state-is-locked",
                "tempActual": "state-temp-actual",
                "tempTarget": "state-temp-target",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-is-locked": 1,
                "state-temp-actual": 20.0,
                "state-temp-target": 21.0,
            },
        )
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        lock_entities = [
            entity
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneControlLockBinaryEntity)
        ]
        self.assertEqual(len(lock_entities), 1)
        self.assertTrue(lock_entities[0].is_on)
        self.assertEqual(lock_entities[0].extra_state_attributes["lock_state"], "isLocked")

    async def test_jalousie_control_does_not_expose_j_locked_binary_sensor(self) -> None:
        control = LoxoneControl(
            uuid="jalousie-uuid",
            uuid_action="jalousie-action",
            name="Roleta Salon",
            type="Jalousie",
            states={
                "position": "state-position",
                "jLocked": "state-j-locked",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-position": 55,
                "state-j-locked": {"locked": 1, "reason": "Visualization lock"},
            },
        )
        entry = types.SimpleNamespace(entry_id="entry-1", runtime_data=bridge, options={})
        hass = types.SimpleNamespace(data={})
        entities = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        lock_entities = [
            entity
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneControlLockBinaryEntity)
        ]
        self.assertEqual(lock_entities, [])


class ProtocolBinarySensorTests(unittest.TestCase):
    def test_alarm_reads_alarm_level_and_never_armed_or_delay(self) -> None:
        for kind in ("Alarm", "SmokeAlarm"):
            with self.subTest(kind=kind):
                control = LoxoneControl(
                    uuid="alarm", uuid_action="alarm-action", type=kind, name="Alarm",
                    states={"armed": "armed", "nextLevelDelayTotal": "delay", "level": "level"},
                )
                values = {"armed": 1, "delay": 30}
                bridge = _FakeBridge([control], values)
                [entity] = binary_sensor_module._build_control_binary_entities(bridge, control)
                self.assertEqual(entity._attr_unique_id, "1234567890:alarm-action")
                self.assertEqual(entity.relevant_state_uuids(), ["level"])
                self.assertIsNone(entity.is_on)
                values["level"] = 0
                self.assertFalse(entity.is_on)
                values["level"] = 2
                self.assertTrue(entity.is_on)

    def test_smoke_alarm_only_exposes_configured_causes(self) -> None:
        control = LoxoneControl(
            uuid="alarm", uuid_action="alarm-action", type="SmokeAlarm", name="Alarm",
            states={"level": "level", "alarmCause": "cause"}, details={"availableAlarms": 3},
        )
        bridge = _FakeBridge([control], {"level": 2, "cause": 2})
        entities = binary_sensor_module._build_control_binary_entities(bridge, control)
        causes = {
            entity._attr_device_class: entity.is_on
            for entity in entities
            if isinstance(entity, binary_sensor_module.LoxoneBitmaskBinaryEntity)
        }
        self.assertEqual(causes, {"smoke": False, "moisture": True})

    def test_window_monitor_distinguishes_closed_tilted_and_unknown(self) -> None:
        control = LoxoneControl(
            uuid="monitor", uuid_action="monitor-action", type="WindowMonitor", name="Monitor",
            states={"windowStates": "states"},
            details={"windows": [
                {"name": "Bedroom", "uuid": "bedroom"},
                {"name": "Hall"},
                {"name": "Dining"},
            ]},
        )
        bridge = _FakeBridge([control], {"states": "9,18,0"})
        closed, tilted, unknown = binary_sensor_module._build_control_binary_entities(bridge, control)
        self.assertEqual(closed._attr_unique_id, "1234567890:monitor-action:opening_bedroom")
        self.assertFalse(closed.is_on)
        self.assertTrue(closed.extra_state_attributes["locked"])
        self.assertTrue(tilted.is_on)
        self.assertTrue(tilted.extra_state_attributes["tilted"])
        self.assertFalse(tilted.extra_state_attributes["locked"])
        self.assertIsNone(unknown.is_on)

    def test_presence_reports_occupancy_and_live_lock_reason(self) -> None:
        control = LoxoneControl(
            uuid="presence", uuid_action="presence-action", type="PresenceDetector", name="Presence",
            states={"activeSince": "since", "active": "active", "locked": "locked", "infoText": "info"},
        )
        bridge = _FakeBridge([control], {"since": 123456, "locked": 1, "info": "Logic input"})
        lock, presence = binary_sensor_module._build_control_binary_entities(bridge, control)
        self.assertIsNone(presence.is_on)
        self.assertEqual(presence._attr_device_class, "occupancy")
        self.assertTrue(lock.is_on)
        self.assertEqual(lock.extra_state_attributes["lock_reason"], "Logic input")
        self.assertEqual(lock.relevant_state_uuids(), ["locked", "info"])

    def test_nfc_reader_connectivity_uses_only_offline_bit(self) -> None:
        control = LoxoneControl(
            uuid="keypad", uuid_action="reader-action", type="NfcCodeTouch", name="Keypad",
            states={"deviceState": "state"},
        )
        values = {"state": 4}
        bridge = _FakeBridge([control], values)
        [entity] = binary_sensor_module._build_control_binary_entities(bridge, control)
        self.assertEqual(entity._attr_device_class, "connectivity")
        self.assertTrue(entity.is_on)
        values["state"] = 5
        self.assertFalse(entity.is_on)


if __name__ == "__main__":
    unittest.main()
