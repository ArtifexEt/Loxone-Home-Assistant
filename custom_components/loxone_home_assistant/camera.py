"""Camera platform for Loxone intercom video preview."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

try:
    from aiohttp import BasicAuth, ClientError
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    class ClientError(Exception):
        """Fallback network error type used in tests without aiohttp."""

    class BasicAuth:  # type: ignore[no-redef]
        """Fallback auth container used in tests without aiohttp."""

        def __init__(self, login: str, password: str) -> None:
            self.login = login
            self.password = password
from homeassistant.components.camera import Camera
try:
    from homeassistant.components.camera import CameraEntityFeature
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    class CameraEntityFeature:  # type: ignore[no-redef]
        """Fallback camera features enum for test stubs."""

        STREAM = 1
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import INTERCOM_CAMERA_CONTROL_TYPES
from .entity import LoxoneEntity, first_matching_state_name, normalize_state_name
from .intercom import (
    intercom_address_state_name,
    intercom_history_state_name,
    is_intercom_control,
    resolve_intercom_http_url,
)
from .models import LoxoneControl
from .runtime import entry_bridge

_LOGGER = logging.getLogger(__name__)

STREAM_STATE_CANDIDATES = (
    "streamUrl",
    "videoStream",
    "videoUrl",
    "video",
)
SNAPSHOT_STATE_CANDIDATES = (
    "alertImage",
    "liveImage",
    "liveImageUrl",
    "image",
    "snapshot",
)
STREAM_DETAIL_PATHS = (
    "securedDetails.videoInfo.streamUrl",
    "securedDetails.videoInfo.streamUrlExtern",
    "securedDetails.videoInfo.streamUrlIntern",
    "securedDetails.videoInfo.videoUrl",
    "securedDetails.streamUrl",
    "videoInfo.streamUrl",
    "videoInfo.streamUrlExtern",
    "videoInfo.streamUrlIntern",
    "videoInfo.videoUrl",
    "videoSettings.streamUrl",
    "videoSettings.streamUrlExtern",
    "videoSettings.streamUrlIntern",
    "videoSettings.videoUrl",
    "streamUrl",
)
SNAPSHOT_DETAIL_PATHS = (
    "securedDetails.videoInfo.alertImage",
    "securedDetails.videoInfo.liveImageUrl",
    "securedDetails.videoInfo.liveImage",
    "securedDetails.videoInfo.imageUrl",
    "securedDetails.videoInfo.alertImageUrl",
    "videoInfo.liveImageUrl",
    "videoInfo.liveImage",
    "videoInfo.alertImage",
    "videoInfo.alertImageUrl",
    "videoInfo.imageUrl",
    "videoSettings.alertImage",
    "videoSettings.liveImageUrl",
    "videoSettings.liveImage",
    "videoSettings.imageUrl",
    "alertImage",
    "liveImage",
    "liveImageUrl",
)
LAST_BELL_EVENTS_DETAIL_PATHS = (
    "lastBellEvents",
    "eventHistoryUrl",
    "videoInfo.lastBellEvents",
    "videoInfo.eventHistoryUrl",
    "securedDetails.lastBellEvents",
    "securedDetails.videoInfo.lastBellEvents",
    "securedDetails.videoInfo.eventHistoryUrl",
    "videoSettings.lastBellEvents",
    "videoSettings.eventHistoryUrl",
)
INTERCOM_CAMERA_CONTROL_TYPES_NORMALIZED = {
    normalize_state_name(value) for value in INTERCOM_CAMERA_CONTROL_TYPES
}
STREAM_OR_SNAPSHOT_STATE_CANDIDATES = (
    *STREAM_STATE_CANDIDATES,
    *SNAPSHOT_STATE_CANDIDATES,
)
SECURED_STREAM_DETAIL_PATHS = (
    "videoInfo.streamUrl",
    "streamUrl",
)
SECURED_SNAPSHOT_DETAIL_PATHS = (
    "videoInfo.alertImage",
    "videoInfo.liveImageUrl",
    "videoInfo.imageUrl",
    "alertImage",
    "liveImageUrl",
    "imageUrl",
)
INTERCOM_DYNAMIC_DETAIL_STATE_CANDIDATES = (
    "videoSettingsIntern",
    "videoSettingsExtern",
    "videoSettings",
    "videoInfo",
    "answers",
    "deviceState",
    "address",
)
INTERCOM_DYNAMIC_DETAIL_STATE_HINTS = (
    "video",
    "stream",
    "image",
    "snapshot",
    "history",
    "event",
    "bell",
    "answer",
    "address",
)
STREAM_KEY_HINTS = ("stream", "video", "mjpeg", "hls", "rtsp")
SNAPSHOT_KEY_HINTS = ("image", "snapshot", "alert", "live", "photo", "thumb")
HISTORY_KEY_HINTS = ("history", "event", "bell", "answer", "record")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = [
        LoxoneIntercomCameraEntity(bridge, control)
        for control in bridge.controls
        if _is_intercom_camera_control(control)
    ]
    async_add_entities(entities)


class LoxoneIntercomCameraEntity(LoxoneEntity, Camera):
    """Intercom camera entity exposing video stream and still image preview."""

    _attr_icon = "mdi:video-wireless"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, bridge, control: LoxoneControl) -> None:
        Camera.__init__(self)
        super().__init__(bridge, control, "Video")
        self._stream_state_name = first_matching_state_name(control, STREAM_STATE_CANDIDATES)
        self._snapshot_state_name = first_matching_state_name(control, SNAPSHOT_STATE_CANDIDATES)
        self._last_bell_events_state_name = intercom_history_state_name(control)
        self._address_state_name = intercom_address_state_name(control)
        self._dynamic_detail_state_names = _dynamic_intercom_state_names(control)
        self._secured_details: dict[str, Any] | None = None
        self._secured_details_loaded = False
        self._secured_details_lock = asyncio.Lock()

    async def stream_source(self) -> str | None:
        await self._ensure_secured_details_loaded()
        return self._stream_url()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        stream_url = self._stream_url()
        if stream_url is not None:
            attrs["stream_url"] = stream_url
        snapshot_url = self._snapshot_url()
        if snapshot_url is not None:
            attrs["snapshot_url"] = snapshot_url
        selected_history_image_url = self._selected_history_image_url()
        if selected_history_image_url is not None:
            attrs["selected_history_image_url"] = selected_history_image_url
        bell_events = self._last_bell_events_url()
        if bell_events is not None:
            attrs["last_bell_events_url"] = bell_events
            attrs["history_events_url"] = bell_events
        return attrs

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        del width, height

        await self._ensure_secured_details_loaded()
        image_url = self._snapshot_url() or self._stream_url()
        if image_url is None:
            return None

        session = getattr(self.bridge, "_session", None)
        if session is None:
            return None

        auth = BasicAuth(self.bridge.username, self.bridge.password)
        for request_auth in (auth, None):
            try:
                async with session.get(image_url, auth=request_auth) as response:
                    if response.status == 401 and request_auth is not None:
                        continue
                    response.raise_for_status()
                    return await response.read()
            except ClientError as err:
                _LOGGER.debug(
                    "Intercom preview request failed for %s (%s)",
                    self.control.uuid_action,
                    err,
                )
                if request_auth is None:
                    return None
        return None

    def _stream_url(self) -> str | None:
        address_value = (
            self.state_value(self._address_state_name)
            if self._address_state_name is not None
            else None
        )
        from_state = self.state_value(self._stream_state_name) if self._stream_state_name else None
        resolved_state = resolve_intercom_http_url(
            self.bridge,
            self.control,
            from_state,
            address_value=address_value,
        )
        if resolved_state is not None:
            return resolved_state
        from_details = _resolve_control_detail_url(
            self.bridge,
            self.control,
            STREAM_DETAIL_PATHS,
            address_value=address_value,
        )
        if from_details is not None:
            return from_details
        from_detail_payload = _resolve_url_from_payload_with_key_hints(
            self.bridge,
            self.control,
            self.control.details,
            key_hints=STREAM_KEY_HINTS,
            address_value=address_value,
        )
        if from_detail_payload is not None:
            return from_detail_payload
        from_dynamic_states = _resolve_url_from_intercom_state_payloads(
            self.bridge,
            self.control,
            self._dynamic_detail_state_names,
            state_value_getter=self.state_value,
            key_hints=STREAM_KEY_HINTS,
            address_value=address_value,
        )
        if from_dynamic_states is not None:
            return from_dynamic_states
        if self._secured_details is not None:
            from_secured_details = _resolve_detail_url(
                self.bridge,
                self._secured_details,
                SECURED_STREAM_DETAIL_PATHS,
                control=self.control,
                address_value=address_value,
            )
            if from_secured_details is not None:
                return from_secured_details
            return _resolve_url_from_payload_with_key_hints(
                self.bridge,
                self.control,
                self._secured_details,
                key_hints=STREAM_KEY_HINTS,
                address_value=address_value,
            )
        return None

    def _snapshot_url(self) -> str | None:
        address_value = (
            self.state_value(self._address_state_name)
            if self._address_state_name is not None
            else None
        )
        selected_history_image_url = self._selected_history_image_url(
            address_value=address_value
        )
        if selected_history_image_url is not None:
            return selected_history_image_url
        from_state = self.state_value(self._snapshot_state_name) if self._snapshot_state_name else None
        resolved_state = resolve_intercom_http_url(
            self.bridge,
            self.control,
            from_state,
            address_value=address_value,
        )
        if resolved_state is not None:
            return resolved_state
        from_details = _resolve_control_detail_url(
            self.bridge,
            self.control,
            SNAPSHOT_DETAIL_PATHS,
            address_value=address_value,
        )
        if from_details is not None:
            return from_details
        from_detail_payload = _resolve_url_from_payload_with_key_hints(
            self.bridge,
            self.control,
            self.control.details,
            key_hints=SNAPSHOT_KEY_HINTS,
            address_value=address_value,
        )
        if from_detail_payload is not None:
            return from_detail_payload
        from_dynamic_states = _resolve_url_from_intercom_state_payloads(
            self.bridge,
            self.control,
            self._dynamic_detail_state_names,
            state_value_getter=self.state_value,
            key_hints=SNAPSHOT_KEY_HINTS,
            address_value=address_value,
        )
        if from_dynamic_states is not None:
            return from_dynamic_states
        if self._secured_details is not None:
            from_secured_details = _resolve_detail_url(
                self.bridge,
                self._secured_details,
                SECURED_SNAPSHOT_DETAIL_PATHS,
                control=self.control,
                address_value=address_value,
            )
            if from_secured_details is not None:
                return from_secured_details
            return _resolve_url_from_payload_with_key_hints(
                self.bridge,
                self.control,
                self._secured_details,
                key_hints=SNAPSHOT_KEY_HINTS,
                address_value=address_value,
            )
        return None

    def _last_bell_events_url(self) -> str | None:
        address_value = (
            self.state_value(self._address_state_name)
            if self._address_state_name is not None
            else None
        )
        from_state = (
            self.state_value(self._last_bell_events_state_name)
            if self._last_bell_events_state_name
            else None
        )
        resolved_state = resolve_intercom_http_url(
            self.bridge,
            self.control,
            from_state,
            address_value=address_value,
        )
        if resolved_state is not None:
            return resolved_state
        from_details = _resolve_control_detail_url(
            self.bridge,
            self.control,
            LAST_BELL_EVENTS_DETAIL_PATHS,
            address_value=address_value,
        )
        if from_details is not None:
            return from_details
        from_detail_payload = _resolve_url_from_payload_with_key_hints(
            self.bridge,
            self.control,
            self.control.details,
            key_hints=HISTORY_KEY_HINTS,
            address_value=address_value,
        )
        if from_detail_payload is not None:
            return from_detail_payload
        return _resolve_url_from_intercom_state_payloads(
            self.bridge,
            self.control,
            self._dynamic_detail_state_names,
            state_value_getter=self.state_value,
            key_hints=HISTORY_KEY_HINTS,
            address_value=address_value,
        )

    def _selected_history_image_url(self, *, address_value: Any = None) -> str | None:
        selected_images = getattr(self.bridge, "_intercom_selected_history_images", None)
        if not isinstance(selected_images, Mapping):
            return None
        selected_value = selected_images.get(self.control.uuid_action)
        if selected_value is None:
            return None
        return resolve_intercom_http_url(
            self.bridge,
            self.control,
            selected_value,
            address_value=address_value,
        )

    async def _ensure_secured_details_loaded(self) -> None:
        if self._secured_details_loaded:
            return

        async with self._secured_details_lock:
            if self._secured_details_loaded:
                return

            send_action = getattr(self.bridge, "async_send_action", None)
            if send_action is None:
                self._secured_details_loaded = True
                return

            try:
                response = await send_action(self.control.uuid_action, "securedDetails")
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not load securedDetails for %s (%s)",
                    self.control.uuid_action,
                    err,
                )
                self._secured_details_loaded = True
                return

            value = response.get("value") if isinstance(response, Mapping) else None
            if isinstance(value, Mapping):
                self._secured_details = dict(value)
            self._secured_details_loaded = True


def _resolve_control_detail_url(
    bridge,
    control: LoxoneControl,
    detail_paths: tuple[str, ...],
    *,
    address_value: Any = None,
) -> str | None:
    return _resolve_detail_url(
        bridge,
        control.details,
        detail_paths,
        control=control,
        address_value=address_value,
    )


def _resolve_detail_url(
    bridge,
    details: Mapping[str, Any],
    detail_paths: tuple[str, ...],
    *,
    control: LoxoneControl | None = None,
    address_value: Any = None,
) -> str | None:
    for path in detail_paths:
        raw_value = _nested_detail_value(details, path)
        if control is not None:
            resolved = resolve_intercom_http_url(
                bridge,
                control,
                raw_value,
                address_value=address_value,
            )
        else:
            resolved = bridge.resolve_http_url(_coerce_text(raw_value))
        if resolved is not None:
            return resolved
    return None


def _dynamic_intercom_state_names(control: LoxoneControl) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()

    for candidate in INTERCOM_DYNAMIC_DETAIL_STATE_CANDIDATES:
        state_name = first_matching_state_name(control, (candidate,))
        if state_name is None or state_name in seen:
            continue
        names.append(state_name)
        seen.add(state_name)

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if not any(hint in normalized for hint in INTERCOM_DYNAMIC_DETAIL_STATE_HINTS):
            continue
        if state_name in seen:
            continue
        names.append(state_name)
        seen.add(state_name)

    return tuple(names)


def _resolve_url_from_intercom_state_payloads(
    bridge,
    control: LoxoneControl,
    state_names: tuple[str, ...],
    *,
    state_value_getter,
    key_hints: tuple[str, ...],
    address_value: Any = None,
) -> str | None:
    for state_name in state_names:
        state_value = state_value_getter(state_name)
        resolved = _resolve_url_from_payload_with_key_hints(
            bridge,
            control,
            state_value,
            key_hints=key_hints,
            address_value=address_value,
        )
        if resolved is not None:
            return resolved
    return None


def _resolve_url_from_payload_with_key_hints(
    bridge,
    control: LoxoneControl,
    payload: Any,
    *,
    key_hints: tuple[str, ...],
    address_value: Any = None,
) -> str | None:
    if payload is None:
        return None

    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return None
        if raw.startswith("{") or raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except ValueError:
                return resolve_intercom_http_url(
                    bridge,
                    control,
                    raw,
                    address_value=address_value,
                )
            return _resolve_url_from_payload_with_key_hints(
                bridge,
                control,
                parsed,
                key_hints=key_hints,
                address_value=address_value,
            )
        return resolve_intercom_http_url(
            bridge,
            control,
            raw,
            address_value=address_value,
        )

    stack: list[Any] = [payload]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        if isinstance(current, list):
            stack.extend(current)
            continue

        if not isinstance(current, Mapping):
            continue

        indicator_value = _first_mapping_value(
            current,
            ("name", "key", "id", "field", "type", "label"),
        )
        candidate_value = _first_mapping_value(
            current,
            ("url", "path", "href", "src", "value", "data"),
        )
        indicator_text = normalize_state_name(str(indicator_value)) if indicator_value else ""
        if indicator_text and any(hint in indicator_text for hint in key_hints):
            resolved = resolve_intercom_http_url(
                bridge,
                control,
                candidate_value,
                address_value=address_value,
            )
            if resolved is not None:
                return resolved

        for key, value in current.items():
            normalized_key = normalize_state_name(str(key))
            key_matches_hints = any(hint in normalized_key for hint in key_hints)

            if isinstance(value, Mapping):
                if key_matches_hints:
                    nested_candidate = _first_mapping_value(
                        value,
                        ("url", "path", "href", "src", "value", "data"),
                    )
                    resolved = resolve_intercom_http_url(
                        bridge,
                        control,
                        nested_candidate,
                        address_value=address_value,
                    )
                    if resolved is not None:
                        return resolved
                stack.append(value)
                continue
            if isinstance(value, list):
                stack.append(value)
                continue

            if key_matches_hints:
                resolved = resolve_intercom_http_url(
                    bridge,
                    control,
                    value,
                    address_value=address_value,
                )
                if resolved is not None:
                    return resolved

            if key_matches_hints and isinstance(value, str):
                stack.append(value)
                continue

            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    stack.append(stripped)

    return None


def _is_intercom_camera_control(control: LoxoneControl) -> bool:
    normalized_type = normalize_state_name(control.type)
    if normalized_type in INTERCOM_CAMERA_CONTROL_TYPES_NORMALIZED:
        return True

    normalized_states = {
        normalize_state_name(state_name) for state_name in control.states
    }
    detail_paths = (
        *STREAM_DETAIL_PATHS,
        *SNAPSHOT_DETAIL_PATHS,
        *LAST_BELL_EVENTS_DETAIL_PATHS,
    )
    if is_intercom_control(control):
        has_video_state = any(
            normalize_state_name(candidate) in normalized_states
            for candidate in STREAM_OR_SNAPSHOT_STATE_CANDIDATES
        )
        has_video_details = any(
            _nested_detail_value(control.details, path) is not None for path in detail_paths
        )
        if has_video_state or has_video_details:
            return True

    for candidate in STREAM_OR_SNAPSHOT_STATE_CANDIDATES:
        if normalize_state_name(candidate) in normalized_states:
            return True

    return any(_nested_detail_value(control.details, path) is not None for path in detail_paths)


def _nested_detail_value(details: Mapping[str, Any], path: str) -> Any:
    current: Any = details
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = _mapping_get_case_insensitive(current, part)
    return current


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_mapping_value(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = _mapping_get_case_insensitive(mapping, key)
        if value is not None:
            return value
    return None


def _mapping_get_case_insensitive(mapping: Mapping[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]

    wanted = normalize_state_name(key)
    for current_key, value in mapping.items():
        if isinstance(current_key, str) and normalize_state_name(current_key) == wanted:
            return value
    return None
