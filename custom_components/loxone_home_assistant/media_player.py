"""Media player platform for Loxone audio zones."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MEDIA_PLAYER_CONTROL_TYPES
from .entity import LoxoneEntity, coerce_bool, coerce_float, first_matching_state_name

try:  # Home Assistant >= 2022.10
    from homeassistant.components.media_player import MediaType
except ImportError:  # pragma: no cover - fallback for minimal test stubs
    class MediaType:  # type: ignore[no-redef]
        MUSIC = "music"

PLAY_STATE_CANDIDATES = ("playState", "play_state", "playstate")
POWER_STATE_CANDIDATES = ("power", "active")
VOLUME_STATE_CANDIDATES = ("volume", "defaultVolume", "masterVolume")
VOLUME_STEP_CANDIDATES = ("volumeStep",)
MUTE_STATE_CANDIDATES = ("mute", "isMuted")
SHUFFLE_STATE_CANDIDATES = ("shuffle",)
REPEAT_STATE_CANDIDATES = ("repeat",)
PROGRESS_STATE_CANDIDATES = ("progress",)
DURATION_STATE_CANDIDATES = ("duration",)
SOURCE_STATE_CANDIDATES = ("source",)
SOURCE_LIST_STATE_CANDIDATES = ("sourceList",)
TITLE_STATE_CANDIDATES = ("songName", "title", "station")
ARTIST_STATE_CANDIDATES = ("artist",)
ALBUM_STATE_CANDIDATES = ("album",)
STATION_STATE_CANDIDATES = ("station",)
GENRE_STATE_CANDIDATES = ("genre",)
IMAGE_STATE_CANDIDATES = ("cover",)
SERVER_STATE_CANDIDATES = ("serverState",)
CLIENT_STATE_CANDIDATES = ("clientState",)
TTS_STATE_CANDIDATES = ("tts",)

REPEAT_OFF = "off"
REPEAT_ONE = "one"
REPEAT_ALL = "all"

FEATURE_SELECT_SOURCE = getattr(MediaPlayerEntityFeature, "SELECT_SOURCE", 0)
FEATURE_PLAY_MEDIA = getattr(MediaPlayerEntityFeature, "PLAY_MEDIA", 0)
FEATURE_SHUFFLE_SET = getattr(MediaPlayerEntityFeature, "SHUFFLE_SET", 0)
FEATURE_REPEAT_SET = getattr(MediaPlayerEntityFeature, "REPEAT_SET", 0)
FEATURE_SEEK = getattr(MediaPlayerEntityFeature, "SEEK", 0)

STATE_OFF = -1
STATE_IDLE = 0
STATE_PAUSED = 1
STATE_PLAYING = 2

DEFAULT_VOLUME_STEP = 3
MIN_VOLUME_STEP = 1
MAX_VOLUME_STEP = 20

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
    bridge = hass.data[DOMAIN]["bridges"][entry.entry_id]
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

    @property
    def state(self) -> MediaPlayerState | None:
        power = coerce_bool(self.state_value(self._power_state_name)) if self._power_state_name else None
        if power is False:
            return MediaPlayerState.OFF

        play_state = _coerce_int(self.state_value(self._play_state_name))
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

    @property
    def volume_level(self) -> float | None:
        if self._volume_state_name is None:
            return None
        raw = coerce_float(self.state_value(self._volume_state_name))
        if raw is None:
            return None
        if 0.0 <= raw <= 1.0:
            return raw
        return max(0.0, min(1.0, raw / 100.0))

    @property
    def is_volume_muted(self) -> bool | None:
        if self._mute_state_name is None:
            return None
        return coerce_bool(self.state_value(self._mute_state_name))

    @property
    def media_title(self) -> str | None:
        if self._title_state_name is None:
            return None
        return _coerce_text(self.state_value(self._title_state_name))

    @property
    def media_artist(self) -> str | None:
        if self._artist_state_name is None:
            return None
        return _coerce_text(self.state_value(self._artist_state_name))

    @property
    def media_album_name(self) -> str | None:
        if self._album_state_name is None:
            return None
        return _coerce_text(self.state_value(self._album_state_name))

    @property
    def media_image_url(self) -> str | None:
        if self._image_state_name is None:
            return None
        return _coerce_text(self.state_value(self._image_state_name))

    @property
    def media_channel(self) -> str | None:
        if self._station_state_name is None:
            return None
        return _coerce_text(self.state_value(self._station_state_name))

    @property
    def media_content_type(self) -> str:
        return getattr(MediaType, "MUSIC", "music")

    @property
    def media_duration(self) -> float | None:
        if self._duration_state_name is None:
            return None
        return _positive_float(self.state_value(self._duration_state_name))

    @property
    def media_position(self) -> float | None:
        if self._progress_state_name is None:
            return None
        return _positive_float(self.state_value(self._progress_state_name))

    @property
    def media_position_updated_at(self) -> datetime | None:
        if self.media_position is None:
            return None
        if self.state not in {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED}:
            return None
        return datetime.now(timezone.utc)

    @property
    def shuffle(self) -> bool | None:
        if self._shuffle_state_name is None:
            return None
        return coerce_bool(self.state_value(self._shuffle_state_name))

    @property
    def repeat(self) -> str | None:
        if self._repeat_state_name is None:
            return None
        repeat_mode = _coerce_int(self.state_value(self._repeat_state_name))
        return _repeat_mode_to_ha_value(repeat_mode)

    @property
    def source_list(self) -> list[str] | None:
        source_options = self._source_options()
        if not source_options:
            return None
        return list(source_options.values())

    @property
    def source(self) -> str | None:
        source_options = self._source_options()
        raw = self.state_value(self._source_state_name) if self._source_state_name else None
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
        play_state = _coerce_int(self.state_value(self._play_state_name))
        if play_state is not None:
            attrs["play_state"] = play_state
        server_state = _coerce_int(self.state_value(self._server_state_name))
        if server_state is not None:
            attrs["server_state"] = server_state
        client_state = _coerce_int(self.state_value(self._client_state_name))
        if client_state is not None:
            attrs["client_state"] = client_state
        source_slot = _coerce_int(self.state_value(self._source_state_name))
        if source_slot is not None:
            attrs["source_id"] = source_slot
        genre = _coerce_text(self.state_value(self._genre_state_name))
        if genre is not None:
            attrs["genre"] = genre
        return attrs

    async def async_turn_on(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "on")

    async def async_turn_off(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "off")

    async def async_media_play(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "play")

    async def async_media_pause(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "pause")

    async def async_media_stop(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "pause")

    async def async_media_next_track(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "next")

    async def async_media_previous_track(self) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, "prev")

    async def async_set_volume_level(self, volume: float) -> None:
        clamped = max(0.0, min(1.0, volume))
        volume_percent = round(clamped * 100)
        await self.bridge.async_send_action(
            self.control.uuid_action,
            f"volume/{volume_percent}",
        )

    async def async_volume_up(self) -> None:
        if self.control.type == "AudioZoneV2":
            await self.bridge.async_send_action(self.control.uuid_action, "volUp")
            return
        await self.bridge.async_send_action(
            self.control.uuid_action, f"volstep/{self._volume_step()}"
        )

    async def async_volume_down(self) -> None:
        if self.control.type == "AudioZoneV2":
            await self.bridge.async_send_action(self.control.uuid_action, "volDown")
            return
        await self.bridge.async_send_action(
            self.control.uuid_action, f"volstep/{-self._volume_step()}"
        )

    async def async_select_source(self, source: str) -> None:
        slot = _source_slot_for_name(source, self._source_options())
        if slot is None:
            return
        await self._async_send_source_slot(slot)

    async def async_play_media(
        self,
        media_type: str | None,
        media_id: str,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        kind = (media_type or "").strip().casefold()
        if kind in {"source", "favorite", "favourite", "playlist", "music"}:
            slot = _coerce_int(media_id)
            if slot is None:
                slot = _source_slot_for_name(media_id, self._source_options())
            if slot is not None:
                await self._async_send_source_slot(slot)
            return

        if kind in {"tts", "announce", "announcement"} and self.control.type == "AudioZoneV2":
            text = _coerce_text(media_id)
            if text:
                await self.bridge.async_send_action(self.control.uuid_action, f"tts/{text}")
            return

        if kind in {"play", "resume"}:
            await self.async_media_play()
            return
        if kind in {"pause", "stop"}:
            await self.async_media_pause()
            return

    async def async_media_seek(self, position: float) -> None:
        if self._progress_state_name is None:
            return
        position_seconds = max(0, round(position))
        await self.bridge.async_send_action(
            self.control.uuid_action,
            f"progress/{position_seconds}",
        )

    async def async_set_shuffle(self, shuffle: bool) -> None:
        if self._shuffle_state_name is None:
            return
        command_value = 1 if shuffle else 0
        await self.bridge.async_send_action(
            self.control.uuid_action, f"shuffle/{command_value}"
        )

    async def async_set_repeat(self, repeat: str) -> None:
        if self._repeat_state_name is None:
            return
        repeat_mode = _ha_repeat_to_repeat_mode(repeat)
        if repeat_mode is None:
            return
        await self.bridge.async_send_action(
            self.control.uuid_action, f"repeat/{repeat_mode}"
        )

    def _volume_step(self) -> int:
        raw_value = None
        if self._volume_step_state_name:
            raw_value = self.state_value(self._volume_step_state_name)
        step_value = _coerce_int(raw_value)
        if step_value is None:
            step_value = _coerce_int(self.control.details.get("volumeStep"))
        if step_value is None:
            step_value = DEFAULT_VOLUME_STEP
        return max(MIN_VOLUME_STEP, min(MAX_VOLUME_STEP, abs(step_value)))

    def _source_options(self) -> dict[int, str]:
        raw = (
            self.state_value(self._source_list_state_name)
            if self._source_list_state_name
            else self.control.details.get("sourceList")
        )
        parsed = _deserialize_source_list(raw)
        if parsed is None:
            return {}
        return _extract_source_slots(parsed)

    async def _async_send_source_slot(self, slot: int) -> None:
        command_name = "playZoneFav" if self.control.type == "AudioZoneV2" else "source"
        await self.bridge.async_send_action(
            self.control.uuid_action,
            f"{command_name}/{slot}",
        )


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
        for key in ("sources", "favourites", "favorites"):
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
