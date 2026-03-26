"""Tests for Intercom command resolution helpers."""

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

    const = types.ModuleType("homeassistant.const")

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


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
load_integration_module("custom_components.loxone_home_assistant.const")
intercom_module = load_integration_module("custom_components.loxone_home_assistant.intercom")
resolve_intercom_command = intercom_module.resolve_intercom_command
build_intercom_tts_command = intercom_module.build_intercom_tts_command
intercom_address_state_name = intercom_module.intercom_address_state_name
resolve_intercom_http_url = intercom_module.resolve_intercom_http_url
LoxoneControl = models.LoxoneControl


class IntercomCommandResolutionTests(unittest.TestCase):
    """Verify Intercom command name and argument mapping."""

    def test_resolve_simple_command(self) -> None:
        self.assertEqual(resolve_intercom_command("answer"), "answer")

    def test_resolve_alias_and_encode_text_argument(self) -> None:
        command = resolve_intercom_command("tts", "2")
        self.assertEqual(command, "playTts/2")

    def test_resolve_play_tts_accepts_non_numeric_text(self) -> None:
        command = resolve_intercom_command("playTts", "Kod niepoprawny")
        self.assertEqual(command, "playTts/Kod%20niepoprawny")

    def test_build_intercom_tts_command_ignores_volume_suffix(self) -> None:
        command = build_intercom_tts_command("Test message", 75)
        self.assertEqual(command, "tts/Test%20message")

    def test_build_intercom_tts_command_returns_none_for_empty_text(self) -> None:
        self.assertIsNone(build_intercom_tts_command("   ", 60))

    def test_resolve_setanswers_from_slash_text(self) -> None:
        command = resolve_intercom_command("setanswers", "Leave package/Call owner")
        self.assertEqual(command, "setAnswers/Leave%20package/Call%20owner")

    def test_resolve_setvideosettings_from_argument_list(self) -> None:
        command = resolve_intercom_command("setvideosettings", [0, 1280, 720])
        self.assertEqual(command, "setvideosettings/0/1280/720")

    def test_unmute_alias_uses_mute_with_default_zero_argument(self) -> None:
        command = resolve_intercom_command("unmute")
        self.assertEqual(command, "mute/0")

    def test_accept_alias_uses_answer_command(self) -> None:
        command = resolve_intercom_command("accept")
        self.assertEqual(command, "answer")

    def test_mute_mic_aliases_map_to_mute_defaults(self) -> None:
        self.assertEqual(resolve_intercom_command("muteMic"), "mute/1")
        self.assertEqual(resolve_intercom_command("unmuteMic"), "mute/0")

    def test_rejects_unknown_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported intercom command"):
            resolve_intercom_command("unknown")

    def test_rejects_missing_required_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "expects at least 1 argument"):
            resolve_intercom_command("setnumberbellimages")

    def test_rejects_non_numeric_argument_when_integer_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            resolve_intercom_command("mute", "abc")

    def test_address_state_prefers_trust_address(self) -> None:
        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "address": "state-address",
                "trustAddress": "state-trust",
            },
        )
        self.assertEqual(intercom_address_state_name(control), "trustAddress")

    def test_resolve_intercom_http_url_falls_back_to_detail_address(self) -> None:
        class _FakeBridge:
            use_tls = False

            @staticmethod
            def resolve_http_url(value: str) -> str:
                path = value if value.startswith("/") else f"/{value}"
                return f"http://miniserver.local{path}"

        control = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Gate",
            type="IntercomV2",
            states={},
            details={"videoInfo": {"host": "192.168.0.50"}},
        )

        self.assertEqual(
            resolve_intercom_http_url(_FakeBridge(), control, "jpg/image.jpg"),
            "http://192.168.0.50/jpg/image.jpg",
        )
