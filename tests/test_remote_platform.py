"""Regression tests for Remote power commands without AQp feedback."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from tests._loader import load_integration_module
from tests.test_media_player_platform import LoxoneControl, _install_homeassistant_stubs


def _load_remote():
    with patch.dict(sys.modules):
        _install_homeassistant_stubs()
        sys.modules["homeassistant.components.remote"] = SimpleNamespace(
            ATTR_ACTIVITY="activity",
            ATTR_DELAY_SECS="delay_secs",
            ATTR_HOLD_SECS="hold_secs",
            ATTR_NUM_REPEATS="num_repeats",
            DEFAULT_DELAY_SECS=0.4,
            DEFAULT_HOLD_SECS=0,
            DEFAULT_NUM_REPEATS=1,
            RemoteEntity=type("RemoteEntity", (), {}),
            RemoteEntityFeature=SimpleNamespace(ACTIVITY=1),
        )
        return load_integration_module("custom_components.loxone_home_assistant.remote")


remote = _load_remote()


class LoxoneRemotePowerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.values = {"mode": 0}
        self.bridge = SimpleNamespace(
            available=True,
            serial="test-server",
            control_state=lambda control, name: self.values.get(name),
            async_send_action=AsyncMock(),
        )
        self.control = LoxoneControl(
            uuid="remote", uuid_action="remote", type="Remote", name="Media controller",
            details={"modeList": {"1": {"name": "TV"}}},
        )
        self.entity = remote.LoxoneRemote(self.bridge, self.control)
        self.entity.async_write_ha_state = Mock()

    async def test_power_commands_work_without_an_active_mode(self) -> None:
        self.assertFalse(self.entity.is_on)
        self.assertTrue(self.entity.assumed_state)

        await self.entity.async_turn_on()
        self.assertTrue(self.entity.is_on)
        self.assertIsNone(self.entity.current_activity)
        self.entity.async_write_ha_state.assert_called_once_with()

        await self.entity.async_turn_off()
        self.assertFalse(self.entity.is_on)
        self.assertEqual(
            self.bridge.async_send_action.await_args_list,
            [call("remote", "on"), call("remote", "reset"), call("remote", "off")],
        )

    async def test_reported_states_and_disconnect(self) -> None:
        await self.entity.async_turn_on(activity="TV")
        self.bridge.async_send_action.assert_awaited_once_with("remote", "mode/1")
        self.values["mode"] = 1
        self.assertTrue(self.entity.is_on)
        self.assertEqual(self.entity.current_activity, "TV")

        await self.entity.async_send_command("on")
        self.values["power"] = 0
        self.assertFalse(self.entity.is_on)
        self.assertFalse(self.entity.assumed_state)

        self.bridge.available = False
        self.entity._handle_bridge_update()
        self.bridge.available = True
        self.values = {"mode": 0}
        self.assertFalse(self.entity.is_on)

    async def test_rejected_command_does_not_change_state(self) -> None:
        self.bridge.async_send_action.side_effect = RuntimeError("send failed")
        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await self.entity.async_turn_on()
        self.assertFalse(self.entity.is_on)
        self.entity.async_write_ha_state.assert_not_called()
