"""Tests for Loxone LightController mood select behavior."""

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

    light = sys.modules["homeassistant.components.light"]
    light.ATTR_BRIGHTNESS = "brightness"
    light.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    light.ATTR_HS_COLOR = "hs_color"

    class ColorMode:
        HS = "hs"
        COLOR_TEMP = "color_temp"
        BRIGHTNESS = "brightness"
        ONOFF = "onoff"

    class LightEntity:
        pass

    light.ColorMode = ColorMode
    light.LightEntity = LightEntity

    select = sys.modules["homeassistant.components.select"]

    class SelectEntity:
        pass

    select.SelectEntity = SelectEntity

    sensor = sys.modules["homeassistant.components.sensor"]

    class SensorEntity:
        pass

    class SensorStateClass:
        MEASUREMENT = "measurement"
        TOTAL_INCREASING = "total_increasing"

    class SensorDeviceClass:
        TIMESTAMP = "timestamp"
        ENERGY = "energy"
        POWER = "power"
        HUMIDITY = "humidity"
        CO2 = "co2"
        VOLATILE_ORGANIC_COMPOUNDS = "voc"
        AQI = "aqi"

    sensor.SensorEntity = SensorEntity
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorDeviceClass = SensorDeviceClass

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

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        pass

    config_entries.ConfigEntry = ConfigEntry
    sys.modules["homeassistant.config_entries"] = config_entries

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers

    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")

    def async_get(_hass):
        return object()

    def async_entries_for_config_entry(_registry, _entry_id):
        return []

    entity_registry.async_get = async_get
    entity_registry.async_entries_for_config_entry = async_entries_for_config_entry
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry

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

    entity.Entity = Entity
    sys.modules["homeassistant.helpers.entity"] = entity


_install_homeassistant_stubs()
models = load_integration_module("custom_components.loxone_home_assistant.models")
select_module = load_integration_module("custom_components.loxone_home_assistant.select")
LoxoneControl = models.LoxoneControl
LoxoneMoodSelectEntity = select_module.LoxoneMoodSelectEntity
LoxoneRadioOutputSelectEntity = select_module.LoxoneRadioOutputSelectEntity
LoxoneIntercomHistorySelectEntity = select_module.LoxoneIntercomHistorySelectEntity


class _FakeHistoryResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type=None):
        del content_type
        return self._payload


class _FakeHistorySession:
    def __init__(self, payload_by_url: dict[str, object]) -> None:
        self._payload_by_url = payload_by_url

    def get(self, url: str, auth=None):
        del auth
        return _FakeHistoryResponse(self._payload_by_url[url])


class _FakeBridge:
    serial = "1234567890"
    available = True
    username = "user"
    password = "pass"
    host = "mini.local"
    port = 443
    use_tls = True
    _ws_path_prefix = ""

    def __init__(self, controls, values, session=None):
        self.controls = controls
        self._controls_by_action = {control.uuid_action: control for control in controls}
        self._values = values
        self.commands: list[tuple[str, str]] = []
        self._session = session

    def add_listener(self, _callback_fn, _watched_uuids):
        return lambda: None

    def state_value(self, state_uuid):
        return self._values.get(state_uuid)

    def control_state(self, control, state_name):
        return self._values.get(control.state_uuid(state_name))

    def control_for_uuid_action(self, uuid_action):
        return self._controls_by_action.get(uuid_action)

    async def async_send_action(self, uuid_action, command):
        self.commands.append((uuid_action, command))

    def resolve_http_url(self, value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        path = raw if raw.startswith("/") else f"/{raw}"
        return f"https://{self.host}{path}"


class MoodSelectTests(unittest.IsolatedAsyncioTestCase):
    """Verify mood select behavior."""

    def _controller(self) -> LoxoneControl:
        return LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
            details={
                "moodList": [
                    {"id": 12, "name": "Reading"},
                    {"id": 55, "name": "Evening"},
                ]
            },
        )

    def test_current_option_resolves_first_non_off_mood(self) -> None:
        controller = self._controller()
        bridge = _FakeBridge([controller], {"state-moods": "[0, 778, 55]"})
        entity = LoxoneMoodSelectEntity(bridge, controller)

        self.assertEqual(entity.current_option, "Evening")
        self.assertEqual(entity._attr_options[0], "Off")

    async def test_select_option_sends_change_to_command(self) -> None:
        controller = self._controller()
        bridge = _FakeBridge([controller], {"state-moods": "[12]"})
        entity = LoxoneMoodSelectEntity(bridge, controller)

        await entity.async_select_option("Reading")

        self.assertEqual(bridge.commands, [("ctrl-action", "changeTo/12")])

    def test_mood_select_accepts_mapping_mood_list(self) -> None:
        controller = LoxoneControl(
            uuid="ctrl-uuid",
            uuid_action="ctrl-action",
            name="Salon",
            type="LightControllerV2",
            states={"activeMoods": "state-moods"},
            details={
                "moodList": {
                    "12": "Reading",
                    "55": "Evening",
                }
            },
        )
        bridge = _FakeBridge([controller], {"state-moods": "55"})
        entity = LoxoneMoodSelectEntity(bridge, controller)

        self.assertEqual(entity._attr_options, ["Off", "Reading", "Evening"])
        self.assertEqual(entity.current_option, "Evening")

    async def test_radio_select_maps_outputs_and_reset_command(self) -> None:
        radio = LoxoneControl(
            uuid="radio-uuid",
            uuid_action="radio-action",
            name="Radio",
            type="Radio",
            states={"activeOutput": "state-output"},
            details={"outputs": {"0": "allOff", "1": "living room", "2": "kitchen"}},
        )
        bridge = _FakeBridge([radio], {"state-output": 2})
        entity = LoxoneRadioOutputSelectEntity(bridge, radio)

        self.assertEqual(entity.current_option, "kitchen")

        await entity.async_select_option("living room")
        await entity.async_select_option("allOff")

        self.assertEqual(
            bridge.commands,
            [("radio-action", "1"), ("radio-action", "reset")],
        )

    async def test_intercom_history_select_exposes_old_photos_as_options(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="IntercomV2",
            states={
                "lastBellEvents": "state-events",
            },
            details={},
        )
        bridge = _FakeBridge(
            [intercom],
            {
                "state-events": "20260314100000|20260314123045",
            },
        )
        entity = LoxoneIntercomHistorySelectEntity(bridge, intercom)

        self.assertTrue(entity._attr_entity_registry_enabled_default)  # noqa: SLF001

        await entity.async_update()

        self.assertEqual(entity._attr_options[0], "Live")
        self.assertEqual(len(entity._attr_options), 3)

        photo_option = entity._attr_options[1]
        await entity.async_select_option(photo_option)
        self.assertEqual(entity.current_option, photo_option)
        self.assertEqual(
            bridge._intercom_selected_history_timestamps["intercom-action"],  # noqa: SLF001
            "20260314123045",
        )

        await entity.async_select_option("Live")
        self.assertEqual(entity.current_option, "Live")
        self.assertNotIn("intercom-action", bridge._intercom_selected_history_timestamps)  # noqa: SLF001

    async def test_intercom_history_select_loads_full_history_from_event_history_url(self) -> None:
        intercom = LoxoneControl(
            uuid="intercom-uuid",
            uuid_action="intercom-action",
            name="Dzwonek",
            type="IntercomV2",
            states={},
            details={"videoInfo": {"eventHistoryUrl": "/history/intercom.json"}},
        )
        history_url = "https://mini.local/history/intercom.json"
        session = _FakeHistorySession(
            {
                history_url: {
                    "events": [
                        {
                            "timestamp": "20260313120000",
                            "imageUrl": "/camimage/intercom-action/20260313120000",
                        },
                        {
                            "timestamp": "20260314153000",
                            "imageUrl": "/camimage/intercom-action/20260314153000",
                        },
                    ]
                }
            }
        )
        bridge = _FakeBridge([intercom], {}, session=session)
        entity = LoxoneIntercomHistorySelectEntity(bridge, intercom)

        await entity.async_update()

        self.assertEqual(entity._attr_options[0], "Live")
        self.assertEqual(len(entity._attr_options), 3)

        latest_photo_option = entity._attr_options[1]
        await entity.async_select_option(latest_photo_option)

        self.assertEqual(
            bridge._intercom_selected_history_timestamps["intercom-action"],  # noqa: SLF001
            "20260314153000",
        )


if __name__ == "__main__":
    unittest.main()
