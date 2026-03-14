"""Shared entity helpers for Loxone."""

from __future__ import annotations

import colorsys
import re
from collections.abc import Iterable
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import BOOLEAN_STATE_NAMES, DOMAIN, MANUFACTURER, PRIMARY_STATE_CANDIDATES, STATE_NAME_UNITS
from .models import LoxoneControl
from .server_model import DEFAULT_SERVER_MODEL

FORMAT_UNIT_RE = re.compile(r"([%°A-Za-z/]+)\s*$")
PRINTF_SPEC_RE = re.compile(
    r"%(?:[-+0 #]*)?(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|L|z|j|t)?[diuoxXfFeEgGaAcspn]"
)
HSV_RE = re.compile(r"hsv\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)", re.IGNORECASE)
TEMP_RE = re.compile(r"(?:temp|lumitech)\(([-0-9.]+),([-0-9.]+)\)", re.IGNORECASE)
NON_ALNUM_STATE_RE = re.compile(r"[^a-z0-9]+")
NORMALIZED_STATE_NAME_UNITS = {
    NON_ALNUM_STATE_RE.sub("", state_name.casefold()): unit
    for state_name, unit in STATE_NAME_UNITS.items()
}


def miniserver_device_identifier(serial: str) -> tuple[str, str]:
    """Return a stable HA device identifier for one Miniserver."""
    return (DOMAIN, str(serial))


def control_device_identifier(serial: str, uuid_action: str) -> tuple[str, str]:
    """Return a stable HA device identifier for one Loxone control."""
    return (DOMAIN, f"{serial}:{uuid_action}")


def control_entity_unique_id(
    serial: str, uuid_action: str, suffix: str | None = None
) -> str:
    """Return a unique entity id stable across multiple Miniservers."""
    base = f"{serial}:{uuid_action}"
    if suffix is None:
        return base
    return f"{base}:{slugify_state_name(suffix)}"


def miniserver_device_info(bridge) -> DeviceInfo:
    """Return DeviceInfo for the Miniserver hub device."""
    model = str(getattr(bridge, "server_model", DEFAULT_SERVER_MODEL)).strip()
    return DeviceInfo(
        identifiers={miniserver_device_identifier(bridge.serial)},
        manufacturer=MANUFACTURER,
        model=model or DEFAULT_SERVER_MODEL,
        name=bridge.miniserver_name,
        sw_version=bridge.software_version,
    )


def control_device_info(bridge, control: LoxoneControl) -> DeviceInfo:
    """Return DeviceInfo for one Loxone control device."""
    kwargs: dict[str, Any] = {
        "identifiers": {control_device_identifier(bridge.serial, control.uuid_action)},
        "manufacturer": MANUFACTURER,
        "model": control.type,
        "name": control.display_name,
        "via_device": miniserver_device_identifier(bridge.serial),
    }
    if control.room_name:
        kwargs["suggested_area"] = control.room_name
    return DeviceInfo(**kwargs)


class LoxoneEntity(Entity):
    """Base entity for one Loxone control."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, bridge, control: LoxoneControl, suffix: str | None = None) -> None:
        self.bridge = bridge
        self.control = control
        self._attr_name = control.display_name if suffix is None else f"{control.display_name} {suffix}"
        self._attr_unique_id = control_entity_unique_id(
            bridge.serial, control.uuid_action, suffix
        )
        icon_url = control_icon_url(bridge, control)
        if icon_url is not None:
            self._attr_entity_picture = icon_url
        self._remove_listener = None

    @property
    def available(self) -> bool:
        return self.bridge.available

    @property
    def device_info(self) -> DeviceInfo:
        if self.uses_miniserver_device():
            return miniserver_device_info(self.bridge)
        return control_device_info(self.bridge, self.control)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "uuid_action": self.control.uuid_action,
            "loxone_type": self.control.type,
        }
        if self.control.icon:
            attrs["loxone_icon"] = self.control.icon
        icon_url = control_icon_url(self.bridge, self.control)
        if icon_url:
            attrs["loxone_icon_url"] = icon_url
        if self.control.room_name:
            attrs["room"] = self.control.room_name
        if self.control.category_name:
            attrs["category"] = self.control.category_name
        if self.control.is_secured:
            attrs["secured_control"] = True
        return attrs

    async def async_added_to_hass(self) -> None:
        uuids = list(self.relevant_state_uuids())
        self._remove_listener = self.bridge.add_listener(self._handle_bridge_update, uuids)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None

    def relevant_state_uuids(self) -> Iterable[str]:
        return self.control.states.values()

    def state_value(self, state_name: str) -> Any:
        return self.bridge.control_state(self.control, state_name)

    def first_state_value(self, *state_names: str) -> Any:
        for state_name in state_names:
            value = self.state_value(state_name)
            if value is not None:
                return value
        return None

    def _handle_bridge_update(self) -> None:
        self.async_write_ha_state()

    def uses_miniserver_device(self) -> bool:
        """Return True when this entity should be attached to the Miniserver device."""
        return False


def slugify_state_name(value: str) -> str:
    return value.lower().replace(" ", "_")


def control_icon_url(bridge: Any, control: LoxoneControl) -> str | None:
    if not control.icon:
        return None
    proxy_resolver = getattr(bridge, "resolve_icon_proxy_url", None)
    if callable(proxy_resolver):
        proxied = proxy_resolver(control.icon)
        if isinstance(proxied, str) and proxied.strip():
            return proxied
    resolver = getattr(bridge, "resolve_http_url", None)
    if not callable(resolver):
        return None
    resolved = resolver(control.icon)
    if isinstance(resolved, str) and resolved.strip():
        return resolved
    return None


def normalize_state_name(value: str) -> str:
    """Normalize one Loxone state key for tolerant matching."""
    return NON_ALNUM_STATE_RE.sub("", value.casefold())


NORMALIZED_BOOLEAN_STATE_NAMES = {
    normalize_state_name(state_name) for state_name in BOOLEAN_STATE_NAMES
}


def first_matching_state_name(control: LoxoneControl, candidates: Iterable[str]) -> str | None:
    """Return the first control state name matching any candidate."""
    normalized_to_state: dict[str, str] = {}
    for state_name in control.states:
        normalized_to_state.setdefault(normalize_state_name(state_name), state_name)

    for candidate in candidates:
        matched = normalized_to_state.get(normalize_state_name(candidate))
        if matched:
            return matched
    return None


def matching_state_names(control: LoxoneControl, candidates: Iterable[str]) -> list[str]:
    """Return control state names matching one of the provided candidates."""
    normalized_candidates = {normalize_state_name(candidate) for candidate in candidates}
    matched: list[str] = []
    for state_name in control.states:
        if normalize_state_name(state_name) in normalized_candidates:
            matched.append(state_name)
    return matched


def control_primary_state(control: LoxoneControl) -> tuple[str | None, str | None]:
    for state_name in PRIMARY_STATE_CANDIDATES:
        state_uuid = control.states.get(state_name)
        if state_uuid:
            return state_name, state_uuid
    for state_name, state_uuid in control.states.items():
        return state_name, state_uuid
    return None, None


def first_numeric_state_name(control: LoxoneControl) -> str | None:
    for candidate in ("position", "value", "actual", "tempTarget", "tempActual"):
        if candidate in control.states:
            return candidate
    return next(iter(control.states), None)


def state_is_boolean(control: LoxoneControl, state_name: str) -> bool:
    return (
        normalize_state_name(state_name) in NORMALIZED_BOOLEAN_STATE_NAMES
        or control.type in {"InfoOnlyDigital", "PresenceDetector", "SmokeAlarm"}
    )


def coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes", "open"}:
            return True
        if lowered in {"0", "false", "off", "no", "closed"}:
            return False
    return None


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        with_value = value.replace(",", ".")
        try:
            return float(with_value)
        except ValueError:
            return None
    return None


def infer_unit(control: LoxoneControl, state_name: str | None = None) -> str | None:
    details = control.details
    keys = ("format", "actualFormat", "text")
    normalized_state_name = normalize_state_name(state_name) if state_name else None
    mapped_unit = (
        NORMALIZED_STATE_NAME_UNITS.get(normalized_state_name)
        if normalized_state_name is not None
        else None
    )

    # Humidity/CO2-like states use fixed canonical units. Prefer those over
    # generic control-level formats (for example plain "°" from climate controls).
    if mapped_unit in {"%", "ppm"}:
        return mapped_unit

    if state_name == "actual":
        keys = ("actualFormat", "format", "text")
    elif state_name == "total":
        keys = ("totalFormat", "format", "actualFormat", "text")
    elif state_name and normalize_state_name(state_name) in {
        normalize_state_name("remainingTime"),
        normalize_state_name("timeRemaining"),
        normalize_state_name("supplyTimeRemaining"),
        normalize_state_name("remainingRuntime"),
        normalize_state_name("remainingDuration"),
        normalize_state_name("batteryRuntime"),
    }:
        keys = (
            "remainingTimeFormat",
            "timeRemainingFormat",
            "supplyTimeRemainingFormat",
            "supplyRemainingTimeFormat",
            "remainingRuntimeFormat",
            "remainingDurationFormat",
            "batteryRuntimeFormat",
            "format",
            "text",
        )

    for key in keys:
        value = details.get(key)
        if isinstance(value, str):
            if "°F" in value:
                return "°F"
            if "°C" in value:
                return "°C"
            cleaned = PRINTF_SPEC_RE.sub("", value).replace("%%", "%").strip()
            if not cleaned:
                continue
            match = FORMAT_UNIT_RE.search(cleaned)
            if match:
                return match.group(1)

    return mapped_unit


def brightness_from_percent(value: float | None) -> int | None:
    if value is None:
        return None
    return max(0, min(255, round((value / 100) * 255)))


def percent_from_brightness(value: int | None) -> int:
    if value is None:
        return 100
    return max(0, min(100, round((value / 255) * 100)))


def parse_color_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}

    hsv_match = HSV_RE.fullmatch(raw.strip())
    if hsv_match:
        hue = float(hsv_match.group(1))
        saturation = float(hsv_match.group(2))
        brightness = float(hsv_match.group(3))
        rgb = colorsys.hsv_to_rgb(hue / 360, saturation / 100, brightness / 100)
        return {
            "hs_color": (hue, saturation),
            "rgb_color": tuple(round(component * 255) for component in rgb),
            "brightness": brightness_from_percent(brightness),
        }

    temp_match = TEMP_RE.fullmatch(raw.strip())
    if temp_match:
        brightness = float(temp_match.group(1))
        kelvin = float(temp_match.group(2))
        return {
            "color_temp_kelvin": round(kelvin),
            "brightness": brightness_from_percent(brightness),
        }

    return {}
