"""Tests for Loxone discovery helpers."""

from __future__ import annotations

import sys
import types
import unittest

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


class _FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url, ssl=False):
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
                    {"LL": {"value": "{'httpsStatus':0,'name':'Legacy'}"}},
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


class BridgeAuthHelpersTests(unittest.TestCase):
    """Verify auth helper behavior for username path handling."""

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


if __name__ == "__main__":
    unittest.main()
