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
    camera.hass = SimpleNamespace(async_create_task=asyncio.create_task)
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

    async def test_companion_audio_only_negotiates_and_relays_audio_both_ways(self):
        try:
            import aiortc
            from aiortc import sdp
            from aiortc.contrib import media
        except ImportError:
            self.skipTest("optional aiortc media runtime is unavailable")

        native = _load_native_camera_module()
        camera = _camera(native, aiortc, media, sdp)
        client = aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[]))
        received = asyncio.Queue()
        client.on("track", received.put_nowait)
        client.addTransceiver("audio", direction="recvonly")
        microphone = aiortc.AudioStreamTrack()
        client.addTransceiver(microphone, direction="sendonly")
        video, speaker = aiortc.VideoStreamTrack(), aiortc.AudioStreamTrack()
        video.recv = AsyncMock(wraps=video.recv)
        upstream = SimpleNamespace(
            video_track=video, async_add_audio=AsyncMock(return_value=speaker), async_close=AsyncMock()
        )
        output = Mock()

        try:
            await client.setLocalDescription(await client.createOffer())
            with patch.object(native.IntercomPeer, "async_connect", AsyncMock(return_value=upstream)):
                await camera.async_handle_async_webrtc_offer(client.localDescription.sdp, "viewer", output)
            answer = output.call_args.args[0]
            self.assertIsInstance(answer, _Answer)
            self.assertEqual(
                [section.kind for section in sdp.SessionDescription.parse(answer.sdp).media], ["audio", "audio"]
            )
            await client.setRemoteDescription(aiortc.RTCSessionDescription(answer.sdp, "answer"))
            doorbell_audio = await asyncio.wait_for(received.get(), timeout=3)
            microphone_relay = upstream.async_add_audio.await_args.args[0]
            for track in (doorbell_audio, microphone_relay):
                frame = await asyncio.wait_for(track.recv(), timeout=3)
                self.assertGreater(frame.samples, 0)
            video.recv.assert_awaited()
        finally:
            await camera.async_will_remove_from_hass()
            await client.close()
            video.stop()
            speaker.stop()
        upstream.async_close.assert_awaited_once()
