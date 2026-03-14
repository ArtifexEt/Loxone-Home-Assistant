"""Constants for the Loxone integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.text import DOMAIN as TEXT_DOMAIN
from homeassistant.const import Platform

DOMAIN = "loxone_home_assistant"
INTEGRATION_TITLE = "Loxone"

PLATFORMS: list[Platform | str] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    getattr(Platform, "MEDIA_PLAYER", "media_player"),
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

DEFAULT_PORT = 443
DEFAULT_SCAN_TIMEOUT = 8
DEFAULT_KEEPALIVE_SECONDS = 240
DEFAULT_VERIFY_SSL = False
APP_PERMISSION = 4
WEB_PERMISSION = 2
CLIENT_INFO = "Loxone"

CONF_SCAN_TIMEOUT = "scan_timeout"
CONF_SERIAL = "serial"
CONF_CLIENT_UUID = "client_uuid"
CONF_TOKEN = "token"
CONF_TOKEN_VALID_UNTIL = "token_valid_until"
CONF_LOXAPP_VERSION = "loxapp_version"
CONF_SOFTWARE_VERSION = "software_version"
CONF_SERVER_MODEL = "server_model"
CONF_USE_TLS = "use_tls"
CONF_ENABLE_LIGHT_MOOD_SELECT = "enable_light_mood_select"
CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS = "expose_controller_child_lights"

DEFAULT_ENABLE_LIGHT_MOOD_SELECT = False
DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS = False

DATA_BRIDGES = "bridges"

SERVICE_SEND_COMMAND = "send_command"
SERVICE_SEND_RAW_COMMAND = "send_raw_command"

SERVICE_ATTR_ENTRY_ID = "entry_id"
SERVICE_ATTR_UUID_ACTION = "uuid_action"
SERVICE_ATTR_COMMAND = "command"

LIGHT_CONTROL_TYPES = {
    "ColorPicker",
    "ColorPickerV2",
    "Dimmer",
    "LightController",
    "LightControllerV2",
    "LightsceneRGB",
}
SWITCH_CONTROL_TYPES = {"Switch", "TimedSwitch"}
BUTTON_CONTROL_TYPES = {"Pushbutton"}
COVER_CONTROL_TYPES = {"Jalousie"}
CLIMATE_CONTROL_TYPES = {"IRoomController", "IRoomControllerV2"}
SENSOR_CONTROL_TYPES = {"InfoOnlyAnalog", "Meter", "TextState"}
BINARY_SENSOR_CONTROL_TYPES = {"InfoOnlyDigital", "PresenceDetector", "SmokeAlarm"}
POWER_SUPPLY_CONTROL_TYPES = {"PowerSupply", "PowerSupplyV2"}
NUMBER_CONTROL_TYPES = {"Slider"}
TEXT_CONTROL_TYPES = {"TextInput"}
MEDIA_PLAYER_CONTROL_TYPES = {"AudioZone", "AudioZoneV2"}

DOORBELL_STATE_CANDIDATES = (
    "bell",
    "doorbell",
    "ring",
    "ringing",
    "isRinging",
    "call",
)

ACCESS_TYPE_HINTS = (
    "access",
    "codetouch",
    "keypad",
    "nfc",
    "intercom",
)

ACCESS_GRANTED_STATE_CANDIDATES = (
    "access",
    "accessGranted",
    "granted",
    "success",
    "codeOk",
    "correctCode",
    "validCode",
    "grantedPulse",
)

ACCESS_DENIED_STATE_CANDIDATES = (
    "accessDenied",
    "denied",
    "wrongCode",
    "invalidCode",
    "accessError",
    "codeError",
    "failed",
    "failure",
)

CLIMATE_CURRENT_TEMPERATURE_STATE_CANDIDATES = (
    "tempActual",
    "temperature",
    "actualTemperature",
    "currentTemperature",
)
CLIMATE_TARGET_TEMPERATURE_STATE_CANDIDATES = (
    "tempTarget",
    "comfortTemperature",
    "setpoint",
    "targetTemperature",
    "desiredTemperature",
)
CLIMATE_HUMIDITY_STATE_CANDIDATES = (
    "humidity",
    "humidityActual",
    "actualHumidity",
    "humActual",
)
CLIMATE_CO2_STATE_CANDIDATES = (
    "co2",
    "co2Actual",
    "airCo2",
    "airCO2",
    "carbonDioxide",
)
CLIMATE_AIR_QUALITY_STATE_CANDIDATES = (
    "airQuality",
    "airQualityIndex",
    "iaq",
    "voc",
)

HANDLED_CONTROL_TYPES = (
    LIGHT_CONTROL_TYPES
    | SWITCH_CONTROL_TYPES
    | BUTTON_CONTROL_TYPES
    | COVER_CONTROL_TYPES
    | CLIMATE_CONTROL_TYPES
    | SENSOR_CONTROL_TYPES
    | BINARY_SENSOR_CONTROL_TYPES
    | POWER_SUPPLY_CONTROL_TYPES
    | NUMBER_CONTROL_TYPES
    | TEXT_CONTROL_TYPES
    | MEDIA_PLAYER_CONTROL_TYPES
)

BOOLEAN_STATE_NAMES = {
    "active",
    "alarm",
    "bell",
    "charging",
    "connected",
    "enabled",
    "hasMail",
    "isAutomatic",
    "isCharging",
    "isLocked",
    "isSecured",
    "motion",
    "presence",
    "sunAutomation",
}

PRIMARY_STATE_CANDIDATES = (
    "active",
    "value",
    "position",
    "tempTarget",
    "tempActual",
    "text",
    "actual",
    "total",
)

STATE_NAME_UNITS = {
    "batteryLevel": "%",
    "chargeLevel": "%",
    "humidity": "%",
    "humidityActual": "%",
    "actualHumidity": "%",
    "humActual": "%",
    "co2": "ppm",
    "co2Actual": "ppm",
    "airCo2": "ppm",
    "airCO2": "ppm",
    "carbonDioxide": "ppm",
    "position": "%",
    "stateOfCharge": "%",
    "tempActual": "°C",
    "tempTarget": "°C",
}

PLATFORM_DOMAINS = {
    BINARY_SENSOR_DOMAIN,
    BUTTON_DOMAIN,
    CLIMATE_DOMAIN,
    COVER_DOMAIN,
    LIGHT_DOMAIN,
    NUMBER_DOMAIN,
    SELECT_DOMAIN,
    SENSOR_DOMAIN,
    SWITCH_DOMAIN,
    TEXT_DOMAIN,
    "media_player",
}

MANUFACTURER = "Loxone"
