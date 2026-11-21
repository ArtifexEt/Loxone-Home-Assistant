"""Light platform for Loxone."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from enum import IntFlag
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
try:
    from homeassistant.components.light import LightEntityFeature
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    class LightEntityFeature(IntFlag):  # type: ignore[no-redef]
        EFFECT = 4
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    DOMAIN,
    LIGHT_CONTROL_TYPES,
)
from .entity import (
    LoxoneEntity,
    brightness_from_percent,
    coerce_bool,
    coerce_float,
    control_entity_unique_id,
    parse_color_state,
    percent_from_brightness,
    switch_control_is_on,
    unique_option_labels,
)
from .intercom import intercom_light_state_name, is_intercom_control
from .models import LoxoneControl
from .options import option_enabled
from .protocol import deserialize_value
from .runtime import entry_bridge

CONTROLLER_TYPES = {"LightController", "LightControllerV2"}
ON_OFF_LIGHT_TYPES = {"Switch", "TimedSwitch"}
CONTROLLER_CHILD_TYPES = LIGHT_CONTROL_TYPES | ON_OFF_LIGHT_TYPES
COLOR_LIGHT_TYPES = {"ColorPicker", "ColorPickerV2", "LightsceneRGB"}
OFF_MOOD_IDS = {0, 778}
NON_DIGIT_RE = re.compile(r"[^0-9,.-]+")
SCENE_LIST_RE = re.compile(r'(?:^|,)\s*(\d+)\s*=\s*("(?:\\.|[^"\\])*")')
ATTR_EFFECT = "effect"
LIGHT_EFFECT_FEATURE = getattr(LightEntityFeature, "EFFECT", 4)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    entry_data = getattr(entry, "data", {}) or {}
    expose_controller_children = option_enabled(
        entry.options.get(
            CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS,
            entry_data.get(CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS),
        ),
        DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    )
    controls = [
        control
        for control in bridge.controls
        if should_expose_as_light(bridge, control, expose_controller_children)
    ]
    intercom_light_controls: list[tuple[LoxoneControl, str]] = []
    exported_control_ids = {control.uuid_action for control in controls}
    for control in bridge.controls:
        if control.uuid_action in exported_control_ids:
            continue
        if not is_intercom_control(control):
            continue
        state_name = intercom_light_state_name(control)
        if state_name is None:
            continue
        intercom_light_controls.append((control, state_name))

    entities: list[LightEntity] = [LoxoneLightEntity(bridge, control) for control in controls]
    entities.extend(
        LoxoneIntercomLightEntity(bridge, control, state_name)
        for control, state_name in intercom_light_controls
    )
    valid_light_unique_ids = {
        control_entity_unique_id(bridge.serial, control.uuid_action)
        for control in controls
    }
    valid_light_unique_ids.update(
        control_entity_unique_id(
            bridge.serial,
            control.uuid_action,
            LoxoneIntercomLightEntity.UNIQUE_SUFFIX,
        )
        for control, _ in intercom_light_controls
    )
    _cleanup_stale_light_entities(
        hass,
        entry,
        valid_light_unique_ids,
    )
    _cleanup_stale_light_devices(
        hass,
        entry,
        bridge,
        {
            *{control.uuid_action for control in controls},
            *{control.uuid_action for control, _ in intercom_light_controls},
        },
    )
    async_add_entities(entities)


class LoxoneLightEntity(LoxoneEntity, LightEntity):
    """Representation of a Loxone light-related block."""

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control)
        self._child_light_controls = tuple(
            candidate
            for candidate in bridge.controls
            if _is_child_of_controller(candidate, control)
            and candidate.type in CONTROLLER_CHILD_TYPES
        ) if control.type in CONTROLLER_TYPES else ()
        self._master_value = self._controller_master("masterValue")
        self._master_color = self._controller_master("masterColor")

    def _controller_master(self, key: str) -> LoxoneControl | None:
        reference = self.control.details.get(key)
        if self.control.type not in CONTROLLER_TYPES or not isinstance(reference, str):
            return None
        control = self.bridge.control_for_uuid_action(reference)
        if control is not None:
            return control
        return next(
            (
                child for child in self._child_light_controls
                if child.uuid_action.casefold() == reference.casefold()
                or child.uuid.casefold() == reference.casefold()
            ),
            None,
        )

    def relevant_state_uuids(self):
        uuids = set(self.control.states.values())
        for child in (*self._child_light_controls, self._master_value, self._master_color):
            if child is not None:
                uuids.update(child.states.values())
        return uuids

    @property
    def is_on(self) -> bool | None:
        if self.control.type in ON_OFF_LIGHT_TYPES:
            return switch_control_is_on(self.bridge, self.control)
        if self.control.type in CONTROLLER_TYPES:
            active_moods = _active_moods_to_bool(self._active_effect_state())
            if active_moods is True:
                return True

            brightness = self.brightness
            if brightness:
                return True

            child_states = [self._is_control_on(control) for control in self._child_light_controls]
            if any(value is True for value in child_states):
                return True
            if brightness == 0 or active_moods is False or False in child_states:
                return False
            return None

        if self.control.type in COLOR_LIGHT_TYPES:
            brightness = self.brightness
            if brightness is not None:
                return brightness > 0
            bool_state = coerce_bool(self.first_state_value("active", "value"))
            if bool_state is not None:
                return bool_state
            return _active_moods_to_bool(self.state_value("activeScene"))

        brightness = self.brightness
        if brightness is not None:
            return brightness > 0

        bool_state = coerce_bool(self.first_state_value("active", "value"))
        if bool_state is not None:
            return bool_state

        value = coerce_float(self.first_state_value("active", "value"))
        return bool(value) if value is not None else None

    @property
    def brightness(self) -> int | None:
        if self.control.type in CONTROLLER_TYPES:
            for master in (self._master_value, self._master_color):
                if master is None:
                    continue
                master_brightness = self._brightness_for_control(master)
                if master_brightness is not None:
                    return master_brightness

            child_brightness_values = [
                value
                for value in (
                    self._brightness_for_control(control)
                    for control in self._child_light_controls
                )
                if value is not None
            ]
            if child_brightness_values:
                return max(child_brightness_values)

            own_value = coerce_float(self.first_state_value("position", "value", "active"))
            return brightness_from_percent(own_value)

        if self.control.type in COLOR_LIGHT_TYPES:
            brightness = self._color_state().get("brightness")
            if brightness is not None:
                return brightness
            fallback_value = coerce_float(self.first_state_value("position", "value"))
            return brightness_from_percent(fallback_value)

        return brightness_from_percent(coerce_float(self.first_state_value("position", "value")))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        return self._color_state().get("hs_color")

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._color_state().get("color_temp_kelvin")

    @property
    def min_color_temp_kelvin(self) -> int:
        return self._color_temperature_range()[0]

    @property
    def max_color_temp_kelvin(self) -> int:
        return self._color_temperature_range()[1]

    @property
    def color_mode(self) -> ColorMode:
        modes = self.supported_color_modes
        if len(modes) == 1:
            return next(iter(modes))
        return ColorMode.COLOR_TEMP if self.color_temp_kelvin is not None else ColorMode.HS

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        if color := self._color_control():
            return _picker_color_modes(color)
        if self.control.type == "Dimmer" or self._master_value is not None:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def supported_features(self) -> LightEntityFeature:
        features = LightEntityFeature(0)
        if self.effect_list:
            features |= LIGHT_EFFECT_FEATURE
        return features

    @property
    def effect_list(self) -> list[str] | None:
        effects, _, _ = self._effect_maps()
        return effects or None

    @property
    def effect(self) -> str | None:
        mood_id = active_light_effect_id(self._active_effect_state())
        if mood_id is None:
            return None
        return self._effect_maps()[2].get(mood_id)

    def _effect_maps(self) -> tuple[list[str], dict[str, int], dict[int, str]]:
        return light_effect_maps(self.bridge, self.control)

    def _active_effect_state(self) -> Any:
        return self.first_state_value(*light_active_effect_states(self.control))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        if self.control.type == "LightControllerV2":
            moods = _light_effect_payload(self.bridge, self.control)
            if moods is not None:
                attrs["moods"] = moods
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        if effect := kwargs.get(ATTR_EFFECT):
            mood_id = self._effect_maps()[1].get(effect)
            if mood_id is not None:
                await self.bridge.async_send_action(
                    self.control.uuid_action, light_effect_command(self.control, mood_id)
                )
                return

        color = self._color_control()
        if color is not None and (
            ATTR_HS_COLOR in kwargs
            or ATTR_COLOR_TEMP_KELVIN in kwargs
            or (ATTR_BRIGHTNESS in kwargs and self._master_value is None)
        ):
            await self.bridge.async_send_action(color.uuid_action, self._color_command(color, kwargs))
            return

        if ATTR_BRIGHTNESS in kwargs:
            dimmer = self._master_value or (self.control if self.control.type == "Dimmer" else None)
            if dimmer is not None:
                percent = percent_from_brightness(kwargs[ATTR_BRIGHTNESS])
                await self.bridge.async_send_action(dimmer.uuid_action, str(percent))
                return

        command = "changeTo/99" if self.control.type == "LightControllerV2" else "on"
        await self.bridge.async_send_action(self.control.uuid_action, command)

    async def async_turn_off(self, **kwargs) -> None:
        command = "changeTo/0" if self.control.type == "LightControllerV2" else "off"
        await self.bridge.async_send_action(self.control.uuid_action, command)

    def _color_control(self) -> LoxoneControl | None:
        if self.control.type in COLOR_LIGHT_TYPES:
            return self.control
        return self._master_color

    def _color_state(self) -> dict[str, Any]:
        control = self._color_control()
        if control is None:
            return {}
        return parse_color_state(
            self.bridge.control_state(control, "color")
            or self.bridge.control_state(control, "sequenceColor")
        )

    def _color_temperature_range(self) -> tuple[int, int]:
        control = self._color_control()
        if control is None:
            return 2700, 6500
        return _picker_temperature_range(control)

    def _color_command(self, control: LoxoneControl, settings: Mapping[str, Any]) -> str:
        requested = settings.get(ATTR_BRIGHTNESS, self.brightness or None)
        brightness = percent_from_brightness(requested)
        if (
            control.type == "ColorPickerV2"
            and ATTR_HS_COLOR not in settings
            and ATTR_COLOR_TEMP_KELVIN not in settings
        ):
            index = coerce_float(self.bridge.control_state(control, "sequenceColorIdx"))
            if index is not None and index >= 0:
                return f"setBrightness/{brightness}"

        modes = _picker_color_modes(control)
        if ColorMode.HS in modes and ATTR_HS_COLOR in settings:
            hue, saturation = settings[ATTR_HS_COLOR]
            return f"hsv({round(hue)},{round(saturation)},{brightness})"
        if ColorMode.COLOR_TEMP in modes and (
            ATTR_COLOR_TEMP_KELVIN in settings or self.color_mode == ColorMode.COLOR_TEMP
        ):
            minimum, maximum = _picker_temperature_range(control)
            requested_kelvin = settings.get(ATTR_COLOR_TEMP_KELVIN, self.color_temp_kelvin or minimum)
            kelvin = max(minimum, min(maximum, round(requested_kelvin)))
            command = "temp" if control.type == "ColorPickerV2" else "lumitech"
            return f"{command}({brightness},{kelvin})"

        hue, saturation = self.hs_color or (0, 0)
        return f"hsv({round(hue)},{round(saturation)},{brightness})"

    def _brightness_for_control(self, control: LoxoneControl) -> int | None:
        if control.type in ON_OFF_LIGHT_TYPES:
            return None

        if control.type in COLOR_LIGHT_TYPES:
            color_state = parse_color_state(
                self.bridge.control_state(control, "color")
                or self.bridge.control_state(control, "sequenceColor")
            )
            brightness = color_state.get("brightness")
            if brightness is not None:
                return brightness

        for state_name in ("position", "value", "active"):
            value = coerce_float(self.bridge.control_state(control, state_name))
            if value is not None:
                return brightness_from_percent(value)
        return None

    def _is_control_on(self, control: LoxoneControl) -> bool | None:
        if control.type in ON_OFF_LIGHT_TYPES:
            return switch_control_is_on(self.bridge, control)

        brightness = self._brightness_for_control(control)
        if brightness is not None:
            return brightness > 0

        boolean_state = coerce_bool(
            self.bridge.control_state(control, "active")
            or self.bridge.control_state(control, "value")
        )
        return boolean_state

class LoxoneIntercomLightEntity(LoxoneEntity, LightEntity):
    """Optional light entity for Intercom controls exposing a light state."""

    UNIQUE_SUFFIX = "intercom_light"

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
    ) -> None:
        super().__init__(bridge, control, "Light")
        self._set_intercom_entity_name("light", "intercom_light")
        self._state_name = state_name
        self._attr_unique_id = control_entity_unique_id(
            bridge.serial,
            control.uuid_action,
            self.UNIQUE_SUFFIX,
        )
        self._on_command, self._off_command = _intercom_light_commands(state_name)

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        bool_state = coerce_bool(self.state_value(self._state_name))
        if bool_state is not None:
            return bool_state
        value = coerce_float(self.state_value(self._state_name))
        return bool(value) if value is not None else None

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return {ColorMode.ONOFF}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs["intercom_light_state"] = self._state_name
        attrs["intercom_light_command_on"] = self._on_command
        attrs["intercom_light_command_off"] = self._off_command
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        del kwargs
        await self.bridge.async_send_action(self.control.uuid_action, self._on_command)

    async def async_turn_off(self, **kwargs) -> None:
        del kwargs
        await self.bridge.async_send_action(self.control.uuid_action, self._off_command)


def _intercom_light_commands(state_name: str) -> tuple[str, str]:
    normalized = state_name.casefold()
    if "led" in normalized:
        return "ledOn", "ledOff"
    if "flash" in normalized or "flood" in normalized:
        return "flashOn", "flashOff"
    if "illum" in normalized or "light" in normalized:
        return "lightOn", "lightOff"
    return "on", "off"

def _is_child_light_control(bridge, control: LoxoneControl) -> bool:
    return any(_is_child_of_controller(control, parent) for parent in bridge.controls)


def should_expose_as_light(bridge, control: LoxoneControl, expose_controller_children: bool) -> bool:
    """Return True when a control should be exposed on the HA light platform."""
    if control.type in CONTROLLER_TYPES:
        # Controller-level lights are primary user-facing entities.
        # Keep them visible even when child-light export is disabled.
        return True

    if control.type in LIGHT_CONTROL_TYPES:
        if expose_controller_children:
            return True
        return not _is_child_light_control(bridge, control)
    if control.type in ON_OFF_LIGHT_TYPES and expose_controller_children:
        return _is_child_light_control(bridge, control)
    return False


def _is_child_of_controller(control: LoxoneControl, parent: LoxoneControl) -> bool:
    if parent.type not in CONTROLLER_TYPES or control.uuid_action == parent.uuid_action:
        return False
    return (
        control.parent_uuid_action == parent.uuid_action
        or control.uuid_action.startswith(f"{parent.uuid_action}/")
        or control.uuid_action.casefold() in {
            reference.strip().casefold()
            for key in ("masterValue", "masterColor")
            if isinstance(reference := parent.details.get(key), str)
        }
    )


def _picker_color_modes(control: LoxoneControl) -> set[ColorMode]:
    picker = str(control.details.get("pickerType", "")).casefold()
    if control.type == "LightsceneRGB" or (control.type == "ColorPicker" and picker == "rgb"):
        return {ColorMode.HS}
    if picker == "tunablewhite":
        return {ColorMode.COLOR_TEMP}
    return {ColorMode.HS, ColorMode.COLOR_TEMP}


def _picker_temperature_range(control: LoxoneControl) -> tuple[int, int]:
    minimum = coerce_float(control.details.get("TWMin"))
    maximum = coerce_float(control.details.get("TWMax"))
    minimum = round(minimum) if minimum and math.isfinite(minimum) and minimum > 0 else 2700
    maximum = round(maximum) if maximum and math.isfinite(maximum) and maximum > 0 else 6500
    return (minimum, maximum) if minimum <= maximum else (2700, 6500)


def light_active_effect_states(control: LoxoneControl) -> tuple[str, ...]:
    if control.type == "LightControllerV2":
        return "activeMoods", "active"
    if control.type in {"LightController", "LightsceneRGB"}:
        return "activeScene", "active"
    return ()


def light_effect_state_name(control: LoxoneControl) -> str | None:
    if control.type == "LightControllerV2":
        return "moodList"
    if control.type == "LightController":
        return "sceneList"
    return None


def _light_effect_payload(bridge, control: LoxoneControl) -> Any:
    state_name = light_effect_state_name(control)
    if state_name is None:
        return None
    state_uuid = control.state_uuid(state_name)
    raw = bridge.state_value(state_uuid) if state_uuid else None
    return deserialize_value(raw if raw is not None else control.details.get(state_name))


def light_effects(bridge, control: LoxoneControl) -> list[tuple[int, str]]:
    payload = _light_effect_payload(bridge, control)
    if control.type == "LightController":
        text = payload.get("text") if isinstance(payload, Mapping) else payload
        if isinstance(text, str):
            scenes = []
            for scene_id, name in SCENE_LIST_RE.findall(text):
                try:
                    scenes.append((int(scene_id), json.loads(name)))
                except ValueError:
                    continue
            return scenes
    return _extract_moods(payload)


def light_effect_command(control: LoxoneControl, mood_id: int) -> str:
    if control.type == "LightControllerV2":
        return f"changeTo/{mood_id}"
    return str(mood_id)


def light_effect_maps(
    bridge, control: LoxoneControl, *, include_off: bool = False
) -> tuple[list[str], dict[str, int], dict[int, str]]:
    moods = light_effects(bridge, control)
    effect_list, by_label, by_id = _build_effect_maps(moods)
    if include_off and 0 not in by_id and "Off" not in by_label:
        effect_list.insert(0, "Off")
        by_label["Off"] = 0
        by_id[0] = "Off"
    return effect_list, by_label, by_id


def _active_moods_to_bool(value: Any) -> bool | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)) and math.isfinite(value):
        if int(value) in OFF_MOOD_IDS:
            return False
        return value > 0

    if isinstance(value, list):
        if not value:
            return False
        mood_ids: list[int] = []
        child_states: list[bool | None] = []
        for item in value:
            mood_id = _coerce_mood_id(item)
            if mood_id is not None:
                mood_ids.append(mood_id)
                continue
            child_states.append(_active_moods_to_bool(item))

        if mood_ids:
            if all(mood_id in OFF_MOOD_IDS for mood_id in mood_ids):
                return False
            return True

        if any(item is True for item in child_states):
            return True
        known_child_states = [item for item in child_states if item is not None]
        if known_child_states and all(item is False for item in known_child_states):
            return False
        return None

    if isinstance(value, str):
        raw = value.strip()
        if raw in {"", "0", "[]"}:
            return False
        if raw.casefold() in {"on", "true"}:
            return True
        if raw.casefold() in {"off", "false"}:
            return False

        parsed = deserialize_value(raw)
        if not isinstance(parsed, str):
            return _active_moods_to_bool(parsed)

        numbers_only = NON_DIGIT_RE.sub("", raw).strip(",")
        if numbers_only:
            parts = [item for item in numbers_only.split(",") if item]
            if parts:
                try:
                    return any(float(item) > 0 for item in parts)
                except ValueError:
                    return None

    return None


def _coerce_mood_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw and raw.lstrip("-").isdigit():
            return int(raw)
    if isinstance(value, dict):
        candidate = value.get("id", value.get("moodId"))
        return _coerce_mood_id(candidate)
    return None


def _extract_moods(raw_moods: Any) -> list[tuple[int, str]]:
    normalized_items: list[Any]
    if isinstance(raw_moods, list):
        normalized_items = raw_moods
    elif isinstance(raw_moods, Mapping):
        normalized_items = []
        for raw_key, raw_value in raw_moods.items():
            if isinstance(raw_value, Mapping):
                normalized_items.append({"id": raw_key, **raw_value})
                continue
            normalized_items.append({"id": raw_key, "name": raw_value})
    else:
        return []

    moods: list[tuple[int, str]] = []
    for item in normalized_items:
        if not isinstance(item, Mapping):
            continue
        mood_id = _coerce_mood_id(
            item.get("id", item.get("moodId", item.get("value")))
        )
        if mood_id is None:
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        if not name:
            name = f"Mood {mood_id}"
        moods.append((mood_id, name))
    return moods


def _build_effect_maps(
    moods: list[tuple[int, str]],
) -> tuple[list[str], dict[str, int], dict[int, str]]:
    by_id = unique_option_labels(moods)
    return list(by_id.values()), {label: mood_id for mood_id, label in by_id.items()}, by_id


def active_light_effect_id(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return 0 if value is False else None

    if isinstance(value, (int, float)):
        return _coerce_mood_id(value)

    if isinstance(value, Mapping):
        return _coerce_mood_id(value.get("id", value.get("moodId")))

    if isinstance(value, list):
        mood_ids: list[int] = []
        for item in value:
            mood_id = _coerce_mood_id(item)
            if mood_id is not None:
                mood_ids.append(mood_id)
                continue
            nested = active_light_effect_id(item)
            if nested is not None:
                mood_ids.append(nested)
        if not mood_ids:
            return None
        for mood_id in mood_ids:
            if mood_id not in OFF_MOOD_IDS:
                return mood_id
        return mood_ids[0]

    if isinstance(value, str):
        raw = value.strip()
        if raw in {"", "[]"}:
            return None
        if raw == "0":
            return 0

        parsed = deserialize_value(raw)
        if not isinstance(parsed, str):
            return active_light_effect_id(parsed)

        numbers_only = NON_DIGIT_RE.sub("", raw).strip(",")
        if not numbers_only:
            return None
        parts = [item for item in numbers_only.split(",") if item]
        if not parts:
            return None
        for item in parts:
            try:
                mood_id = int(float(item))
            except ValueError:
                continue
            if mood_id not in OFF_MOOD_IDS:
                return mood_id
        try:
            return int(float(parts[0]))
        except ValueError:
            return None

    return None


def _cleanup_stale_light_entities(
    hass: HomeAssistant, entry: ConfigEntry, valid_unique_ids: set[str]
) -> None:
    """Remove light entities that are no longer exported by the integration."""
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not registry_entry.entity_id.startswith("light."):
            continue
        unique_id = registry_entry.unique_id or ""
        if unique_id in valid_unique_ids:
            continue
        registry.async_remove(registry_entry.entity_id)


def _cleanup_stale_light_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    bridge,
    valid_uuid_actions: set[str],
) -> None:
    """Remove stale light devices that no longer have any entities."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        matching_identifier = next(
            (
                identifier
                for identifier in (device_entry.identifiers or set())
                if identifier[0] == DOMAIN
                and identifier[1].startswith(f"{bridge.serial}:")
            ),
            None,
        )
        if matching_identifier is None:
            continue

        _, _, uuid_action = matching_identifier[1].partition(":")
        if not uuid_action or uuid_action in valid_uuid_actions:
            continue

        control = bridge.control_for_uuid_action(uuid_action)
        if control is None or control.type not in LIGHT_CONTROL_TYPES:
            continue

        device_entities = er.async_entries_for_device(
            entity_registry,
            device_entry.id,
            include_disabled_entities=True,
        )
        if device_entities:
            continue
        device_registry.async_remove_device(device_entry.id)
