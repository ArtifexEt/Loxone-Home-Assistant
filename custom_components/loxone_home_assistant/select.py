"""Select platform for Loxone."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENABLE_LIGHT_MOOD_SELECT,
    DEFAULT_ENABLE_LIGHT_MOOD_SELECT,
    DOMAIN,
)
from .entity import LoxoneEntity, control_entity_unique_id
from .light import CONTROLLER_TYPES
from .models import LoxoneControl

OFF_MOOD_IDS = {0, 778}
NON_DIGIT_RE = re.compile(r"[^0-9,.-]+")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = hass.data[DOMAIN]["bridges"][entry.entry_id]
    mood_select_enabled = _option_enabled(
        entry.options.get(CONF_ENABLE_LIGHT_MOOD_SELECT),
        DEFAULT_ENABLE_LIGHT_MOOD_SELECT,
    )

    controls: list[LoxoneControl] = []
    if mood_select_enabled:
        controls = [
            control
            for control in bridge.controls
            if control.type in CONTROLLER_TYPES and _extract_moods(control)
        ]

    entities = [LoxoneMoodSelectEntity(bridge, control) for control in controls]
    _cleanup_stale_select_entities(
        hass,
        entry,
        {
            control_entity_unique_id(bridge.serial, control.uuid_action, "mood")
            for control in controls
        },
    )
    async_add_entities(entities)


class LoxoneMoodSelectEntity(LoxoneEntity, SelectEntity):
    """Mood selector for a LightController."""

    _attr_icon = "mdi:palette"

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "Mood")
        moods = _extract_moods(control)
        (
            self._attr_options,
            self._option_to_mood_id,
            self._mood_id_to_option,
        ) = _build_option_maps(moods)

    @property
    def current_option(self) -> str | None:
        mood_id = _extract_active_mood_id(
            self.first_state_value("activeMoods", "moodList", "active")
        )
        if mood_id is None:
            return None
        return self._mood_id_to_option.get(mood_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        active_mood_id = _extract_active_mood_id(
            self.first_state_value("activeMoods", "moodList", "active")
        )
        if active_mood_id is not None:
            attrs["active_mood_id"] = active_mood_id
        return attrs

    async def async_select_option(self, option: str) -> None:
        mood_id = self._option_to_mood_id[option]
        await self.bridge.async_send_action(self.control.uuid_action, f"changeTo/{mood_id}")


def _extract_moods(control: LoxoneControl) -> list[tuple[int, str]]:
    raw_moods = control.details.get("moodList")
    if not isinstance(raw_moods, list):
        return []

    moods: list[tuple[int, str]] = []
    for item in raw_moods:
        if not isinstance(item, Mapping):
            continue
        mood_id = _coerce_mood_id(item.get("id", item.get("moodId")))
        if mood_id is None:
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        if not name:
            name = f"Mood {mood_id}"
        moods.append((mood_id, name))
    return moods


def _build_option_maps(
    moods: list[tuple[int, str]],
) -> tuple[list[str], dict[str, int], dict[int, str]]:
    options: list[str] = []
    option_to_mood_id: dict[str, int] = {}
    mood_id_to_option: dict[int, str] = {}
    seen_labels: set[str] = set()

    for mood_id, mood_name in moods:
        option = mood_name
        if option in seen_labels:
            option = f"{mood_name} ({mood_id})"
        seen_labels.add(option)
        options.append(option)
        option_to_mood_id[option] = mood_id
        mood_id_to_option[mood_id] = option

    if 0 not in mood_id_to_option and "Off" not in option_to_mood_id:
        options.insert(0, "Off")
        option_to_mood_id["Off"] = 0
        mood_id_to_option[0] = "Off"

    return options, option_to_mood_id, mood_id_to_option


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


def _coerce_mood_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw and raw.lstrip("-").isdigit():
            return int(raw)
    if isinstance(value, Mapping):
        return _coerce_mood_id(value.get("id", value.get("moodId")))
    return None


def _cleanup_stale_select_entities(
    hass: HomeAssistant, entry: ConfigEntry, valid_unique_ids: set[str]
) -> None:
    """Remove select entities that are no longer exported by the integration."""
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not registry_entry.entity_id.startswith("select."):
            continue
        unique_id = registry_entry.unique_id or ""
        if unique_id in valid_unique_ids:
            continue
        registry.async_remove(registry_entry.entity_id)


def _option_enabled(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
    return default
