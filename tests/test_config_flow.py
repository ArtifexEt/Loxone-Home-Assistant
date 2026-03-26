"""Tests for Loxone config flow forms."""

from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass

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

        async def async_initialize(self) -> None:
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

        self.assertEqual(defaults["enable_light_mood_select"], True)
        self.assertEqual(defaults["expose_controller_child_lights"], True)
        self.assertEqual(defaults["use_loxone_icons"], False)


if __name__ == "__main__":
    unittest.main()
