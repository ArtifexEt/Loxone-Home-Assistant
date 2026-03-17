"""Media player platform for Loxone audio zones."""

from __future__ import annotations

import json
import ipaddress
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MEDIA_PLAYER_CONTROL_TYPES
from .entity import (
    LoxoneEntity,
    coerce_bool,
    coerce_float,
    first_matching_state_name,
    normalize_state_name,
)
from .runtime import entry_bridge

try:  # Home Assistant >= 2022.10
    from homeassistant.components.media_player import MediaType
except ImportError:  # pragma: no cover - fallback for minimal test stubs
    class MediaType:  # type: ignore[no-redef]
        MUSIC = "music"

PLAY_STATE_CANDIDATES = ("playState", "play_state", "playstate", "mode")
POWER_STATE_CANDIDATES = ("power", "active")
VOLUME_STATE_CANDIDATES = ("volume", "defaultVolume", "masterVolume")
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
TTS_STATE_CANDIDATES = ("tts",)
MEDIA_SERVER_CONN_STATE_CANDIDATES = ("connState", "connectionState")
MEDIA_SERVER_CERTIFICATE_STATE_CANDIDATES = ("certificateValid", "certValid")
MEDIA_SERVER_HOST_STATE_CANDIDATES = ("host",)

REPEAT_OFF = "off"
REPEAT_ONE = "one"
REPEAT_ALL = "all"

FEATURE_SELECT_SOURCE = getattr(MediaPlayerEntityFeature, "SELECT_SOURCE", 0)
FEATURE_PLAY_MEDIA = getattr(MediaPlayerEntityFeature, "PLAY_MEDIA", 0)
FEATURE_SHUFFLE_SET = getattr(MediaPlayerEntityFeature, "SHUFFLE_SET", 0)
FEATURE_REPEAT_SET = getattr(MediaPlayerEntityFeature, "REPEAT_SET", 0)
FEATURE_SEEK = getattr(MediaPlayerEntityFeature, "SEEK", 0)
FEATURE_VOLUME_MUTE = getattr(MediaPlayerEntityFeature, "VOLUME_MUTE", 0)

AUDIO_ZONE_V2_CONTROL_TYPE = "AudioZoneV2"
CENTRAL_AUDIO_ZONE_CONTROL_TYPE = "CentralAudioZone"
CHILD_AUDIO_ZONE_CONTROL_TYPES = {"AudioZone", AUDIO_ZONE_V2_CONTROL_TYPE}

STATE_OFF = -1
STATE_IDLE = 0
STATE_PAUSED = 1
STATE_PLAYING = 2

DEFAULT_VOLUME_STEP = 3
MIN_VOLUME_STEP = 1
MAX_VOLUME_STEP = 20

_MAC_NON_HEX_RE = re.compile(r"[^0-9A-Fa-f]")

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | FEATURE_PLAY_MEDIA
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = [
        LoxoneAudioZoneEntity(bridge, control)
        for control in bridge.controls
        if control.type in MEDIA_PLAYER_CONTROL_TYPES
    ]
    async_add_entities(entities)


class LoxoneAudioZoneEntity(LoxoneEntity, MediaPlayerEntity):
    """Representation of a Loxone Audio Zone block."""

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def __init__(self, bridge, control) -> None:
        super().__init__(bridge, control)
        self._media_position_updated_at: datetime | None = None
        self._play_state_name = first_matching_state_name(control, PLAY_STATE_CANDIDATES)
        self._power_state_name = first_matching_state_name(control, POWER_STATE_CANDIDATES)
        self._volume_state_name = first_matching_state_name(control, VOLUME_STATE_CANDIDATES)
        self._volume_step_state_name = first_matching_state_name(control, VOLUME_STEP_CANDIDATES)
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
        self._tts_state_name = first_matching_state_name(control, TTS_STATE_CANDIDATES)
        self._media_server = _resolve_media_server(bridge, control)
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
        self._linked_audio_zone_refs = _resolve_linked_audio_zone_refs(bridge, control)

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
            for state_name in (play_state_name, power_state_name):
                if state_name is None:
                    continue
                state_uuid = linked_control.state_uuid(state_name)
                if state_uuid:
                    watched.add(state_uuid)
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

    async def _async_send_tts_action(self, command: str) -> None:
        await self.bridge.async_send_action(self._tts_target_uuid_action(), command)

    def _tts_target_uuid_action(self) -> str:
        if self._media_server is not None:
            media_server_uuid_action = _coerce_text(
                getattr(self._media_server, "uuid_action", None)
            )
            if media_server_uuid_action is not None:
                return media_server_uuid_action
        return self.control.uuid_action

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
        if raw is None:
            return None
        if 0.0 <= raw <= 1.0:
            return raw
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
    def media_content_type(self) -> str:
        return getattr(MediaType, "MUSIC", "music")

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
    def repeat(self) -> str | None:
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

        text_value = _coerce_text(raw)
        if text_value is not None:
            return text_value
        return None

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = SUPPORTED_FEATURES
        if self._source_options() or self._source_state_name is not None:
            features |= FEATURE_SELECT_SOURCE
        if self._mute_state_name is not None:
            features |= FEATURE_VOLUME_MUTE
        if self._shuffle_state_name is not None:
            features |= FEATURE_SHUFFLE_SET
        if self._repeat_state_name is not None:
            features |= FEATURE_REPEAT_SET
        if self._progress_state_name is not None:
            features |= FEATURE_SEEK
        return features

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
        self._append_audio_server_attributes(attrs)
        self._append_linked_audio_zone_attributes(attrs)
        return attrs

    def _append_audio_server_attributes(self, attrs: dict[str, Any]) -> None:
        if self._media_server is None:
            return

        attrs["audio_server_name"] = self._media_server.name
        attrs["audio_server_uuid_action"] = self._media_server.uuid_action

        audio_server_host = self._audio_server_host()
        if audio_server_host is not None:
            attrs["audio_server_host"] = audio_server_host
            local_hint = _host_local_hint(audio_server_host)
            if local_hint is not None:
                attrs["audio_server_local_hint"] = local_hint

        masked_mac = _mask_mac(self._media_server.mac)
        if masked_mac is not None:
            attrs["audio_server_mac"] = masked_mac

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
        clamped = max(0.0, min(1.0, volume))
        volume_percent = round(clamped * 100)
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
            return
        await self._async_send_source_slot(slot)

    async def _async_handle_source_media(self, kind: str, media_id: str) -> bool:
        if kind not in {"source", "favorite", "favourite", "playlist", "music"}:
            return False
        slot = _coerce_int(media_id)
        if slot is None:
            slot = _source_slot_for_name(media_id, self._source_options())
        if slot is not None:
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

        text = _coerce_text(media_id)
        if not text:
            return True
        volume = _coerce_tts_volume(kwargs)
        encoded_text = _encode_tts_text(text)
        command = (
            f"tts/{encoded_text}" if volume is None else f"tts/{encoded_text}/{volume}"
        )
        await self._async_send_tts_action(command)
        return True

    async def _async_handle_command_media(self, kind: str, media_id: str) -> bool:
        if kind not in {"command", "raw", "event", "loxone"}:
            return False
        command = _coerce_text(media_id)
        if command:
            await self._async_send_action(command)
        return True

    async def _async_handle_alarm_media(self, kind: str) -> bool:
        if kind not in {"alarm", "firealarm", "fire_alarm", "bell", "buzzer"}:
            return False
        await self._async_send_action(kind.replace("_", ""))
        return True

    async def async_play_media(
        self,
        media_type: str | None,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        kind = (media_type or "").strip().casefold()

        if await self._async_handle_source_media(kind, media_id):
            return
        if await self._async_handle_tts_media(kind, media_id, kwargs):
            return
        if await self._async_handle_command_media(kind, media_id):
            return
        if await self._async_handle_alarm_media(kind):
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

    async def async_media_seek(self, position: float) -> None:
        if self._progress_state_name is None:
            return
        position_seconds = max(0, round(position))
        await self._async_send_action(f"progress/{position_seconds}")

    async def async_set_shuffle(self, shuffle: bool) -> None:
        if self._shuffle_state_name is None:
            return
        command_value = 1 if shuffle else 0
        await self._async_send_action(f"shuffle/{command_value}")

    async def async_set_repeat(self, repeat: str) -> None:
        if self._repeat_state_name is None:
            return
        repeat_mode = _ha_repeat_to_repeat_mode(repeat)
        if repeat_mode is None:
            return
        await self._async_send_action(f"repeat/{repeat_mode}")

    def _handle_bridge_update(self) -> None:
        if self.media_position is None:
            self._media_position_updated_at = None
        elif self.state in {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED}:
            self._media_position_updated_at = datetime.now(timezone.utc)
        else:
            self._media_position_updated_at = None
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
        parsed = _deserialize_source_list(raw)
        if parsed is None:
            return {}
        return _extract_source_slots(parsed)

    def _source_command_name(self) -> str:
        return (
            "playZoneFav"
            if self.control.type
            in {AUDIO_ZONE_V2_CONTROL_TYPE, CENTRAL_AUDIO_ZONE_CONTROL_TYPE}
            else "source"
        )

    async def _async_send_source_slot(self, slot: int) -> None:
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

    def _linked_audio_zone_states(self) -> list[tuple[Any, MediaPlayerState | None]]:
        states: list[tuple[Any, MediaPlayerState | None]] = []
        for linked_control, play_state_name, power_state_name in self._linked_audio_zone_refs:
            states.append(
                (
                    linked_control,
                    _media_player_state_from_values(
                        _control_state_raw(self.bridge, linked_control, power_state_name),
                        _control_state_raw(self.bridge, linked_control, play_state_name),
                        treat_idle_as_available=(
                            getattr(linked_control, "type", None)
                            == AUDIO_ZONE_V2_CONTROL_TYPE
                        ),
                    ),
                )
            )
        return states

    def _aggregate_linked_audio_zone_state(self) -> MediaPlayerState | None:
        if not self._linked_audio_zone_refs:
            return None

        saw_paused = False
        saw_known_state = False
        for _linked_control, linked_state in self._linked_audio_zone_states():
            if linked_state is None:
                continue
            saw_known_state = True
            if linked_state == MediaPlayerState.PLAYING:
                return MediaPlayerState.PLAYING
            if linked_state == MediaPlayerState.PAUSED:
                saw_paused = True

        if saw_paused:
            return MediaPlayerState.PAUSED
        if saw_known_state:
            return MediaPlayerState.IDLE
        return MediaPlayerState.IDLE

    def _append_linked_audio_zone_attributes(self, attrs: dict[str, Any]) -> None:
        if not self._linked_audio_zone_refs:
            return

        active_zone_names: list[str] = []
        for linked_control, linked_state in self._linked_audio_zone_states():
            if linked_state not in {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED}:
                continue
            display_name = _coerce_text(getattr(linked_control, "display_name", None))
            if display_name is None:
                display_name = _coerce_text(getattr(linked_control, "name", None))
            if display_name is not None:
                active_zone_names.append(display_name)

        attrs["linked_audio_zone_count"] = len(self._linked_audio_zone_refs)
        attrs["active_audio_zone_count"] = len(active_zone_names)
        if active_zone_names:
            attrs["active_audio_zones"] = active_zone_names


def _resolve_media_server(bridge: Any, control: Any) -> Any | None:
    raw_servers = getattr(bridge, "media_servers_by_uuid_action", None)
    if not isinstance(raw_servers, Mapping) or not raw_servers:
        return None

    media_servers = [server for server in raw_servers.values() if server is not None]
    if not media_servers:
        return None

    direct = raw_servers.get(control.uuid_action)
    if direct is not None:
        return direct

    matched_by_states = _match_media_server_by_state_overlap(control, media_servers)
    if matched_by_states is not None:
        return matched_by_states

    details = control.details if isinstance(control.details, Mapping) else {}
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


def _resolve_linked_audio_zone_refs(
    bridge: Any,
    control: Any,
) -> tuple[tuple[Any, str | None, str | None], ...]:
    if getattr(control, "type", None) != CENTRAL_AUDIO_ZONE_CONTROL_TYPE:
        return ()

    raw_controls = getattr(bridge, "controls", None)
    if raw_controls is None:
        return ()

    detail_tokens = {
        token.casefold()
        for token in _extract_text_tokens(
            control.details if isinstance(control.details, Mapping) else {}
        )
    }
    linked_refs: list[tuple[Any, str | None, str | None]] = []

    for candidate in raw_controls:
        if candidate is control:
            continue
        if getattr(candidate, "type", None) not in CHILD_AUDIO_ZONE_CONTROL_TYPES:
            continue
        if not _is_linked_audio_zone_candidate(control, candidate, detail_tokens):
            continue
        linked_refs.append(
            (
                candidate,
                first_matching_state_name(candidate, PLAY_STATE_CANDIDATES),
                first_matching_state_name(candidate, POWER_STATE_CANDIDATES),
            )
        )

    return tuple(linked_refs)


def _is_linked_audio_zone_candidate(
    parent_control: Any,
    candidate_control: Any,
    detail_tokens: set[str],
) -> bool:
    if getattr(candidate_control, "parent_uuid_action", None) == getattr(
        parent_control, "uuid_action", None
    ):
        return True

    if not detail_tokens:
        return False

    candidate_tokens = {
        token.casefold()
        for token in (
            _coerce_text(getattr(candidate_control, "uuid_action", None)),
            _coerce_text(getattr(candidate_control, "uuid", None)),
        )
        if token is not None
    }
    return bool(candidate_tokens & detail_tokens)


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

    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    hostname = parsed.hostname
    if not hostname:
        return set()

    normalized_host = hostname.casefold()
    if "." not in normalized_host and normalized_host != "localhost" and parsed.port is None:
        try:
            ipaddress.ip_address(normalized_host)
        except ValueError:
            return set()
    keys = {normalized_host}
    if parsed.port is not None:
        keys.add(f"{normalized_host}:{parsed.port}")
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
    if numeric is None:
        return None
    return int(numeric)


def _positive_float(value: Any) -> float | None:
    numeric = coerce_float(value)
    if numeric is None or numeric < 0:
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
    if normalized in {"off", "offline"}:
        return STATE_OFF
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
    if play_state == STATE_OFF:
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


def _coerce_tts_volume(kwargs: Mapping[str, Any]) -> int | None:
    raw_value = kwargs.get("volume")
    if raw_value is None:
        extra = kwargs.get("extra")
        if isinstance(extra, Mapping):
            raw_value = extra.get("volume")
    volume = _coerce_int(raw_value)
    if volume is None:
        return None
    return max(0, min(100, volume))


def _encode_tts_text(value: str) -> str:
    # Keep `/` literal inside a single TTS segment to avoid path splits.
    return value.replace("/", "%2F")


def _deserialize_source_list(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, (Mapping, list)):
        return value
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None
    try:
        return json.loads(raw.replace("'", '"'))
    except json.JSONDecodeError:
        return None


def _extract_source_slots(raw: Any) -> dict[int, str]:
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        items.extend(item for item in raw if isinstance(item, dict))
    elif isinstance(raw, Mapping):
        zone_result = raw.get("getroomfavs_result")
        if isinstance(zone_result, list):
            for group in zone_result:
                if not isinstance(group, Mapping):
                    continue
                group_items = group.get("items")
                if isinstance(group_items, list):
                    items.extend(item for item in group_items if isinstance(item, dict))
        direct_items = raw.get("items")
        if isinstance(direct_items, list):
            items.extend(item for item in direct_items if isinstance(item, dict))
        for key in ("sources", "favourites", "favorites", "zoneFavorites"):
            direct_candidates = raw.get(key)
            if isinstance(direct_candidates, list):
                items.extend(item for item in direct_candidates if isinstance(item, dict))

    slots: dict[int, str] = {}
    seen_names: set[str] = set()
    for item in items:
        slot = _coerce_int(item.get("slot", item.get("id")))
        if slot is None:
            continue
        name = _coerce_text(item.get("name", item.get("title")))
        if name is None:
            name = f"Source {slot}"
        if name in seen_names:
            name = f"{name} ({slot})"
        seen_names.add(name)
        slots[slot] = name
    return slots


def _source_slot_for_name(name: str, options: Mapping[int, str]) -> int | None:
    direct_slot = _coerce_int(name)
    if direct_slot is not None:
        return direct_slot

    target = name.strip().casefold()
    for slot, label in options.items():
        if label.strip().casefold() == target:
            return slot
    return None


def _repeat_mode_to_ha_value(value: int | None) -> str | None:
    if value is None:
        return None
    if value <= 0:
        return REPEAT_OFF
    # Current Loxone docs define: 1=list/all, 3=track/one.
    # Keep `2` as an all/list compatibility alias for older payload variants.
    if value in {1, 2}:
        return REPEAT_ALL
    if value == 3:
        return REPEAT_ONE
    return REPEAT_ALL


def _ha_repeat_to_repeat_mode(value: str) -> int | None:
    normalized = value.strip().casefold()
    if normalized in {"off", "none"}:
        return 0
    if normalized in {"all", "playlist"}:
        return 1
    if normalized in {"one", "single"}:
        return 3
    return None
