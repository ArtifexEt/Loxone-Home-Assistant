"""Tests for Loxone AudioZone media_player behavior."""

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


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
media_player_module = load_integration_module(
    "custom_components.loxone_home_assistant.media_player"
)
LoxoneControl = models.LoxoneControl
LoxoneAudioZoneEntity = media_player_module.LoxoneAudioZoneEntity


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


class MediaPlayerPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify AudioZone entities are mapped to Home Assistant media_player."""

    def _audio_zone_v2(self) -> LoxoneControl:
        return LoxoneControl(
            uuid="audio-uuid",
            uuid_action="audio-action",
            name="Salon Audio",
            type="AudioZoneV2",
            states={
                "playState": "state-play",
                "power": "state-power",
                "volume": "state-volume",
                "songName": "state-title",
                "artist": "state-artist",
                "album": "state-album",
                "shuffle": "state-shuffle",
                "repeat": "state-repeat",
                "duration": "state-duration",
                "progress": "state-progress",
            },
        )

    async def test_setup_adds_audio_zone_controls_only(self) -> None:
        audio = self._audio_zone_v2()
        switch = LoxoneControl(
            uuid="switch-uuid",
            uuid_action="switch-action",
            name="Pompa",
            type="Switch",
            states={"active": "state-switch"},
        )
        bridge = _FakeBridge([audio, switch], {})
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await media_player_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].control.uuid_action, "audio-action")

    def test_state_volume_and_metadata_are_exposed(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge(
            [control],
            {
                "state-play": 2,
                "state-power": 1,
                "state-volume": 45,
                "state-title": "Song X",
                "state-artist": "Artist Y",
                "state-album": "Album Z",
                "state-shuffle": 1,
                "state-repeat": 1,
                "state-duration": 245,
                "state-progress": 22,
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        self.assertEqual(entity.state, media_player_module.MediaPlayerState.PLAYING)
        self.assertAlmostEqual(entity.volume_level, 0.45)
        self.assertEqual(entity.media_title, "Song X")
        self.assertEqual(entity.media_artist, "Artist Y")
        self.assertEqual(entity.media_album_name, "Album Z")
        self.assertTrue(entity.shuffle)
        self.assertEqual(entity.repeat, "all")
        self.assertEqual(entity.media_duration, 245.0)
        self.assertEqual(entity.media_position, 22.0)

    def test_state_falls_back_to_off_when_power_is_off(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge(
            [control],
            {
                "state-play": 2,
                "state-power": 0,
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        self.assertEqual(entity.state, media_player_module.MediaPlayerState.OFF)

    def test_repeat_mode_value_2_is_treated_as_all_for_compatibility(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge(
            [control],
            {
                "state-repeat": 2,
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        self.assertEqual(entity.repeat, "all")

    async def test_audio_zone_v2_commands_match_expected_actions(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge([control], {})
        entity = LoxoneAudioZoneEntity(bridge, control)

        await entity.async_media_play()
        await entity.async_media_pause()
        await entity.async_media_next_track()
        await entity.async_media_previous_track()
        await entity.async_set_volume_level(0.63)
        await entity.async_volume_up()
        await entity.async_volume_down()
        await entity.async_turn_on()
        await entity.async_turn_off()
        await entity.async_set_shuffle(True)
        await entity.async_set_repeat("one")
        await entity.async_media_seek(37)

        self.assertEqual(
            bridge.commands,
            [
                ("audio-action", "play"),
                ("audio-action", "pause"),
                ("audio-action", "next"),
                ("audio-action", "prev"),
                ("audio-action", "volume/63"),
                ("audio-action", "volUp"),
                ("audio-action", "volDown"),
                ("audio-action", "on"),
                ("audio-action", "off"),
                ("audio-action", "shuffle/1"),
                ("audio-action", "repeat/3"),
                ("audio-action", "progress/37"),
            ],
        )

    async def test_audio_zone_legacy_uses_volstep_source_shuffle_repeat(self) -> None:
        control = LoxoneControl(
            uuid="audio-legacy-uuid",
            uuid_action="audio-legacy-action",
            name="Kuchnia Audio",
            type="AudioZone",
            states={
                "playState": "state-play",
                "volumeStep": "state-step",
                "source": "state-source",
                "sourceList": "state-source-list",
                "shuffle": "state-shuffle",
                "repeat": "state-repeat",
                "progress": "state-progress",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-play": 1,
                "state-step": 5,
                "state-source": 2,
                "state-shuffle": 0,
                "state-repeat": 0,
                "state-progress": 12,
                "state-source-list": (
                    "{'getroomfavs_result':[{'items':["
                    "{'slot':1,'name':'Radio'},"
                    "{'slot':2,'name':'Spotify'}"
                    "]}]}"
                ),
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        self.assertEqual(entity.source_list, ["Radio", "Spotify"])
        self.assertEqual(entity.source, "Spotify")
        self.assertFalse(entity.shuffle)
        self.assertEqual(entity.repeat, "off")

        await entity.async_volume_up()
        await entity.async_volume_down()
        await entity.async_select_source("Radio")
        await entity.async_play_media("source", "2")
        await entity.async_set_shuffle(True)
        await entity.async_set_repeat("all")
        await entity.async_media_seek(99)

        self.assertEqual(
            bridge.commands,
            [
                ("audio-legacy-action", "volstep/5"),
                ("audio-legacy-action", "volstep/-5"),
                ("audio-legacy-action", "source/1"),
                ("audio-legacy-action", "source/2"),
                ("audio-legacy-action", "shuffle/1"),
                ("audio-legacy-action", "repeat/1"),
                ("audio-legacy-action", "progress/99"),
            ],
        )

    async def test_audio_zone_v2_play_media_supports_source_and_tts(self) -> None:
        control = self._audio_zone_v2()
        control.states["source"] = "state-source"
        control.states["sourceList"] = "state-source-list"
        control.states["tts"] = "state-tts"

        bridge = _FakeBridge(
            [control],
            {
                "state-source": 1,
                "state-source-list": "{'items':[{'slot':1,'name':'Radio'},{'slot':2,'name':'News'}]}",
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        await entity.async_play_media("source", "News")
        await entity.async_play_media("tts", "Dzień dobry")

        self.assertEqual(
            bridge.commands,
            [
                ("audio-action", "playZoneFav/2"),
                ("audio-action", "tts/Dzień dobry"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
