"""Tests for Loxone discovery helpers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import ssl
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, call, patch
from urllib.parse import unquote

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tests._loader import load_integration_module


def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    sys.modules["homeassistant"] = homeassistant

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

    for module_name, domain in {
        "binary_sensor": "binary_sensor",
        "button": "button",
        "climate": "climate",
        "cover": "cover",
        "light": "light",
        "number": "number",
        "select": "select",
        "sensor": "sensor",
        "switch": "switch",
        "text": "text",
    }.items():
        module = types.ModuleType(f"homeassistant.components.{module_name}")
        module.DOMAIN = domain
        sys.modules[f"homeassistant.components.{module_name}"] = module

    network = types.ModuleType("homeassistant.components.network")

    async def async_get_adapters(_hass):
        return []

    network.async_get_adapters = async_get_adapters
    sys.modules["homeassistant.components.network"] = network

    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    const.CONF_PORT = "port"
    const.CONF_USERNAME = "username"
    const.CONF_VERIFY_SSL = "verify_ssl"

    class Platform:
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        CLIMATE = "climate"
        COVER = "cover"
        LIGHT = "light"
        NUMBER = "number"
        SELECT = "select"
        SENSOR = "sensor"
        SWITCH = "switch"
        TEXT = "text"

    const.Platform = Platform
    sys.modules["homeassistant.const"] = const

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401
        """Minimal HomeAssistant stub for imports."""

    def callback(func):
        return func

    core.HomeAssistant = HomeAssistant
    core.callback = callback
    sys.modules["homeassistant.core"] = core

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers

    device_registry = types.ModuleType("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        pass

    device_registry.DeviceInfo = DeviceInfo
    sys.modules["homeassistant.helpers.device_registry"] = device_registry

    entity = types.ModuleType("homeassistant.helpers.entity")

    class Entity:
        pass

    entity.Entity = Entity
    sys.modules["homeassistant.helpers.entity"] = entity

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")

    def async_get_clientsession(_hass, verify_ssl=True):
        return None

    aiohttp_client.async_get_clientsession = async_get_clientsession
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientSession:  # noqa: D401
        """Minimal ClientSession stub for imports."""

    class WSMessage:  # noqa: D401
        """Minimal websocket message stub for imports."""

    class WSMsgType:
        BINARY = 2
        TEXT = 1
        CLOSE = 8
        CLOSED = 257
        ERROR = 258

    aiohttp.ClientError = ClientError
    aiohttp.ClientSession = ClientSession
    aiohttp.WSMessage = WSMessage
    aiohttp.WSMsgType = WSMsgType
    sys.modules["aiohttp"] = aiohttp


_install_homeassistant_stubs()
bridge = load_integration_module("custom_components.loxone_home_assistant.bridge")
models = load_integration_module("custom_components.loxone_home_assistant.models")
LoxoneControl = models.LoxoneControl


class _FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url, ssl=False, allow_redirects=True):
        self.urls.append(url)
        return self.responses[url]


class DiscoveryHelpersTests(unittest.IsolatedAsyncioTestCase):
    """Verify network discovery logic without Home Assistant."""

    def test_candidate_hosts_uses_enabled_ipv4_adapters_only(self) -> None:
        hosts = bridge._candidate_hosts(
            [
                {
                    "name": "docker0",
                    "enabled": False,
                    "default": False,
                    "auto": False,
                    "ipv4": [{"address": "203.0.113.1", "network_prefix": 23}],
                },
                {
                    "name": "lo",
                    "enabled": True,
                    "default": False,
                    "auto": False,
                    "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}],
                },
                {
                    "name": "enp0s1",
                    "enabled": True,
                    "default": True,
                    "auto": True,
                    "ipv4": [{"address": "198.51.100.156", "network_prefix": 24}],
                },
            ]
        )

        self.assertEqual(len(hosts), 253)
        self.assertNotIn("127.0.0.1", hosts)
        self.assertNotIn("203.0.113.2", hosts)
        self.assertIn("198.51.100.101", hosts)

    def test_candidate_hosts_prioritizes_default_adapters(self) -> None:
        hosts = bridge._candidate_hosts(
            [
                {
                    "name": "backup0",
                    "enabled": True,
                    "default": False,
                    "auto": False,
                    "ipv4": [{"address": "203.0.113.10", "network_prefix": 24}],
                },
                {
                    "name": "enp0s1",
                    "enabled": True,
                    "default": True,
                    "auto": True,
                    "ipv4": [{"address": "198.51.100.10", "network_prefix": 24}],
                },
            ]
        )

        self.assertEqual(hosts[0], "198.51.100.1")
        self.assertIn("203.0.113.1", hosts[253:])

    async def test_probe_host_prefers_https_for_supported_miniservers(self) -> None:
        session = _FakeSession(
            {
                "https://198.51.100.101:443/jdev/cfg/apiKey": _FakeResponse(
                    200,
                    {"LL": {"value": "{'httpsStatus':1,'name':'Miniserver'}"}},
                )
            }
        )

        result = await bridge._probe_host(
            session,
            "198.51.100.101",
            1,
            probe_legacy=False,
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.use_tls)
        self.assertEqual(result.server_model, "Miniserver")
        self.assertEqual(session.urls, ["https://198.51.100.101:443/jdev/cfg/apiKey"])

    async def test_probe_host_uses_sw_version_when_name_is_missing(self) -> None:
        session = _FakeSession(
            {
                "https://198.51.100.101:443/jdev/cfg/apiKey": _FakeResponse(
                    200,
                    {"LL": {"value": "{'httpsStatus':1,'swVersion':'14.7.6.19'}"}},
                )
            }
        )

        result = await bridge._probe_host(
            session,
            "198.51.100.101",
            1,
            probe_legacy=False,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.server_model, "Miniserver")
        self.assertEqual(result.label, "14.7.6.19 (198.51.100.101)")

    async def test_probe_host_can_fall_back_to_legacy_detection(self) -> None:
        session = _FakeSession(
            {
                "https://198.51.100.101:443/jdev/cfg/apiKey": _FakeResponse(404, {}),
                "http://198.51.100.101:80/jdev/cfg/apiKey": _FakeResponse(
                    200,
                    {"LL": {"Code": "200", "value": {
                        "httpsStatus": 0, "name": "Legacy", "snr": "504F94000000",
                        "version": "15.5.0.0",
                    }}},
                ),
            }
        )

        result = await bridge._probe_host(
            session,
            "198.51.100.101",
            1,
            probe_legacy=True,
        )

        self.assertIsNotNone(result)
        self.assertFalse(result.use_tls)
        self.assertEqual(result.server_model, "Miniserver")
        self.assertEqual(
            session.urls,
            [
                "https://198.51.100.101:443/jdev/cfg/apiKey",
                "http://198.51.100.101:80/jdev/cfg/apiKey",
            ],
        )

    async def test_probe_host_detects_go_and_exposes_model_in_label(self) -> None:
        session = _FakeSession(
            {
                "https://198.51.100.102:443/jdev/cfg/apiKey": _FakeResponse(
                    200,
                    {"LL": {"value": "{'httpsStatus':1,'name':'Domowy serwer','deviceType':'Go'}"}},
                )
            }
        )

        result = await bridge._probe_host(
            session,
            "198.51.100.102",
            1,
            probe_legacy=False,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.server_model, "Miniserver Go")
        self.assertEqual(
            result.label,
            "Domowy serwer - Miniserver Go (198.51.100.102)",
        )


class BridgeConnectionTests(unittest.IsolatedAsyncioTestCase):
    """Verify custom-port handling during the actual bridge setup."""

    host = "198.51.100.101"
    legacy_api_url = f"http://{host}:7778/jdev/cfg/apiKey"
    legacy_certificate_url = f"http://{host}:7778/jdev/sys/getcertificate"
    legacy_ws_url = f"ws://{host}:7778/ws/rfc6455"
    legacy_metadata = {"snr": "504F94000000", "version": "15.5.0.0"}

    def setUp(self) -> None:
        self.session = Mock(ws_connect=AsyncMock(), get=Mock())
        self.probe_factory = self.enterContext(patch.object(bridge, "ClientSession"))
        self.probe_factory.return_value.__aenter__.return_value = types.SimpleNamespace(
            get=self.session.get
        )

    def _new_bridge(self, port=7778, *, host=None, verify_ssl=False):
        hass = types.SimpleNamespace(
            async_create_background_task=lambda coroutine, _name: coroutine.close(),
        )
        with patch.object(bridge, "async_get_clientsession", return_value=self.session):
            return bridge.LoxoneBridge(
                hass,
                {
                    "host": host or self.host,
                    "port": port,
                    "username": "loxone-user",
                    "password": "loxone-password",
                    "verify_ssl": verify_ssl,
                },
            )

    def _websocket_call(self, port=7778, *, verify_ssl=False):
        return call.ws_connect(
            f"wss://{self.host}:{port}/ws/rfc6455",
            protocols=("remotecontrol",),
            ssl=None if verify_ssl else False,
            heartbeat=None,
        )

    async def test_manual_setup_refuses_untrusted_legacy_certificate_before_ws(self):
        for metadata in (
            str(self.legacy_metadata),
            {**self.legacy_metadata, "httpsStatus": 0},
        ):
            with self.subTest(metadata=metadata):
                self.session.reset_mock()
                self.probe_factory.reset_mock()
                self.session.ws_connect.side_effect = OSError("WSS handshake failed")
                self.session.get.return_value = _FakeResponse(
                    200, {"LL": {"Code": "200", "value": str(metadata)}}
                )
                legacy_client = Mock(
                    get=Mock(return_value=_FakeResponse(200, "untrusted certificate")),
                    ws_connect=AsyncMock(),
                    close=AsyncMock(),
                )
                self.probe_factory.side_effect = (self.probe_factory.return_value, legacy_client)
                candidate = self._new_bridge()
                candidate._authenticate = AsyncMock()

                with self.assertRaisesRegex(bridge.LoxoneUnsupportedError, "could not be verified"):
                    await candidate.async_initialize(probe_legacy=True)

                self.assertEqual(
                    self.session.mock_calls,
                    [
                        self._websocket_call(),
                        call.get(self.legacy_api_url, ssl=False, allow_redirects=False),
                    ],
                )
                legacy_client.get.assert_called_once_with(
                    self.legacy_certificate_url, allow_redirects=False
                )
                legacy_client.ws_connect.assert_not_awaited()
                legacy_client.close.assert_awaited_once()
                candidate._authenticate.assert_not_awaited()

    async def test_gen1_uses_encrypted_jwt_then_reauthenticates_after_reconnect(self):
        serial = self.legacy_metadata["snr"]
        token = "test.jwt.token"
        password_key, password_salt = "aabbccdd", "testuser-salt"
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        session_keys = []
        commands = []
        self.session.ws_connect.side_effect = OSError("WSS handshake failed")
        self.session.get.return_value = _FakeResponse(200, {
            "LL": {"Code": "200", "value": str(self.legacy_metadata)}
        })
        candidate = self._new_bridge()
        candidate.async_refresh_system_stats = AsyncMock()

        def legacy_client(challenge: str):
            cipher = None
            salt = None

            def encrypt_response(response: str) -> str:
                plaintext = response.encode("utf-8")
                plaintext += b"\0" * (-len(plaintext) % 16)
                encryptor = cipher.encryptor()
                return base64.b64encode(encryptor.update(plaintext) + encryptor.finalize()).decode()

            async def respond(wire: str) -> None:
                nonlocal cipher, salt
                if wire.startswith("jdev/sys/keyexchange/"):
                    material = key.decrypt(
                        base64.b64decode(wire.split("/", 3)[-1], validate=True), padding.PKCS1v15()
                    ).decode("ascii")
                    session_keys.append(material)
                    aes_key, iv = (bytes.fromhex(item) for item in material.split(":"))
                    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
                    candidate._deliver_text(json.dumps({"LL": {
                        "Code": "200", "value": encrypt_response(challenge)
                    }}))
                    return

                prefix, encoded = wire.rsplit("/", 1)
                self.assertIn(prefix, {"jdev/sys/enc", "jdev/sys/fenc"})
                ciphertext = base64.b64decode(unquote(encoded), validate=True)
                decryptor = cipher.decryptor()
                plaintext = (decryptor.update(ciphertext) + decryptor.finalize()).rstrip(b"\0").decode()
                if salt is None:
                    mode, salt, command = plaintext.split("/", 2)
                    self.assertEqual(mode, "salt")
                else:
                    mode, old_salt, new_salt, command = plaintext.split("/", 3)
                    self.assertEqual(mode, "nextSalt")
                    self.assertEqual(old_salt, salt)
                    self.assertNotEqual(new_salt, salt)
                    salt = new_salt
                commands.append((prefix, command))

                value = {}
                if command == "jdev/sys/getkey2/loxone-user":
                    value = {"key": password_key, "salt": password_salt, "hashAlg": "SHA256"}
                elif command.startswith("jdev/sys/getjwt/"):
                    value = {"token": token, "validUntil": "9999999999"}
                elif command == "jdev/cfg/apiKey":
                    value = self.legacy_metadata
                response = json.dumps({"LL": {"Code": "200", "value": value}})
                if command == "data/LoxAPP3.json":
                    response = json.dumps({"msInfo": {"serialNr": serial}, "controls": {}})
                if prefix == "jdev/sys/fenc":
                    response = encrypt_response(response)
                candidate._deliver_text(response)

            websocket = types.SimpleNamespace(closed=False, send_str=respond, close=AsyncMock())
            return Mock(
                get=Mock(return_value=_FakeResponse(200, json.dumps({"LL": {
                    "Code": "200", "value": "test PEM chain"
                }}))),
                ws_connect=AsyncMock(return_value=websocket),
                close=AsyncMock(),
            )

        first_client = legacy_client("bbaa9988")
        second_client = legacy_client("9988bbaa")
        self.probe_factory.side_effect = (self.probe_factory.return_value, first_client, second_client)
        with patch.object(
            bridge.LegacySession,
            "from_certificate_chain",
            side_effect=lambda *_args, **_kwargs: bridge.LegacySession(key.public_key(), serial),
        ) as verify_certificate:
            await candidate.async_initialize(probe_legacy=True)
            self.assertFalse(candidate.use_tls)
            self.assertTrue(candidate.available)
            self.assertEqual(candidate.token, token)
            self.assertEqual(candidate.serial, serial)
            await candidate._cleanup_connection(cancel_reconnect=False)
            await candidate._ensure_connected()
            await candidate.async_send_raw_command("jdev/sps/io/test/on")
            await candidate.async_shutdown()

        self.session.ws_connect.assert_awaited_once()
        self.assertEqual(verify_certificate.call_args_list, [
            call("test PEM chain", expected_serial=serial), call("test PEM chain", expected_serial=serial)
        ])
        for client in (first_client, second_client):
            client.get.assert_called_once_with(self.legacy_certificate_url, allow_redirects=False)
            client.ws_connect.assert_awaited_once_with(
                self.legacy_ws_url, protocols=("remotecontrol",), ssl=False, heartbeat=None
            )
            client.close.assert_awaited_once()
        self.assertEqual(len(set(session_keys)), 2)
        password_hash = hashlib.sha256(f"loxone-password:{password_salt}".encode()).hexdigest().upper()
        password_proof = hmac.new(bytes.fromhex(password_key), f"loxone-user:{password_hash}".encode(), "sha256").hexdigest()
        token_proof = hmac.new(bytes.fromhex("9988bbaa"), token.encode(), "sha256").hexdigest()
        self.assertEqual(commands[0], ("jdev/sys/enc", "jdev/sys/getkey2/loxone-user"))
        self.assertEqual(commands[1][0], "jdev/sys/fenc")
        self.assertTrue(commands[1][1].startswith(f"jdev/sys/getjwt/{password_proof}/loxone-user/4/"))
        self.assertEqual(commands[2:], [
            ("jdev/sys/enc", "data/LoxAPP3.json"),
            ("jdev/sys/enc", "jdev/cfg/apiKey"),
            ("jdev/sys/enc", "jdev/sps/enablebinstatusupdate"),
            ("jdev/sys/fenc", f"authwithtoken/{token_proof}/loxone-user"),
            ("jdev/sys/enc", "jdev/sps/enablebinstatusupdate"),
            ("jdev/sys/enc", "jdev/sps/io/test/on"),
        ])

    async def test_http_response_must_confirm_legacy_loxone(self):
        for status, code, value in (
            (302, 200, self.legacy_metadata),
            (200, 401, self.legacy_metadata),
            (200, 200, "unrelated HTTP response"),
            (200, 200, {"snr": "   ", "version": "15.5.0.0"}),
            (200, 200, {"snr": "invalid", "version": "15.5.0.0"}),
            (200, 200, {"snr": "504F94000000"}),
            (200, 200, {**self.legacy_metadata, "version": "unknown"}),
            (200, 200, {**self.legacy_metadata, "httpsStatus": 1}),
        ):
            with self.subTest(status=status, code=code, value=value):
                self.session.reset_mock()
                error = OSError("WSS handshake failed")
                self.session.ws_connect.side_effect = error
                self.session.get.return_value = _FakeResponse(
                    status, {"LL": {"Code": code, "value": value}}
                )

                with self.assertRaises(bridge.LoxoneConnectionError) as raised:
                    await self._new_bridge().async_initialize(probe_legacy=True)

                self.assertIs(raised.exception.__cause__, error)
                self.session.get.assert_called_once()

    async def test_runtime_does_not_probe_http_after_wss_failure(self):
        self.session.ws_connect.side_effect = OSError("WSS handshake failed")

        with self.assertRaises(bridge.LoxoneConnectionError):
            await self._new_bridge().async_initialize()

        self.assertEqual(self.session.mock_calls, [self._websocket_call()])
        self.probe_factory.assert_not_called()

    async def test_certificate_verification_failure_keeps_tls_error(self):
        error = ssl.SSLCertVerificationError("certificate verification failed")
        self.session.ws_connect.side_effect = error

        with self.assertRaises(bridge.LoxoneConnectionError) as raised:
            await self._new_bridge(verify_ssl=True).async_initialize(probe_legacy=True)

        self.assertIs(raised.exception.__cause__, error)
        self.assertEqual(self.session.mock_calls, [self._websocket_call(verify_ssl=True)])

    async def test_wss_success_on_default_or_custom_port_skips_http(self):
        for port in (443, 7778):
            with self.subTest(port=port):
                self.session.reset_mock()
                self.session.ws_connect.return_value = types.SimpleNamespace(closed=False)
                candidate = self._new_bridge(port)
                candidate._authenticate = AsyncMock()
                candidate._load_structure = AsyncMock()
                candidate._send_loxone_command = AsyncMock()
                candidate.async_refresh_system_stats = AsyncMock()

                await candidate.async_initialize(probe_legacy=True)

                self.assertEqual(self.session.mock_calls, [self._websocket_call(port)])
                candidate._authenticate.assert_awaited_once()
                candidate._load_structure.assert_awaited_once()
                self.assertTrue(candidate.available)

    async def test_direct_websocket_preserves_configured_path_and_port(self):
        candidate = self._new_bridge(host=f"https://{self.host}/loxone/")

        await candidate._open_websocket(self.session, use_tls=True)

        self.session.ws_connect.assert_awaited_once_with(
            f"wss://{self.host}:7778/loxone/ws/rfc6455",
            protocols=("remotecontrol",),
            ssl=False,
            heartbeat=None,
        )
        self.probe_factory.assert_not_called()
        self.assertEqual(
            candidate.resolve_http_url("/camimage/control/time"),
            f"https://{self.host}:7778/loxone/camimage/control/time",
        )

    async def test_remote_aliases_resolve_and_use_verified_relay_for_websocket_and_media(self):
        relay = "eu1.remcov3.loxonecloud.com:32123"
        trace = types.SimpleNamespace(on_request_redirect=[])
        self.enterContext(
            patch.object(sys.modules["aiohttp"], "TraceConfig", return_value=trace, create=True)
        )
        for host in (
            "504F94000000",
            "https://dns.loxonecloud.com/50:4F:94:00:00:00",
            "connect.loxonecloud.com/504F94000000",
        ):
            with self.subTest(host=host):
                websocket = types.SimpleNamespace(closed=False, close=AsyncMock())
                response = _FakeResponse(200, None)
                response.json = AsyncMock(return_value={"url": f"https://{relay}/hub/"})
                remote_client = Mock(
                    get=Mock(return_value=response),
                    ws_connect=AsyncMock(return_value=websocket),
                    close=AsyncMock(),
                )
                self.probe_factory.return_value = remote_client
                candidate = self._new_bridge(host=host)
                candidate._authenticate = AsyncMock()
                candidate._load_structure = AsyncMock()
                candidate._send_loxone_command = AsyncMock()
                candidate.async_refresh_system_stats = AsyncMock()

                await candidate.async_initialize(probe_legacy=True)

                remote_client.get.assert_called_once_with(
                    "https://connect.loxonecloud.com/getip",
                    params={"snr": "504F94000000"},
                    allow_redirects=False,
                )
                response.json.assert_awaited_once_with(content_type=None)
                remote_client.ws_connect.assert_awaited_once_with(
                    f"wss://{relay}/hub/ws/rfc6455",
                    protocols=("remotecontrol",),
                    ssl=None,
                    heartbeat=None,
                )
                candidate._authenticate.assert_awaited_once()
                self.assertTrue(candidate.verify_ssl)
                self.assertEqual(
                    candidate.resolve_http_url("camimage/control/time"),
                    f"https://{relay}/hub/camimage/control/time",
                )
                await candidate.async_shutdown()
                remote_client.close.assert_awaited_once()
        self.assertEqual(self.session.mock_calls, [])

    async def test_remote_connect_does_not_authenticate_on_invalid_relay_or_redirect(self):
        trace = types.SimpleNamespace(on_request_redirect=[])
        self.enterContext(
            patch.object(sys.modules["aiohttp"], "TraceConfig", return_value=trace, create=True)
        )

        async def redirect(*_args, **_kwargs):
            await trace.on_request_redirect[-1](None, None, None)

        for location, error in (
            ("https://eu.remcov3.loxonecloud.com.example.net:32123", "invalid relay URL"),
            ("https://eu.remcov3.loxonecloud.com:32123", "redirected"),
        ):
            with self.subTest(location=location):
                remote_client = Mock(
                    get=Mock(return_value=_FakeResponse(200, {"url": location})),
                    ws_connect=AsyncMock(side_effect=redirect),
                    close=AsyncMock(),
                )
                self.probe_factory.return_value = remote_client
                candidate = self._new_bridge(host="504F94000000")
                candidate._authenticate = AsyncMock()

                with self.assertRaisesRegex(bridge.LoxoneConnectionError, error):
                    await candidate.async_initialize(probe_legacy=True)

                candidate._authenticate.assert_not_awaited()
                remote_client.close.assert_awaited_once()
        self.assertEqual(self.session.mock_calls, [])

    async def test_remote_connect_closes_client_when_lookup_is_cancelled_or_shutdown(self):
        trace = types.SimpleNamespace(on_request_redirect=[])
        self.enterContext(
            patch.object(sys.modules["aiohttp"], "TraceConfig", return_value=trace, create=True)
        )
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown):
                reading = asyncio.Event()
                release = asyncio.Event()

                async def read_json(**_kwargs):
                    reading.set()
                    await release.wait()
                    return {"url": "https://eu1.remcov3.loxonecloud.com:32123"}

                response = _FakeResponse(200, None)
                response.json = read_json
                remote_client = Mock(
                    get=Mock(return_value=response), ws_connect=AsyncMock(), close=AsyncMock()
                )
                self.probe_factory.return_value = remote_client
                candidate = self._new_bridge(host="504F94000000")
                candidate._authenticate = AsyncMock()
                connect = asyncio.create_task(candidate.async_initialize())
                await asyncio.wait_for(reading.wait(), timeout=1)

                if shutdown:
                    await candidate.async_shutdown()
                    release.set()
                    with self.assertRaisesRegex(bridge.LoxoneConnectionError, "shutting down"):
                        await connect
                else:
                    connect.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await connect

                remote_client.ws_connect.assert_not_awaited()
                remote_client.close.assert_awaited_once()
                candidate._authenticate.assert_not_awaited()

    async def test_error_after_wss_upgrade_does_not_trigger_http(self):
        self.session.ws_connect.return_value = types.SimpleNamespace(
            closed=False, close=AsyncMock()
        )
        candidate = self._new_bridge()
        error = bridge.LoxoneConnectionError("WebSocket authentication timed out")
        candidate._authenticate = AsyncMock(side_effect=error)

        with self.assertRaises(bridge.LoxoneConnectionError) as raised:
            await candidate.async_initialize(probe_legacy=True)

        self.assertIs(raised.exception, error)
        self.assertEqual(self.session.mock_calls, [self._websocket_call()])


class BridgeAuthHelpersTests(unittest.TestCase):
    """Verify auth helper behavior for username path handling."""

    def test_token_expiry_uses_miniserver_epoch_for_numeric_values(self) -> None:
        candidate = bridge.LoxoneBridge.__new__(bridge.LoxoneBridge)
        now = datetime.now(timezone.utc)
        epoch = datetime(2009, 1, 1, tzinfo=timezone.utc)
        elapsed = int((now - epoch).total_seconds())

        candidate.token_valid_until = str(elapsed + 3600)
        self.assertFalse(candidate._token_expired())

        candidate.token_valid_until = str(elapsed - 3600)
        self.assertTrue(candidate._token_expired())

    def test_token_expiry_preserves_iso_datetime_values(self) -> None:
        candidate = bridge.LoxoneBridge.__new__(bridge.LoxoneBridge)
        now = datetime.now(timezone.utc)

        candidate.token_valid_until = (now + timedelta(hours=1)).isoformat()
        self.assertFalse(candidate._token_expired())

        candidate.token_valid_until = (now - timedelta(hours=1)).isoformat()
        self.assertTrue(candidate._token_expired())

    def test_auth_path_segment_allows_special_characters(self) -> None:
        self.assertEqual(bridge._auth_path_segment("user@name!"), "user@name!")

    def test_auth_path_segment_rejects_slash(self) -> None:
        with self.assertRaises(bridge.LoxoneAuthenticationError):
            bridge._auth_path_segment("user/name")

    def test_auth_path_segment_can_be_url_encoded_for_path(self) -> None:
        segment = bridge._auth_path_segment("user@name!")
        self.assertEqual(bridge.quote(segment, safe=""), "user%40name%21")

    def test_raise_for_ll_error_payload_maps_auth_error(self) -> None:
        with self.assertRaises(bridge.LoxoneAuthenticationError):
            bridge._raise_for_ll_error_payload({"LL": {"Code": "401"}})

    def test_ensure_supported_miniserver_version_ignores_unknown(self) -> None:
        bridge._ensure_supported_miniserver_version(None)
        bridge._ensure_supported_miniserver_version("unknown")

    def test_ensure_supported_miniserver_version_raises_only_for_real_mismatch(self) -> None:
        with self.assertRaises(bridge.LoxoneVersionUnsupportedError):
            bridge._ensure_supported_miniserver_version("10.1.12.4")

    def test_extract_software_version_supports_multiple_keys(self) -> None:
        self.assertEqual(
            bridge._extract_software_version({"softwareVersion": "14.7.5.7"}),
            "14.7.5.7",
        )
        self.assertEqual(
            bridge._extract_software_version({"swVersion": "14.7.6.19"}),
            "14.7.6.19",
        )
        self.assertEqual(
            bridge._extract_software_version({"version": "10.2.0.0"}),
            "10.2.0.0",
        )
        self.assertIsNone(bridge._extract_software_version({"name": "Miniserver"}))

    def test_resolve_miniserver_target_supports_mac_only_input(self) -> None:
        host, ws_prefix = bridge._resolve_miniserver_target("02AABBCCDDEE")
        self.assertEqual(host, "dns.loxonecloud.com")
        self.assertEqual(ws_prefix, "/02AABBCCDDEE")

    def test_resolve_miniserver_target_supports_dns_url_with_mac_path(self) -> None:
        host, ws_prefix = bridge._resolve_miniserver_target(
            "https://dns.loxonecloud.com/02:AA:BB:CC:DD:EE"
        )
        self.assertEqual(host, "dns.loxonecloud.com")
        self.assertEqual(ws_prefix, "/02AABBCCDDEE")

    def test_resolve_miniserver_target_keeps_regular_host_unchanged(self) -> None:
        host, ws_prefix = bridge._resolve_miniserver_target("192.0.2.10")
        self.assertEqual(host, "192.0.2.10")
        self.assertEqual(ws_prefix, "")


class _FakeFuture:
    def __init__(self) -> None:
        self._done = False
        self.result = None

    def done(self) -> bool:
        return self._done

    def set_result(self, value) -> None:
        self.result = value
        self._done = True


class BridgeTextStateUpdateTests(unittest.TestCase):
    """Verify text websocket status events update runtime entity state."""

    def _new_bridge(self):
        candidate = bridge.LoxoneBridge.__new__(bridge.LoxoneBridge)
        candidate.state_values = {}
        candidate._listeners = {}
        candidate._pending_response = None
        return candidate

    def test_deliver_text_updates_state_and_notifies_listener(self) -> None:
        candidate = self._new_bridge()
        state_uuid = "12345678-1234-5678-1234-567812345678"
        calls = 0

        def listener() -> None:
            nonlocal calls
            calls += 1

        candidate._listeners = {listener: {state_uuid}}

        candidate._deliver_text(
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345678","value":1}}'
        )

        self.assertEqual(candidate.state_values[state_uuid], 1)
        self.assertEqual(calls, 1)

    def test_async_state_update_does_not_consume_pending_command_response(self) -> None:
        candidate = self._new_bridge()
        candidate._pending_response = _FakeFuture()

        candidate._deliver_text(
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345678","value":0}}'
        )

        self.assertFalse(candidate._pending_response.done())
        self.assertEqual(
            candidate.state_values["12345678-1234-5678-1234-567812345678"],
            0,
        )

    def test_state_update_with_code_still_resolves_waiting_command(self) -> None:
        candidate = self._new_bridge()
        pending = _FakeFuture()
        candidate._pending_response = pending
        payload = (
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345678",'
            '"Code":"200","value":1}}'
        )

        candidate._deliver_text(payload)

        self.assertTrue(pending.done())
        self.assertEqual(pending.result, payload)
        self.assertEqual(
            candidate.state_values["12345678-1234-5678-1234-567812345678"],
            1,
        )

    def test_listener_exception_does_not_break_state_processing(self) -> None:
        candidate = self._new_bridge()
        state_uuid = "12345678-1234-5678-1234-567812345678"
        healthy_calls = 0

        def broken_listener() -> None:
            raise RuntimeError("boom")

        def healthy_listener() -> None:
            nonlocal healthy_calls
            healthy_calls += 1

        candidate._listeners = {
            broken_listener: {state_uuid},
            healthy_listener: {state_uuid},
        }

        candidate._deliver_text(
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345678","value":1}}'
        )

        self.assertEqual(candidate.state_values[state_uuid], 1)
        self.assertEqual(healthy_calls, 1)

    def test_intercom_rising_edge_fires_hass_bus_event(self) -> None:
        candidate = self._new_bridge()
        state_uuid = "12345678-1234-5678-1234-567812345678"
        intercom_control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={"bell": state_uuid},
        )
        candidate.structure = types.SimpleNamespace(
            states={
                state_uuid: types.SimpleNamespace(
                    control_uuid_action="intercom-action",
                    state_name="bell",
                )
            },
            controls_by_action={"intercom-action": intercom_control},
        )
        candidate.serial = "1234567890"
        fired_events: list[tuple[str, dict]] = []
        candidate.hass = types.SimpleNamespace(
            bus=types.SimpleNamespace(
                async_fire=lambda event_type, data: fired_events.append((event_type, data))
            )
        )

        candidate._deliver_text(
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345678","value":1}}'
        )
        candidate._deliver_text(
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345678","value":1}}'
        )

        self.assertEqual(len(fired_events), 1)
        self.assertEqual(fired_events[0][0], bridge.EVENT_INTERCOM)
        self.assertEqual(fired_events[0][1]["event"], "doorbell")
        self.assertEqual(fired_events[0][1]["uuid_action"], "intercom-action")

    def test_repeated_access_denied_updates_fire_hass_bus_events(self) -> None:
        candidate = self._new_bridge()
        state_uuid = "12345678-1234-5678-1234-567812345679"
        access_control = LoxoneControl(
            uuid="nfc-uuid",
            uuid_action="nfc-action",
            name="Front Keypad",
            type="NfcCodeTouch",
            states={"wrongCode": state_uuid},
        )
        candidate.structure = types.SimpleNamespace(
            states={
                state_uuid: types.SimpleNamespace(
                    control_uuid_action="nfc-action",
                    state_name="wrongCode",
                )
            },
            controls_by_action={"nfc-action": access_control},
        )
        candidate.serial = "1234567890"
        fired_events: list[tuple[str, dict]] = []
        candidate.hass = types.SimpleNamespace(
            bus=types.SimpleNamespace(
                async_fire=lambda event_type, data: fired_events.append((event_type, data))
            )
        )

        candidate._deliver_text(
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345679",'
            '"value":"Wrong code"}}'
        )
        candidate._deliver_text(
            '{"LL":{"control":"dev/sps/io/12345678-1234-5678-1234-567812345679",'
            '"value":"Wrong code"}}'
        )

        self.assertEqual(len(fired_events), 2)
        self.assertEqual(fired_events[0][0], bridge.EVENT_ACCESS)
        self.assertEqual(fired_events[0][1]["event"], "access_denied")
        self.assertEqual(fired_events[0][1]["uuid_action"], "nfc-action")
        self.assertEqual(fired_events[1][0], bridge.EVENT_ACCESS)
        self.assertEqual(fired_events[1][1]["event"], "access_denied")



if __name__ == "__main__":
    unittest.main()
