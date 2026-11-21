"""Tests for the Home Assistant side of the Intercom WebRTC camera."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests._loader import load_integration_module
from tests.test_camera_platform import _install_homeassistant_stubs, LoxoneControl


@dataclass
class _Answer:
    sdp: str


@dataclass
class _Error:
    code: str
    message: str


def _load_native_camera_module():
    with patch.dict(sys.modules):
        _install_homeassistant_stubs()
        camera = sys.modules["homeassistant.components.camera"]
        camera.CameraEntityCapabilities = SimpleNamespace
        core = sys.modules["homeassistant.core"]
        core.callback = lambda fn: fn

        webrtc = ModuleType("homeassistant.components.camera.webrtc")
        webrtc.WebRTCAnswer = _Answer
        webrtc.WebRTCError = _Error
        sys.modules[webrtc.__name__] = webrtc

        requirements = ModuleType("homeassistant.requirements")
        requirements.async_process_requirements = AsyncMock()
        sys.modules[requirements.__name__] = requirements
        return load_integration_module(
            "custom_components.loxone_home_assistant.intercom_webrtc_camera"
        )


class _Peer:
    def __init__(self, _configuration):
        self.handlers = {}
        self.tracks = []
        self.candidates = []
        self.close = AsyncMock()
        self.localDescription = None
        self.transceivers = []

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback
        return register

    async def setRemoteDescription(self, description):
        sections = _OFFERS[description.sdp]
        self.transceivers = [
            SimpleNamespace(
                kind=kind,
                mid=str(index),
                sender=SimpleNamespace(replaceTrack=Mock(side_effect=self.tracks.append)),
            )
            for index, (kind, _) in enumerate(sections)
        ]
        if any(
            kind == "audio" and direction in {"sendrecv", "sendonly"}
            for kind, direction in sections
        ):
            self.handlers["track"](SimpleNamespace(kind="audio"))

    def getTransceivers(self):
        return self.transceivers

    async def addIceCandidate(self, candidate):
        self.candidates.append(candidate)

    async def createAnswer(self):
        return _Answer("answer")

    async def setLocalDescription(self, description):
        self.localDescription = description


_OFFERS = {
    "preview": [("audio", "recvonly"), ("video", "recvonly")],
    "browser-talk": [("audio", "sendrecv"), ("video", "recvonly")],
    "companion-talk": [("audio", "sendonly"), ("audio", "recvonly")],
    "send-only-talk": [("audio", "sendonly")],
}


def _offer_sections(offer):
    return SimpleNamespace(
        media=[
            SimpleNamespace(port=9, direction=direction, rtp=SimpleNamespace(muxId=str(index)))
            for index, (_, direction) in enumerate(_OFFERS[offer])
        ]
    )


def _camera(native, rtc, media, sdp):
    loader = SimpleNamespace(async_load=AsyncMock(return_value=(rtc, media, sdp)))
    snapshots = SimpleNamespace(loader=loader, device_uuid="device")
    control = LoxoneControl(uuid="control", uuid_action="control", name="Doorbell", type="IntercomV2")
    bridge = SimpleNamespace(serial="miniserver", available=True)
    camera = native.LoxoneIntercomWebRTCCamera(bridge, control, snapshots)
    camera.hass = SimpleNamespace(
        async_create_task=asyncio.create_task,
        async_create_background_task=asyncio.create_task,
    )
    return camera


class WebRTCCameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_only_the_media_offered_by_each_client(self):
        native = _load_native_camera_module()
        client_media = {
            "preview": (["video"], []),
            "browser-talk": (["video", "audio"], []),
            "companion-talk": (["audio"], ["video"]),
            "send-only-talk": ([], ["video", "audio"]),
        }
        for offer, (relayed, discarded) in client_media.items():
            with self.subTest(offer=offer):
                rtc = SimpleNamespace(
                    RTCPeerConnection=Mock(side_effect=_Peer),
                    RTCConfiguration=SimpleNamespace,
                    RTCSessionDescription=lambda sdp, type: SimpleNamespace(sdp=sdp, type=type),
                )
                relay = SimpleNamespace(subscribe=lambda track, buffered: track)
                blackhole = SimpleNamespace(addTrack=Mock(), start=AsyncMock(), stop=AsyncMock())
                media = SimpleNamespace(MediaRelay=lambda: relay, MediaBlackhole=lambda: blackhole)
                sdp = SimpleNamespace(
                    SessionDescription=SimpleNamespace(parse=_offer_sections), candidate_from_sdp=Mock()
                )
                camera = _camera(native, rtc, media, sdp)
                output = Mock()
                upstream = SimpleNamespace(
                    video_track=SimpleNamespace(kind="video"),
                    async_add_audio=AsyncMock(return_value=SimpleNamespace(kind="audio")),
                    async_close=AsyncMock(),
                    async_wait_disconnected=asyncio.Event().wait,
                )
                with patch.object(native.IntercomPeer, "async_connect", AsyncMock(return_value=upstream)):
                    try:
                        await camera.async_handle_async_webrtc_offer(offer, "viewer", output)
                        session = camera._sessions["viewer"]
                        output.assert_called_once_with(_Answer("answer"))
                        self.assertEqual(
                            [track.kind for track in session.connection.tracks], relayed,
                        )
                        self.assertEqual(
                            [call.args[0].kind for call in blackhole.addTrack.call_args_list], discarded,
                        )
                        if offer == "companion-talk":
                            _, audio_receiver = session.connection.getTransceivers()
                            audio_receiver.sender.replaceTrack.assert_called_once()
                        self.assertEqual(upstream.async_add_audio.await_count, int(offer != "preview"))
                    finally:
                        await camera.async_will_remove_from_hass()
                upstream.async_close.assert_awaited_once()
                self.assertEqual(blackhole.start.await_count, len(discarded))
                self.assertEqual(blackhole.stop.await_count, int(bool(discarded)))

    async def test_relays_media_for_browser_and_companion_offers(self):
        try:
            import aiortc
            from aiortc import sdp
            from aiortc.contrib import media
        except ImportError:
            self.skipTest("optional aiortc media runtime is unavailable")

        for offer in ("browser-talk", "companion-talk", "send-only-talk"):
            with self.subTest(offer=offer):
                await self._assert_audio_relay(aiortc, media, sdp, offer)

    async def _assert_audio_relay(self, aiortc, media, sdp, offer):
        native = _load_native_camera_module()
        camera = _camera(native, aiortc, media, sdp)
        client = aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[]))
        received = asyncio.Queue()
        client.on("track", received.put_nowait)
        microphone = aiortc.AudioStreamTrack()
        sections = _OFFERS[offer]
        for kind, direction in sections:
            track = microphone if direction in {"sendrecv", "sendonly"} else kind
            client.addTransceiver(track, direction=direction)
        video, speaker = aiortc.VideoStreamTrack(), aiortc.AudioStreamTrack()
        video.recv = AsyncMock(wraps=video.recv)
        upstream = SimpleNamespace(
            video_track=video, async_add_audio=AsyncMock(return_value=speaker),
            async_close=AsyncMock(), async_wait_disconnected=asyncio.Event().wait,
        )
        output = Mock()

        try:
            await client.setLocalDescription(await client.createOffer())
            with patch.object(native.IntercomPeer, "async_connect", AsyncMock(return_value=upstream)):
                await camera.async_handle_async_webrtc_offer(client.localDescription.sdp, "viewer", output)
            answer = output.call_args.args[0]
            self.assertIsInstance(answer, _Answer)
            self.assertEqual(
                [section.kind for section in sdp.SessionDescription.parse(answer.sdp).media],
                [kind for kind, _ in sections],
            )
            await client.setRemoteDescription(aiortc.RTCSessionDescription(answer.sdp, "answer"))
            tracks = {}
            expected_tracks = {kind for kind, direction in sections if direction != "sendonly"}
            for _ in expected_tracks:
                received_track = await asyncio.wait_for(received.get(), timeout=3)
                tracks[received_track.kind] = received_track
            self.assertEqual(tracks.keys(), expected_tracks)
            microphone_relay = upstream.async_add_audio.await_args.args[0]
            frame = await asyncio.wait_for(microphone_relay.recv(), timeout=3)
            self.assertGreater(frame.samples, 0)
            if "audio" in tracks:
                frame = await asyncio.wait_for(tracks["audio"].recv(), timeout=3)
                self.assertGreater(frame.samples, 0)
            if "video" in tracks:
                frame = await asyncio.wait_for(tracks["video"].recv(), timeout=3)
                self.assertGreater(frame.width, 0)
            self.assertTrue(received.empty())
            video.recv.assert_awaited()
        finally:
            await camera.async_will_remove_from_hass()
            await client.close()
            video.stop()
            speaker.stop()
        upstream.async_close.assert_awaited_once()

    async def test_upstream_disconnect_closes_viewer_and_reports_error(self):
        native = _load_native_camera_module()
        rtc = SimpleNamespace(
            RTCPeerConnection=_Peer, RTCConfiguration=SimpleNamespace,
            RTCSessionDescription=lambda sdp, type: SimpleNamespace(sdp=sdp, type=type),
        )
        media = SimpleNamespace(MediaRelay=lambda: SimpleNamespace(subscribe=lambda track, buffered: track))
        sdp = SimpleNamespace(
            SessionDescription=SimpleNamespace(parse=_offer_sections), candidate_from_sdp=Mock(),
        )
        disconnect = asyncio.Event()
        upstream = SimpleNamespace(
            video_track=SimpleNamespace(kind="video"), async_close=AsyncMock(),
            async_wait_disconnected=disconnect.wait,
        )
        camera = _camera(native, rtc, media, sdp)
        output = asyncio.Queue()
        with patch.object(native.IntercomPeer, "async_connect", AsyncMock(return_value=upstream)):
            await camera.async_handle_async_webrtc_offer("preview", "viewer", output.put_nowait)
        connection = camera._sessions["viewer"].connection
        self.assertEqual(output.get_nowait(), _Answer("answer"))

        disconnect.set()
        error = await asyncio.wait_for(output.get(), timeout=1)
        self.assertEqual(error, _Error("loxone_intercom", "The Loxone Intercom stream disconnected."))
        self.assertNotIn("viewer", camera._sessions)
        await camera.async_will_remove_from_hass()
        connection.close.assert_awaited_once()
        upstream.async_close.assert_awaited_once()

    async def test_optional_dependency_failure_can_recover_without_camera_reload(self):
        native = _load_native_camera_module()
        hass = SimpleNamespace(
            async_create_background_task=asyncio.create_task,
            async_add_executor_job=AsyncMock(return_value="loaded"),
        )
        loader = native.IntercomRtcLoader(hass)
        with (
            patch.object(native.async_process_requirements, "side_effect", [RuntimeError(), None]),
            patch.object(native, "monotonic", return_value=100) as time,
        ):
            for _ in range(2):
                with self.assertRaises(native.IntercomPeerError):
                    await loader.async_load()
            native.async_process_requirements.assert_awaited_once()

            time.return_value += native._MEDIA_RETRY_INTERVAL
            self.assertEqual(await loader.async_load(), "loaded")
            self.assertEqual(await loader.async_load(), "loaded")
            self.assertEqual(native.async_process_requirements.await_count, 2)
