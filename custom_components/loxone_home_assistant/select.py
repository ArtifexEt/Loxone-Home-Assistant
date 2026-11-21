"""Select platform for Loxone."""

from __future__ import annotations

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
    RADIO_SELECT_CONTROL_TYPES,
)
from .entity import LoxoneEntity, control_entity_unique_id, unique_option_labels
from .intercom import is_intercom_control
from .intercom_media import (
    IntercomHistoryEntry,
    async_intercom_history_entries,
    set_intercom_selected_history_timestamp,
    supports_intercom_history,
)
from .light import (
    active_light_effect_id,
    light_active_effect_states,
    light_effect_command,
    light_effect_maps,
    light_effect_state_name,
    light_effects,
)
from .models import LoxoneControl
from .options import option_enabled
from .runtime import entry_bridge

INTERCOM_HISTORY_SELECT_SUFFIX = "history_photo"
INTERCOM_HISTORY_SELECT_LIVE_OPTION = "Live"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
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
            if (state_name := light_effect_state_name(control)) is not None
            and (control.state_uuid(state_name) or light_effects(bridge, control))
        ]

    radio_controls = [
        control
        for control in bridge.controls
        if control.type in RADIO_SELECT_CONTROL_TYPES and _extract_radio_outputs(control)
    ]
    intercom_controls = [
        control
        for control in bridge.controls
        if is_intercom_control(control) and supports_intercom_history(bridge, control)
    ]

    entities = [LoxoneMoodSelectEntity(bridge, control) for control in controls]
    entities.extend(LoxoneRadioOutputSelectEntity(bridge, control) for control in radio_controls)
    entities.extend(LoxoneIntercomHistorySelectEntity(bridge, control) for control in intercom_controls)
    for control in bridge.controls:
        if (
            control.type != "Ventilation"
            or control.is_secured
            or not control.state_uuid("activeTimerProfile")
        ):
            continue
        profiles = _ventilation_profiles(control)
        if profiles:
            entities.append(LoxoneVentilationTimerSelect(bridge, control, profiles))
    _cleanup_stale_select_entities(
        hass, entry, {key for entity in entities if (key := entity.unique_id) is not None}
    )
    async_add_entities(entities)


class LoxoneMoodSelectEntity(LoxoneEntity, SelectEntity):
    """Mood selector for a LightController."""

    _attr_icon = "mdi:palette"

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "Mood")

    def _options(self) -> tuple[list[str], dict[str, int], dict[int, str]]:
        return light_effect_maps(self.bridge, self.control, include_off=True)

    @property
    def options(self) -> list[str]:
        return self._options()[0]

    def _active_mood_id(self) -> int | None:
        return active_light_effect_id(
            self.first_state_value(*light_active_effect_states(self.control))
        )

    @property
    def current_option(self) -> str | None:
        mood_id = self._active_mood_id()
        return self._options()[2].get(mood_id) if mood_id is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        active_mood_id = self._active_mood_id()
        if active_mood_id is not None:
            attrs["active_mood_id"] = active_mood_id
        return attrs

    async def async_select_option(self, option: str) -> None:
        mood_id = self._options()[1].get(option)
        if mood_id is not None:
            await self.bridge.async_send_action(
                self.control.uuid_action, light_effect_command(self.control, mood_id)
            )


class LoxoneRadioOutputSelectEntity(LoxoneEntity, SelectEntity):
    """Output selector for Loxone Radio controls."""

    _attr_icon = "mdi:radio"

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "Output")
        self._output_id_to_option = unique_option_labels(_extract_radio_outputs(control))
        self._option_to_output_id = {
            label: output_id for output_id, label in self._output_id_to_option.items()
        }
        self._attr_options = list(self._option_to_output_id)

    @property
    def current_option(self) -> str | None:
        output_id = _coerce_int(self.first_state_value("activeOutput", "output", "value"))
        if output_id is None:
            return None
        return self._output_id_to_option.get(output_id)

    async def async_select_option(self, option: str) -> None:
        output_id = self._option_to_output_id.get(option)
        if output_id is None:
            return
        command = "reset" if output_id == 0 else str(output_id)
        await self.bridge.async_send_action(self.control.uuid_action, command)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        output_id = _coerce_int(self.first_state_value("activeOutput", "output", "value"))
        if output_id is not None:
            attrs["active_output_id"] = output_id
        return attrs


class LoxoneVentilationTimerSelect(LoxoneEntity, SelectEntity):
    """Start a configured ventilation timer or return to automatic control."""

    def __init__(
        self, bridge, control: LoxoneControl, profiles: dict[int, tuple[str, str]]
    ) -> None:
        super().__init__(bridge, control, "Ventilation Profile")
        self._labels = unique_option_labels(
            [(-2, "Automatic"), *((idx, name) for idx, (name, _) in profiles.items())]
        )
        self._commands = {self._labels[-2]: "setTimer/0"}
        self._commands.update(
            {self._labels[idx]: command for idx, (_, command) in profiles.items()}
        )
        self._attr_options = list(self._commands)

    @property
    def current_option(self) -> str | None:
        profile_id = _coerce_int(self.state_value("activeTimerProfile"))
        return None if profile_id is None else self._labels.get(profile_id)

    async def async_select_option(self, option: str) -> None:
        await self.bridge.async_send_action(self.control.uuid_action, self._commands[option])


class LoxoneIntercomHistorySelectEntity(LoxoneEntity, SelectEntity):
    """Select old Intercom snapshots and expose them for camera preview."""

    _attr_icon = "mdi:image-multiple"
    _attr_entity_registry_enabled_default = True

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control, "History Photo")
        self._set_intercom_entity_name("select", "intercom_history_photo")
        self._attr_unique_id = control_entity_unique_id(
            bridge.serial,
            control.uuid_action,
            INTERCOM_HISTORY_SELECT_SUFFIX,
        )
        self._option_to_event: dict[str, IntercomHistoryEntry] = {}
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
        for state_name in self.control.states:
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
        history_entries = await async_intercom_history_entries(
            self.bridge,
            self.control,
            state_value_getter=self.state_value,
        )
        previous = self._option_to_event.get(self._selected_option)
        self._attr_options, self._option_to_event = _build_intercom_history_options(
            history_entries
        )
        self._selected_option = INTERCOM_HISTORY_SELECT_LIVE_OPTION
        if previous is not None:
            self._selected_option = next(
                (
                    option for option, event in self._option_to_event.items()
                    if event.raw_timestamp == previous.raw_timestamp
                ),
                INTERCOM_HISTORY_SELECT_LIVE_OPTION,
            )

        self._apply_selected_history_timestamp()

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            return
        self._selected_option = option
        self._apply_selected_history_timestamp()
        write_state = getattr(self, "async_write_ha_state", None)
        if callable(write_state):
            write_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs["option_count"] = len(self._attr_options)
        event = self._option_to_event.get(self._selected_option)
        if event is not None and event.event_time is not None:
            attrs["selected_timestamp"] = event.raw_timestamp
        return attrs

    def _apply_selected_history_timestamp(self) -> None:
        event = self._option_to_event.get(self._selected_option)
        set_intercom_selected_history_timestamp(
            self.bridge,
            self.control.uuid_action,
            event.selection_value if event is not None else None,
        )


def _extract_radio_outputs(control: LoxoneControl) -> list[tuple[int, str]]:
    raw_outputs = control.details.get("outputs")
    if not isinstance(raw_outputs, Mapping):
        return []

    outputs: list[tuple[int, str]] = []
    all_off = control.details.get("allOff")
    if isinstance(all_off, str) and all_off.strip():
        outputs.append((0, all_off.strip()))
    for output_id_raw, output_name_raw in raw_outputs.items():
        output_id = _coerce_int(output_id_raw)
        if output_id is None:
            continue
        output_name = output_name_raw.strip() if isinstance(output_name_raw, str) else ""
        if not output_name:
            output_name = f"Output {output_id}"
        outputs.append((output_id, output_name))

    outputs.sort(key=lambda item: item[0])
    return outputs


def _ventilation_profiles(control: LoxoneControl) -> dict[int, tuple[str, str]]:
    timer_profiles = control.details.get("timerProfiles")
    if not isinstance(timer_profiles, list):
        return {}

    profiles: dict[int, tuple[str, str]] = {}
    for index, profile in enumerate(timer_profiles):
        if not isinstance(profile, Mapping):
            continue
        speed_detail = profile.get("speed")
        speed = _coerce_int(speed_detail.get("value")) if isinstance(speed_detail, Mapping) else None
        interval = _coerce_int(profile.get("interval"))
        mode = _coerce_int(profile.get("defaultMode"))
        if (
            interval is None
            or interval <= 0
            or speed is None
            or not 0 <= speed <= 100
            or mode is None
        ):
            continue
        name = profile.get("name")
        label = name.strip() if isinstance(name, str) and name.strip() else f"Timer {index + 1}"
        profiles[index] = label, f"setTimer/{interval}/{speed}/{mode}/{index}"
    return profiles


def _build_intercom_history_options(
    history_entries: tuple[IntercomHistoryEntry, ...],
) -> tuple[list[str], dict[str, IntercomHistoryEntry]]:
    options = [INTERCOM_HISTORY_SELECT_LIVE_OPTION]
    option_to_event: dict[str, IntercomHistoryEntry] = {}
    seen_labels: set[str] = set(options)

    for index, event in enumerate(history_entries, start=1):
        label = _intercom_event_option_label(event, index)
        while label in seen_labels:
            label = f"{label} ({index})"
        seen_labels.add(label)
        options.append(label)
        option_to_event[label] = event

    return options, option_to_event


def _intercom_event_option_label(event: IntercomHistoryEntry, index: int) -> str:
    timestamp = event.event_time
    if isinstance(timestamp, datetime):
        localized = (
            timestamp.astimezone()
            if timestamp.tzinfo is not None
            else timestamp.replace(tzinfo=timezone.utc).astimezone()
        )
        return localized.strftime("%Y-%m-%d %H:%M:%S")
    return f"Photo {index}"


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if number.is_integer() else None


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
