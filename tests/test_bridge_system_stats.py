"""Tests for Miniserver bridge diagnostics and connection lifecycle."""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

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

    class HomeAssistant:
        pass

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
        del verify_ssl
        return None

    aiohttp_client.async_get_clientsession = async_get_clientsession
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientSession:
        pass

    class WSMessage:
        pass

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
bridge_module = load_integration_module("custom_components.loxone_home_assistant.bridge")
connection_config = load_integration_module("custom_components.loxone_home_assistant.connection_config")


class BridgeSystemStatsTests(unittest.IsolatedAsyncioTestCase):
    """Verify bridge caches and throttles `dev/sys/*` refreshes."""

    async def test_refresh_system_stats_updates_synthetic_state_values(self) -> None:
        bridge = bridge_module.LoxoneBridge.__new__(bridge_module.LoxoneBridge)
        bridge.state_values = {}
        bridge._system_stats_lock = asyncio.Lock()
        bridge._system_stats_last_refresh = 0.0

        calls: list[str] = []
        command_values = {
            "dev/sys/numtasks": 11,
            "dev/sys/cpu": 12.5,
            "dev/sys/heap": 48.1,
            "dev/sys/ints": 2048,
        }

        async def fake_send(command: str, *, ensure_connected: bool = True):
            del ensure_connected
            calls.append(command)
            return {"value": command_values[command]}

        def fake_merge(changed):
            bridge.state_values.update(changed)

        bridge._send_loxone_command = fake_send
        bridge._merge_changed_states = fake_merge

        first = await bridge_module.LoxoneBridge.async_refresh_system_stats(
            bridge,
            force=True,
            ensure_connected=False,
        )
        self.assertEqual(first["cpu"], 12.5)
        self.assertEqual(first["numtasks"], 11)
        self.assertEqual(bridge.state_values["sysdiag-cpu"], 12.5)
        self.assertEqual(bridge.state_values["sysdiag-numtasks"], 11)
        self.assertEqual(len(calls), 4)

        command_values["dev/sys/cpu"] = 55.0
        second = await bridge_module.LoxoneBridge.async_refresh_system_stats(
            bridge,
            force=False,
            ensure_connected=False,
        )
        self.assertEqual(second["cpu"], 12.5)
        self.assertEqual(len(calls), 4)

        third = await bridge_module.LoxoneBridge.async_refresh_system_stats(
            bridge,
            force=True,
            ensure_connected=False,
        )
        self.assertEqual(third["cpu"], 55.0)
        self.assertEqual(len(calls), 8)

    async def test_send_loxone_command_accepts_plain_scalar_json_responses(self) -> None:
        bridge = bridge_module.LoxoneBridge.__new__(bridge_module.LoxoneBridge)

        async def fake_send_text(command: str, *, ensure_connected: bool = True) -> str:
            del command, ensure_connected
            return "0"

        bridge._send_text_command = fake_send_text

        response = await bridge_module.LoxoneBridge._send_loxone_command(
            bridge,
            "dev/sys/cpu",
            ensure_connected=False,
        )
        self.assertEqual(response["code"], 200)
        self.assertEqual(response["control"], "dev/sys/cpu")
        self.assertEqual(response["value"], 0)

    async def test_send_loxone_command_accepts_non_ll_json_payloads(self) -> None:
        bridge = bridge_module.LoxoneBridge.__new__(bridge_module.LoxoneBridge)

        async def fake_send_text(command: str, *, ensure_connected: bool = True) -> str:
            del command, ensure_connected
            return '{"controls":{"abc":1}}'

        bridge._send_text_command = fake_send_text

        response = await bridge_module.LoxoneBridge._send_loxone_command(
            bridge,
            "data/LoxAPP3.json",
            ensure_connected=False,
        )
        self.assertEqual(response["code"], 200)
        self.assertEqual(response["control"], "data/LoxAPP3.json")
        self.assertEqual(response["value"], {"controls": {"abc": 1}})

    async def test_send_loxone_command_rejects_empty_responses(self) -> None:
        bridge = bridge_module.LoxoneBridge.__new__(bridge_module.LoxoneBridge)

        async def fake_send_text(command: str, *, ensure_connected: bool = True) -> str:
            del command, ensure_connected
            return ""

        bridge._send_text_command = fake_send_text

        with self.assertRaises(bridge_module.LoxoneConnectionError):
            await bridge_module.LoxoneBridge._send_loxone_command(
                bridge,
                "dev/sys/cpu",
                ensure_connected=False,
            )


class BridgeConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bridge = bridge_module.LoxoneBridge(
            object(),
            {"host": "miniserver.local", "username": "test", "password": "test"},
        )
        self.addAsyncCleanup(self.bridge.async_shutdown)

    async def test_reconnect_and_command_share_one_websocket(self) -> None:
        connect_started = asyncio.Event()
        finish_connect = asyncio.Event()
        websocket = types.SimpleNamespace(closed=False, close=AsyncMock())

        async def connect(*, load_structure, probe_legacy=False):
            connect_started.set()
            await finish_connect.wait()
            self.bridge._ws = websocket

        self.bridge.structure = object()
        self.bridge._connect = AsyncMock(side_effect=connect)
        with patch.object(bridge_module.asyncio, "sleep", new=AsyncMock()):
            async with asyncio.timeout(2):
                reconnect = asyncio.create_task(self.bridge._reconnect_loop())
                await connect_started.wait()
                command_connect = asyncio.create_task(self.bridge._ensure_connected())
                asyncio.get_running_loop().call_soon(finish_connect.set)
                await asyncio.gather(reconnect, command_connect)

        self.bridge._connect.assert_awaited_once()

    async def test_websocket_opened_during_shutdown_is_closed(self) -> None:
        connect_started = asyncio.Event()
        finish_connect = asyncio.Event()
        websocket = types.SimpleNamespace(closed=False, close=AsyncMock())

        async def ws_connect(*args, **kwargs):
            connect_started.set()
            await finish_connect.wait()
            return websocket

        self.bridge._session = types.SimpleNamespace(ws_connect=ws_connect)
        async with asyncio.timeout(2):
            connection = asyncio.create_task(self.bridge._ensure_connected())
            await connect_started.wait()
            await self.bridge.async_shutdown()
            finish_connect.set()
            with self.assertRaises(bridge_module.LoxoneConnectionError):
                await connection

        websocket.close.assert_awaited_once()
        self.assertIsNone(self.bridge._ws)

    async def test_structure_sets_the_allowlist_of_configured_icon_paths(self) -> None:
        structure = {
            "msInfo": {"serialNr": "504f94000000", "softwareVersion": "17.3.11.12"},
            "controls": {
                "doorbell": {
                    "name": "Doorbell",
                    "type": "Pushbutton",
                    "defaultIcon": "/IconsFilled/doorbell.svg",
                },
                "switch": {
                    "name": "Switch",
                    "type": "Switch",
                    "defaultIcon": "https://example.test/icon.svg",
                },
            },
        }
        self.bridge._send_text_command = AsyncMock(return_value=json.dumps(structure))

        await self.bridge._load_structure()

        self.assertEqual(self.bridge.icon_paths, {"IconsFilled/doorbell.svg"})

    def test_runtime_persists_the_fresh_token_with_edited_connection_settings(self) -> None:
        stored = {
            "host": "miniserver.local",
            "port": 443,
            "username": "old-user",
            "password": "old-password",
            "token": "old-token",
            "token_valid_until": "999999999",
        }
        changes = {
            "host": "new-address.local",
            "port": 8443,
            "username": "new-user",
            "password": "new-password",
        }

        merged = connection_config.merge_connection_config(stored, changes)
        bridge = bridge_module.LoxoneBridge(object(), merged)
        self.addAsyncCleanup(bridge.async_shutdown)
        self.assertIsNone(bridge.token)
        self.assertIsNone(bridge.token_valid_until)
        bridge.token = "fresh-token"
        bridge.token_valid_until = "999999999"

        exported = bridge.export_runtime_data()
        for key, value in changes.items():
            self.assertEqual(exported[key], value)
        reloaded = connection_config.merge_connection_config({**stored, **exported}, changes)
        self.assertEqual(reloaded["token"], "fresh-token")
        self.assertEqual(reloaded["token_valid_until"], "999999999")


if __name__ == "__main__":
    unittest.main()
