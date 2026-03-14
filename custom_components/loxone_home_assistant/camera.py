"""Camera platform for Loxone intercom video preview."""

from __future__ import annotations

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

from .const import DOMAIN, INTERCOM_CAMERA_CONTROL_TYPES
from .entity import LoxoneEntity, first_matching_state_name
from .models import LoxoneControl

_LOGGER = logging.getLogger(__name__)

STREAM_STATE_CANDIDATES = (
    "streamUrl",
    "videoStream",
    "videoUrl",
)
SNAPSHOT_STATE_CANDIDATES = (
    "alertImage",
    "liveImage",
    "image",
    "snapshot",
)
STREAM_DETAIL_PATHS = (
    "videoInfo.streamUrl",
    "streamUrl",
)
SNAPSHOT_DETAIL_PATHS = (
    "videoInfo.liveImageUrl",
    "videoInfo.imageUrl",
    "liveImageUrl",
)
LAST_BELL_EVENTS_DETAIL_PATHS = ("lastBellEvents",)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = hass.data[DOMAIN]["bridges"][entry.entry_id]
    entities = [
        LoxoneIntercomCameraEntity(bridge, control)
        for control in bridge.controls
        if control.type in INTERCOM_CAMERA_CONTROL_TYPES
    ]
    async_add_entities(entities)


class LoxoneIntercomCameraEntity(LoxoneEntity, Camera):
    """Intercom camera entity exposing video stream and still image preview."""

    _attr_icon = "mdi:video-wireless"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "Video")
        self._stream_state_name = first_matching_state_name(control, STREAM_STATE_CANDIDATES)
        self._snapshot_state_name = first_matching_state_name(control, SNAPSHOT_STATE_CANDIDATES)

    async def stream_source(self) -> str | None:
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
        bell_events = _resolve_control_detail_url(self.bridge, self.control, LAST_BELL_EVENTS_DETAIL_PATHS)
        if bell_events is not None:
            attrs["last_bell_events_url"] = bell_events
        return attrs

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        del width, height

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
        from_state = self.state_value(self._stream_state_name) if self._stream_state_name else None
        resolved_state = self.bridge.resolve_http_url(_coerce_text(from_state))
        if resolved_state is not None:
            return resolved_state
        return _resolve_control_detail_url(self.bridge, self.control, STREAM_DETAIL_PATHS)

    def _snapshot_url(self) -> str | None:
        from_state = self.state_value(self._snapshot_state_name) if self._snapshot_state_name else None
        resolved_state = self.bridge.resolve_http_url(_coerce_text(from_state))
        if resolved_state is not None:
            return resolved_state
        return _resolve_control_detail_url(self.bridge, self.control, SNAPSHOT_DETAIL_PATHS)


def _resolve_control_detail_url(bridge, control: LoxoneControl, detail_paths: tuple[str, ...]) -> str | None:
    for path in detail_paths:
        raw_value = _nested_detail_value(control.details, path)
        resolved = bridge.resolve_http_url(_coerce_text(raw_value))
        if resolved is not None:
            return resolved
    return None


def _nested_detail_value(details: Mapping[str, Any], path: str) -> Any:
    current: Any = details
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
