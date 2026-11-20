"""WebRTC media peer for a local Loxone Intercom Gen 2."""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from typing import Any

from .intercom_signaling import IntercomSignaling, IntercomSignalingError

_LOGGER = logging.getLogger(__name__)
_NEGOTIATION_TIMEOUT = 15
_SNAPSHOT_TIMEOUT = 8
_FRAME_TIMEOUT = 4
_CLOSE_TIMEOUT = 2
_ICE_ACK_TIMEOUT = 2
_HANGUP_TIMEOUT = 1


class IntercomPeerError(Exception):
    """The Intercom could not establish or provide WebRTC media."""


class IntercomPeer:
    """Receive live video and optionally establish a two-way audio path."""

    def __init__(self, signaling: IntercomSignaling, rtc: Any, parse_candidate: Any) -> None:
        self._signaling = signaling
        self._rtc = rtc
        self._parse_candidate = parse_candidate
        self._connection = rtc.RTCPeerConnection(rtc.RTCConfiguration(iceServers=[]))
        self._negotiation_lock = asyncio.Lock()
        self._notification_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._call_active = False
        self.video_track: Any | None = None
        self.audio_track: Any | None = None
        self.audio_sender: Any | None = None
        self._connection.on("track", self._on_track)
        self._connection.on("connectionstatechange", self._connection_state_changed)

    @classmethod
    async def async_connect(cls, bridge: Any, device_uuid: str) -> IntercomPeer:
        """Authenticate and negotiate video without opening an audio channel."""
        rtc, parse_candidate = await asyncio.to_thread(_load_media_runtime)
        signaling = None
        peer = None
        try:
            async with asyncio.timeout(_NEGOTIATION_TIMEOUT):
                signaling = await IntercomSignaling.async_connect(bridge, device_uuid)
                peer = cls(signaling, rtc, parse_candidate)
                video = peer._connection.addTransceiver("video", direction="recvonly")
                codecs = rtc.RTCRtpReceiver.getCapabilities("video").codecs
                video.setCodecPreferences([codec for codec in codecs if codec.mimeType.lower() == "video/h264"])
                await peer._async_negotiate("new")
                if peer.video_track is None or peer._close_task is not None:
                    raise IntercomPeerError("The Intercom did not offer a video track.")
                peer._notification_task = asyncio.create_task(
                    peer._async_read_notifications(), name="loxone_intercom_peer_notifications"
                )
                return peer
        except BaseException as error:
            if peer is not None:
                await peer.async_close()
            elif signaling is not None:
                await _async_close_resource(signaling.async_close)
            if isinstance(error, IntercomPeerError) or not isinstance(error, Exception):
                raise
            raise IntercomPeerError("The Intercom video connection could not be established.") from None

    async def async_add_audio(self, microphone_track: Any) -> Any:
        """Send microphone audio and return the Intercom's audio track."""
        if microphone_track.kind != "audio":
            raise IntercomPeerError("The microphone must provide an audio track.")

        async with self._negotiation_lock:
            if self._close_task is not None:
                raise IntercomPeerError("The Intercom media session is closed.")
            if self.audio_sender is not None:
                raise IntercomPeerError("The Intercom media session already has an audio channel.")

            try:
                async with asyncio.timeout(_NEGOTIATION_TIMEOUT):
                    audio = self._connection.addTransceiver(microphone_track, direction="sendrecv")
                    await self._async_negotiate("add_audio")
                    if self.audio_track is None or audio.currentDirection != "sendrecv":
                        raise IntercomPeerError("The Intercom did not accept two-way audio.")
                    self.audio_sender = audio.sender
                    return self.audio_track
            except BaseException as error:
                await self.async_close()
                if isinstance(error, IntercomPeerError) or not isinstance(error, Exception):
                    raise
                raise IntercomPeerError("The Intercom audio connection could not be established.") from None

    async def async_snapshot(self, width: int | None = None, height: int | None = None) -> bytes:
        """Read one JPEG frame from a video track owned by the snapshot caller."""
        if self.video_track is None or self._close_task is not None:
            raise IntercomPeerError("The Intercom has no active video track.")
        try:
            async with asyncio.timeout(_FRAME_TIMEOUT):
                frame = await self.video_track.recv()
                return await asyncio.to_thread(_jpeg_from_frame, frame, width, height)
        except Exception:
            raise IntercomPeerError("The Intercom did not provide a video frame.") from None

    async def async_close(self) -> None:
        """Release both media and signaling, including after cancellation."""
        await asyncio.shield(self._start_close())

    async def _async_negotiate(self, action: str) -> None:
        offer = await self._connection.createOffer()
        await self._connection.setLocalDescription(offer)
        # aiortc gathers local ICE candidates before setting localDescription.
        sdp = self._connection.localDescription.sdp
        answer = await self._signaling.async_call(sdp, action=action)
        self._call_active = True
        await self._connection.setRemoteDescription(self._rtc.RTCSessionDescription(sdp=answer, type="answer"))

        # Also send gathered candidates after the answer, as the Loxone app does.
        # The full SDP already contains them, so missing duplicate ACKs are harmless.
        try:
            async with asyncio.timeout(_ICE_ACK_TIMEOUT):
                await asyncio.gather(
                    *(self._signaling.async_add_ice_candidate(*candidate) for candidate in _sdp_ice_candidates(sdp)),
                    return_exceptions=True,
                )
        except TimeoutError:
            _LOGGER.debug("The Intercom did not acknowledge its explicit ICE updates in time.")
        await self._signaling.async_ice_gathering_finished()

    def _on_track(self, track: Any) -> None:
        if track.kind == "video":
            self.video_track = track
        elif track.kind == "audio":
            self.audio_track = track

    def _connection_state_changed(self) -> None:
        if self._connection.connectionState in {"failed", "closed"}:
            self._start_close()

    def _start_close(self) -> asyncio.Task[None]:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._async_close_resources(), name="loxone_intercom_peer_close")
        return self._close_task

    async def _async_close_resources(self) -> None:
        if self._notification_task is not None:
            self._notification_task.cancel()
            await asyncio.gather(self._notification_task, return_exceptions=True)
        await asyncio.gather(
            _async_close_resource(self._connection.close),
            _async_close_resource(self._async_close_signaling),
        )

    async def _async_close_signaling(self) -> None:
        try:
            if self._call_active:
                async with asyncio.timeout(_HANGUP_TIMEOUT):
                    await self._signaling.async_hangup()
        except (IntercomSignalingError, TimeoutError):
            _LOGGER.debug("The Intercom did not acknowledge its session hangup.")
        finally:
            await self._signaling.async_close()

    async def _async_read_notifications(self) -> None:
        try:
            while True:
                message = await self._signaling.async_next_notification()
                method = message.get("method")
                if method == "addIceCandidate":
                    await self._async_add_remote_candidate(message.get("params"))
                elif method in {"kick", "hangup"}:
                    self._call_active = False
                    break
        except asyncio.CancelledError:
            raise
        except IntercomSignalingError:
            pass
        finally:
            self._start_close()

    async def _async_add_remote_candidate(self, params: Any) -> None:
        if not isinstance(params, list) or not params:
            return
        candidate_text = params[0]
        index = params[1] if len(params) > 1 else None
        mid = params[2] if len(params) > 2 else None
        if isinstance(candidate_text, dict):
            index = candidate_text.get("sdpMLineIndex")
            mid = candidate_text.get("sdpMid")
            candidate_text = candidate_text.get("candidate")
        index = index if type(index) is int and index >= 0 else None
        mid = mid if isinstance(mid, str) and mid else None
        if not isinstance(candidate_text, str) or not candidate_text or (index is None and mid is None):
            return
        try:
            candidate = self._parse_candidate(candidate_text.removeprefix("a=").removeprefix("candidate:"))
            candidate.sdpMLineIndex = index
            candidate.sdpMid = mid
            async with self._negotiation_lock:
                await self._connection.addIceCandidate(candidate)
        except (AssertionError, IndexError, TypeError, ValueError):
            _LOGGER.debug("Ignoring an invalid Intercom ICE candidate.")


async def async_intercom_snapshot(
    bridge: Any, device_uuid: str, width: int | None = None, height: int | None = None
) -> bytes:
    """Capture a bounded, video-only snapshot and release the device session."""
    peer = None
    try:
        async with asyncio.timeout(_SNAPSHOT_TIMEOUT):
            peer = await IntercomPeer.async_connect(bridge, device_uuid)
            return await peer.async_snapshot(width, height)
    except TimeoutError:
        raise IntercomPeerError("The Intercom snapshot timed out.") from None
    finally:
        if peer is not None:
            await peer.async_close()


def _load_media_runtime() -> tuple[Any, Any]:
    try:
        import aiortc
        from aiortc.sdp import candidate_from_sdp
    except (ImportError, OSError):
        raise IntercomPeerError("The Intercom WebRTC media runtime is unavailable.") from None
    return aiortc, candidate_from_sdp


def _sdp_ice_candidates(sdp: str) -> list[tuple[str, int, str | None]]:
    candidates = []
    for index, section in enumerate(sdp.split("\nm=")[1:]):
        lines = section.splitlines()
        mid = next((line[6:] for line in lines if line.startswith("a=mid:")), None)
        candidates.extend(
            (line[2:], index, mid) for line in lines if line.startswith("a=candidate:")
        )
    return candidates


async def _async_close_resource(close: Any) -> None:
    try:
        async with asyncio.timeout(_CLOSE_TIMEOUT):
            await close()
    except Exception:
        _LOGGER.debug("An Intercom media resource could not be closed cleanly.")


def _jpeg_from_frame(frame: Any, width: int | None, height: int | None) -> bytes:
    image = frame.to_image()
    if width or height:
        # Treat the requested dimensions as a bounding box and preserve aspect ratio.
        max_width = width if width and width > 0 else image.width
        max_height = height if height and height > 0 else image.height
        image.thumbnail((max_width, max_height))
    with BytesIO() as output:
        image.save(output, format="JPEG")
        return output.getvalue()
