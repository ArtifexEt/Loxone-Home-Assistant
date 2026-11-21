"""Camera platform for the Loxone Intercom."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from aiohttp import ClientError, StreamReader, web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

try:
    from homeassistant.components.camera import CameraEntityCapabilities
except ImportError:  # Added with Home Assistant's two-way WebRTC audio.
    CameraEntityCapabilities = None

from .entity import LoxoneEntity, normalize_state_name
from .intercom import intercom_detail_value, is_intercom_control
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
    intercom_history_selection_signal,
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
_IMAGE_MEDIA_TYPES = frozenset({'image/jpeg', 'image/png', 'image/webp', 'image/gif'})
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
    bridge = entry_bridge(entry)
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
        device_uuid = _local_intercom_device_uuid(control)
        if loader is not None and device_uuid is not None:
            snapshots = IntercomSnapshots(loader, bridge, device_uuid)
            entities.append(LoxoneIntercomWebRTCCamera(bridge, control, snapshots))
        if snapshots is None and not (
            is_gen2_archive_control(bridge, control) or _is_intercom_camera_control(control)
        ):
            continue
        entities.append(
            LoxoneIntercomCameraEntity(
                bridge, control, native_snapshot=snapshots.async_image if snapshots else None
            )
        )
    async_add_entities(entities)


def _local_intercom_device_uuid(control: LoxoneControl) -> str | None:
    if normalize_state_name(control.type) != "intercomv2" or control.details.get("trustMember"):
        return None
    device_uuid = control.details.get("deviceUuid")
    return device_uuid if isinstance(device_uuid, str) and device_uuid else None


class LoxoneIntercomCameraEntity(LoxoneEntity, Camera):
    """Expose the Loxone Intercom as a native MJPEG camera."""

    _attr_icon = "mdi:video-wireless"
    _attr_supported_features = CameraEntityFeature(0)

    def __init__(self, bridge, control: LoxoneControl, *, native_snapshot=None) -> None:
        Camera.__init__(self)
        super().__init__(bridge, control, "Video")
        self._set_intercom_entity_name("camera", "intercom_video")
        self._secured_details: Mapping[str, Any] | None = None
        self._last_live_image: bytes | None = None
        self._native_snapshot = native_snapshot
        self._attr_entity_picture = None

    async def async_added_to_hass(self) -> None:
        """Publish the Home Assistant history link once this camera is registered."""
        from homeassistant.helpers.dispatcher import async_dispatcher_connect

        from .intercom_gallery import async_add_intercom_history_device_link

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass,
            intercom_history_selection_signal(self.bridge, self.control.uuid_action),
            self._async_history_selection_changed,
        ))
        async_add_intercom_history_device_link(self.hass, self.registry_entry, self.bridge, self.control)

    @callback
    def _async_history_selection_changed(self) -> None:
        """Refresh Home Assistant's camera thumbnail for the selected photo."""
        self.async_update_token()
        self.async_write_ha_state()

    async def stream_source(self) -> str | None:
        """Keep the Photo preview separate from the Live WebRTC camera."""
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

        selected_timestamp = self._selected_history_timestamp()
        if selected_timestamp is not None and selected_timestamp.startswith(GEN2_ARCHIVE_SELECTION_PREFIX):
            return await async_gen2_archive_image(self.bridge, self.control, selected_timestamp)

        await self._ensure_secured_details_loaded()
        if selected_timestamp is not None:
            image_url = intercom_history_image_url(self.bridge, self.control, selected_timestamp)
            return await self._async_fetch_image(image_url) if image_url else None

        for image_url in self._image_url_candidates():
            image = await self._async_fetch_image(image_url)
            if image is not None:
                self._last_live_image = image
                return image
        if self._native_snapshot is not None:
            image = await self._native_snapshot()
            if image is not None:
                self._last_live_image = image
        return self._last_live_image

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse:
        """Proxy the raw MJPEG stream or return the selected still image."""
        if self._selected_history_timestamp() is not None:
            return _still_image_response(await self.async_camera_image())

        await self._ensure_secured_details_loaded()
        stream_url = self._stream_url()
        if stream_url is None:
            return _still_image_response(await self.async_camera_image())

        session = self.bridge._session
        try:
            request_auth = intercom_request_auth(
                self.bridge, stream_url, control=self.control, secured_details=self._secured_details
            )
            async with get_media_response(session, stream_url, request_auth) as upstream:
                if upstream.status != 200:
                    status = upstream.status if upstream.status >= 400 else 502
                    return web.Response(status=status)
                if not _is_image_stream(upstream.content_type):
                    return web.Response(status=502)
                content_type = upstream.headers.get("Content-Type")
                if content_type is None:
                    return web.Response(status=502)
                response = web.StreamResponse(
                    headers={
                        "Content-Type": content_type,
                        "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
                await response.prepare(request)
                try:
                    async for chunk in upstream.content.iter_chunked(_MJPEG_CHUNK_SIZE):
                        await response.write(chunk)
                    await response.write_eof()
                except (ClientError, OSError):
                    # Headers have already been sent, so let the client reconnect.
                    return response
                return response
        except (ClientError, OSError, ValueError) as err:
            _LOGGER.debug("Intercom stream fetch failed (%s)", type(err).__name__)
            return web.Response(status=502)

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
            self.control,
            secured_details=self._secured_details,
            state_value_getter=self.state_value,
        )

    def _selected_history_timestamp(self) -> str | None:
        return intercom_selected_history_timestamp(self.bridge, self.control.uuid_action)

    def _image_url_candidates(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(url for url in (self._snapshot_url(), self._stream_url()) if url)
        )

    async def _async_fetch_image(self, image_url: str) -> bytes | None:
        session = self.bridge._session
        try:
            request_auth = intercom_request_auth(
                self.bridge, image_url, control=self.control, secured_details=self._secured_details
            )
            async with asyncio.timeout(_SNAPSHOT_TIMEOUT), get_media_response(
                session, image_url, request_auth
            ) as response:
                if response.status != 200:
                    return None
                content_type = response.content_type
                if content_type in ("application/octet-stream", "multipart/x-mixed-replace"):
                    return await _extract_first_mjpeg_frame(response.content)
                if content_type in _IMAGE_MEDIA_TYPES:
                    return await _read_still_image(response.content)
        except (ClientError, OSError, ValueError) as err:
            _LOGGER.debug(
                "Intercom image fetch failed (%s)",
                type(err).__name__,
            )
        return None

    async def _ensure_secured_details_loaded(self) -> None:
        if self._secured_details is None:
            self._secured_details = await async_intercom_secured_details(self.bridge, self.control)


def _is_intercom_camera_control(control: LoxoneControl) -> bool:
    if not is_intercom_control(control):
        return False
    secured_details = intercom_detail_value(control.details, "securedDetails")
    if secured_details is True:
        return True
    details = (control.details, secured_details)
    return any(
        intercom_detail_value(source, path) is not None
        for source in details
        for path in INTERCOM_CAMERA_DETAIL_PATHS
    ) or any(normalize_state_name(name) in INTERCOM_CAMERA_STATE_HINTS for name in control.states)


def _is_image_stream(content_type: str) -> bool:
    return content_type in _IMAGE_MEDIA_TYPES or content_type == "multipart/x-mixed-replace"


async def _extract_first_mjpeg_frame(content: StreamReader) -> bytes | None:
    buffer = bytearray()
    start = -1
    async for chunk in content.iter_chunked(_MJPEG_CHUNK_SIZE):
        scan_from = max(0, len(buffer) - 1)
        buffer.extend(chunk[: _MJPEG_MAX_SCAN_BYTES - len(buffer)])
        if start == -1:
            start = buffer.find(b"\xff\xd8", scan_from)
        if start >= 0:
            end = buffer.find(b"\xff\xd9", max(scan_from, start + 2))
            if end >= 0:
                return bytes(buffer[start : end + 2])
        if len(buffer) >= _MJPEG_MAX_SCAN_BYTES:
            break
    return None


async def _read_still_image(content: StreamReader) -> bytes | None:
    image = bytearray()
    async for chunk in content.iter_chunked(_MJPEG_CHUNK_SIZE):
        if len(image) + len(chunk) > _MJPEG_MAX_SCAN_BYTES:
            return None
        image.extend(chunk)
    return bytes(image) if image else None


def _still_image_response(image: bytes | None) -> web.Response:
    if image is None:
        return web.Response(status=404)
    return web.Response(
        body=image,
        content_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
