"""Select platform for Loxone."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
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
    RADIO_SELECT_CONTROL_TYPES,
)
from .entity import LoxoneEntity, control_entity_unique_id
from .intercom import (
    intercom_address_state_name,
    intercom_history_state_name,
    is_intercom_control,
)
from .light import CONTROLLER_TYPES
from .models import LoxoneControl
from .options import option_enabled
from .runtime import entry_bridge
from .sensor import (
    _async_fetch_json,
    _dynamic_intercom_history_state_names,
    _extract_intercom_events,
    _has_intercom_history_detail,
    _intercom_history_url_candidates,
    _intercom_history_payload_from_state,
    _is_intercom_video_settings_payload,
)

OFF_MOOD_IDS = {0, 778}
NON_DIGIT_RE = re.compile(r"[^0-9,.-]+")
INTERCOM_HISTORY_SELECT_SUFFIX = "history_photo"
INTERCOM_HISTORY_SELECT_LIVE_OPTION = "Live"
INTERCOM_HISTORY_SELECT_MAX_OPTIONS = 15


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entry_data = getattr(entry, "data", {}) or {}
    mood_select_enabled = option_enabled(
        entry.options.get(
            CONF_ENABLE_LIGHT_MOOD_SELECT,
            entry_data.get(CONF_ENABLE_LIGHT_MOOD_SELECT),
        ),
        DEFAULT_ENABLE_LIGHT_MOOD_SELECT,
    )

    controls: list[LoxoneControl] = []
    if mood_select_enabled:
        controls = [
            control
            for control in bridge.controls
            if control.type in CONTROLLER_TYPES and _extract_moods(control)
        ]

    radio_controls = [
        control
        for control in bridge.controls
        if control.type in RADIO_SELECT_CONTROL_TYPES and _extract_radio_outputs(control)
    ]
    intercom_controls = [
        control
        for control in bridge.controls
        if is_intercom_control(control) and _supports_intercom_history_select(control)
    ]

    entities = [LoxoneMoodSelectEntity(bridge, control) for control in controls]
    entities.extend(LoxoneRadioOutputSelectEntity(bridge, control) for control in radio_controls)
    entities.extend(LoxoneIntercomHistorySelectEntity(bridge, control) for control in intercom_controls)
    _cleanup_stale_select_entities(
        hass,
        entry,
        {
            control_entity_unique_id(bridge.serial, control.uuid_action, "mood")
            for control in controls
        }
        | {
            control_entity_unique_id(bridge.serial, control.uuid_action, "radio")
            for control in radio_controls
        }
        | {
            control_entity_unique_id(
                bridge.serial,
                control.uuid_action,
                INTERCOM_HISTORY_SELECT_SUFFIX,
            )
            for control in intercom_controls
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


class LoxoneRadioOutputSelectEntity(LoxoneEntity, SelectEntity):
    """Output selector for Loxone Radio controls."""

    _attr_icon = "mdi:radio"

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "Output")
        outputs = _extract_radio_outputs(control)
        self._option_to_output_id = {label: output_id for output_id, label in outputs}
        self._output_id_to_option = {output_id: label for output_id, label in outputs}
        self._attr_options = [label for _, label in outputs]

    @property
    def current_option(self) -> str | None:
        output_id = _coerce_int(self.first_state_value("activeOutput", "output", "value"))
        if output_id is None:
            return None
        return self._output_id_to_option.get(output_id, f"Output {output_id}")

    async def async_select_option(self, option: str) -> None:
        output_id = self._option_to_output_id.get(option)
        if output_id is None:
            return
        if output_id == 0:
            await self.bridge.async_send_action(self.control.uuid_action, "reset")
            return
        await self.bridge.async_send_action(self.control.uuid_action, str(output_id))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        output_id = _coerce_int(self.first_state_value("activeOutput", "output", "value"))
        if output_id is not None:
            attrs["active_output_id"] = output_id
        return attrs


class LoxoneIntercomHistorySelectEntity(LoxoneEntity, SelectEntity):
    """Select old Intercom snapshots and expose them for camera preview."""

    _attr_icon = "mdi:image-multiple"

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "History Photo")
        self._attr_unique_id = control_entity_unique_id(
            bridge.serial,
            control.uuid_action,
            INTERCOM_HISTORY_SELECT_SUFFIX,
        )
        self._history_state_name = intercom_history_state_name(control)
        self._dynamic_history_state_names = _dynamic_intercom_history_state_names(control)
        self._address_state_name = intercom_address_state_name(control)
        self._events_url: str | None = None
        self._option_to_image_url: dict[str, str] = {}
        self._selected_option = INTERCOM_HISTORY_SELECT_LIVE_OPTION
        self._attr_options = [INTERCOM_HISTORY_SELECT_LIVE_OPTION]

    @property
    def should_poll(self) -> bool:
        return True

    @property
    def current_option(self) -> str | None:
        return self._selected_option

    def relevant_state_uuids(self):
        uuids: list[str] = []
        for state_name in (
            self._history_state_name,
            self._address_state_name,
            *self._dynamic_history_state_names,
        ):
            if state_name is None:
                continue
            state_uuid = self.control.state_uuid(state_name)
            if state_uuid and state_uuid not in uuids:
                uuids.append(state_uuid)
        return uuids

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()

    def _handle_bridge_update(self) -> None:
        schedule_update = getattr(self, "async_schedule_update_ha_state", None)
        if callable(schedule_update):
            schedule_update(True)

    async def async_update(self) -> None:
        address_value = self._address_state_value()
        normalized_events: list[dict[str, Any]] = []

        for state_name in (
            self._history_state_name,
            *self._dynamic_history_state_names,
        ):
            state_payload = _intercom_history_payload_from_state(
                self.bridge,
                self.control,
                state_name,
            )
            if state_payload is None:
                continue
            if _is_intercom_video_settings_payload(state_payload):
                continue
            normalized_events = _extract_intercom_events(
                state_payload,
                self.bridge,
                self.control,
                address_value,
            )
            if normalized_events:
                self._events_url = None
                break

        if not normalized_events:
            self._events_url = None
            for events_url in _intercom_history_url_candidates(
                self.bridge,
                self.control,
                self._history_state_name,
                address_value,
                dynamic_state_names=self._dynamic_history_state_names,
            ):
                payload = await _async_fetch_json(self.bridge, events_url)
                if payload is None:
                    continue
                normalized_events = _extract_intercom_events(
                    payload,
                    self.bridge,
                    self.control,
                    address_value,
                )
                if not normalized_events:
                    continue
                self._events_url = events_url
                break

        options, option_to_image_url = _build_intercom_history_options(normalized_events)
        self._attr_options = options
        self._option_to_image_url = option_to_image_url

        if self._selected_option not in self._attr_options:
            self._selected_option = INTERCOM_HISTORY_SELECT_LIVE_OPTION

        self._apply_selected_history_image()

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            return
        self._selected_option = option
        self._apply_selected_history_image()
        write_state = getattr(self, "async_write_ha_state", None)
        if callable(write_state):
            write_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs["option_count"] = len(self._attr_options)
        if self._events_url is not None:
            attrs["history_events_url"] = self._events_url
        selected_image_url = self._option_to_image_url.get(self._selected_option)
        if selected_image_url is not None:
            attrs["selected_image_url"] = selected_image_url
        return attrs

    def _address_state_value(self) -> Any:
        if self._address_state_name is None:
            return None
        return self.bridge.control_state(self.control, self._address_state_name)

    def _apply_selected_history_image(self) -> None:
        selected_images = getattr(self.bridge, "_intercom_selected_history_images", None)
        if not isinstance(selected_images, dict):
            selected_images = {}
            setattr(self.bridge, "_intercom_selected_history_images", selected_images)

        selected_image_url = self._option_to_image_url.get(self._selected_option)
        if selected_image_url is None:
            selected_images.pop(self.control.uuid_action, None)
            return

        selected_images[self.control.uuid_action] = selected_image_url


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


def _extract_radio_outputs(control: LoxoneControl) -> list[tuple[int, str]]:
    raw_outputs = control.details.get("outputs")
    if not isinstance(raw_outputs, Mapping):
        return []

    outputs: list[tuple[int, str]] = []
    for output_id_raw, output_name_raw in raw_outputs.items():
        output_id = _coerce_int(output_id_raw)
        if output_id is None:
            continue
        output_name = str(output_name_raw).strip()
        if not output_name:
            output_name = f"Output {output_id}"
        outputs.append((output_id, output_name))

    outputs.sort(key=lambda item: item[0])
    return outputs


def _supports_intercom_history_select(control: LoxoneControl) -> bool:
    history_state_name = intercom_history_state_name(control)
    if history_state_name is not None:
        return True
    if _dynamic_intercom_history_state_names(control):
        return True
    return _has_intercom_history_detail(control)


def _build_intercom_history_options(
    normalized_events: list[dict[str, Any]],
) -> tuple[list[str], dict[str, str]]:
    options = [INTERCOM_HISTORY_SELECT_LIVE_OPTION]
    option_to_image_url: dict[str, str] = {}
    seen_labels: set[str] = set(options)

    for index, event in enumerate(normalized_events, start=1):
        if len(options) > INTERCOM_HISTORY_SELECT_MAX_OPTIONS:
            break
        image_url = event.get("image_url")
        if not isinstance(image_url, str):
            continue
        label = _intercom_event_option_label(event, index)
        while label in seen_labels:
            label = f"{label} ({index})"
        seen_labels.add(label)
        options.append(label)
        option_to_image_url[label] = image_url

    return options, option_to_image_url


def _intercom_event_option_label(event: Mapping[str, Any], index: int) -> str:
    timestamp = event.get("timestamp")
    if isinstance(timestamp, datetime):
        localized = (
            timestamp.astimezone()
            if timestamp.tzinfo is not None
            else timestamp.replace(tzinfo=timezone.utc).astimezone()
        )
        return localized.strftime("%Y-%m-%d %H:%M:%S")
    return f"Photo {index}"


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


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None
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
