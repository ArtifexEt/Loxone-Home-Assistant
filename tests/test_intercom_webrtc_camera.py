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

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback
        return register

    async def setRemoteDescription(self, description):
        if description.sdp == "offer-with-microphone":
            self.handlers["track"](SimpleNamespace(kind="audio"))

    async def addIceCandidate(self, candidate):
        self.candidates.append(candidate)

    async def createAnswer(self):
        return _Answer("answer")

    async def setLocalDescription(self, description):
        self.localDescription = description

    def addTrack(self, track):
        self.tracks.append(track)


class WebRTCCameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_has_no_audio_and_talk_adds_audio_on_the_upstream_peer(self):
        native = _load_native_camera_module()
        for has_microphone in (False, True):
            with self.subTest(has_microphone=has_microphone):
                rtc = SimpleNamespace(
                    RTCPeerConnection=Mock(side_effect=_Peer),
                    RTCConfiguration=SimpleNamespace,
                    RTCSessionDescription=lambda sdp, type: SimpleNamespace(sdp=sdp, type=type),
                )
                relay = SimpleNamespace(subscribe=lambda track, buffered: track)
                loader = SimpleNamespace(async_load=AsyncMock(return_value=(rtc, lambda: relay, Mock())))
                snapshots = SimpleNamespace(loader=loader, device_uuid="device")
                control = LoxoneControl(
                    uuid="control", uuid_action="control", name="Doorbell", type="IntercomV2"
                )
                bridge = SimpleNamespace(serial="miniserver", available=True)
                camera = native.LoxoneIntercomWebRTCCamera(bridge, control, snapshots)
                camera.hass = SimpleNamespace(async_create_task=asyncio.create_task)
                output = Mock()
                upstream = SimpleNamespace(
                    video_track=SimpleNamespace(kind="video"),
                    async_add_audio=AsyncMock(return_value=SimpleNamespace(kind="audio")),
                    async_close=AsyncMock(),
                )
                offer = "offer-with-microphone" if has_microphone else "offer"

                with patch.object(native.IntercomPeer, "async_connect", AsyncMock(return_value=upstream)):
                    try:
                        await camera.async_handle_async_webrtc_offer(offer, "viewer", output)
                        session = camera._sessions["viewer"]
                        output.assert_called_once_with(_Answer("answer"))
                        self.assertEqual(
                            [track.kind for track in session.connection.tracks],
                            ["video", "audio"] if has_microphone else ["video"],
                        )
                        self.assertEqual(upstream.async_add_audio.await_count, int(has_microphone))
                    finally:
                        await camera.async_will_remove_from_hass()
                upstream.async_close.assert_awaited_once()

