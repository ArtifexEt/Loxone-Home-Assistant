"""Verify Intercom media negotiation, frame capture, and session teardown."""

from __future__ import annotations

from array import array
import asyncio
from io import BytesIO
from math import cos, sin, tau
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests._loader import load_integration_module


peer_module = load_integration_module("custom_components.loxone_home_assistant.intercom_peer")


class FakePeerConnection:
    def __init__(self) -> None:
        self.callbacks = {}
        self.transceivers = []
        self.localDescription = None
        self.connectionState = "new"
        self.video = SimpleNamespace(kind="video", recv=AsyncMock())
        self.audio = SimpleNamespace(kind="audio", recv=AsyncMock())
        self.audio_direction = "sendrecv"
        self.candidates = asyncio.Queue()
        self.close = AsyncMock()

    def on(self, event, callback) -> None:
        self.callbacks[event] = callback

    def addTransceiver(self, value, *, direction):
        transceiver = SimpleNamespace(
            source=value,
            direction=direction,
            sender=object(),
            currentDirection=None,
            setCodecPreferences=lambda codecs: None,
        )
        self.transceivers.append(transceiver)
        return transceiver

    async def createOffer(self):
        return SimpleNamespace(sdp=f"offer {len(self.transceivers)}")

    async def setLocalDescription(self, offer) -> None:
        self.localDescription = SimpleNamespace(sdp=offer.sdp + " with gathered ICE")

    async def setRemoteDescription(self, answer) -> None:
        if answer.sdp == "answer new":
            self.callbacks["track"](self.video)
        elif answer.sdp == "answer add_audio":
            self.transceivers[-1].currentDirection = self.audio_direction
            self.callbacks["track"](self.audio)

    async def addIceCandidate(self, candidate) -> None:
        self.candidates.put_nowait(candidate)


class IntercomPeerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.connection = FakePeerConnection()
        self.notifications = asyncio.Queue()
        self.signaling = SimpleNamespace(
            async_call=AsyncMock(side_effect=self._answer),
            async_add_ice_candidate=AsyncMock(),
            async_ice_gathering_finished=AsyncMock(),
            async_hangup=AsyncMock(),
            async_close=AsyncMock(),
            async_next_notification=self.notifications.get,
        )
        self.connection_configs = []

        def make_connection(config):
            self.connection_configs.append(config)
            return self.connection

        rtc = SimpleNamespace(
            RTCPeerConnection=make_connection,
            RTCConfiguration=SimpleNamespace,
            RTCSessionDescription=SimpleNamespace,
            RTCRtpReceiver=SimpleNamespace(
                getCapabilities=lambda kind: SimpleNamespace(
                    codecs=[SimpleNamespace(mimeType="video/H264")]
                )
            ),
        )
        self.enterContext(
            patch.object(
                peer_module,
                "_load_media_runtime",
                return_value=(rtc, lambda candidate: SimpleNamespace(value=candidate)),
            )
        )
        self.enterContext(
            patch.object(peer_module.IntercomSignaling, "async_connect", return_value=self.signaling)
        )

    async def _answer(self, sdp, *, action):
        return f"answer {action}"

    async def test_video_only_snapshot_closes_both_connections(self) -> None:
        image = SimpleNamespace(width=640, height=360)
        image.thumbnail = Mock()

        def save(output, *, format):
            self.assertEqual(format, "JPEG")
            output.write(b"image bytes")

        image.save = save
        self.connection.video.recv.return_value = SimpleNamespace(to_image=lambda: image)

        result = await peer_module.async_intercom_snapshot(object(), "device-id", width=320)

        self.assertEqual(result, b"image bytes")
        image.thumbnail.assert_called_once_with((320, 360))
        self.assertEqual(self.connection_configs[0].iceServers, [])
        self.assertEqual(self.connection.transceivers[0].direction, "recvonly")
        self.signaling.async_call.assert_awaited_once_with("offer 1 with gathered ICE", action="new")
        self.signaling.async_ice_gathering_finished.assert_awaited_once()
        self.connection.close.assert_awaited_once()
        self.signaling.async_hangup.assert_awaited_once()
        self.signaling.async_close.assert_awaited_once()

    async def test_audio_uses_the_same_connection_and_a_new_complete_offer(self) -> None:
        peer = await peer_module.IntercomPeer.async_connect(object(), "device-id")
        self.addAsyncCleanup(peer.async_close)
        microphone = SimpleNamespace(kind="audio")

        self.assertIs(await peer.async_add_audio(microphone), self.connection.audio)
        self.assertIs(peer.video_track, self.connection.video)
        self.assertIs(self.connection.transceivers[1].source, microphone)
        self.assertEqual(self.connection.transceivers[1].direction, "sendrecv")
        self.assertIs(peer.audio_sender, self.connection.transceivers[1].sender)
        self.signaling.async_call.assert_awaited_with("offer 2 with gathered ICE", action="add_audio")
        await asyncio.gather(peer.async_close(), peer.async_close())
        self.connection.close.assert_awaited_once()

    async def test_audio_rejection_closes_the_media_session(self) -> None:
        self.connection.audio_direction = "recvonly"
        peer = await peer_module.IntercomPeer.async_connect(object(), "device-id")

        with self.assertRaisesRegex(peer_module.IntercomPeerError, "did not accept two-way audio"):
            await peer.async_add_audio(SimpleNamespace(kind="audio"))

        self.connection.close.assert_awaited_once()
        self.signaling.async_close.assert_awaited_once()

    async def test_remote_ice_handles_both_candidate_forms(self) -> None:
        self.notifications.put_nowait({"method": "addIceCandidate", "params": ["candidate:video", 0, "v"]})
        peer = await peer_module.IntercomPeer.async_connect(object(), "device-id")
        self.addAsyncCleanup(peer.async_close)
        self.notifications.put_nowait({
            "method": "addIceCandidate",
            "params": [{"candidate": "a=candidate:audio", "sdpMLineIndex": 1, "sdpMid": "a"}],
        })

        video = await asyncio.wait_for(self.connection.candidates.get(), timeout=1)
        audio = await asyncio.wait_for(self.connection.candidates.get(), timeout=1)

        self.assertEqual((video.value, video.sdpMLineIndex, video.sdpMid), ("video", 0, "v"))
        self.assertEqual((audio.value, audio.sdpMLineIndex, audio.sdpMid), ("audio", 1, "a"))

    async def test_cancelled_negotiation_closes_both_connections(self) -> None:
        entered = asyncio.Event()

        async def wait_for_answer(sdp, *, action):
            entered.set()
            await asyncio.Future()

        self.signaling.async_call.side_effect = wait_for_answer
        setup = asyncio.create_task(peer_module.IntercomPeer.async_connect(object(), "device-id"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        setup.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await setup

        self.connection.close.assert_awaited_once()
        self.signaling.async_hangup.assert_not_called()
        self.signaling.async_close.assert_awaited_once()

    async def test_trickles_full_sdp_candidates_before_reporting_gathering_finished(self) -> None:
        self.connection.createOffer = AsyncMock(return_value=SimpleNamespace(sdp=(
            "v=0\r\nm=video 123 UDP/TLS/RTP/SAVPF 99\r\n"
            "a=candidate:video-host\r\na=mid:camera\r\n"
        )))
        events = []

        async def add_candidate(*candidate):
            events.append(candidate)

        async def gathering_finished():
            events.append("finished")

        self.signaling.async_add_ice_candidate.side_effect = add_candidate
        self.signaling.async_ice_gathering_finished.side_effect = gathering_finished
        peer = await peer_module.IntercomPeer.async_connect(object(), "device-id")
        self.addAsyncCleanup(peer.async_close)

        self.assertEqual(events, [("candidate:video-host", 0, "camera"), "finished"])

class RealIntercomPeerTests(unittest.IsolatedAsyncioTestCase):
    async def test_h264_preview_and_audio_in_both_directions(self) -> None:
        try:
            from aiortc import (
                AudioStreamTrack,
                RTCConfiguration,
                RTCPeerConnection,
                RTCSessionDescription,
                VideoStreamTrack,
            )
            from PIL import Image
        except ImportError:
            self.skipTest("optional aiortc and Pillow media runtime is unavailable")

        class ToneTrack(AudioStreamTrack):
            def __init__(self, frequency: int) -> None:
                super().__init__()
                self.frequency = frequency

            async def recv(self):
                frame = await super().recv()
                payload = array("h", (
                    int(12_000 * sin(tau * self.frequency * (frame.pts + index) / frame.sample_rate))
                    for index in range(frame.samples)
                )).tobytes()
                plane = frame.planes[0]
                plane.update(payload.ljust(plane.buffer_size, b"\0"))
                return frame

        doorbell = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.addAsyncCleanup(doorbell.close)
        microphone_at_door = asyncio.get_running_loop().create_future()
        notifications = asyncio.Queue()
        actions = []

        @doorbell.on("track")
        def on_track(track):
            if track.kind == "audio" and not microphone_at_door.done():
                microphone_at_door.set_result(track)

        async def answer_offer(sdp: str, *, action: str) -> str:
            actions.append(action)
            self.assertIn("H264/90000", sdp)
            self.assertNotIn("VP8/90000", sdp)
            await doorbell.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            if action == "new":
                self.assertNotIn("m=audio", sdp)
                doorbell.addTrack(VideoStreamTrack())
            elif action == "add_audio":
                doorbell.addTrack(ToneTrack(1000))
            await doorbell.setLocalDescription(await doorbell.createAnswer())
            return doorbell.localDescription.sdp

        signaling = SimpleNamespace(
            async_call=answer_offer,
            async_next_notification=notifications.get,
            async_add_ice_candidate=AsyncMock(),
            async_ice_gathering_finished=AsyncMock(),
            async_hangup=AsyncMock(),
            async_close=AsyncMock(),
        )
        with patch.object(peer_module.IntercomSignaling, "async_connect", return_value=signaling):
            peer = await peer_module.IntercomPeer.async_connect(object(), "simulated-doorbell")
        self.addAsyncCleanup(peer.async_close)

        preview = await peer.async_snapshot(width=320)
        with Image.open(BytesIO(preview)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (320, 240)))

        doorbell_audio = await peer.async_add_audio(ToneTrack(400))
        self.assertEqual(actions, ["new", "add_audio"])
        app_audio = await asyncio.wait_for(microphone_at_door, timeout=2)
        await asyncio.wait_for(asyncio.gather(
            self._assert_tone(doorbell_audio, expected=1000, other=400),
            self._assert_tone(app_audio, expected=400, other=1000),
        ), timeout=6)

        await peer.async_close()
        self.assertEqual(peer._connection.connectionState, "closed")
        signaling.async_hangup.assert_awaited_once()
        signaling.async_close.assert_awaited_once()

    async def _assert_tone(self, track, *, expected: int, other: int) -> None:
        # Opus may emit an initial quiet frame while the codec buffers.
        for _ in range(8):
            frame = await track.recv()
            channels = len(frame.layout.channels)
            samples = array("h", bytes(frame.planes[0])[: frame.samples * channels * 2])[::channels]
            power = _frequency_power(samples, expected, frame.sample_rate)
            if power > 100_000 and power > 5 * _frequency_power(samples, other, frame.sample_rate):
                return
        self.fail(f"The expected {expected} Hz audio did not reach the receiving peer.")


def _frequency_power(samples, frequency: int, sample_rate: int) -> float:
    step = tau * frequency / sample_rate
    real = sum(sample * cos(index * step) for index, sample in enumerate(samples))
    imaginary = sum(sample * sin(index * step) for index, sample in enumerate(samples))
    return abs(complex(real, imaginary))
