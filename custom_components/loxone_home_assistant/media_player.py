"""Media player platform for Loxone audio zones."""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from yarl import URL

from .const import MEDIA_PLAYER_CONTROL_TYPES
from .entity import (
    LoxoneEntity,
    coerce_bool,
    coerce_float,
    first_matching_state_name,
    normalize_state_name,
)
from .models import LoxoneControl, LoxoneMediaServer
from .protocol import deserialize_value, normalize_uuid
from .runtime import entry_bridge

PLAY_STATE_CANDIDATES = ("playState", "play_state", "playstate", "mode")
POWER_STATE_CANDIDATES = ("power", "active")
VOLUME_STATE_CANDIDATES = ("volume", "masterVolume")
VOLUME_STEP_CANDIDATES = ("volumeStep",)
MUTE_STATE_CANDIDATES = ("mute", "isMuted")
SHUFFLE_STATE_CANDIDATES = ("shuffle", "plshuffle")
REPEAT_STATE_CANDIDATES = ("repeat", "plrepeat")
PROGRESS_STATE_CANDIDATES = ("progress", "time")
DURATION_STATE_CANDIDATES = ("duration",)
SOURCE_STATE_CANDIDATES = ("source", "sourceName", "currentFavorite", "slot")
SOURCE_LIST_STATE_CANDIDATES = ("sourceList", "zoneFavorites", "favorites", "favourites")
TITLE_STATE_CANDIDATES = ("songName", "title", "station")
ARTIST_STATE_CANDIDATES = ("artist",)
ALBUM_STATE_CANDIDATES = ("album",)
STATION_STATE_CANDIDATES = ("station",)
GENRE_STATE_CANDIDATES = ("genre",)
IMAGE_STATE_CANDIDATES = ("cover", "coverurl")
SERVER_STATE_CANDIDATES = ("serverState",)
CLIENT_STATE_CANDIDATES = ("clientState",)
TTS_VOLUME_STATE_CANDIDATES = ("ttsVolume",)
MAX_VOLUME_STATE_CANDIDATES = ("maxVolume",)
VOLUME_ATTRIBUTES = {
    "alarm_volume": ("alarmVolume",),
    "bell_volume": ("bellVolume",),
    "buzzer_volume": ("buzzerVolume",),
    "tts_volume": TTS_VOLUME_STATE_CANDIDATES,
    "default_volume": ("defaultVolume",),
    "max_volume": MAX_VOLUME_STATE_CANDIDATES,
    "volume_step": VOLUME_STEP_CANDIDATES,
}
MEDIA_SERVER_CONN_STATE_CANDIDATES = ("connState", "connectionState")
MEDIA_SERVER_CERTIFICATE_STATE_CANDIDATES = ("certificateValid", "certValid")
MEDIA_SERVER_HOST_STATE_CANDIDATES = ("host",)
DEFAULT_AUDIO_TTS_MESSAGE = ""
DEFAULT_AUDIO_TTS_VOLUME = 60
MUSIC_SERVER_TYPE = 1
MUSIC_SERVER_PORT = 7091
MUSIC_SERVER_TIMEOUT = 5
LEGACY_TTS_MAX_LENGTH = 150
AUDIO_TTS_MAX_LENGTH = 400
_AUDIO_TTS_MESSAGES_ATTR = "_audio_tts_messages"
_AUDIO_TTS_VOLUMES_ATTR = "_audio_tts_volumes"
_MEDIA_PLAYER_ENTITIES_ATTR = "_loxone_media_player_entities"

AUDIO_ZONE_CONTROL_TYPE = "AudioZone"
AUDIO_ZONE_V2_CONTROL_TYPE = "AudioZoneV2"
CENTRAL_AUDIO_ZONE_CONTROL_TYPE = "CentralAudioZone"
CHILD_AUDIO_ZONE_CONTROL_TYPES = {AUDIO_ZONE_CONTROL_TYPE, AUDIO_ZONE_V2_CONTROL_TYPE}

STATE_UNKNOWN = -1
STATE_IDLE = 0
STATE_PAUSED = 1
STATE_PLAYING = 2
PLAY_STATE_OFF_NAMES = {"off", "offline"}

DEFAULT_VOLUME_STEP = 3
MIN_VOLUME_STEP = 1
MAX_VOLUME_STEP = 100

_MAC_NON_HEX_RE = re.compile(r"[^0-9A-Fa-f]")

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.PLAY_MEDIA
)

CENTRAL_FEATURES = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.VOLUME_STEP
)

LEGACY_EVENTS = {
    "alarm": ("alarm", "alarmVolume"),
    "firealarm": ("firealarm", "alarmVolume"),
    "bell": ("bell", "bellVolume"),
    "buzzer": ("wecker", "buzzerVolume"),
}

BROWSE_CONTENT_TYPE_ROOT = "loxone_library"
BROWSE_CONTENT_TYPE_DIRECTORY = "directory"
BROWSE_CONTENT_TYPE_SOURCE = "source"
BROWSE_CONTENT_TYPE_AUDIO_ZONE = "audio_zone"
BROWSE_MEDIA_CLASS_SPEAKER = "speaker"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    entities = [
        LoxoneAudioZoneEntity(bridge, control)
        for control in bridge.controls
        if control.type in MEDIA_PLAYER_CONTROL_TYPES
    ]
    setattr(
        bridge,
        _MEDIA_PLAYER_ENTITIES_ATTR,
        {entity.control.uuid_action: entity for entity in entities},
    )
    async_add_entities(entities)


class LoxoneAudioZoneEntity(LoxoneEntity, MediaPlayerEntity):
    """Representation of a Loxone Audio Zone block."""

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control)
        self._media_position_updated_at: datetime | None = None
        self._last_playback_position: tuple[MediaPlayerState | None, float | None] | None = None
        self._play_state_name = first_matching_state_name(control, PLAY_STATE_CANDIDATES)
        self._power_state_name = first_matching_state_name(control, POWER_STATE_CANDIDATES)
        self._volume_state_name = first_matching_state_name(control, VOLUME_STATE_CANDIDATES)
        self._volume_step_state_name = first_matching_state_name(control, VOLUME_STEP_CANDIDATES)
        self._max_volume_state_name = first_matching_state_name(control, MAX_VOLUME_STATE_CANDIDATES)
        self._mute_state_name = first_matching_state_name(control, MUTE_STATE_CANDIDATES)
        self._shuffle_state_name = first_matching_state_name(control, SHUFFLE_STATE_CANDIDATES)
        self._repeat_state_name = first_matching_state_name(control, REPEAT_STATE_CANDIDATES)
        self._progress_state_name = first_matching_state_name(control, PROGRESS_STATE_CANDIDATES)
        self._duration_state_name = first_matching_state_name(control, DURATION_STATE_CANDIDATES)
        self._source_state_name = first_matching_state_name(control, SOURCE_STATE_CANDIDATES)
        self._source_list_state_name = first_matching_state_name(control, SOURCE_LIST_STATE_CANDIDATES)
        self._title_state_name = first_matching_state_name(control, TITLE_STATE_CANDIDATES)
        self._artist_state_name = first_matching_state_name(control, ARTIST_STATE_CANDIDATES)
        self._album_state_name = first_matching_state_name(control, ALBUM_STATE_CANDIDATES)
        self._station_state_name = first_matching_state_name(control, STATION_STATE_CANDIDATES)
        self._genre_state_name = first_matching_state_name(control, GENRE_STATE_CANDIDATES)
        self._image_state_name = first_matching_state_name(control, IMAGE_STATE_CANDIDATES)
        self._server_state_name = first_matching_state_name(control, SERVER_STATE_CANDIDATES)
        self._client_state_name = first_matching_state_name(control, CLIENT_STATE_CANDIDATES)
        self._volume_attributes = {
            attribute: name
            for attribute, candidates in VOLUME_ATTRIBUTES.items()
            if (name := first_matching_state_name(control, candidates)) is not None
        }
        self._bluetooth_state_name = first_matching_state_name(control, ("bluetooth",))
        self._queue_index_state_name = first_matching_state_name(control, ("queueIndex",))
        self._media_server = resolve_media_server(bridge, control)
        media_server_states = (
            self._media_server.states
            if self._media_server is not None
            else {}
        )
        self._media_server_state_uuid = _first_matching_state_uuid(
            media_server_states, SERVER_STATE_CANDIDATES
        )
        self._media_server_conn_state_uuid = _first_matching_state_uuid(
            media_server_states, MEDIA_SERVER_CONN_STATE_CANDIDATES
        )
        self._media_server_certificate_state_uuid = _first_matching_state_uuid(
            media_server_states, MEDIA_SERVER_CERTIFICATE_STATE_CANDIDATES
        )
        self._media_server_host_uuid = _first_matching_state_uuid(
            media_server_states, MEDIA_SERVER_HOST_STATE_CANDIDATES
        )
        self._configured_group = _resolve_audio_group(bridge.controls, control)
        self._playback_group = _linked_player_group(bridge.controls, control)
        self._audio_group = self._playback_group or self._configured_group
        self._linked_audio_zone_refs = (
            tuple(
                (
                    child,
                    first_matching_state_name(child, PLAY_STATE_CANDIDATES),
                    first_matching_state_name(child, POWER_STATE_CANDIDATES),
                )
                for child in self._configured_group[1:]
            )
            if self._is_central_audio_zone()
            else ()
        )

    def relevant_state_uuids(self) -> Iterable[str]:
        watched = set(super().relevant_state_uuids())
        for state_uuid in (
            self._media_server_state_uuid,
            self._media_server_conn_state_uuid,
            self._media_server_certificate_state_uuid,
            self._media_server_host_uuid,
        ):
            if state_uuid:
                watched.add(state_uuid)
        for linked_control, play_state_name, power_state_name in self._linked_audio_zone_refs:
            watched.update(
                state_uuid
                for state_name in (play_state_name, power_state_name)
                if state_name is not None
                and (state_uuid := linked_control.state_uuid(state_name)) is not None
            )
        return watched

    def _state_raw(self, state_name: str | None) -> Any:
        if state_name is None:
            return None
        return self.state_value(state_name)

    def _state_int(self, state_name: str | None) -> int | None:
        return _coerce_int(self._state_raw(state_name))

    def _state_text(self, state_name: str | None) -> str | None:
        return _coerce_text(self._state_raw(state_name))

    def _state_positive_float(self, state_name: str | None) -> float | None:
        return _positive_float(self._state_raw(state_name))

    def _play_state(self) -> int | None:
        return _coerce_play_state(self._state_raw(self._play_state_name))

    def _is_audio_zone_v2(self) -> bool:
        return self.control.type == AUDIO_ZONE_V2_CONTROL_TYPE

    def _is_central_audio_zone(self) -> bool:
        return self.control.type == CENTRAL_AUDIO_ZONE_CONTROL_TYPE

    async def _async_send_action(self, command: str) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, command)

    @property
    def available(self) -> bool:
        return super().available and coerce_bool(self.control.details.get("validOutputs")) is not False

    @property
    def state(self) -> MediaPlayerState | None:
        local_state = _media_player_state_from_values(
            self._state_raw(self._power_state_name),
            self._state_raw(self._play_state_name),
            treat_idle_as_available=self._is_audio_zone_v2(),
        )
        if local_state is not None:
            return local_state

        if self._is_central_audio_zone():
            return self._aggregate_linked_audio_zone_state()
        return None

    @property
    def volume_level(self) -> float | None:
        raw = coerce_float(self._state_raw(self._volume_state_name))
        if raw is None or not math.isfinite(raw):
            return None
        return max(0.0, min(1.0, raw / 100.0))

    @property
    def is_volume_muted(self) -> bool | None:
        return coerce_bool(self._state_raw(self._mute_state_name))

    @property
    def media_title(self) -> str | None:
        return self._state_text(self._title_state_name)

    @property
    def media_artist(self) -> str | None:
        return self._state_text(self._artist_state_name)

    @property
    def media_album_name(self) -> str | None:
        return self._state_text(self._album_state_name)

    @property
    def media_image_url(self) -> str | None:
        return self._state_text(self._image_state_name)

    @property
    def media_channel(self) -> str | None:
        return self._state_text(self._station_state_name)

    @property
    def media_content_type(self) -> MediaType:
        return MediaType.MUSIC

    @property
    def media_duration(self) -> float | None:
        return self._state_positive_float(self._duration_state_name)

    @property
    def media_position(self) -> float | None:
        return self._state_positive_float(self._progress_state_name)

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._media_position_updated_at

    @property
    def shuffle(self) -> bool | None:
        return coerce_bool(self._state_raw(self._shuffle_state_name))

    @property
    def repeat(self) -> RepeatMode | None:
        return _repeat_mode_to_ha_value(self._state_int(self._repeat_state_name))

    @property
    def source_list(self) -> list[str] | None:
        source_options = self._source_options()
        if not source_options:
            return None
        return list(source_options.values())

    @property
    def source(self) -> str | None:
        source_options = self._source_options()
        raw = self._state_raw(self._source_state_name)
        slot = _coerce_int(raw)
        if slot is not None:
            return source_options.get(slot, f"Source {slot}")

        return _coerce_text(raw)

    @property
    def group_members(self) -> list[str] | None:
        member_entity_ids = self._group_member_entity_ids()
        return member_entity_ids or None

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = CENTRAL_FEATURES if self._is_central_audio_zone() else SUPPORTED_FEATURES
        if self._can_set_volume():
            features |= MediaPlayerEntityFeature.VOLUME_SET
        if self._source_options():
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        if self._mute_state_name is not None:
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if self._shuffle_state_name is not None:
            features |= MediaPlayerEntityFeature.SHUFFLE_SET
        if self._repeat_state_name is not None:
            features |= MediaPlayerEntityFeature.REPEAT_SET
        if self._progress_state_name is not None:
            features |= MediaPlayerEntityFeature.SEEK
        if self._browse_children_available():
            features |= MediaPlayerEntityFeature.BROWSE_MEDIA
        return features

    def _can_set_volume(self) -> bool:
        if self._is_central_audio_zone():
            return self._volume_state_name is not None
        modes = _coerce_int(self.control.details.get("volumeModes"))
        # Bit 2 indicates an output driven by volume-up/down pulses.
        return not (self._is_audio_zone_v2() and modes is not None and modes & (1 << 2))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        _set_if_not_none(attrs, "play_state", self._play_state())
        _set_if_not_none(
            attrs,
            "server_state",
            _coerce_int(
                self._state_value_with_fallback(
                    self._server_state_name, self._media_server_state_uuid
                )
            ),
        )
        _set_if_not_none(attrs, "client_state", self._state_int(self._client_state_name))
        _set_if_not_none(attrs, "source_id", self._state_int(self._source_state_name))
        _set_if_not_none(attrs, "genre", self._state_text(self._genre_state_name))
        _set_if_not_none(attrs, "queue_index", self._state_int(self._queue_index_state_name))
        _set_if_not_none(attrs, "bluetooth", coerce_bool(self._state_raw(self._bluetooth_state_name)))
        attrs.update(
            (attribute, volume)
            for attribute, state_name in self._volume_attributes.items()
            if (volume := self._state_int(state_name)) is not None
        )
        self._append_audio_server_attributes(attrs)
        self._append_linked_audio_zone_attributes(attrs)
        self._append_group_attributes(attrs)
        return attrs

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        normalized_type = (media_content_type or BROWSE_CONTENT_TYPE_ROOT).strip().casefold()
        normalized_id = _coerce_text(media_content_id)

        if normalized_type not in {BROWSE_CONTENT_TYPE_ROOT, BROWSE_CONTENT_TYPE_DIRECTORY, ""}:
            return self._browse_root()
        if normalized_id == self._browse_sources_directory_id():
            return self._browse_sources_directory()
        if normalized_id == self._browse_linked_zones_directory_id():
            return self._browse_linked_zones_directory()
        return self._browse_root()

    def _append_audio_server_attributes(self, attrs: dict[str, Any]) -> None:
        if self._media_server is None:
            return

        attrs["audio_server_name"] = self._media_server.name
        attrs["audio_server_uuid_action"] = self._media_server.uuid_action

        audio_server_host = self._audio_server_host()
        if audio_server_host is not None:
            attrs["audio_server_host"] = audio_server_host
            _set_if_not_none(attrs, "audio_server_local_hint", _host_local_hint(audio_server_host))

        _set_if_not_none(attrs, "audio_server_mac", _mask_mac(self._media_server.mac))

        _set_if_not_none(
            attrs,
            "audio_server_conn_state",
            _coerce_int(self.bridge.state_value(self._media_server_conn_state_uuid)),
        )
        _set_if_not_none(
            attrs,
            "audio_server_certificate_valid",
            coerce_bool(self.bridge.state_value(self._media_server_certificate_state_uuid)),
        )

    def _append_group_attributes(self, attrs: dict[str, Any]) -> None:
        if not self._audio_group:
            return

        attrs["group_member_uuid_actions"] = [control.uuid_action for control in self._audio_group]
        attrs["group_member_names"] = [control.display_name for control in self._audio_group]
        group_entity_ids = self._group_member_entity_ids()
        if group_entity_ids:
            attrs["group_member_entity_ids"] = group_entity_ids

        if self._playback_group:
            _set_if_not_none(attrs, "group_name", _coerce_text(self.control.details.get("groupName")))
            return

        leader = self._audio_group[0]
        attrs["group_leader_uuid_action"] = leader.uuid_action
        attrs["group_leader_name"] = leader.display_name
        _set_if_not_none(attrs, "group_leader_entity_id", self._entity_id_for_control(leader))

    def _browse_children_available(self) -> bool:
        return bool(self._source_options() or self._linked_audio_zone_refs)

    def _browse_root(self) -> BrowseMedia:
        children = [
            directory
            for directory in (self._browse_sources_directory(), self._browse_linked_zones_directory())
            if directory.can_expand
        ]

        return BrowseMedia(
            title=self.control.display_name,
            media_class=MediaClass.APP,
            media_content_id=self.control.uuid_action,
            media_content_type=BROWSE_CONTENT_TYPE_ROOT,
            can_play=False,
            can_expand=bool(children),
            children=children,
        )

    def _browse_sources_directory(self) -> BrowseMedia:
        children = self._browse_source_children()
        return BrowseMedia(
            title="Sources",
            media_class=MediaClass.DIRECTORY,
            media_content_id=self._browse_sources_directory_id(),
            media_content_type=BROWSE_CONTENT_TYPE_DIRECTORY,
            can_play=False,
            can_expand=bool(children),
            children=children,
            children_media_class=MediaClass.MUSIC,
        )

    def _browse_linked_zones_directory(self) -> BrowseMedia:
        children = self._browse_linked_zone_children()
        return BrowseMedia(
            title="Linked Zones",
            media_class=MediaClass.DIRECTORY,
            media_content_id=self._browse_linked_zones_directory_id(),
            media_content_type=BROWSE_CONTENT_TYPE_DIRECTORY,
            can_play=False,
            can_expand=bool(children),
            children=children,
            children_media_class=BROWSE_MEDIA_CLASS_SPEAKER,
        )

    def _browse_source_children(self) -> list[BrowseMedia]:
        children: list[BrowseMedia] = []
        for slot, label in self._source_options().items():
            children.append(
                BrowseMedia(
                    title=label,
                    media_class=MediaClass.MUSIC,
                    media_content_id=str(slot),
                    media_content_type=BROWSE_CONTENT_TYPE_SOURCE,
                    can_play=True,
                    can_expand=False,
                )
            )
        return children

    def _browse_linked_zone_children(self) -> list[BrowseMedia]:
        return [
            BrowseMedia(
                title=control.display_name,
                media_class=BROWSE_MEDIA_CLASS_SPEAKER,
                media_content_id=control.uuid_action,
                media_content_type=BROWSE_CONTENT_TYPE_AUDIO_ZONE,
                can_play=False,
                can_expand=False,
            )
            for control, _, _ in self._linked_audio_zone_refs
        ]

    def _browse_sources_directory_id(self) -> str:
        return f"{self.control.uuid_action}:sources"

    def _browse_linked_zones_directory_id(self) -> str:
        return f"{self.control.uuid_action}:linked_zones"

    def _group_member_entity_ids(self) -> list[str]:
        return [
            entity_id
            for control in self._audio_group
            if (entity_id := self._entity_id_for_control(control)) is not None
        ]

    def _entity_id_for_control(self, control: LoxoneControl) -> str | None:
        entities = getattr(self.bridge, _MEDIA_PLAYER_ENTITIES_ATTR, {})
        return getattr(entities.get(control.uuid_action), "entity_id", None)

    async def async_turn_on(self) -> None:
        await self._async_send_action("on")

    async def async_turn_off(self) -> None:
        await self._async_send_action("off")

    async def async_media_play(self) -> None:
        await self._async_send_action("play")

    async def async_media_pause(self) -> None:
        await self._async_send_action("pause")

    async def async_media_stop(self) -> None:
        await self._async_send_action("stop")

    async def async_media_next_track(self) -> None:
        await self._async_send_action("next")

    async def async_media_previous_track(self) -> None:
        await self._async_send_action("prev")

    async def async_set_volume_level(self, volume: float) -> None:
        if not self._can_set_volume():
            raise ServiceValidationError("This Loxone audio output only supports volume steps.")
        clamped = max(0.0, min(1.0, volume))
        volume_limit = self._state_int(self._max_volume_state_name)
        percent = round(clamped * 100)
        volume_percent = min(percent, volume_limit) if volume_limit is not None and volume_limit >= 0 else percent
        await self._async_send_action(f"volume/{volume_percent}")

    async def async_mute_volume(self, mute: bool) -> None:
        command_value = 1 if mute else 0
        await self._async_send_action(f"mute/{command_value}")

    def _volume_step_command(self, *, up: bool) -> str:
        if self._is_audio_zone_v2():
            return "volUp" if up else "volDown"
        if self._is_central_audio_zone():
            return "volup" if up else "voldown"
        signed_step = self._volume_step() if up else -self._volume_step()
        return f"volstep/{signed_step}"

    async def async_volume_up(self) -> None:
        await self._async_send_action(self._volume_step_command(up=True))

    async def async_volume_down(self) -> None:
        await self._async_send_action(self._volume_step_command(up=False))

    async def async_select_source(self, source: str) -> None:
        slot = _source_slot_for_name(source, self._source_options())
        if slot is None:
            slot = _coerce_int(source)
        await self._async_send_source_slot(slot)

    async def _async_handle_source_media(self, kind: str, media_id: str) -> bool:
        if kind not in {"source", "favorite", "favourite", "playlist", "music"}:
            return False
        slot = _coerce_int(media_id)
        if slot is None:
            await self.async_select_source(media_id)
        else:
            await self._async_send_source_slot(slot)
        return True

    async def _async_handle_tts_media(
        self,
        kind: str,
        media_id: str,
        kwargs: Mapping[str, Any],
    ) -> bool:
        if kind not in {"tts", "announce", "announcement"}:
            return False

        await async_send_audio_tts(self.bridge, self.control, media_id, _tts_volume_argument(kwargs))
        return True

    async def _async_handle_command_media(self, kind: str, media_id: str) -> bool:
        if kind not in {"command", "raw", "event", "loxone"}:
            return False
        command = _coerce_text(media_id)
        if command:
            await self._async_send_action(command)
        return True

    async def _async_play_event(self, event: str, volume: int | None) -> None:
        targets = _audio_zone_targets(self.bridge, self.control)
        if not targets:
            raise ServiceValidationError("This Central Audio control has no mapped audio zones.")
        command, volume_state = LEGACY_EVENTS[event]
        for target in targets:
            legacy = _legacy_audio_endpoint(self.bridge, target)
            if legacy is None:
                await self.bridge.async_send_action(target.uuid_action, event)
                continue
            state_name = first_matching_state_name(target, (volume_state,))
            target_volume = volume
            if target_volume is None:
                target_volume = _coerce_audio_tts_volume(
                    _control_state_raw(self.bridge, target, state_name)
                )
            percent = target_volume if target_volume is not None else DEFAULT_AUDIO_TTS_VOLUME
            await _async_send_music_server_command(legacy, f"{command}/{percent}")

    async def _async_play_event_media(self, kind: str, media_id: str, volume: int | None) -> None:
        legacy = _legacy_audio_endpoint(self.bridge, self.control)
        if legacy is None:
            raise ServiceValidationError(
                "Direct event files and playlists require an original Loxone Music Server zone."
            )
        media_name = _coerce_text(media_id)
        if media_name is None:
            raise ServiceValidationError("Choose an event filename or playlist name.")
        command = _music_server_event_command(kind, media_name)
        percent = volume if volume is not None else audio_tts_volume(self.bridge, self.control)
        await _async_send_music_server_command(legacy, f"{command}/{percent}")

    async def async_play_media(
        self,
        media_type: str | None,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        kind = (media_type or "").strip().casefold()

        if kind in {"event_file", "event_playlist"}:
            await self._async_play_event_media(
                kind, media_id, _coerce_audio_tts_volume(_tts_volume_argument(kwargs))
            )
            return
        if (event := kind.replace("_", "")) in LEGACY_EVENTS:
            await self._async_play_event(event, _coerce_audio_tts_volume(_tts_volume_argument(kwargs)))
            return
        if await self._async_handle_source_media(kind, media_id):
            return
        if await self._async_handle_tts_media(kind, media_id, kwargs):
            return
        if await self._async_handle_command_media(kind, media_id):
            return
        if kind in {"play", "resume"}:
            await self.async_media_play()
            return
        if kind == "pause":
            await self.async_media_pause()
            return
        if kind == "stop":
            await self.async_media_stop()
            return
        raise ServiceValidationError(
            "Loxone accepts configured favourites, native text announcements or supported audio commands."
        )

    async def async_media_seek(self, position: float) -> None:
        if self._progress_state_name is None:
            return
        position_seconds = max(0, round(position))
        await self._async_send_action(f"progress/{position_seconds}")

    async def async_set_shuffle(self, shuffle: bool) -> None:
        if self._shuffle_state_name is None:
            return
        if self.control.type != AUDIO_ZONE_CONTROL_TYPE:
            await self._async_send_action(f"shuffle/{int(shuffle)}")
            return

        # The original Music Server implements a toggle command.
        current = self.shuffle
        if current is not None and current != shuffle:
            await self._async_send_action("shuffle")

    async def async_set_repeat(self, repeat: str) -> None:
        if self._repeat_state_name is None:
            return
        repeat_mode = _ha_repeat_to_repeat_mode(repeat)
        if repeat_mode is None:
            return
        await self._async_send_action(f"repeat/{repeat_mode}")

    def _handle_bridge_update(self) -> None:
        playback = self.state, self.media_position
        if playback != self._last_playback_position:
            state, position = playback
            self._media_position_updated_at = (
                datetime.now(UTC)
                if position is not None and state in {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED}
                else None
            )
            self._last_playback_position = playback
        super()._handle_bridge_update()

    def _volume_step(self) -> int:
        raw_value = self._state_raw(self._volume_step_state_name)
        step_value = _coerce_int(raw_value)
        if step_value is None:
            step_value = _coerce_int(self.control.details.get("volumeStep"))
        if step_value is None:
            step_value = DEFAULT_VOLUME_STEP
        return max(MIN_VOLUME_STEP, min(MAX_VOLUME_STEP, abs(step_value)))

    def _source_options(self) -> dict[int, str]:
        raw = self._state_raw(self._source_list_state_name)
        if raw is None:
            for detail_key in SOURCE_LIST_STATE_CANDIDATES:
                raw = self.control.details.get(detail_key)
                if raw is not None:
                    break
        return _extract_source_slots(deserialize_value(raw))

    def _source_command_name(self) -> str:
        return (
            "playZoneFav"
            if self.control.type
            in {AUDIO_ZONE_V2_CONTROL_TYPE, CENTRAL_AUDIO_ZONE_CONTROL_TYPE}
            else "source"
        )

    async def _async_send_source_slot(self, slot: int | None) -> None:
        if slot is None or slot < 0 or (
            self.control.type == AUDIO_ZONE_CONTROL_TYPE and not 1 <= slot <= 8
        ):
            raise ServiceValidationError("Select a configured Loxone audio favourite.")
        await self._async_send_action(f"{self._source_command_name()}/{slot}")

    def _state_value_with_fallback(
        self, state_name: str | None, fallback_state_uuid: str | None
    ) -> Any:
        if state_name is not None:
            value = self.state_value(state_name)
            if value is not None:
                return value
        return self.bridge.state_value(fallback_state_uuid)

    def _audio_server_host(self) -> str | None:
        state_host = _coerce_text(self.bridge.state_value(self._media_server_host_uuid))
        if state_host is not None:
            return state_host
        if self._media_server is None:
            return None
        return _coerce_text(self._media_server.host)

    def _linked_audio_zone_states(self) -> Iterable[tuple[LoxoneControl, MediaPlayerState | None]]:
        for linked_control, play_state_name, power_state_name in self._linked_audio_zone_refs:
            yield linked_control, _media_player_state_from_values(
                _control_state_raw(self.bridge, linked_control, power_state_name),
                _control_state_raw(self.bridge, linked_control, play_state_name),
                treat_idle_as_available=linked_control.type == AUDIO_ZONE_V2_CONTROL_TYPE,
            )

    def _aggregate_linked_audio_zone_state(self) -> MediaPlayerState | None:
        if not self._linked_audio_zone_refs:
            return None

        states = {state for _, state in self._linked_audio_zone_states()}
        if MediaPlayerState.PLAYING in states:
            return MediaPlayerState.PLAYING
        if MediaPlayerState.PAUSED in states:
            return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

    def _append_linked_audio_zone_attributes(self, attrs: dict[str, Any]) -> None:
        if not self._linked_audio_zone_refs:
            return

        active_zone_names = [
            control.display_name
            for control, state in self._linked_audio_zone_states()
            if state in {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED}
        ]

        attrs["linked_audio_zone_count"] = len(self._linked_audio_zone_refs)
        attrs["active_audio_zone_count"] = len(active_zone_names)
        if active_zone_names:
            attrs["active_audio_zones"] = active_zone_names


def resolve_media_server(bridge: Any, control: Any) -> Any | None:
    raw_servers = getattr(bridge, "media_servers_by_uuid_action", None)
    if not isinstance(raw_servers, Mapping) or not raw_servers:
        return None

    media_servers = [server for server in raw_servers.values() if server is not None]
    if not media_servers:
        return None

    details = control.details if isinstance(control.details, Mapping) else {}
    server_reference = details.get("server")
    if isinstance(server_reference, str) and server_reference.strip():
        server_uuid = normalize_uuid(server_reference)
        return next(
            (
                server
                for action, server in raw_servers.items()
                if server is not None
                and server_uuid in (normalize_uuid(action), normalize_uuid(server.uuid_action))
            ),
            None,
        )

    direct = raw_servers.get(control.uuid_action)
    if direct is not None:
        return direct

    matched_by_states = _match_media_server_by_state_overlap(control, media_servers)
    if matched_by_states is not None:
        return matched_by_states

    detail_tokens = _extract_text_tokens(details)
    host_state_name = first_matching_state_name(control, MEDIA_SERVER_HOST_STATE_CANDIDATES)
    if host_state_name is not None:
        runtime_host = _coerce_text(
            getattr(bridge, "control_state", lambda *_: None)(control, host_state_name)
        )
        if runtime_host is not None:
            detail_tokens.add(runtime_host)
    if detail_tokens:
        uuid_lookup = {
            str(uuid_action).casefold(): server
            for uuid_action, server in raw_servers.items()
            if server is not None
        }
        for token in detail_tokens:
            by_uuid = uuid_lookup.get(token.casefold())
            if by_uuid is not None:
                return by_uuid

        matched_by_host = _match_media_server_by_host(detail_tokens, media_servers)
        if matched_by_host is not None:
            return matched_by_host

        matched_by_mac = _match_media_server_by_mac(detail_tokens, media_servers)
        if matched_by_mac is not None:
            return matched_by_mac

    if len(media_servers) == 1:
        return media_servers[0]
    return None


def _audio_zone_targets(bridge: Any, control: LoxoneControl) -> tuple[LoxoneControl, ...]:
    if control.type != CENTRAL_AUDIO_ZONE_CONTROL_TYPE:
        return (control,)
    members = _central_audio_group(bridge.controls, control)[1:]
    if members or "controls" in control.details:
        return members
    return (control,)


def audio_tts_target_uuids(bridge: Any, control: LoxoneControl) -> tuple[str, ...]:
    """Return the zone identities that receive a text announcement."""
    return tuple(target.uuid_action for target in _audio_zone_targets(bridge, control))


async def async_send_audio_tts(
    bridge: Any, control: LoxoneControl, message: Any, volume: Any = None
) -> dict[str, Any]:
    """Speak native text on the selected audio zone or its group members."""
    text = _coerce_text(message)
    if text is None:
        raise ServiceValidationError("TTS message cannot be empty.")
    targets = _audio_zone_targets(bridge, control)
    if not targets:
        raise ServiceValidationError("This Central Audio control has no mapped audio zones.")

    endpoints = [(target, _legacy_audio_endpoint(bridge, target)) for target in targets]
    max_length = (
        LEGACY_TTS_MAX_LENGTH
        if any(endpoint is not None for _, endpoint in endpoints)
        else AUDIO_TTS_MAX_LENGTH
    )
    if len(text) > max_length:
        raise ServiceValidationError(f"Loxone TTS supports at most {max_length} characters here.")

    selected_volume = _coerce_audio_tts_volume(volume)
    command = _audio_tts_command(text, selected_volume)
    responses = {}
    for target, endpoint in endpoints:
        if endpoint is not None:
            effective_volume = selected_volume if selected_volume is not None else audio_tts_volume(bridge, target)
            responses[target.uuid_action] = await _async_send_music_server_command(
                endpoint, _audio_tts_command(text, effective_volume)
            )
            continue
        responses[target.uuid_action] = await bridge.async_send_action(target.uuid_action, command)
    return next(iter(responses.values())) if len(responses) == 1 else {"results": responses}


def _legacy_audio_endpoint(bridge: Any, control: LoxoneControl) -> URL | None:
    """Resolve the original Music Server's documented local API for a zone."""
    if control.type != AUDIO_ZONE_CONTROL_TYPE or control.is_secured:
        return None
    server_ref = control.details.get("server")
    player_id = _coerce_int(control.details.get("playerid"))
    if not isinstance(server_ref, str) or player_id is None or player_id < 1:
        return None

    server: LoxoneMediaServer | None = resolve_media_server(bridge, control)
    if (
        server is None
        or server.server_type != MUSIC_SERVER_TYPE
        or not server.host
        or normalize_uuid(server.uuid_action) != normalize_uuid(server_ref)
    ):
        return None

    try:
        host = urlsplit(server.host if "://" in server.host else f"//{server.host}")
        if (
            not host.hostname
            or host.scheme not in {"", "http"}
            or host.username is not None
            or host.path not in {"", "/"}
            or host.query
            or host.fragment
        ):
            return None
        base = URL.build(scheme="http", host=host.hostname, port=host.port or MUSIC_SERVER_PORT)
    except (ValueError, UnicodeError):
        return None
    return base / "audio" / str(player_id)


async def _async_send_music_server_command(endpoint: URL, command: str) -> dict[str, Any]:
    url = URL(f"{endpoint}/{command}", encoded=True)
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=MUSIC_SERVER_TIMEOUT)) as session,
            session.get(url, allow_redirects=False) as response,
        ):
            if response.status != 200:
                raise HomeAssistantError(f"Loxone Music Server returned HTTP {response.status}.")
            return {"code": response.status, "control": command, "value": None}
    except (ClientError, TimeoutError):
        raise HomeAssistantError("Could not reach the original Loxone Music Server.") from None


def _music_server_event_command(kind: str, media_name: str) -> str:
    if kind == "event_playlist":
        if media_name in {".", ".."}:
            raise ServiceValidationError("Choose an event playlist name.")
        return f"playeventplaylist/{quote(media_name, safe='')}"
    path = media_name.removeprefix("Event_Sounds/").split("/")
    if any(part in {"", ".", ".."} or "\\" in part for part in path) or not path[-1].lower().endswith(".mp3"):
        raise ServiceValidationError("Choose an MP3 path relative to the Music Server's Event_Sounds directory.")
    return "playeventfile/Event_Sounds/" + "/".join(quote(part, safe="") for part in path)


def _resolve_audio_group(
    controls: list[LoxoneControl], control: LoxoneControl
) -> tuple[LoxoneControl, ...]:
    if control.type == CENTRAL_AUDIO_ZONE_CONTROL_TYPE:
        return _central_audio_group(controls, control)

    matches: list[tuple[LoxoneControl, ...]] = []
    for central in controls:
        if central.type != CENTRAL_AUDIO_ZONE_CONTROL_TYPE:
            continue
        group = _central_audio_group(controls, central)
        if control not in group:
            continue
        if control.parent_uuid_action == central.uuid_action:
            return group
        matches.append(group)

    # Central objects can overlap. HA can represent one group per player.
    return matches[0] if len(matches) == 1 else ()


def _central_audio_group(
    controls: list[LoxoneControl], central: LoxoneControl
) -> tuple[LoxoneControl, ...]:
    members = _audio_group_references(central.details.get("controls"))
    linked = {
        control.uuid_action: control
        for control in controls
        if control.type in CHILD_AUDIO_ZONE_CONTROL_TYPES
        and (
            control.parent_uuid_action == central.uuid_action
            or normalize_uuid(control.uuid_action) in members
            or normalize_uuid(control.uuid) in members
        )
    }
    return (central, *linked.values()) if linked else ()


def _linked_player_group(
    controls: list[LoxoneControl], control: LoxoneControl
) -> tuple[LoxoneControl, ...]:
    """Resolve the actual Audio Player group from the documented groupObjects."""
    if control.type != AUDIO_ZONE_V2_CONTROL_TYPE:
        return ()
    linked = _audio_group_references(control.details.get("groupObjects"))
    if not linked:
        return ()
    linked.add(normalize_uuid(control.uuid_action))
    members = {
        member.uuid_action: member
        for member in controls
        if member.type in CHILD_AUDIO_ZONE_CONTROL_TYPES
        and (normalize_uuid(member.uuid_action) in linked or normalize_uuid(member.uuid) in linked)
    }
    return tuple(members.values()) if len(members) > 1 else ()


def _audio_group_references(entries: Any) -> set[str]:
    if not isinstance(entries, list):
        return set()
    return {
        normalize_uuid(reference)
        for item in entries
        if isinstance(item, Mapping) and isinstance(reference := item.get("uuid"), str)
    }


def _first_matching_state_uuid(
    states: Mapping[str, str], candidates: tuple[str, ...]
) -> str | None:
    normalized_to_uuid: dict[str, str] = {}
    for state_name, state_uuid in states.items():
        normalized_to_uuid.setdefault(normalize_state_name(state_name), state_uuid)

    for candidate in candidates:
        matched = normalized_to_uuid.get(normalize_state_name(candidate))
        if matched:
            return matched
    return None


def _match_media_server_by_state_overlap(control: Any, media_servers: list[Any]) -> Any | None:
    control_uuids = {
        str(state_uuid).strip().casefold()
        for state_uuid in getattr(control, "states", {}).values()
        if isinstance(state_uuid, str) and state_uuid.strip()
    }
    if not control_uuids:
        return None

    best_match: Any | None = None
    best_score = 0
    is_ambiguous = False
    for media_server in media_servers:
        server_states = getattr(media_server, "states", {})
        if not isinstance(server_states, Mapping):
            continue
        server_uuids = {
            str(state_uuid).strip().casefold()
            for state_uuid in server_states.values()
            if isinstance(state_uuid, str) and state_uuid.strip()
        }
        overlap = len(control_uuids & server_uuids)
        if overlap == 0:
            continue
        if overlap > best_score:
            best_match = media_server
            best_score = overlap
            is_ambiguous = False
        elif overlap == best_score:
            is_ambiguous = True

    if is_ambiguous:
        return None
    return best_match


def _extract_text_tokens(raw: Any) -> set[str]:
    tokens: set[str] = set()
    stack: list[Any] = [raw]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            stack.extend(current.values())
            continue
        if isinstance(current, list):
            stack.extend(current)
            continue
        if not isinstance(current, str):
            continue
        cleaned = current.strip()
        if cleaned:
            tokens.add(cleaned)
    return tokens


def _match_media_server_by_host(tokens: set[str], media_servers: list[Any]) -> Any | None:
    host_hints: set[str] = set()
    for token in tokens:
        host_hints.update(_host_match_keys(token))
    if not host_hints:
        return None

    matches = [
        media_server
        for media_server in media_servers
        if host_hints & _host_match_keys(getattr(media_server, "host", None))
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _host_match_keys(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()

    raw = value.strip()
    if not raw:
        return set()

    try:
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return set()
    if not hostname:
        return set()

    normalized_host = hostname.casefold()
    if "." not in normalized_host and normalized_host != "localhost" and port is None:
        try:
            ipaddress.ip_address(normalized_host)
        except ValueError:
            return set()
    keys = {normalized_host}
    if port is not None:
        keys.add(f"{normalized_host}:{port}")
    return keys


def _match_media_server_by_mac(tokens: set[str], media_servers: list[Any]) -> Any | None:
    normalized_hints = {
        normalized
        for token in tokens
        if (normalized := _normalize_mac(token)) is not None
    }
    if not normalized_hints:
        return None

    matches = [
        media_server
        for media_server in media_servers
        if _normalize_mac(getattr(media_server, "mac", None)) in normalized_hints
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _normalize_mac(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact = _MAC_NON_HEX_RE.sub("", value.strip())
    if len(compact) != 12:
        return None
    return compact.upper()


def _mask_mac(value: Any) -> str | None:
    normalized = _normalize_mac(value)
    if normalized is None:
        return None
    return f"{normalized[:6]}****{normalized[-2:]}"


def _host_local_hint(value: str) -> bool | None:
    host_keys = _host_match_keys(value)
    if not host_keys:
        return None
    host_only = next(iter(sorted(host_keys, key=len)))
    try:
        host_ip = ipaddress.ip_address(host_only)
    except ValueError:
        host_ip = None
    if host_ip is not None:
        return bool(host_ip.is_private or host_ip.is_link_local or host_ip.is_loopback)
    if host_only.endswith(".local") or host_only.endswith(".lan"):
        return True
    if host_only == "localhost":
        return True
    return None


def _set_if_not_none(attrs: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        attrs[key] = value


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    numeric = coerce_float(value)
    if numeric is None or not math.isfinite(numeric):
        return None
    return int(numeric)


def _positive_float(value: Any) -> float | None:
    numeric = coerce_float(value)
    if numeric is None or not math.isfinite(numeric) or numeric < 0:
        return None
    return numeric


def _coerce_power_state(value: Any) -> bool | None:
    power = coerce_bool(value)
    if power is not None:
        return power

    text = _coerce_text(value)
    if text is None:
        return None

    normalized = text.casefold()
    if normalized == "offline":
        return False
    if normalized in {"starting", "rebooting", "updating"}:
        return True
    return None


def _coerce_play_state(value: Any) -> int | None:
    numeric = _coerce_int(value)
    if numeric is not None:
        return numeric

    text = _coerce_text(value)
    if text is None:
        return None

    normalized = text.casefold()
    if normalized in {"play", "playing", "resume", "resumed"}:
        return STATE_PLAYING
    if normalized in {"pause", "paused"}:
        return STATE_PAUSED
    if normalized in {"stop", "stopped", "idle"}:
        return STATE_IDLE
    if normalized in PLAY_STATE_OFF_NAMES:
        return STATE_UNKNOWN
    return None


def _media_player_state_from_values(
    power_value: Any,
    play_state_value: Any,
    *,
    treat_idle_as_available: bool,
) -> MediaPlayerState | None:
    power = _coerce_power_state(power_value)
    play_state = _coerce_play_state(play_state_value)

    if treat_idle_as_available and play_state == STATE_IDLE:
        return MediaPlayerState.IDLE

    if power is False:
        return MediaPlayerState.OFF

    if play_state == STATE_PLAYING:
        return MediaPlayerState.PLAYING
    if play_state == STATE_PAUSED:
        return MediaPlayerState.PAUSED
    if play_state == STATE_IDLE:
        return MediaPlayerState.IDLE
    if play_state == STATE_UNKNOWN and str(play_state_value).strip().casefold() in PLAY_STATE_OFF_NAMES:
        return MediaPlayerState.OFF

    if power is True:
        return MediaPlayerState.IDLE
    return None


def _control_state_raw(bridge: Any, control: Any, state_name: str | None) -> Any:
    if state_name is None:
        return None

    resolver = getattr(bridge, "control_state", None)
    if callable(resolver):
        return resolver(control, state_name)

    state_uuid = getattr(control, "state_uuid", lambda _name: None)(state_name)
    return getattr(bridge, "state_value", lambda _uuid: None)(state_uuid)


def _tts_volume_argument(kwargs: Mapping[str, Any]) -> Any:
    raw_value = kwargs.get("volume")
    extra = kwargs.get("extra")
    if raw_value is None and isinstance(extra, Mapping):
        return extra.get("volume")
    return raw_value


def audio_tts_message(bridge: Any, control_or_uuid_action: Any) -> str:
    store = _audio_tts_message_store(bridge)
    key = _audio_tts_store_key(control_or_uuid_action)
    value = store.get(key, DEFAULT_AUDIO_TTS_MESSAGE)
    return str(value).strip()


def set_audio_tts_message(bridge: Any, control_or_uuid_action: Any, value: Any) -> str:
    text = _coerce_text(value) or DEFAULT_AUDIO_TTS_MESSAGE
    _audio_tts_message_store(bridge)[_audio_tts_store_key(control_or_uuid_action)] = text
    return text


def audio_tts_volume(bridge: Any, control_or_uuid_action: Any) -> int:
    store = _audio_tts_volume_store(bridge)
    key = _audio_tts_store_key(control_or_uuid_action)
    raw = store.get(key)
    volume = _coerce_audio_tts_volume(raw)
    if volume is not None:
        return volume

    fallback = _default_audio_tts_volume(bridge, control_or_uuid_action)
    if fallback is not None:
        return fallback
    return DEFAULT_AUDIO_TTS_VOLUME


def set_audio_tts_volume(bridge: Any, control_or_uuid_action: Any, value: Any) -> int:
    volume = _coerce_audio_tts_volume(value)
    if volume is None:
        volume = DEFAULT_AUDIO_TTS_VOLUME
    _audio_tts_volume_store(bridge)[_audio_tts_store_key(control_or_uuid_action)] = volume
    return volume


def _default_audio_tts_volume(bridge: Any, control_or_uuid_action: Any) -> int | None:
    control = _audio_tts_control(bridge, control_or_uuid_action)
    if control is None:
        return None

    state_name = first_matching_state_name(control, TTS_VOLUME_STATE_CANDIDATES)
    tts_volume = _coerce_audio_tts_volume(_control_state_raw(bridge, control, state_name))
    if tts_volume is not None:
        return tts_volume

    state_name = first_matching_state_name(control, VOLUME_STATE_CANDIDATES)
    return _coerce_audio_tts_volume(_control_state_raw(bridge, control, state_name)) or None


def _audio_tts_store_key(control_or_uuid_action: Any) -> str:
    uuid_action = _coerce_text(getattr(control_or_uuid_action, "uuid_action", control_or_uuid_action))
    return uuid_action or ""


def _audio_tts_control(bridge: Any, control_or_uuid_action: Any) -> Any | None:
    if hasattr(control_or_uuid_action, "uuid_action"):
        return control_or_uuid_action
    resolver = getattr(bridge, "control_for_uuid_action", None)
    if callable(resolver):
        return resolver(control_or_uuid_action)
    return None


def _audio_tts_message_store(bridge: Any) -> dict[str, str]:
    store = getattr(bridge, _AUDIO_TTS_MESSAGES_ATTR, None)
    if isinstance(store, dict):
        return store
    created: dict[str, str] = {}
    setattr(bridge, _AUDIO_TTS_MESSAGES_ATTR, created)
    return created


def _audio_tts_volume_store(bridge: Any) -> dict[str, int]:
    store = getattr(bridge, _AUDIO_TTS_VOLUMES_ATTR, None)
    if isinstance(store, dict):
        return store
    created: dict[str, int] = {}
    setattr(bridge, _AUDIO_TTS_VOLUMES_ATTR, created)
    return created


def _coerce_audio_tts_volume(value: Any) -> int | None:
    volume = _coerce_int(value)
    if volume is None:
        return None
    return max(0, min(100, volume))


def build_audio_tts_command(message: Any, volume: Any = None) -> str | None:
    text = _coerce_text(message)
    if text is None:
        return None
    return _audio_tts_command(text, _coerce_audio_tts_volume(volume))


def _audio_tts_command(text: str, volume: int | None) -> str:
    command = f"tts/{quote(text, safe='')}"
    return command if volume is None else f"{command}/{volume}"


def _source_items(raw: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(raw, list):
        yield from (item for item in raw if isinstance(item, Mapping))
        return
    if not isinstance(raw, Mapping):
        return

    groups = raw.get("getroomfavs_result")
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, Mapping) and isinstance(items := group.get("items"), list):
                yield from (item for item in items if isinstance(item, Mapping))

    for key in ("items", "sources", "favourites", "favorites", "zoneFavorites"):
        items = raw.get(key)
        if isinstance(items, list):
            yield from (item for item in items if isinstance(item, Mapping))


def _extract_source_slots(raw: Any) -> dict[int, str]:
    slots: dict[int, str] = {}
    seen_names: set[str] = set()
    for item in _source_items(raw):
        slot = _coerce_int(item.get("slot", item.get("id")))
        if slot is None or slot in slots or slot < 0:
            continue
        name = _coerce_text(item.get("name", item.get("title")))
        if name is None:
            name = f"Source {slot}"
        if name.casefold() in seen_names:
            name = f"{name} ({slot})"
        seen_names.add(name.casefold())
        slots[slot] = name
    return slots


def _source_slot_for_name(name: str, options: Mapping[int, str]) -> int | None:
    target = name.strip().casefold()
    for slot, label in options.items():
        if label.strip().casefold() == target:
            return slot
    return None


def _repeat_mode_to_ha_value(value: int | None) -> RepeatMode | None:
    return {0: RepeatMode.OFF, 1: RepeatMode.ALL, 3: RepeatMode.ONE}.get(value)


def _ha_repeat_to_repeat_mode(value: str) -> int | None:
    normalized = value.strip().casefold()
    if normalized in {"off", "none"}:
        return 0
    if normalized in {"all", "playlist"}:
        return 1
    if normalized in {"one", "single"}:
        return 3
    return None
