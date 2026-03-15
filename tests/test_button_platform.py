"""Tests for Loxone button platform aliases and actions."""

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

    button = sys.modules["homeassistant.components.button"]

    class ButtonEntity:
        pass

    button.ButtonEntity = ButtonEntity

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
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    const.CONF_PORT = "port"
    const.CONF_USERNAME = "username"
    const.CONF_VERIFY_SSL = "verify_ssl"
    sys.modules["homeassistant.const"] = const

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        pass

    config_entries.ConfigEntry = ConfigEntry
    sys.modules["homeassistant.config_entries"] = config_entries

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

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")

    def async_get_clientsession(_hass, verify_ssl=True):
        return None

    aiohttp_client.async_get_clientsession = async_get_clientsession
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform

    device_registry = types.ModuleType("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        pass

    device_registry.DeviceInfo = DeviceInfo
    sys.modules["homeassistant.helpers.device_registry"] = device_registry

    entity = types.ModuleType("homeassistant.helpers.entity")

    class Entity:
        pass

    class EntityCategory:
        CONFIG = "config"

    entity.Entity = Entity
    entity.EntityCategory = EntityCategory
    sys.modules["homeassistant.helpers.entity"] = entity

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


class _FakeBridge:
    serial = "1234567890"
    available = True
    miniserver_name = "Loxone Miniserver"
    software_version = "16.1.11.6"

    def __init__(self, controls):
        self.controls = controls
        self.commands: list[tuple[str, str]] = []
        self.raw_commands: list[str] = []

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    async def async_send_action(self, uuid_action, command):
        self.commands.append((uuid_action, command))

    async def async_send_raw_command(self, command):
        self.raw_commands.append(command)


class _FakeConfigEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, entry_id: str, bridge, domain: str) -> None:
        self.data = {domain: {"bridges": {entry_id: bridge}}}


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
const = load_integration_module("custom_components.loxone_home_assistant.const")
button_module = load_integration_module("custom_components.loxone_home_assistant.button")
intercom_module = load_integration_module("custom_components.loxone_home_assistant.intercom")
LoxoneControl = models.LoxoneControl


class ButtonPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Verify button setup handles known type aliases."""

    async def test_setup_includes_push_button_alias_type(self) -> None:
        push_button = LoxoneControl(
            uuid="button-uuid",
            uuid_action="button-action",
            name="Dzwonek",
            type="Push Button",
            states={"active": "state-active"},
        )
        bridge = _FakeBridge([push_button])
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await button_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 2)
        entity = entities[0]
        await entity.async_press()
        self.assertEqual(bridge.commands, [("button-action", "pulse")])

    async def test_setup_adds_refresh_system_stats_hub_button_when_supported(self) -> None:
        bridge = _FakeBridge([])
        refresh_calls: list[bool] = []

        async def async_refresh_system_stats(*, force=False):
            refresh_calls.append(bool(force))

        bridge.async_refresh_system_stats = async_refresh_system_stats
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await button_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        self.assertEqual(len(entities), 2)
        refresh_buttons = [
            entity
            for entity in entities
            if isinstance(entity, button_module.LoxoneHubRefreshSystemStatsButton)
        ]
        self.assertEqual(len(refresh_buttons), 1)

        await refresh_buttons[0].async_press()
        self.assertEqual(refresh_calls, [True])

    async def test_setup_adds_intercom_gen2_command_buttons(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "bell": "state-bell",
                "trustAddress": "state-trust",
            },
        )
        bridge = _FakeBridge([intercom])
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await button_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        intercom_buttons = [
            entity
            for entity in entities
            if isinstance(entity, button_module.LoxoneIntercomCommandButton)
        ]
        self.assertEqual(len(intercom_buttons), 3)

        for button in intercom_buttons:
            await button.async_press()

        self.assertEqual(
            bridge.commands,
            [
                ("intercom-action", "answer"),
                ("intercom-action", "mute/1"),
                ("intercom-action", "mute/0"),
            ],
        )

    async def test_setup_skips_legacy_intercom_command_buttons(self) -> None:
        legacy_intercom = LoxoneControl(
            uuid="intercom-legacy-uuid",
            uuid_action="intercom-legacy-action",
            name="Furtka Legacy",
            type="Intercom",
            states={
                "bell": "state-bell",
            },
        )
        bridge = _FakeBridge([legacy_intercom])
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await button_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        intercom_buttons = [
            entity
            for entity in entities
            if isinstance(entity, button_module.LoxoneIntercomCommandButton)
        ]
        self.assertEqual(intercom_buttons, [])

    async def test_setup_adds_intercom_tts_button_and_sends_tts(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Furtka",
            type="IntercomV2",
            states={
                "bell": "state-bell",
                "trustAddress": "state-trust",
            },
        )
        bridge = _FakeBridge([intercom])
        entry = _FakeConfigEntry("entry-1")
        hass = _FakeHass(entry.entry_id, bridge, const.DOMAIN)
        entities: list = []

        await button_module.async_setup_entry(
            hass,
            entry,
            lambda new_entities: entities.extend(new_entities),
        )

        tts_buttons = [
            entity
            for entity in entities
            if isinstance(entity, button_module.LoxoneIntercomTtsButton)
        ]
        self.assertEqual(len(tts_buttons), 1)
        tts_button = tts_buttons[0]

        await tts_button.async_press()
        self.assertEqual(bridge.commands, [])

        intercom_module.set_intercom_tts_message(bridge, "intercom-action", "Test message")
        intercom_module.set_intercom_tts_volume(bridge, "intercom-action", 75)

        await tts_button.async_press()
        self.assertEqual(
            bridge.commands,
            [("intercom-action", "tts/Test%20message")],
        )


if __name__ == "__main__":
    unittest.main()
