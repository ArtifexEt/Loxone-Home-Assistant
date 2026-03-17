"""Camera platform for the Loxone Intercom."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from enum import IntFlag
from typing import Any
from urllib.parse import urlsplit

try:
    from aiohttp import BasicAuth, ClientError, web
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    class ClientError(Exception):
        """Fallback network error type used in tests without aiohttp."""

    class BasicAuth:  # type: ignore[no-redef]
        """Fallback auth container used in tests without aiohttp."""

        def __init__(self, login: str, password: str) -> None:
            self.login = login
            self.password = password

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
from .intercom_media import (
    INTERCOM_DYNAMIC_PAYLOAD_STATE_NAMES,
    LAST_BELL_EVENTS_DETAIL_PATHS,
    SNAPSHOT_DETAIL_PATHS,
    STREAM_DETAIL_PATHS,
    intercom_auth_credentials,
    intercom_history_image_url,
    intercom_last_bell_events,
    intercom_selected_history_timestamp,
    intercom_snapshot_url,
    intercom_stream_url,
    miniserver_auth_credentials,
)
from .models import LoxoneControl
from .runtime import entry_bridge

_LOGGER = logging.getLogger(__name__)

_MJPEG_CHUNK_SIZE = 16 * 1024
_MJPEG_MAX_SCAN_BYTES = 4 * 1024 * 1024
INTERCOM_CAMERA_DETAIL_PATHS = (
    *STREAM_DETAIL_PATHS,
    *SNAPSHOT_DETAIL_PATHS,
    *LAST_BELL_EVENTS_DETAIL_PATHS,
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
    entities = [
        LoxoneIntercomCameraEntity(bridge, control)
        for control in bridge.controls
        if _is_intercom_camera_control(control)
    ]
    async_add_entities(entities)


class LoxoneIntercomCameraEntity(LoxoneEntity, Camera):
    """Expose the Loxone Intercom as a native MJPEG camera."""

    _attr_icon = "mdi:video-wireless"
    _attr_supported_features = CAMERA_FEATURE_NONE

    def __init__(self, bridge, control: LoxoneControl) -> None:
        Camera.__init__(self)
        super().__init__(bridge, control, "Video")
        self._secured_details: dict[str, Any] | None = None
        self._secured_details_loaded = False
        self._secured_details_lock = asyncio.Lock()

    async def stream_source(self) -> str | None:
        """Disable Home Assistant stream/WebRTC for the Intercom."""
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        stream_url = self._stream_url()
        if stream_url is not None:
            attrs["stream_url"] = stream_url

        snapshot_url = self._snapshot_url()
        if snapshot_url is not None:
            attrs["snapshot_url"] = snapshot_url

        history_tokens = self._history_timestamps()
        if history_tokens:
            attrs["history_timestamps"] = list(history_tokens)
            attrs["history_count"] = len(history_tokens)

        selected_timestamp = self._selected_history_timestamp()
        if selected_timestamp is not None:
            attrs["selected_history_timestamp"] = selected_timestamp
            selected_image_url = intercom_history_image_url(
                self.bridge,
                self.control,
                selected_timestamp,
            )
            if selected_image_url is not None:
                attrs["selected_history_image_url"] = selected_image_url

        return attrs

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        del width, height

        await self._ensure_secured_details_loaded()
        image_url = self._current_image_url()
        if image_url is None:
            return None

        session = getattr(self.bridge, "_session", None)
        if session is None:
            return None

        request_auth = self._request_auth_for_url(image_url)
        try:
            async with session.get(image_url, auth=request_auth) as response:
                response.raise_for_status()
                content_type = _response_content_type(response)
                if content_type == "multipart/x-mixed-replace":
                    return await _extract_first_mjpeg_frame(response)
                payload = await response.read()
                return payload or None
        except asyncio.CancelledError:
            raise
        except ClientError as err:
            _LOGGER.debug(
                "Intercom image fetch failed for %s from %s (%s)",
                self.control.uuid_action,
                image_url,
                err,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Intercom image fetch errored for %s from %s (%s)",
                self.control.uuid_action,
                image_url,
                err,
            )
        return None

    async def handle_async_mjpeg_stream(self, request) -> Any:
        """Proxy the raw MJPEG stream directly from the Intercom."""
        await self._ensure_secured_details_loaded()

        selected_timestamp = self._selected_history_timestamp()
        if selected_timestamp is not None:
            image = await self.async_camera_image()
            if image is None or web is None:
                return _web_response(status=404)
            return web.Response(
                body=image,
                content_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )

        stream_url = self._stream_url()
        if stream_url is None:
            return _web_response(status=404)

        session = getattr(self.bridge, "_session", None)
        if session is None:
            return _web_response(status=503)

        request_auth = self._request_auth_for_url(stream_url)
        try:
            async with session.get(stream_url, auth=request_auth) as upstream:
                if upstream.status != 200 or web is None:
                    return _web_response(status=upstream.status)

                content_type = upstream.headers.get(
                    "Content-Type",
                    "multipart/x-mixed-replace",
                )
                response = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": content_type,
                        "Cache-Control": "no-store",
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
        except ClientError as err:
            _LOGGER.debug(
                "Intercom MJPEG proxy failed for %s from %s (%s)",
                self.control.uuid_action,
                stream_url,
                err,
            )
            return _web_response(status=502)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Intercom MJPEG proxy errored for %s from %s (%s)",
                self.control.uuid_action,
                stream_url,
                err,
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

    def _current_image_url(self) -> str | None:
        selected_timestamp = self._selected_history_timestamp()
        if selected_timestamp is not None:
            return intercom_history_image_url(self.bridge, self.control, selected_timestamp)

        snapshot_url = self._snapshot_url()
        if snapshot_url is not None:
            return snapshot_url
        return self._stream_url()

    def _request_auth_for_url(self, url: str) -> BasicAuth | None:
        if self._is_miniserver_url(url):
            username, password = miniserver_auth_credentials(self.bridge)
        else:
            username, password = intercom_auth_credentials(self.bridge)
        if username is None:
            return None
        return BasicAuth(username, password)

    def _is_miniserver_url(self, url: str) -> bool:
        parsed = urlsplit(url)
        if not parsed.hostname:
            return False

        if parsed.hostname != str(getattr(self.bridge, "host", "")):
            return False

        bridge_port = int(getattr(self.bridge, "port", 0) or 0)
        if parsed.port is not None:
            return parsed.port == bridge_port

        default_port = 443 if bool(getattr(self.bridge, "use_tls", True)) else 80
        return bridge_port == default_port

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


def _is_intercom_camera_control(control: LoxoneControl) -> bool:
    if not is_intercom_control(control):
        return False

    if _control_has_camera_details(control.details):
        return True

    secured_details = (
        control.details.get("securedDetails") if isinstance(control.details, Mapping) else None
    )
    if _control_has_camera_details(secured_details):
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
