"""Home Assistant WebRTC camera for a Loxone Intercom Gen 2."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
import logging
from time import monotonic
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityCapabilities, CameraEntityFeature
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCError
from homeassistant.core import callback
from homeassistant.requirements import async_process_requirements

from .const import DOMAIN
from .entity import LoxoneEntity
from .intercom_peer import IntercomPeer, IntercomPeerError, async_intercom_snapshot

_LOGGER = logging.getLogger(__name__)
_MEDIA_REQUIREMENT = "aiortc==1.16.0"
_MEDIA_RETRY_INTERVAL = 30
_SNAPSHOT_INTERVAL = 10
_OFFER_TIMEOUT = 16
_MAX_QUEUED_CANDIDATES = 64


class IntercomRtcLoader:
    """Load optional media libraries without holding up the integration setup."""

    def __init__(self, hass) -> None:
        self._hass = hass
        self._task: asyncio.Task | None = None
        self._retry_at = 0.0

    async def async_load(self):
        if self._task is not None and self._retry_at and monotonic() >= self._retry_at:
            self._task = None
            self._retry_at = 0.0
        if self._task is None:
            self._task = self._hass.async_create_background_task(
                self._async_install_and_import(), name="loxone_intercom_media"
            )
        return await asyncio.shield(self._task)

    async def _async_install_and_import(self):
        try:
            await async_process_requirements(
                self._hass, DOMAIN, [_MEDIA_REQUIREMENT], is_built_in=False
            )
            return await self._hass.async_add_executor_job(_import_rtc)
        except Exception:
            self._retry_at = monotonic() + _MEDIA_RETRY_INTERVAL
            raise IntercomPeerError("The Intercom WebRTC media dependency is unavailable.") from None


def _import_rtc():
    return (
        import_module("aiortc"),
        import_module("aiortc.contrib.media"),
        import_module("aiortc.sdp"),
    )


class IntercomSnapshots:
    """Throttled still-image capture shared by the preview and live camera."""

    def __init__(self, loader: IntercomRtcLoader, bridge, device_uuid: str) -> None:
        self.loader = loader
        self.bridge = bridge
        self.device_uuid = device_uuid
        self._lock = asyncio.Lock()
        self._last_attempt = -float("inf")
        self._image: bytes | None = None

    async def async_image(self) -> bytes | None:
        try:
            async with asyncio.timeout(8):
                await self.loader.async_load()
                async with self._lock:
                    if monotonic() - self._last_attempt >= _SNAPSHOT_INTERVAL:
                        self._last_attempt = monotonic()
                        self._image = await async_intercom_snapshot(self.bridge, self.device_uuid)
                    return self._image
        except (IntercomPeerError, TimeoutError):
            return self._image


@dataclass
class _ClientSession:
    """Resources owned by one Home Assistant viewing session."""

    connection: Any = None
    intercom: IntercomPeer | None = None
    discarded_media: Any = None
    negotiation: asyncio.Task | None = None
    disconnect_watch: asyncio.Task | None = None
    parse_candidate: Callable | None = None
    remote_description_set: bool = False
    candidates: list = field(default_factory=list)


class LoxoneIntercomWebRTCCamera(LoxoneEntity, Camera):
    """Expose live video and on-demand talk without replacing the history camera."""

    _attr_icon = "mdi:video-wireless"
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_camera_entity_capabilities = CameraEntityCapabilities(two_way_audio=True)

    def __init__(self, bridge, control, snapshots: IntercomSnapshots) -> None:
        Camera.__init__(self)
        LoxoneEntity.__init__(self, bridge, control, "Live")
        self._set_intercom_entity_name("camera", "intercom_live")
        self._snapshots = snapshots
        self._sessions: dict[str, _ClientSession] = {}
        self._closing_tasks: set[asyncio.Task] = set()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return await self._snapshots.async_image()

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message) -> None:
        self.close_webrtc_session(session_id)
        session = self._sessions[session_id] = _ClientSession()
        session.negotiation = self.hass.async_create_task(
            self._async_negotiate(session_id, session, offer_sdp, send_message),
            name="loxone_intercom_webrtc",
        )
        try:
            await session.negotiation
        except asyncio.CancelledError:
            if self._sessions.get(session_id) is session:
                self.close_webrtc_session(session_id)
                raise

    async def _async_negotiate(self, session_id, session, offer_sdp, send_message) -> None:
        stage = "loading the media runtime"
        try:
            async with asyncio.timeout(_OFFER_TIMEOUT):
                rtc, media, sdp = await self._snapshots.loader.async_load()
                stage = "processing the Home Assistant offer"
                session.parse_candidate = sdp.candidate_from_sdp
                connection = session.connection = rtc.RTCPeerConnection(
                    rtc.RTCConfiguration(iceServers=[])
                )
                relay = media.MediaRelay()
                microphone = None

                @connection.on("track")
                def receive_track(track):
                    nonlocal microphone
                    if track.kind == "audio":
                        microphone = track

                @connection.on("connectionstatechange")
                def connection_state_changed():
                    if (
                        connection.connectionState in {"failed", "closed"}
                        and self._sessions.get(session_id) is session
                    ):
                        self.close_webrtc_session(session_id)

                await connection.setRemoteDescription(rtc.RTCSessionDescription(offer_sdp, "offer"))
                receive_mids = {
                    section.rtp.muxId
                    for section in sdp.SessionDescription.parse(offer_sdp).media
                    if section.port != 0 and section.direction in {"recvonly", "sendrecv"}
                }
                receivers = {}
                for transceiver in connection.getTransceivers():
                    if transceiver.mid in receive_mids:
                        receivers.setdefault(transceiver.kind, transceiver)

                async def route_to_client(track):
                    if (transceiver := receivers.get(track.kind)) is not None:
                        transceiver.sender.replaceTrack(relay.subscribe(track, buffered=False))
                        transceiver.direction = "sendrecv"
                        return
                    # Companion microphone sessions may offer audio without video.
                    # Drain unused upstream tracks so their frame queues stay bounded.
                    if session.discarded_media is None:
                        session.discarded_media = media.MediaBlackhole()
                    session.discarded_media.addTrack(track)
                    await session.discarded_media.start()

                session.remote_description_set = True
                for candidate in session.candidates:
                    await _async_add_candidate(session, candidate)
                session.candidates.clear()

                stage = "opening Intercom video"
                intercom = session.intercom = await IntercomPeer.async_connect(
                    self.bridge, self._snapshots.device_uuid
                )
                await route_to_client(intercom.video_track)
                if microphone is not None:
                    stage = "opening Intercom audio"
                    doorbell_audio = await intercom.async_add_audio(
                        relay.subscribe(microphone, buffered=False)
                    )
                    await route_to_client(doorbell_audio)

                stage = "answering the Home Assistant offer"
                await connection.setLocalDescription(await connection.createAnswer())
                send_message(WebRTCAnswer(connection.localDescription.sdp))
            session.disconnect_watch = self.hass.async_create_background_task(
                self._async_watch_disconnect(session_id, session, intercom, send_message),
                name="loxone_intercom_watch",
            )
        except Exception as error:
            _LOGGER.debug("Intercom WebRTC setup failed while %s (%s).", stage, type(error).__name__)
            if self._sessions.get(session_id) is session:
                try:
                    send_message(WebRTCError("loxone_intercom", "Could not start the Loxone Intercom stream."))
                finally:
                    self.close_webrtc_session(session_id)

    async def _async_watch_disconnect(self, session_id, session, intercom, send_message) -> None:
        await intercom.async_wait_disconnected()
        if self._sessions.get(session_id) is session:
            try:
                send_message(WebRTCError("loxone_intercom", "The Loxone Intercom stream disconnected."))
            finally:
                self.close_webrtc_session(session_id)

    async def async_on_webrtc_candidate(self, session_id, candidate) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.remote_description_set:
            await _async_add_candidate(session, candidate)
        elif len(session.candidates) < _MAX_QUEUED_CANDIDATES:
            session.candidates.append(candidate)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            task = self.hass.async_create_task(
                _async_close_session(session), name="loxone_intercom_close"
            )
            self._closing_tasks.add(task)
            task.add_done_callback(self._closing_tasks.discard)

    async def async_will_remove_from_hass(self) -> None:
        for session_id in tuple(self._sessions):
            self.close_webrtc_session(session_id)
        await asyncio.gather(*self._closing_tasks)
        await super().async_will_remove_from_hass()


async def _async_add_candidate(session: _ClientSession, candidate) -> None:
    if not candidate.candidate:
        await session.connection.addIceCandidate(None)
        return
    if candidate.sdp_mid is None and candidate.sdp_m_line_index is None:
        return
    try:
        ice = session.parse_candidate(candidate.candidate.removeprefix("candidate:"))
    except (AssertionError, ValueError, IndexError):
        return
    ice.sdpMid = candidate.sdp_mid
    ice.sdpMLineIndex = candidate.sdp_m_line_index
    await session.connection.addIceCandidate(ice)


async def _async_close_session(session: _ClientSession) -> None:
    tasks = [
        task for task in (session.negotiation, session.disconnect_watch)
        if task is not None and not task.done()
    ]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    resources = []
    if session.connection is not None:
        resources.append(session.connection.close())
    if session.intercom is not None:
        resources.append(session.intercom.async_close())
    if session.discarded_media is not None:
        resources.append(session.discarded_media.stop())
    await asyncio.gather(*resources, return_exceptions=True)
