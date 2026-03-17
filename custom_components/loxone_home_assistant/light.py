"""Light platform for Loxone."""

from __future__ import annotations

import json
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
)
from .intercom import intercom_light_state_name, is_intercom_control
from .models import LoxoneControl
from .options import option_enabled
from .runtime import entry_bridge

CONTROLLER_TYPES = {"LightController", "LightControllerV2"}
ON_OFF_LIGHT_TYPES = {"Switch", "TimedSwitch"}
CONTROLLER_CHILD_TYPES = LIGHT_CONTROL_TYPES | ON_OFF_LIGHT_TYPES
COLOR_LIGHT_TYPES = {"ColorPicker", "ColorPickerV2", "LightsceneRGB"}
OFF_MOOD_IDS = {0, 778}
NON_DIGIT_RE = re.compile(r"[^0-9,.-]+")
ATTR_EFFECT = "effect"
LIGHT_EFFECT_FEATURE = getattr(LightEntityFeature, "EFFECT", 4)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
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
        )
        self._moods = _extract_moods(control) if control.type in CONTROLLER_TYPES else []
        (
            self._effect_list,
            self._effect_to_mood_id,
            self._mood_id_to_effect,
        ) = _build_effect_maps(self._moods)

    def relevant_state_uuids(self):
        uuids = set(self.control.states.values())
        if self.control.type in CONTROLLER_TYPES:
            master_control = self._master_control()
            if master_control is not None:
                uuids.update(master_control.states.values())
            for child_control in self._child_light_controls:
                uuids.update(child_control.states.values())
        return uuids

    @property
    def is_on(self) -> bool | None:
        if self.control.type in CONTROLLER_TYPES:
            active_moods = _active_moods_to_bool(
                self.first_state_value("activeMoods", "moodList", "active")
            )
            if active_moods is True:
                return True

            brightness = self.brightness
            if brightness is not None:
                return brightness > 0

            child_states = [self._is_control_on(control) for control in self._child_light_controls]
            if any(value is True for value in child_states):
                return True
            known_child_states = [value for value in child_states if value is not None]
            if known_child_states and all(value is False for value in known_child_states):
                return False
            if active_moods is False:
                return False
            return None

        if self.control.type in COLOR_LIGHT_TYPES:
            brightness = self.brightness
            if brightness is not None:
                return brightness > 0
            bool_state = coerce_bool(self.first_state_value("active", "value"))
            if bool_state is not None:
                return bool_state
            return None

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
            master_control = self._master_control()
            if master_control is not None:
                master_brightness = self._brightness_for_control(master_control)
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
            color_state = parse_color_state(self.first_state_value("color", "sequenceColor"))
            brightness = color_state.get("brightness")
            if brightness is not None:
                return brightness
            fallback_value = coerce_float(self.first_state_value("position", "value"))
            return brightness_from_percent(fallback_value)

        return brightness_from_percent(coerce_float(self.first_state_value("position", "value")))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        color_state = parse_color_state(self.first_state_value("color", "sequenceColor"))
        return color_state.get("hs_color")

    @property
    def color_temp_kelvin(self) -> int | None:
        color_state = parse_color_state(self.first_state_value("color", "sequenceColor"))
        return color_state.get("color_temp_kelvin")

    @property
    def color_mode(self) -> ColorMode:
        if self.control.type in COLOR_LIGHT_TYPES:
            if self.hs_color is not None:
                return ColorMode.HS
            if self.color_temp_kelvin is not None:
                return ColorMode.COLOR_TEMP
            # Keep a supported mode even before first runtime value arrives.
            return ColorMode.HS
        if self.control.type in {"Dimmer", *CONTROLLER_TYPES}:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        if self.control.type in COLOR_LIGHT_TYPES:
            return {ColorMode.HS, ColorMode.COLOR_TEMP}
        if self.control.type in {"Dimmer", *CONTROLLER_TYPES}:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def supported_features(self) -> LightEntityFeature:
        features = LightEntityFeature(0)
        if self.control.type in CONTROLLER_TYPES and self._effect_list:
            features |= LIGHT_EFFECT_FEATURE
        return features

    @property
    def effect_list(self) -> list[str] | None:
        if self.control.type not in CONTROLLER_TYPES or not self._effect_list:
            return None
        return list(self._effect_list)

    @property
    def effect(self) -> str | None:
        if self.control.type not in CONTROLLER_TYPES:
            return None
        mood_id = _extract_active_mood_id(
            self.first_state_value("activeMoods", "moodList", "active")
        )
        if mood_id is None:
            return None
        return self._mood_id_to_effect.get(mood_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        if self.control.type == "LightControllerV2":
            mood_list = self.control.details.get("moodList")
            if mood_list is not None:
                attrs["moods"] = mood_list
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        if self.control.type in CONTROLLER_TYPES:
            await self._async_turn_on_controller(**kwargs)
            return

        if ATTR_HS_COLOR in kwargs:
            hs_color = kwargs[ATTR_HS_COLOR]
            brightness = percent_from_brightness(kwargs.get(ATTR_BRIGHTNESS))
            await self.bridge.async_send_action(
                self.control.uuid_action,
                f"hsv({round(hs_color[0])},{round(hs_color[1])},{brightness})",
            )
            return

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            brightness = percent_from_brightness(kwargs.get(ATTR_BRIGHTNESS))
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            command = "temp" if self.control.type == "ColorPickerV2" else "lumitech"
            await self.bridge.async_send_action(
                self.control.uuid_action,
                f"{command}({brightness},{kelvin})",
            )
            return

        if ATTR_BRIGHTNESS in kwargs:
            percent = percent_from_brightness(kwargs[ATTR_BRIGHTNESS])
            await self.bridge.async_send_action(self.control.uuid_action, str(percent))
            return

        await self.bridge.async_send_action(self.control.uuid_action, "on")

    async def async_turn_off(self, **kwargs) -> None:
        if self.control.type == "LightControllerV2":
            await self.bridge.async_send_action(self.control.uuid_action, "changeTo/0")
            return
        await self.bridge.async_send_action(self.control.uuid_action, "off")

    async def _async_turn_on_controller(self, **kwargs) -> None:
        master_control = self._master_control()

        if ATTR_EFFECT in kwargs and self._effect_to_mood_id:
            selected_effect = str(kwargs[ATTR_EFFECT])
            mood_id = self._effect_to_mood_id.get(selected_effect)
            if mood_id is not None:
                await self.bridge.async_send_action(
                    self.control.uuid_action,
                    f"changeTo/{mood_id}",
                )
                return

        if master_control is not None and ATTR_BRIGHTNESS in kwargs:
            percent = percent_from_brightness(kwargs[ATTR_BRIGHTNESS])
            await self.bridge.async_send_action(master_control.uuid_action, str(percent))
            return

        if self.control.type == "LightControllerV2":
            # `changeTo/99` restores the "on" scene for grouped LightControllerV2 blocks.
            await self.bridge.async_send_action(self.control.uuid_action, "changeTo/99")
            return

        await self.bridge.async_send_action(self.control.uuid_action, "on")

    def _brightness_for_control(self, control: LoxoneControl) -> int | None:
        if control.type in COLOR_LIGHT_TYPES:
            color_state = parse_color_state(
                self.bridge.control_state(control, "color")
                or self.bridge.control_state(control, "sequenceColor")
            )
            brightness = color_state.get("brightness")
            if brightness is not None:
                return brightness

        value = coerce_float(
            self.bridge.control_state(control, "position")
            or self.bridge.control_state(control, "value")
            or self.bridge.control_state(control, "active")
        )
        return brightness_from_percent(value) if value is not None else None

    def _is_control_on(self, control: LoxoneControl) -> bool | None:
        brightness = self._brightness_for_control(control)
        if brightness is not None:
            return brightness > 0

        boolean_state = coerce_bool(
            self.bridge.control_state(control, "active")
            or self.bridge.control_state(control, "value")
        )
        return boolean_state

    def _master_control(self):
        master_uuid = self.control.details.get("masterValue") or self.control.details.get("masterColor")
        if not master_uuid:
            return None
        return self.bridge.control_for_uuid_action(master_uuid)


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
    for candidate_parent in bridge.controls:
        if _is_child_of_controller(control, candidate_parent):
            return True
    return False


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
    if parent.type not in CONTROLLER_TYPES:
        return False
    if control.uuid_action == parent.uuid_action:
        return False
    if control.parent_uuid_action == parent.uuid_action:
        return True
    if control.uuid_action.startswith(f"{parent.uuid_action}/"):
        return True
    return _details_reference_action(parent.details, control.uuid_action)


def _details_reference_action(details: Mapping[str, Any], uuid_action: str) -> bool:
    target = uuid_action.strip().casefold()
    if not target:
        return False

    stack: list[Any] = [details]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            stack.extend(current.values())
            continue
        if isinstance(current, list):
            stack.extend(current)
            continue
        if isinstance(current, str) and current.strip().casefold() == target:
            return True
    return False


def _active_moods_to_bool(value: Any) -> bool | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
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

        try:
            parsed = json.loads(raw.replace("'", '"'))
        except json.JSONDecodeError:
            parsed = None

        if parsed is not None:
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
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw and raw.lstrip("-").isdigit():
            return int(raw)
    if isinstance(value, dict):
        candidate = value.get("id", value.get("moodId"))
        return _coerce_mood_id(candidate)
    return None


def _extract_moods(control: LoxoneControl) -> list[tuple[int, str]]:
    raw_moods: Any = control.details.get("moodList")
    if isinstance(raw_moods, str):
        raw = raw_moods.strip()
        if not raw:
            return []
        try:
            raw_moods = json.loads(raw.replace("'", '"'))
        except json.JSONDecodeError:
            return []

    normalized_items: list[Any]
    if isinstance(raw_moods, list):
        normalized_items = raw_moods
    elif isinstance(raw_moods, Mapping):
        normalized_items = []
        for raw_key, raw_value in raw_moods.items():
            if isinstance(raw_value, Mapping):
                normalized_items.append(raw_value)
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
    effect_list: list[str] = []
    effect_to_mood_id: dict[str, int] = {}
    mood_id_to_effect: dict[int, str] = {}
    seen_labels: set[str] = set()

    for mood_id, mood_name in moods:
        effect = mood_name
        if effect in seen_labels:
            effect = f"{mood_name} ({mood_id})"
        seen_labels.add(effect)
        effect_list.append(effect)
        effect_to_mood_id[effect] = mood_id
        mood_id_to_effect[mood_id] = effect

    return effect_list, effect_to_mood_id, mood_id_to_effect


def _extract_active_mood_id(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return 0 if value is False else None

    if isinstance(value, (int, float)):
        return int(value)

    if isinstance(value, Mapping):
        return _coerce_mood_id(value.get("id", value.get("moodId")))

    if isinstance(value, list):
        mood_ids: list[int] = []
        for item in value:
            mood_id = _coerce_mood_id(item)
            if mood_id is not None:
                mood_ids.append(mood_id)
                continue
            nested = _extract_active_mood_id(item)
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

        try:
            parsed = json.loads(raw.replace("'", '"'))
        except json.JSONDecodeError:
            parsed = None

        if parsed is not None:
            return _extract_active_mood_id(parsed)

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
