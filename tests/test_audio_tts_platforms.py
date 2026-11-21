"""Tests for TTS helper entities."""

from __future__ import annotations

import sys
import types
import unittest

from tests._loader import install_media_player_stubs, load_integration_module


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
        "media_player": "media_player",
        "number": "number",
        "select": "select",
        "sensor": "sensor",
        "switch": "switch",
        "text": "text",
    }.items():
        module = types.ModuleType(f"homeassistant.components.{module_name}")
        module.DOMAIN = domain
        sys.modules[f"homeassistant.components.{module_name}"] = module

    install_media_player_stubs()

    text = sys.modules["homeassistant.components.text"]

    class TextEntity:
        def async_write_ha_state(self) -> None:
            return None

    class TextMode:
        TEXT = "text"

    text.TextEntity = TextEntity
    text.TextMode = TextMode

    number = sys.modules["homeassistant.components.number"]

    class NumberEntity:
        def async_write_ha_state(self) -> None:
            return None

    number.NumberEntity = NumberEntity

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
    const.MAX_LENGTH_STATE_STATE = 255
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
        def async_write_ha_state(self) -> None:
            return None

    entity.Entity = Entity
    sys.modules["homeassistant.helpers.entity"] = entity


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls, values):
        self.controls = controls
        self._controls_by_action = {control.uuid_action: control for control in controls}
        self._values = values
        self.media_servers_by_uuid_action = {}

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    def control_for_uuid_action(self, uuid_action):
        return self._controls_by_action.get(uuid_action)


class _FakeConfigEntry:
    def __init__(self, bridge: _FakeBridge) -> None:
        self.runtime_data = bridge


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
text_module = load_integration_module("custom_components.loxone_home_assistant.text")
number_module = load_integration_module("custom_components.loxone_home_assistant.number")
LoxoneControl = models.LoxoneControl


class AudioTtsHelperPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify helper entities exposed for audio TTS."""

    def _audio_zone(self) -> LoxoneControl:
        return LoxoneControl(
            uuid="audio-uuid",
            uuid_action="audio-action",
            name="Korytarz",
            type="AudioZoneV2",
            states={
                "volume": "state-volume",
            },
        )

    async def test_text_platform_adds_tts_message_helpers(self) -> None:
        audio_zone = self._audio_zone()
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Intercom",
            type="IntercomV2",
        )
        bridge = _FakeBridge([audio_zone, intercom], {"state-volume": 35})
        entry = _FakeConfigEntry(bridge)
        entities: list = []

        await text_module.async_setup_entry(
            object(),
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        tts_entities = [
            entity
            for entity in entities
            if isinstance(entity, text_module.LoxoneAudioTtsMessageEntity)
        ]
        self.assertEqual(len(tts_entities), 1)
        tts_entity = tts_entities[0]
        self.assertEqual(tts_entity.native_value, "")
        self.assertEqual(tts_entity._attr_native_max, 120)

        await tts_entity.async_set_value("Alarm test")

        self.assertEqual(tts_entity.native_value, "Alarm test")
        intercom_entity = next(
            entity
            for entity in entities
            if isinstance(entity, text_module.LoxoneIntercomTtsMessageEntity)
        )
        self.assertEqual(intercom_entity._attr_native_max, 255)

    async def test_number_platform_only_adds_volume_helper_for_audio_zones(self) -> None:
        audio_zone = self._audio_zone()
        intercom = LoxoneControl(
            uuid="intercom-uuid", uuid_action="intercom-action", name="Intercom", type="IntercomV2"
        )
        bridge = _FakeBridge([audio_zone, intercom], {"state-volume": 35})
        entry = _FakeConfigEntry(bridge)
        entities: list = []

        await number_module.async_setup_entry(
            object(),
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        tts_entities = [
            entity
            for entity in entities
            if isinstance(entity, number_module.LoxoneAudioTtsVolumeEntity)
        ]
        self.assertEqual(entities, tts_entities)
        self.assertEqual(len(tts_entities), 1)
        tts_entity = tts_entities[0]
        self.assertEqual(tts_entity.native_value, 35.0)

        await tts_entity.async_set_native_value(75)

        self.assertEqual(tts_entity.native_value, 75.0)

    async def test_original_music_server_minimum_volume_is_writable(self) -> None:
        zone = self._audio_zone()
        zone.type = "AudioZone"
        zone.states["ttsVolume"] = "minimum-tts-volume"

        modern = self._audio_zone()
        modern.uuid_action = "audio-v2-action"
        modern.states["ttsVolume"] = "minimum-tts-volume"
        bridge = _FakeBridge([zone, modern], {"minimum-tts-volume": 0})
        commands = []

        async def send(uuid_action, command):
            commands.append((uuid_action, command))

        bridge.async_send_action = send
        entities = []
        await number_module.async_setup_entry(object(), _FakeConfigEntry(bridge), entities.extend)

        volumes = [entity for entity in entities if isinstance(entity, number_module.LoxoneMusicServerVolumeEntity)]
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0].native_value, 0)
        await volumes[0].async_set_native_value(24)
        self.assertEqual(commands, [("audio-action", "ttsvolume/24")])

    async def test_value_selector_uses_live_range_and_preserves_zero(self) -> None:
        control = LoxoneControl(
            uuid="selector-uuid",
            uuid_action="selector-action",
            name="Temperature offset",
            type="ValueSelector",
            states={"min": "min", "max": "max", "step": "step", "value": "value"},
        )
        bridge = _FakeBridge(
            [control], {"min": -6, "max": 0, "step": 0.5, "value": -2}
        )
        entry = _FakeConfigEntry(bridge)
        entities = []

        await number_module.async_setup_entry(object(), entry, entities.extend)

        self.assertEqual(len(entities), 1)
        number = entities[0]
        self.assertEqual(number.native_value, -2)
        self.assertEqual(number.native_min_value, -6)
        self.assertEqual(number.native_max_value, 0)
        self.assertEqual(number.native_step, 0.5)


if __name__ == "__main__":
    unittest.main()
