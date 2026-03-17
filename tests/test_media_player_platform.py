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
        BROWSE_MEDIA = 1 << 14

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
LoxoneMediaServer = models.LoxoneMediaServer
LoxoneAudioZoneEntity = media_player_module.LoxoneAudioZoneEntity
build_audio_tts_command = media_player_module.build_audio_tts_command
resolve_audio_tts_target_uuid_action = media_player_module.resolve_audio_tts_target_uuid_action


class _FakeBridge:
    serial = "1234567890"
    available = True

    def __init__(self, controls, values, media_servers=None):
        self.controls = controls
        self._values = values
        self.media_servers_by_uuid_action = media_servers or {}
        self.commands: list[tuple[str, str]] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    def control_for_uuid_action(self, uuid_action):
        return next(
            (control for control in self.controls if control.uuid_action == uuid_action),
            None,
        )

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

    def _central_audio_zone(self) -> LoxoneControl:
        return LoxoneControl(
            uuid="central-audio-uuid",
            uuid_action="central-audio-action",
            name="Central Audio",
            type="CentralAudioZone",
            states={
                "playState": "state-central-play",
                "power": "state-central-power",
                "volume": "state-central-volume",
                "mute": "state-central-mute",
                "source": "state-central-source",
                "sourceList": "state-central-source-list",
            },
        )

    def _audio_zone_v2_event_style(self) -> LoxoneControl:
        return LoxoneControl(
            uuid="audio-event-uuid",
            uuid_action="audio-event-action",
            name="Event Audio",
            type="AudioZoneV2",
            states={
                "mode": "state-mode",
                "power": "state-power",
                "plshuffle": "state-shuffle",
                "plrepeat": "state-repeat",
                "time": "state-progress",
                "duration": "state-duration",
                "sourceName": "state-source",
                "zoneFavorites": "state-zone-favorites",
                "coverurl": "state-cover",
            },
        )

    async def test_setup_adds_audio_zone_controls_only(self) -> None:
        audio = self._audio_zone_v2()
        central = self._central_audio_zone()
        switch = LoxoneControl(
            uuid="switch-uuid",
            uuid_action="switch-action",
            name="Pompa",
            type="Switch",
            states={"active": "state-switch"},
        )
        bridge = _FakeBridge([audio, central, switch], {})
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await media_player_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 2)
        self.assertEqual(entities[0].control.uuid_action, "audio-action")
        self.assertEqual(entities[1].control.uuid_action, "central-audio-action")

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

    def test_audio_zone_v2_idle_playstate_stays_idle_even_when_power_reports_off(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge(
            [control],
            {
                "state-play": 0,
                "state-power": 0,
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        self.assertEqual(entity.state, media_player_module.MediaPlayerState.IDLE)

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
        await entity.async_media_stop()
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
                ("audio-action", "stop"),
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

    async def test_play_media_stop_routes_to_stop_command(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge([control], {})
        entity = LoxoneAudioZoneEntity(bridge, control)

        await entity.async_play_media("stop", "")

        self.assertEqual(bridge.commands, [("audio-action", "stop")])

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

    async def test_audio_zone_v2_browse_media_exposes_sources_directory(self) -> None:
        control = self._audio_zone_v2()
        control.states["source"] = "state-source"
        control.states["sourceList"] = "state-source-list"
        bridge = _FakeBridge(
            [control],
            {
                "state-source": 1,
                "state-source-list": (
                    "{'items':[{'slot':1,'name':'Radio'},{'slot':2,'name':'News'}]}"
                ),
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        root = await entity.async_browse_media()
        sources = await entity.async_browse_media("directory", "audio-action:sources")

        self.assertTrue(entity.supported_features & media_player_module.FEATURE_BROWSE_MEDIA)
        self.assertEqual(root.title, "Salon Audio")
        self.assertEqual([child.title for child in root.children], ["Sources"])
        self.assertEqual([child.title for child in sources.children], ["Radio", "News"])
        self.assertTrue(all(child.can_play for child in sources.children))
        self.assertEqual(
            [child.media_content_type for child in sources.children],
            ["source", "source"],
        )

    def test_audio_zone_v2_event_style_states_are_supported(self) -> None:
        control = self._audio_zone_v2_event_style()
        bridge = _FakeBridge(
            [control],
            {
                "state-mode": "resume",
                "state-power": "on",
                "state-shuffle": 1,
                "state-repeat": 3,
                "state-progress": 21,
                "state-duration": 245,
                "state-source": "Jazz",
                "state-zone-favorites": {
                    "zoneFavorites": [
                        {"slot": 1, "name": "Radio"},
                        {"slot": 2, "name": "Jazz"},
                    ]
                },
                "state-cover": "https://example.invalid/cover.jpg",
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        self.assertEqual(entity.state, media_player_module.MediaPlayerState.PLAYING)
        self.assertTrue(entity.shuffle)
        self.assertEqual(entity.repeat, "one")
        self.assertEqual(entity.media_position, 21.0)
        self.assertEqual(entity.media_duration, 245.0)
        self.assertEqual(entity.source_list, ["Radio", "Jazz"])
        self.assertEqual(entity.source, "Jazz")
        self.assertEqual(entity.media_image_url, "https://example.invalid/cover.jpg")
        self.assertEqual(entity.extra_state_attributes["play_state"], 2)

    async def test_audio_zone_v2_tts_escapes_forward_slash(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge([control], {})
        entity = LoxoneAudioZoneEntity(bridge, control)

        await entity.async_play_media("tts", "Hall/Entry")

        self.assertEqual(bridge.commands, [("audio-action", "tts/Hall%2FEntry")])

    async def test_audio_zone_tts_routes_to_matched_audio_server(self) -> None:
        control = self._audio_zone_v2()
        control.details["audioServerHost"] = "audioserver.lan:7091"
        media_server = LoxoneMediaServer(
            uuid_action="media-server-action",
            name="AudioServer",
            host="audioserver.lan:7091",
            states={},
        )
        bridge = _FakeBridge(
            [control],
            {},
            media_servers={media_server.uuid_action: media_server},
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        await entity.async_play_media("tts", "Alarm test", extra={"volume": 35})

        self.assertEqual(bridge.commands, [("media-server-action", "tts/Alarm test/35")])

    def test_build_audio_tts_command_keeps_entity_encoding_rules(self) -> None:
        self.assertEqual(build_audio_tts_command("Hall/Entry", 35), "tts/Hall%2FEntry/35")
        self.assertEqual(build_audio_tts_command("Alarm test"), "tts/Alarm test")

    def test_resolve_audio_tts_target_uuid_action_prefers_matched_media_server(self) -> None:
        control = self._audio_zone_v2()
        control.details["audioServerHost"] = "audioserver.lan:7091"
        media_server = LoxoneMediaServer(
            uuid_action="media-server-action",
            name="AudioServer",
            host="audioserver.lan:7091",
            states={},
        )
        bridge = _FakeBridge(
            [control],
            {},
            media_servers={media_server.uuid_action: media_server},
        )

        self.assertEqual(
            resolve_audio_tts_target_uuid_action(bridge, control),
            "media-server-action",
        )

    def test_media_position_updated_at_changes_only_on_updates(self) -> None:
        control = self._audio_zone_v2()
        bridge = _FakeBridge(
            [control],
            {
                "state-play": 2,
                "state-progress": 21,
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)
        entity.async_write_ha_state = lambda: None  # type: ignore[attr-defined]

        self.assertIsNone(entity.media_position_updated_at)
        entity._handle_bridge_update()
        first_timestamp = entity.media_position_updated_at
        self.assertIsNotNone(first_timestamp)
        self.assertEqual(entity.media_position_updated_at, first_timestamp)

    async def test_central_audio_zone_supports_commands_events_and_tts_volume(self) -> None:
        control = self._central_audio_zone()
        bridge = _FakeBridge(
            [control],
            {
                "state-central-source": 2,
                "state-central-source-list": (
                    "{'items':[{'slot':1,'name':'Radio'},{'slot':2,'name':'Jazz'}]}"
                ),
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        await entity.async_media_play()
        await entity.async_media_pause()
        await entity.async_set_volume_level(0.25)
        await entity.async_volume_up()
        await entity.async_volume_down()
        await entity.async_mute_volume(True)
        await entity.async_select_source("Jazz")
        await entity.async_play_media("tts", "Alarm test", extra={"volume": 35})
        await entity.async_play_media("bell", "")
        await entity.async_play_media("command", "selectedcontrols/1,2/alarm")

        self.assertEqual(
            bridge.commands,
            [
                ("central-audio-action", "play"),
                ("central-audio-action", "pause"),
                ("central-audio-action", "volume/25"),
                ("central-audio-action", "volup"),
                ("central-audio-action", "voldown"),
                ("central-audio-action", "mute/1"),
                ("central-audio-action", "playZoneFav/2"),
                ("central-audio-action", "tts/Alarm test/35"),
                ("central-audio-action", "bell"),
                ("central-audio-action", "selectedcontrols/1,2/alarm"),
            ],
        )

    def test_central_audio_zone_aggregates_state_from_linked_audio_zones(self) -> None:
        central = self._central_audio_zone()
        central.states = {}
        child = self._audio_zone_v2()
        child.uuid = "child-audio-uuid"
        child.uuid_action = "child-audio-action"
        child.name = "Kuchnia Audio"
        child.parent_uuid_action = central.uuid_action

        bridge = _FakeBridge(
            [central, child],
            {
                "state-play": 2,
                "state-power": 1,
            },
        )
        entity = LoxoneAudioZoneEntity(bridge, central)

        self.assertEqual(entity.state, media_player_module.MediaPlayerState.PLAYING)
        self.assertEqual(entity.extra_state_attributes["linked_audio_zone_count"], 1)
        self.assertEqual(entity.extra_state_attributes["active_audio_zone_count"], 1)
        self.assertEqual(
            entity.extra_state_attributes["active_audio_zones"],
            ["Kuchnia Audio"],
        )
        self.assertIn("state-play", set(entity.relevant_state_uuids()))

    async def test_central_audio_zone_browse_media_and_group_members_follow_linked_zones(self) -> None:
        central = self._central_audio_zone()
        central.states = {
            "source": "state-central-source",
            "sourceList": "state-central-source-list",
        }
        child = self._audio_zone_v2()
        child.uuid = "child-audio-uuid"
        child.uuid_action = "child-audio-action"
        child.name = "Kuchnia Audio"
        child.parent_uuid_action = central.uuid_action

        bridge = _FakeBridge(
            [child, central],
            {
                "state-central-source": 1,
                "state-central-source-list": "{'items':[{'slot':1,'name':'Radio'}]}",
                "state-play": 2,
                "state-power": 1,
            },
        )
        entry = _FakeConfigEntry("entry-central")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await media_player_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        entities_by_uuid_action = {entity.control.uuid_action: entity for entity in entities}
        central_entity = entities_by_uuid_action["central-audio-action"]
        child_entity = entities_by_uuid_action["child-audio-action"]
        central_entity.entity_id = "media_player.central_audio"
        child_entity.entity_id = "media_player.kuchnia_audio"

        root = await central_entity.async_browse_media()
        linked = await central_entity.async_browse_media(
            "directory",
            "central-audio-action:linked_zones",
        )

        self.assertEqual(
            central_entity.group_members,
            ["media_player.central_audio", "media_player.kuchnia_audio"],
        )
        self.assertEqual(
            child_entity.group_members,
            ["media_player.central_audio", "media_player.kuchnia_audio"],
        )
        self.assertEqual(
            [child.title for child in root.children],
            ["Sources", "Linked Zones"],
        )
        self.assertEqual([child.title for child in linked.children], ["Kuchnia Audio"])
        self.assertEqual(
            central_entity.extra_state_attributes["group_member_entity_ids"],
            ["media_player.central_audio", "media_player.kuchnia_audio"],
        )
        self.assertEqual(
            central_entity.extra_state_attributes["group_member_names"],
            ["Central Audio", "Kuchnia Audio"],
        )

    def test_audio_zone_uses_media_server_states_and_matches_by_host(self) -> None:
        control = self._audio_zone_v2()
        control.states.pop("serverState", None)
        control.details["audioServerHost"] = "audioserver.lan:7091"

        media_server = LoxoneMediaServer(
            uuid_action="media-server-action",
            name="AudioServer",
            host="audioserver.lan:7091",
            mac="AABBCCDDEEFF",
            states={
                "serverState": "state-media-server",
                "connState": "state-media-conn",
                "certificateValid": "state-media-cert",
                "host": "state-media-host",
            },
        )
        bridge = _FakeBridge(
            [control],
            {
                "state-media-server": 2,
                "state-media-conn": 1,
                "state-media-cert": 1,
                "state-media-host": "audioserver.lan:7091",
            },
            media_servers={media_server.uuid_action: media_server},
        )
        entity = LoxoneAudioZoneEntity(bridge, control)

        attrs = entity.extra_state_attributes
        self.assertEqual(attrs["server_state"], 2)
        self.assertEqual(attrs["audio_server_name"], "AudioServer")
        self.assertEqual(attrs["audio_server_uuid_action"], "media-server-action")
        self.assertEqual(attrs["audio_server_host"], "audioserver.lan:7091")
        self.assertEqual(attrs["audio_server_conn_state"], 1)
        self.assertTrue(attrs["audio_server_certificate_valid"])
        self.assertEqual(attrs["audio_server_mac"], "AABBCC****FF")
        self.assertTrue(attrs["audio_server_local_hint"])
        self.assertIn("state-media-server", set(entity.relevant_state_uuids()))

    def test_audio_zone_can_match_media_server_by_mac_hint(self) -> None:
        control = self._audio_zone_v2()
        control.details["audioServerMac"] = "aa-bb-cc-dd-ee-ff"

        media_server = LoxoneMediaServer(
            uuid_action="media-server-action",
            name="AudioServer",
            host="198.51.100.25:7091",
            mac="AABBCCDDEEFF",
            states={},
        )
        bridge = _FakeBridge(
            [control],
            {},
            media_servers={media_server.uuid_action: media_server},
        )

        entity = LoxoneAudioZoneEntity(bridge, control)
        attrs = entity.extra_state_attributes

        self.assertEqual(attrs["audio_server_uuid_action"], "media-server-action")
        self.assertEqual(attrs["audio_server_mac"], "AABBCC****FF")


if __name__ == "__main__":
    unittest.main()
