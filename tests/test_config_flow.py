"""Tests for Loxone config flow forms."""

from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
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

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigFlowResult = dict

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            del kwargs

        def async_show_form(self, **kwargs):
            return kwargs

        def async_show_menu(self, **kwargs):
            return kwargs

        def async_create_entry(self, **kwargs):
            return kwargs

        async def async_set_unique_id(self, _unique_id):
            return None

        def _abort_if_unique_id_configured(self, updates=None):
            del updates
            return None

    class OptionsFlow:
        def __init__(self, config_entry=None) -> None:
            self.config_entry = config_entry

        def async_show_form(self, **kwargs):
            return kwargs

        def async_create_entry(self, **kwargs):
            return kwargs

    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    sys.modules["homeassistant.config_entries"] = config_entries

    core = types.ModuleType("homeassistant.core")

    def callback(func):
        return func

    core.callback = callback
    sys.modules["homeassistant.core"] = core


def _install_voluptuous_stub() -> None:
    voluptuous = types.ModuleType("voluptuous")

    class _Marker:
        def __init__(self, schema, default=None) -> None:
            self.schema = schema
            self.default = default

        def __hash__(self) -> int:
            return hash((type(self), self.schema))

        def __eq__(self, other) -> bool:
            return (
                isinstance(other, type(self))
                and getattr(other, "schema", None) == self.schema
            )

    class Required(_Marker):
        pass

    class Optional(_Marker):
        pass

    class In:
        def __init__(self, container) -> None:
            self.container = container

    class Schema:
        def __init__(self, schema) -> None:
            self.schema = schema

    voluptuous.Required = Required
    voluptuous.Optional = Optional
    voluptuous.In = In
    voluptuous.Schema = Schema
    sys.modules["voluptuous"] = voluptuous


def _install_config_flow_dependency_stubs() -> None:
    bridge = types.ModuleType("custom_components.loxone_home_assistant.bridge")

    @dataclass
    class DiscoveryResult:
        host: str
        port: int
        use_tls: bool
        label: str
        server_model: str = "Miniserver"

    class LoxoneAuthenticationError(Exception):
        pass

    class LoxoneConnectionError(Exception):
        pass

    class LoxoneUnsupportedError(Exception):
        pass

    class LoxoneVersionUnsupportedError(Exception):
        pass

    class LoxoneBridge:
        def __init__(self, hass, data) -> None:
            del hass, data

        async def async_initialize(self, *, probe_legacy: bool = False) -> None:
            del probe_legacy
            return None

        async def async_shutdown(self) -> None:
            return None

    async def async_discover_miniservers(hass, timeout):
        del hass, timeout
        return types.SimpleNamespace(devices=[], legacy_found=False)

    bridge.DiscoveryResult = DiscoveryResult
    bridge.LoxoneAuthenticationError = LoxoneAuthenticationError
    bridge.LoxoneBridge = LoxoneBridge
    bridge.LoxoneConnectionError = LoxoneConnectionError
    bridge.LoxoneUnsupportedError = LoxoneUnsupportedError
    bridge.LoxoneVersionUnsupportedError = LoxoneVersionUnsupportedError
    bridge.async_discover_miniservers = async_discover_miniservers
    sys.modules["custom_components.loxone_home_assistant.bridge"] = bridge

    runtime = types.ModuleType("custom_components.loxone_home_assistant.runtime")

    def entry_bridge(hass, entry):
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is not None:
            return runtime_data
        for domain_data in getattr(hass, "data", {}).values():
            if not isinstance(domain_data, dict):
                continue
            bridges = domain_data.get("bridges")
            if isinstance(bridges, dict) and entry.entry_id in bridges:
                return bridges[entry.entry_id]
        return None

    def set_entry_bridge(hass, entry, bridge):
        data = getattr(hass, "data", None)
        if not isinstance(data, dict):
            return None
        for domain_data in data.values():
            if not isinstance(domain_data, dict):
                continue
            bridges = domain_data.setdefault("bridges", {})
            if isinstance(bridges, dict):
                bridges[entry.entry_id] = bridge
                setattr(entry, "runtime_data", bridge)
                return None
        return None

    def remove_entry_bridge(hass, entry):
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is not None:
            setattr(entry, "runtime_data", None)
        data = getattr(hass, "data", None)
        if not isinstance(data, dict):
            return runtime_data
        for domain_data in data.values():
            if not isinstance(domain_data, dict):
                continue
            bridges = domain_data.get("bridges")
            if isinstance(bridges, dict):
                removed = bridges.pop(entry.entry_id, None)
                if removed is not None:
                    return removed
        return runtime_data

    def bridges_by_entry_id(hass):
        result = {}
        for domain_data in getattr(hass, "data", {}).values():
            if not isinstance(domain_data, dict):
                continue
            bridges = domain_data.get("bridges")
            if isinstance(bridges, dict):
                result.update(bridges)
        return result

    def runtime_bridge(config_entry):
        return getattr(config_entry, "runtime_data", None)

    runtime.entry_bridge = entry_bridge
    runtime.set_entry_bridge = set_entry_bridge
    runtime.remove_entry_bridge = remove_entry_bridge
    runtime.bridges_by_entry_id = bridges_by_entry_id
    runtime.runtime_bridge = runtime_bridge
    sys.modules["custom_components.loxone_home_assistant.runtime"] = runtime

    versioning = types.ModuleType("custom_components.loxone_home_assistant.versioning")
    versioning.MIN_SUPPORTED_VERSION = (10, 2)
    versioning.MIN_SUPPORTED_VERSION_TEXT = "10.2"

    def parse_miniserver_version(value):
        if value is None:
            return None
        parts = [int(item) for item in str(value).replace("-", ".").split(".") if item.isdigit()]
        if len(parts) < 2:
            return None
        return tuple(parts)

    def is_supported_miniserver_version(value):
        parsed = parse_miniserver_version(value)
        if parsed is None:
            return False
        return parsed[:2] >= versioning.MIN_SUPPORTED_VERSION

    versioning.parse_miniserver_version = parse_miniserver_version
    versioning.is_supported_miniserver_version = is_supported_miniserver_version
    sys.modules["custom_components.loxone_home_assistant.versioning"] = versioning


_install_homeassistant_stubs()
_install_voluptuous_stub()
_install_config_flow_dependency_stubs()
config_flow = load_integration_module("custom_components.loxone_home_assistant.config_flow")


def _schema_keys(schema) -> set[str]:
    return {field.schema for field in schema.schema}


def _schema_defaults(schema) -> dict[str, object]:
    return {
        field.schema: getattr(field, "default", None)
        for field in schema.schema
    }


def _connected_bridge(*, use_tls: bool):
    return types.SimpleNamespace(
        async_initialize=AsyncMock(),
        async_shutdown=AsyncMock(),
        miniserver_name="Workshop",
        serial="504F94000000",
        client_uuid="integration-client",
        loxapp_version="1.0",
        software_version="15.5.0.0",
        server_model="Miniserver",
        use_tls=use_tls,
        token="test-token",
        token_valid_until=None,
    )


class ConfigFlowFormTests(unittest.TestCase):
    """Verify exposed config flow fields."""

    def test_auto_form_uses_only_main_credentials(self) -> None:
        flow = config_flow.LoxoneCommunityConfigFlow()

        result = flow._async_show_auto_form(errors={})

        self.assertEqual(result["step_id"], "auto")
        self.assertEqual(_schema_keys(result["data_schema"]), {"username", "password"})

    def test_manual_form_uses_only_main_credentials(self) -> None:
        flow = config_flow.LoxoneCommunityConfigFlow()

        result = flow._async_show_manual_form(errors={})

        self.assertEqual(result["step_id"], "manual")
        self.assertEqual(
            _schema_keys(result["data_schema"]),
            {"host", "port", "username", "password", "verify_ssl"},
        )

    def test_setup_form_defaults_prefer_child_lights_and_disable_icons(self) -> None:
        flow = config_flow.LoxoneCommunityConfigFlow()

        result = flow._async_show_setup_form()
        defaults = _schema_defaults(result["data_schema"])

        self.assertEqual(result["step_id"], "finish")
        self.assertEqual(defaults["enable_light_mood_select"], True)
        self.assertEqual(defaults["expose_controller_child_lights"], True)
        self.assertEqual(defaults["use_loxone_icons"], False)


class ConnectionConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    """Verify the setup routes and persisted connection mode."""

    async def test_manual_entry_is_available_without_running_discovery(self) -> None:
        flow = config_flow.LoxoneCommunityConfigFlow()
        flow.hass = object()

        with patch.object(config_flow, "async_discover_miniservers", new_callable=AsyncMock) as discover:
            menu = await flow.async_step_user()
            form = await flow.async_step_manual()

        self.assertEqual(menu, {"step_id": "user", "menu_options": ["auto", "manual"]})
        self.assertEqual(form["step_id"], "manual")
        discover.assert_not_awaited()

    async def test_manual_gen1_setup_persists_validated_transport_and_port(self) -> None:
        flow = config_flow.LoxoneCommunityConfigFlow()
        flow.hass = object()
        bridge = _connected_bridge(use_tls=False)
        host = "miniserver.local"

        with patch.object(config_flow, "LoxoneBridge", return_value=bridge) as bridge_factory:
            form = await flow.async_step_manual(
                {"host": host, "port": 7778, "username": "loxone", "password": "password"}
            )
            entry = await flow.async_step_finish({})

        self.assertTrue(bridge_factory.call_args.args[1]["use_tls"])
        bridge.async_initialize.assert_awaited_once_with(probe_legacy=True)
        self.assertEqual(form["step_id"], "finish")
        self.assertFalse(entry["data"]["use_tls"])
        self.assertEqual(entry["data"]["host"], host)
        self.assertEqual(entry["data"]["port"], 7778)

    async def test_automatic_setup_connects_to_discovered_gen1(self) -> None:
        flow = config_flow.LoxoneCommunityConfigFlow()
        flow.hass = object()
        bridge = _connected_bridge(use_tls=False)
        result = config_flow.DiscoveryResult(
            host="miniserver.local", port=80, use_tls=False, label="Workshop"
        )
        discovery = AsyncMock(return_value=types.SimpleNamespace(devices=[result], legacy_found=True))

        with (
            patch.object(config_flow, "async_discover_miniservers", discovery),
            patch.object(config_flow, "LoxoneBridge", return_value=bridge) as bridge_factory,
        ):
            await flow.async_step_auto({"username": "loxone", "password": "password"})
            entry = await flow.async_step_finish({})

        self.assertFalse(bridge_factory.call_args.args[1]["use_tls"])
        self.assertFalse(entry["data"]["use_tls"])
        self.assertEqual(entry["data"]["port"], 80)

    async def test_manual_legacy_error_keeps_configured_port(self) -> None:
        flow = config_flow.LoxoneCommunityConfigFlow()
        flow.hass = object()
        bridge = types.SimpleNamespace(
            async_initialize=AsyncMock(side_effect=config_flow.LoxoneUnsupportedError),
            async_shutdown=AsyncMock(),
        )
        host = "miniserver.local"

        with patch.object(config_flow, "LoxoneBridge", return_value=bridge) as bridge_factory:
            result = await flow.async_step_manual(
                {"host": host, "port": 7778, "username": "loxone", "password": "password"}
            )

        self.assertEqual(bridge_factory.call_args.args[1]["port"], 7778)
        bridge.async_initialize.assert_awaited_once_with(probe_legacy=True)
        bridge.async_shutdown.assert_awaited_once_with()
        self.assertEqual(result["step_id"], "manual")
        self.assertEqual(result["errors"], {"base": "legacy_not_supported"})
        defaults = _schema_defaults(result["data_schema"])
        self.assertEqual(defaults["host"], host)
        self.assertEqual(defaults["port"], 7778)

    async def test_options_persist_transport_and_redetect_on_endpoint_change(self) -> None:
        for old_tls, port, expected_initial_tls, new_tls in (
            (False, 7778, False, False),
            (False, 443, True, True),
            (True, 7778, True, False),
        ):
            with self.subTest(old_tls=old_tls, port=port):
                config_entry = types.SimpleNamespace(
                    data={
                        "host": "miniserver.local",
                        "port": 7778 if not old_tls else 443,
                        "username": "loxone",
                        "password": "password",
                    },
                    options={"use_tls": old_tls},
                )
                flow = config_flow.LoxoneCommunityOptionsFlow(config_entry)
                flow.hass = object()
                bridge = _connected_bridge(use_tls=new_tls)

                with patch.object(config_flow, "LoxoneBridge", return_value=bridge) as bridge_factory:
                    result = await flow.async_step_init({"port": port})

                self.assertIs(bridge_factory.call_args.args[1]["use_tls"], expected_initial_tls)
                bridge.async_initialize.assert_awaited_once_with(probe_legacy=True)
                self.assertEqual(result["data"], {"port": port, "use_tls": new_tls})

    async def test_options_keep_existing_password_overrides_when_blank(self) -> None:
        config_entry = types.SimpleNamespace(
            data={
                "host": "miniserver.local",
                "port": 443,
                "username": "loxone",
                "password": "initial-password",
                "intercom_password": "initial-intercom-password",
                "token": "initial-token",
            },
            options={"password": "updated-password", "intercom_password": "updated-intercom"},
        )
        flow = config_flow.LoxoneCommunityOptionsFlow(config_entry)
        flow.hass = object()
        with patch.object(config_flow, "LoxoneBridge", return_value=_connected_bridge(use_tls=True)) as factory:
            result = await flow.async_step_init({"password": "", "intercom_password": ""})

        connection = factory.call_args.args[1]
        self.assertEqual(connection["password"], "updated-password")
        self.assertEqual(connection["intercom_password"], "updated-intercom")
        self.assertIsNone(connection["token"])
        self.assertEqual(result["data"]["password"], "updated-password")
        self.assertEqual(result["data"]["intercom_password"], "updated-intercom")

    async def test_options_use_cached_token_only_for_unchanged_connection(self) -> None:
        for key, value, expect_cached_token in (
            ("host", "new-address.local", False),
            ("port", 8443, False),
            ("username", "new-user", False),
            ("password", "new-password", False),
            ("use_loxone_icons", True, True),
        ):
            with self.subTest(key=key):
                config_entry = types.SimpleNamespace(
                    data={
                        "host": "miniserver.local",
                        "port": 443,
                        "username": "loxone",
                        "password": "initial-password",
                        "token": "cached-token",
                        "token_valid_until": "999999999",
                    },
                    options={},
                )
                flow = config_flow.LoxoneCommunityOptionsFlow(config_entry)
                flow.hass = object()
                bridge = _connected_bridge(use_tls=True)
                with patch.object(config_flow, "LoxoneBridge", return_value=bridge) as factory:
                    await flow.async_step_init({key: value})

                connection = factory.call_args.args[1]
                self.assertEqual(connection["token"], "cached-token" if expect_cached_token else None)
                self.assertEqual(
                    connection["token_valid_until"], "999999999" if expect_cached_token else None
                )


if __name__ == "__main__":
    unittest.main()
