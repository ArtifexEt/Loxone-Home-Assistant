"""Camera platform for the Loxone Intercom."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from enum import IntFlag
from typing import Any

try:
    from aiohttp import ClientError, web
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    class ClientError(Exception):
        """Fallback network error type used in tests without aiohttp."""

    class _FallbackResponse:
        def __init__(
            self,
            *,
            status: int = 200,
            body: bytes | None = None,
            content_type: str | None = None,
            headers: Mapping[str, str] | None = None,
        ) -> None:
            self.status = status
            self.body = body
            self.content_type = content_type
            self.headers = dict(headers or {})

    class _FallbackStreamResponse(_FallbackResponse):
        async def prepare(self, request) -> None:
            del request

        async def write(self, chunk: bytes) -> None:
            current = self.body or b""
            self.body = current + chunk

        async def write_eof(self) -> None:
            return None

    class _FallbackWeb:
        Response = _FallbackResponse
        StreamResponse = _FallbackStreamResponse

    web = _FallbackWeb()  # type: ignore[assignment]

from homeassistant.components.camera import Camera
try:
    from homeassistant.components.camera import CameraEntityCapabilities
except ImportError:
    CameraEntityCapabilities = None

try:
    from homeassistant.components.camera import CameraEntityFeature
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    class CameraEntityFeature(IntFlag):  # type: ignore[no-redef]
        STREAM = 1 << 0

try:
    CAMERA_FEATURE_NONE = CameraEntityFeature(0)
except TypeError:  # pragma: no cover - defensive for minimal stubs
    CAMERA_FEATURE_NONE = 0
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import LoxoneEntity, normalize_state_name
from .intercom import is_intercom_control
from .intercom_archive import (
    GEN2_ARCHIVE_SELECTION_PREFIX,
    async_gen2_archive_image,
    is_gen2_archive_control,
)
from .intercom_media import (
    HISTORY_URL_DETAIL_PATHS,
    INTERCOM_DYNAMIC_PAYLOAD_STATE_NAMES,
    LAST_BELL_EVENTS_DETAIL_PATHS,
    SNAPSHOT_DETAIL_PATHS,
    STREAM_DETAIL_PATHS,
    async_intercom_secured_details,
    get_media_response,
    intercom_history_image_url,
    intercom_last_bell_events,
    intercom_request_auth,
    intercom_selected_history_timestamp,
    intercom_snapshot_url,
    intercom_stream_url,
)
from .models import LoxoneControl
from .runtime import entry_bridge

_LOGGER = logging.getLogger(__name__)

_SNAPSHOT_TIMEOUT = 4
_MJPEG_CHUNK_SIZE = 16 * 1024
_MJPEG_MAX_SCAN_BYTES = 4 * 1024 * 1024
_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
INTERCOM_CAMERA_DETAIL_PATHS = (
    *STREAM_DETAIL_PATHS,
    *SNAPSHOT_DETAIL_PATHS,
    *LAST_BELL_EVENTS_DETAIL_PATHS,
    *HISTORY_URL_DETAIL_PATHS,
)
INTERCOM_CAMERA_STATE_HINTS = frozenset(
    {
        *(
            normalize_state_name(detail_path.rsplit(".", 1)[-1])
            for detail_path in INTERCOM_CAMERA_DETAIL_PATHS
        ),
        *(normalize_state_name(state_name) for state_name in INTERCOM_DYNAMIC_PAYLOAD_STATE_NAMES),
    }
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = []
    loader = None
    if (
        CameraEntityCapabilities is not None
        and getattr(bridge, "use_tls", True)
        and not getattr(bridge, "_remote_serial", None)
    ):
        from .intercom_webrtc_camera import (
            IntercomRtcLoader,
            IntercomSnapshots,
            LoxoneIntercomWebRTCCamera,
        )

        loader = IntercomRtcLoader(hass)

    for control in bridge.controls:
        snapshots = None
        if loader is not None and normalize_state_name(control.type) == "intercomv2":
            device_uuid = control.details.get("deviceUuid")
            if isinstance(device_uuid, str) and device_uuid and not control.details.get("trustMember"):
                snapshots = IntercomSnapshots(loader, bridge, device_uuid)
                entities.append(LoxoneIntercomWebRTCCamera(bridge, control, snapshots))
        if (
            snapshots is not None
            or is_gen2_archive_control(bridge, control)
            or _is_intercom_camera_control(control)
        ):
            entities.append(
                LoxoneIntercomCameraEntity(
                    bridge, control, native_snapshot=snapshots.async_image if snapshots else None
                )
            )
    async_add_entities(entities)


class LoxoneIntercomCameraEntity(LoxoneEntity, Camera):
    """Expose the Loxone Intercom as a native MJPEG camera."""

    _attr_icon = "mdi:video-wireless"
    _attr_supported_features = CAMERA_FEATURE_NONE

    def __init__(self, bridge, control: LoxoneControl, *, native_snapshot=None) -> None:
        Camera.__init__(self)
        super().__init__(bridge, control, "Video")
        self._secured_details: Mapping[str, Any] | None = None
        self._last_live_image: bytes | None = None
        self._native_snapshot = native_snapshot
        self._attr_entity_picture = None

    async def stream_source(self) -> str | None:
        """Disable Home Assistant stream/WebRTC for the Intercom."""
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        history_tokens = self._history_timestamps()
        if history_tokens:
            attrs["history_count"] = len(history_tokens)

        return attrs

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        del width, height

        selected_history = self._selected_history_timestamp()
        if selected_history is not None and selected_history.startswith(GEN2_ARCHIVE_SELECTION_PREFIX):
            return await async_gen2_archive_image(self.bridge, self.control, selected_history)

        await self._ensure_secured_details_loaded()
        for image_url in self._image_url_candidates(selected_history):
            image = await self._async_fetch_image(image_url)
            if image is None:
                continue
            if selected_history is None:
                self._last_live_image = image
            return image
        if selected_history is not None:
            return None
        if self._native_snapshot is not None:
            image = await self._native_snapshot()
            if image is not None:
                self._last_live_image = image
        return self._last_live_image

    async def handle_async_mjpeg_stream(self, request) -> Any:
        """Proxy the raw MJPEG stream directly from the Intercom."""
        await self._ensure_secured_details_loaded()

        stream_url = self._stream_url()
        if self._selected_history_timestamp() is not None or stream_url is None:
            image = await self.async_camera_image()
            if image is None or web is None:
                return _web_response(status=404)
            return web.Response(
                body=image,
                content_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )

        session = getattr(self.bridge, "_session", None)
        if session is None:
            return _web_response(status=503)

        try:
            request_auth = intercom_request_auth(
                self.bridge, stream_url, control=self.control, secured_details=self._secured_details
            )
            async with get_media_response(session, stream_url, request_auth) as upstream:
                if upstream.status != 200:
                    return _web_response(status=upstream.status if upstream.status >= 400 else 502)
                if web is None:
                    return _web_response(status=502)

                content_type = upstream.headers.get("Content-Type")
                media_type = _response_content_type(upstream)
                if content_type is None or not (
                    media_type in _IMAGE_MEDIA_TYPES or media_type == "multipart/x-mixed-replace"
                ):
                    return _web_response(status=502)
                response = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": content_type,
                        "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
                await response.prepare(request)
                try:
                    async for chunk in upstream.content.iter_chunked(_MJPEG_CHUNK_SIZE):
                        if not chunk:
                            continue
                        await response.write(chunk)
                except ConnectionResetError:
                    return response
                await response.write_eof()
                return response
        except asyncio.CancelledError:
            raise
        except ClientError:
            _LOGGER.debug("Intercom MJPEG proxy failed")
            return _web_response(status=502)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Intercom MJPEG proxy errored (%s)",
                type(err).__name__,
            )
            return _web_response(status=500)

    def _stream_url(self) -> str | None:
        return intercom_stream_url(
            self.bridge,
            self.control,
            secured_details=self._secured_details,
            state_value_getter=self.state_value,
        )

    def _snapshot_url(self) -> str | None:
        return intercom_snapshot_url(
            self.bridge,
            self.control,
            secured_details=self._secured_details,
            state_value_getter=self.state_value,
        )

    def _history_timestamps(self) -> tuple[str, ...]:
        return intercom_last_bell_events(
            self.bridge,
            self.control,
            secured_details=self._secured_details,
            state_value_getter=self.state_value,
        )

    def _selected_history_timestamp(self) -> str | None:
        return intercom_selected_history_timestamp(self.bridge, self.control.uuid_action)

    def _image_url_candidates(self, selected_timestamp: str | None) -> tuple[str, ...]:
        if selected_timestamp is not None:
            selected_url = intercom_history_image_url(
                self.bridge,
                self.control,
                selected_timestamp,
            )
            return (selected_url,) if selected_url is not None else ()

        candidates: list[str] = []
        for image_url in (self._snapshot_url(), self._stream_url()):
            if image_url is None or image_url in candidates:
                continue
            candidates.append(image_url)
        return tuple(candidates)

    async def _async_fetch_image(self, image_url: str) -> bytes | None:
        session = getattr(self.bridge, "_session", None)
        if session is None:
            return None

        try:
            request_auth = intercom_request_auth(
                self.bridge, image_url, control=self.control, secured_details=self._secured_details
            )
            async with asyncio.timeout(_SNAPSHOT_TIMEOUT), get_media_response(
                session, image_url, request_auth
            ) as response:
                if response.status != 200:
                    return None
                content_type = _response_content_type(response)
                if content_type in (None, "application/octet-stream", "multipart/x-mixed-replace"):
                    return await _extract_first_mjpeg_frame(response)
                if content_type in _IMAGE_MEDIA_TYPES:
                    return await _read_still_image(response)
        except asyncio.CancelledError:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug(
                "Intercom image fetch failed (%s)",
                type(err).__name__,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Intercom image fetch errored (%s)",
                type(err).__name__,
            )
        return None

    async def _ensure_secured_details_loaded(self) -> None:
        if self._secured_details is None:
            self._secured_details = await async_intercom_secured_details(self.bridge, self.control)


def _is_intercom_camera_control(control: LoxoneControl) -> bool:
    if not is_intercom_control(control):
        return False

    if _control_has_camera_details(control.details):
        return True

    secured_details = (
        control.details.get("securedDetails") if isinstance(control.details, Mapping) else None
    )
    if secured_details is True or _control_has_camera_details(secured_details):
        return True

    return any(
        normalize_state_name(state_name) in INTERCOM_CAMERA_STATE_HINTS
        for state_name in control.states
    )


def _control_has_camera_details(details: Any) -> bool:
    if not isinstance(details, Mapping):
        return False
    return any(_mapping_has_detail_path(details, detail_path) for detail_path in INTERCOM_CAMERA_DETAIL_PATHS)


def _mapping_has_detail_path(details: Mapping[str, Any], detail_path: str) -> bool:
    current: Any = details
    for segment in detail_path.split("."):
        if not isinstance(current, Mapping):
            return False
        current = _case_insensitive_mapping_value(current, segment)
        if current is None:
            return False
    return True


def _case_insensitive_mapping_value(details: Mapping[str, Any], key: str) -> Any:
    if key in details:
        return details[key]

    normalized_key = normalize_state_name(key)
    for current_key, current_value in details.items():
        if isinstance(current_key, str) and normalize_state_name(current_key) == normalized_key:
            return current_value
    return None


def _response_content_type(response: Any) -> str | None:
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        value = headers.get("Content-Type") or headers.get("content-type")
        if isinstance(value, str):
            cleaned = value.split(";", 1)[0].strip().casefold()
            if cleaned:
                return cleaned

    value = getattr(response, "content_type", None)
    if isinstance(value, str):
        cleaned = value.split(";", 1)[0].strip().casefold()
        if cleaned:
            return cleaned
    return None


async def _extract_first_mjpeg_frame(response: Any) -> bytes | None:
    content = getattr(response, "content", None)
    if content is None:
        payload = await response.read()
        return _extract_jpeg_from_bytes(payload)

    buffer = bytearray()
    async for chunk in content.iter_chunked(_MJPEG_CHUNK_SIZE):
        if not chunk:
            continue
        buffer.extend(chunk)
        frame = _extract_jpeg_from_bytes(buffer)
        if frame is not None:
            return frame
        if len(buffer) >= _MJPEG_MAX_SCAN_BYTES:
            break
    return _extract_jpeg_from_bytes(buffer)


async def _read_still_image(response: Any) -> bytes | None:
    image = bytearray()
    async for chunk in response.content.iter_chunked(_MJPEG_CHUNK_SIZE):
        if len(image) + len(chunk) > _MJPEG_MAX_SCAN_BYTES:
            return None
        image.extend(chunk)
    return bytes(image) if image else None


def _extract_jpeg_from_bytes(payload: bytes | bytearray) -> bytes | None:
    start = payload.find(b"\xff\xd8")
    if start < 0:
        return None
    end = payload.find(b"\xff\xd9", start + 2)
    if end < 0:
        return None
    frame = payload[start : end + 2]
    return bytes(frame) if frame else None


def _web_response(*, status: int) -> Any:
    if web is None:  # pragma: no cover - only used in stripped test stubs
        return status
    return web.Response(status=status)
