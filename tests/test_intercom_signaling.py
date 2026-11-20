"""Tests for authenticated Loxone Intercom WebRTC signaling."""

from __future__ import annotations

import asyncio
import base64
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tests._loader import load_integration_module

signaling = load_integration_module("custom_components.loxone_home_assistant.intercom_signaling")

DEVICE_UUID = "01234567-89ab-cdef-0123456789abcdef"


class FakeWebSocket:
    protocol = "webrtc-signaling"

    def __init__(self) -> None:
        self.closed = False
        self.sent: list[dict] = []
        self.received: asyncio.Queue[dict] = asyncio.Queue()
        self.sent_messages: asyncio.Queue[dict] = asyncio.Queue()

    async def receive_json(self):
        return await self.received.get()

    async def send_json(self, payload):
        self.sent.append(payload)
        self.sent_messages.put_nowait(payload)

    def receive(self, **data):
        self.received.put_nowait({"jsonrpc": "2.0", **data})


class FakeClient:
    def __init__(self, websocket: FakeWebSocket):
        self.websocket = websocket
        self.connect_call = None
        self.closed = False

    async def ws_connect(self, url, **options):
        self.connect_call = (url, options)
        return self.websocket

    async def close(self):
        self.closed = True
        self.websocket.closed = True


class IntercomSignalingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.websocket = FakeWebSocket()
        self.client = FakeClient(self.websocket)
        self.trace = SimpleNamespace(on_request_redirect=[])
        self.bridge = SimpleNamespace(
            host="loxone.local",
            port=8443,
            verify_ssl=True,
            username="test-user",
            token="existing.test.token",
            resolve_http_url=lambda path: f"https://loxone.local:8443/base{path}",
        )
        aiohttp = SimpleNamespace(
            ClientError=type("ClientError", (Exception,), {}),
            ClientSession=lambda **kwargs: self.client,
            Fingerprint=lambda digest: SimpleNamespace(digest=digest),
            TraceConfig=lambda: self.trace,
        )
        patcher = patch.dict(sys.modules, {"aiohttp": aiohttp})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def async_connect(self):
        public = self.private_key.public_key().public_numbers()
        challenge = "device-issued-challenge"
        self.websocket.receive(id=0, method="authenticate", params=[challenge, f"{public.n:x}", f"{public.e:x}"])
        self.websocket.receive(id=1, method="reachMode")
        self.websocket.receive(method="ready")
        session = await signaling.IntercomSignaling.async_connect(self.bridge, DEVICE_UUID)
        self.addAsyncCleanup(session.async_close)
        return session, self.private_key, challenge

    async def test_connect_uses_the_configured_origin_and_encrypts_the_existing_token(self):
        session, private_key, challenge = await self.async_connect()

        self.assertEqual(self.client.connect_call, (
            f"wss://loxone.local:8443/base/proxy/{DEVICE_UUID}/",
            {"protocols": ("webrtc-signaling",), "ssl": True},
        ))
        response, reach_mode = self.websocket.sent
        self.assertEqual(response["id"], 0)
        self.assertEqual(response["result"]["code"], 200)
        self.assertEqual(reach_mode["result"]["data"], [2])
        username, wrapped_key, encrypted_token = response["result"]["data"]
        key, iv, echoed_challenge = private_key.decrypt(
            base64.b64decode(wrapped_key), rsa_padding.PKCS1v15()
        ).decode().split(":")
        self.assertEqual(username, self.bridge.username)
        self.assertEqual(echoed_challenge, challenge)
        decryptor = Cipher(algorithms.AES(bytes.fromhex(key)), modes.CBC(bytes.fromhex(iv))).decryptor()
        plaintext = decryptor.update(base64.b64decode(encrypted_token)) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        self.assertEqual((unpadder.update(plaintext) + unpadder.finalize()).decode(), self.bridge.token)

        await session.async_close()
        self.assertTrue(self.client.closed)

    async def test_call_and_remote_candidate_can_arrive_in_either_order(self):
        session, _, _ = await self.async_connect()
        await self.websocket.sent_messages.get()
        await self.websocket.sent_messages.get()
        call = asyncio.create_task(session.async_call("offer SDP", action="add_audio"))
        self.addCleanup(call.cancel)
        offer = await asyncio.wait_for(self.websocket.sent_messages.get(), 1)
        self.assertEqual(offer["method"], "call")
        self.assertEqual(offer["params"], [{"type": "offer", "sdp": "offer SDP"}, "add_audio", False, 0])

        ice = ["candidate:abc", 0, "video"]
        self.websocket.receive(id=99, method="addIceCandidate", params=ice)
        self.websocket.receive(id=offer["id"], result={"code": 200, "data": {"sdp": "answer SDP"}})
        received = await asyncio.wait_for(session.async_next_notification(), 1)
        self.assertEqual(received["params"], ice)
        self.assertEqual(await asyncio.wait_for(call, 1), "answer SDP")
        self.assertEqual(self.websocket.sent[-1], {
            "jsonrpc": "2.0", "id": 99, "result": {"code": 200, "message": "Ok"},
        })

        await session.async_ice_gathering_finished()
        self.assertEqual(self.websocket.sent[-1], {"jsonrpc": "2.0", "method": "iceGatheringFinished"})
        await self.websocket.sent_messages.get()  # Acknowledgement of the device's candidate.
        await self.websocket.sent_messages.get()  # Local ICE gathering notification.

        teardown = asyncio.create_task(session.async_hangup())
        self.addCleanup(teardown.cancel)
        hangup = await asyncio.wait_for(self.websocket.sent_messages.get(), 1)
        self.assertEqual(hangup, {"jsonrpc": "2.0", "id": offer["id"] + 1, "method": "hangup"})
        self.websocket.receive(id=hangup["id"], result={"code": 200})
        await asyncio.wait_for(teardown, 1)

    async def test_connection_rejects_redirects_and_untrusted_origins(self):
        self.bridge.resolve_http_url = lambda path: f"https://untrusted.local:8443{path}"
        with self.assertRaisesRegex(signaling.IntercomSignalingError, "HTTPS origin"):
            await signaling.IntercomSignaling.async_connect(self.bridge, DEVICE_UUID)
        self.assertIsNone(self.client.connect_call)

        self.bridge.resolve_http_url = lambda path: f"https://loxone.local:8443{path}"

        async def try_redirect(*_args, **_kwargs):
            await self.trace.on_request_redirect[0]()

        with patch.object(self.client, "ws_connect", side_effect=try_redirect):
            with self.assertRaisesRegex(signaling.IntercomSignalingError, "redirected"):
                await signaling.IntercomSignaling.async_connect(self.bridge, DEVICE_UUID)
        self.assertTrue(self.client.closed)
        self.assertFalse(self.websocket.sent)

    async def test_ready_without_authentication_is_rejected(self):
        self.websocket.receive(method="ready")
        with self.assertRaisesRegex(signaling.IntercomSignalingError, "did not accept"):
            await signaling.IntercomSignaling.async_connect(self.bridge, DEVICE_UUID)
        self.assertTrue(self.client.closed)
        self.assertFalse(self.websocket.sent)

    async def test_connection_verifies_the_existing_tls_pin_when_ca_verification_is_disabled(self):
        self.bridge.verify_ssl = False
        self.bridge.miniserver_tls_fingerprint = lambda: bytes(range(32))
        await self.async_connect()

        self.assertEqual(self.client.connect_call[1]["ssl"].digest, bytes(range(32)))

    async def test_connection_fails_before_connecting_without_a_tls_pin(self):
        self.bridge.verify_ssl = False
        self.bridge.miniserver_tls_fingerprint = lambda: None

        with self.assertRaisesRegex(signaling.IntercomSignalingError, "TLS certificate is unavailable"):
            await signaling.IntercomSignaling.async_connect(self.bridge, DEVICE_UUID)
        self.assertIsNone(self.client.connect_call)
        self.assertFalse(self.websocket.sent)

    async def test_shutdown_releases_inflight_requests(self):
        session, _, _ = await self.async_connect()
        await self.websocket.sent_messages.get()
        await self.websocket.sent_messages.get()
        request = asyncio.create_task(session.async_request("info", []))
        self.addCleanup(request.cancel)
        await asyncio.wait_for(self.websocket.sent_messages.get(), 1)
        await session.async_close()
        with self.assertRaisesRegex(signaling.IntercomSignalingError, "was closed"):
            await request
