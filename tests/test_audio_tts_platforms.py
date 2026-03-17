"""Tests for AudioZone TTS helper entities."""

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

    media_player = sys.modules["homeassistant.components.media_player"]

    class MediaPlayerEntity:
        pass

    class MediaPlayerDeviceClass:
        SPEAKER = "speaker"

    class MediaPlayerState:
        OFF = "off"
        IDLE = "idle"
        PAUSED = "paused"
        PLAYING = "playing"

    class MediaPlayerEntityFeature:
        PAUSE = 1 << 0
        PLAY = 1 << 1
        STOP = 1 << 2
        NEXT_TRACK = 1 << 3
        PREVIOUS_TRACK = 1 << 4
        VOLUME_SET = 1 << 5
        VOLUME_STEP = 1 << 6
        TURN_ON = 1 << 7
        TURN_OFF = 1 << 8
        SELECT_SOURCE = 1 << 9
        PLAY_MEDIA = 1 << 10
        SHUFFLE_SET = 1 << 11
        REPEAT_SET = 1 << 12
        SEEK = 1 << 13

    class MediaType:
        MUSIC = "music"

    media_player.MediaPlayerEntity = MediaPlayerEntity
    media_player.MediaPlayerDeviceClass = MediaPlayerDeviceClass
    media_player.MediaPlayerState = MediaPlayerState
    media_player.MediaPlayerEntityFeature = MediaPlayerEntityFeature
    media_player.MediaType = MediaType

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
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


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

    async def test_text_platform_adds_audio_tts_message_helper(self) -> None:
        audio_zone = self._audio_zone()
        bridge = _FakeBridge([audio_zone], {"state-volume": 35})
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await text_module.async_setup_entry(
            hass,
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

        await tts_entity.async_set_value("Alarm test")

        self.assertEqual(tts_entity.native_value, "Alarm test")

    async def test_number_platform_adds_audio_tts_volume_helper(self) -> None:
        audio_zone = self._audio_zone()
        bridge = _FakeBridge([audio_zone], {"state-volume": 35})
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await number_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        tts_entities = [
            entity
            for entity in entities
            if isinstance(entity, number_module.LoxoneAudioTtsVolumeEntity)
        ]
        self.assertEqual(len(tts_entities), 1)
        tts_entity = tts_entities[0]
        self.assertEqual(tts_entity.native_value, 35.0)

        await tts_entity.async_set_native_value(75)

        self.assertEqual(tts_entity.native_value, 75.0)


if __name__ == "__main__":
    unittest.main()
