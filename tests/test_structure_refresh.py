"""Tests for detecting and monitoring changes to the Miniserver project."""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from tests.test_bridge_system_stats import bridge_module

LOADED_VERSION = "2026-11-20 10:00:00"
NEW_VERSION = "2026-11-20 10:05:00"
VERSION_COMMAND = "jdev/sps/LoxAPPversion3"
STRUCTURE_COMMAND = "data/LoxAPP3.json"


def version_response(value: object) -> str:
    return json.dumps({"LL": {"Code": "200", "control": VERSION_COMMAND, "value": value}})


def structure_response(last_modified: str) -> str:
    return json.dumps({"lastModified": last_modified, "msInfo": {}, "controls": {}})


class StructureRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bridge = bridge_module.LoxoneBridge(
            SimpleNamespace(async_create_background_task=self._create_background_task),
            {"host": "miniserver.local", "username": "test", "password": "test"},
        )
        self.bridge.loxapp_version = LOADED_VERSION
        self.addAsyncCleanup(self.bridge.async_shutdown)

    @staticmethod
    def _create_background_task(coroutine, name):
        return asyncio.create_task(coroutine, name=name)

    async def test_changed_project_is_confirmed_by_the_structure_file(self) -> None:
        send = self.bridge._send_text_command = AsyncMock(
            side_effect=[version_response(NEW_VERSION), structure_response(NEW_VERSION)]
        )

        self.assertTrue(await self.bridge._async_structure_has_changed())

        self.assertEqual(
            send.await_args_list,
            [
                call(VERSION_COMMAND, ensure_connected=False),
                call(STRUCTURE_COMMAND, ensure_connected=False),
            ],
        )
        self.assertEqual(self.bridge.loxapp_version, LOADED_VERSION)

    async def test_unchanged_or_invalid_version_does_not_fetch_the_structure(self) -> None:
        for version in ("2026-11-20T10:00:00", None, "invalid", {"lastModified": NEW_VERSION}):
            with self.subTest(version=version):
                send = self.bridge._send_text_command = AsyncMock(
                    return_value=version_response(version)
                )

                self.assertFalse(await self.bridge._async_structure_has_changed())

                send.assert_awaited_once_with(VERSION_COMMAND, ensure_connected=False)

    async def test_missing_baseline_does_not_query_the_miniserver(self) -> None:
        self.bridge.loxapp_version = ""
        send = self.bridge._send_text_command = AsyncMock()

        self.assertFalse(await self.bridge._async_structure_has_changed())

        send.assert_not_awaited()

    async def test_unchanged_or_unavailable_structure_does_not_trigger_reload(self) -> None:
        for structure in (
            structure_response(LOADED_VERSION),
            structure_response("invalid"),
            json.dumps({"lastModified": NEW_VERSION}),
            bridge_module.LoxoneConnectionError("connection lost"),
        ):
            with self.subTest(structure=structure):
                self.bridge._send_text_command = AsyncMock(
                    side_effect=[version_response(NEW_VERSION), structure]
                )

                self.assertFalse(await self.bridge._async_structure_has_changed())

    async def test_monitor_checks_immediately_and_requests_reload(self) -> None:
        self.bridge.available = True
        check = self.bridge._async_structure_has_changed = AsyncMock(return_value=True)
        reload_requested = asyncio.Event()
        on_change = Mock(side_effect=reload_requested.set)

        self.bridge.start_structure_monitor(on_change)
        await asyncio.wait_for(reload_requested.wait(), timeout=2)

        check.assert_awaited_once()
        on_change.assert_called_once_with()

    async def test_monitor_retries_while_the_old_bridge_remains_available(self) -> None:
        self.bridge.available = True
        check = self.bridge._async_structure_has_changed = AsyncMock(
            side_effect=[False, True, True]
        )
        on_change = Mock()

        def request_reload() -> None:
            if on_change.call_count == 2:
                self.bridge._mark_unavailable()

        on_change.side_effect = request_reload

        with patch.object(bridge_module, "STRUCTURE_CHECK_SECONDS", 0):
            self.bridge.start_structure_monitor(on_change)
            await asyncio.wait_for(self.bridge._structure_task, timeout=2)

        self.assertEqual(check.await_count, 3)
        self.assertEqual(on_change.call_args_list, [call(), call()])

    async def test_monitor_is_cancelled_on_disconnect_and_restarts_on_reconnect(self) -> None:
        first_check = asyncio.Event()
        second_check = asyncio.Event()

        async def check_structure():
            if first_check.is_set():
                second_check.set()
            else:
                first_check.set()
            await asyncio.Event().wait()

        self.bridge.available = True
        self.bridge.structure = object()
        self.bridge._async_structure_has_changed = check_structure
        on_change = Mock()
        self.bridge.start_structure_monitor(on_change)
        first_monitor = self.bridge._structure_task
        await asyncio.wait_for(first_check.wait(), timeout=2)

        self.bridge._mark_unavailable()
        await self.bridge._cleanup_connection(cancel_reconnect=False)
        self.assertTrue(first_monitor.cancelled())

        self.bridge._open_websocket = AsyncMock()
        self.bridge._reader_loop = AsyncMock()
        self.bridge._keepalive_loop = AsyncMock()
        self.bridge._authenticate = AsyncMock()
        self.bridge._send_loxone_command = AsyncMock()
        self.bridge.async_refresh_system_stats = AsyncMock()
        self.bridge._ensure_system_stats_task = Mock()
        await self.bridge._connect(load_structure=False)
        second_monitor = self.bridge._structure_task
        await asyncio.wait_for(second_check.wait(), timeout=2)

        await self.bridge.async_shutdown()
        self.assertTrue(second_monitor.cancelled())
        on_change.assert_not_called()
