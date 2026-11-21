"""Binary sensor platform for Loxone."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from homeassistant.components.binary_sensor import BinarySensorEntity

try:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    BinarySensorDeviceClass = None  # type: ignore[assignment]
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACCESS_DENIED_STATE_CANDIDATES,
    ACCESS_GRANTED_STATE_CANDIDATES,
    ACCESS_TYPE_HINTS,
    BINARY_SENSOR_CONTROL_TYPES,
    DOORBELL_STATE_CANDIDATES,
    HANDLED_CONTROL_TYPES,
    POWER_SUPPLY_CONTROL_TYPES,
)
from .entity import (
    LoxoneEntity,
    coerce_bool,
    coerce_float,
    control_entity_unique_id,
    first_matching_state_name,
    normalize_state_name,
    state_is_boolean,
)
from .intercom import (
    intercom_boolean_state_roles,
    intercom_doorbell_state_name,
    is_intercom_control,
)
from .models import LoxoneControl
from .runtime import entry_bridge

POWER_SUPPLY_CHARGING_STATE_CANDIDATES = (
    "isCharging",
    "charging",
    "chargeActive",
    "batteryCharging",
)
LOCK_STATE_CANDIDATES = ("isLocked",)
ACCESS_TYPE_HINT_KEYS = tuple(hint.casefold() for hint in ACCESS_TYPE_HINTS)
LOCK_SOURCE_LABELS = {
    1: "visualization",
    2: "logic",
}
PRIMARY_BINARY_STATES = {
    "Alarm": ("level", "alarm", "isAlarmActive", "active"),
    "SmokeAlarm": ("level", "alarm", "isAlarmActive", "active"),
    "InfoOnlyDigital": ("active", "value"),
    "PresenceDetector": ("active", "presence", "motion", "value"),
}
EXTRA_BINARY_STATES = {
    "AlarmClock": (
        ("isEnabled", "Enabled", None),
        ("isAlarmActive", "Ringing", None),
        ("confirmationNeeded", "Confirmation needed", None),
    ),
    "Hourcounter": (
        ("active", "Active", "RUNNING"),
        ("overdue", "Maintenance overdue", "PROBLEM"),
    ),
    "SmokeAlarm": (
        ("acousticAlarm", "Acoustic alarm", None),
        ("testAlarm", "Test alarm", None),
        ("areAlarmSignalsOff", "Alarm signals off", None),
    ),
    "PulseAt": (("isActive", "Pulse active", None),),
    "Ventilation": (("presence", "Presence", "OCCUPANCY"),),
    "Wallbox2": (
        ("connected", "Connected", "PLUG"),
        ("enabled", "Charging allowed", None),
        ("active", "Charging", "BATTERY_CHARGING"),
        ("loadshed", "Load shedding", None),
        ("phaseSwitching", "Phase switching", None),
    ),
}
SMOKE_ALARM_CAUSES = (
    (0x01, "Smoke", "SMOKE"),
    (0x02, "Water", "MOISTURE"),
    (0x04, "High temperature", "HEAT"),
    (0x08, "Arc fault", "PROBLEM"),
)
POWER_UNIT_STATUS = (
    ("Backup active", None),
    ("Overcurrent", "PROBLEM"),
    ("Battery missing", "PROBLEM"),
    ("Battery empty", "BATTERY"),
    ("Battery test active", "RUNNING"),
    ("Battery test finished", None),
    ("Battery needs service", "PROBLEM"),
    ("Battery defective", "PROBLEM"),
    ("Battery test OK", None),
    ("Overheating", "HEAT"),
)
WINDOW_CLOSED = 0x01
WINDOW_TILTED = 0x02
WINDOW_OPEN = 0x04
WINDOW_LOCKED = 0x08
WINDOW_UNLOCKED = 0x10


def _find_power_supply_charging_state(control: LoxoneControl) -> str | None:
    matched = first_matching_state_name(control, POWER_SUPPLY_CHARGING_STATE_CANDIDATES)
    return matched or next(
        (
            name
            for name in control.states
            if "charging" in (key := normalize_state_name(name))
            and not any(part in key for part in ("level", "power", "current", "voltage", "time"))
        ),
        None,
    )


def _find_lock_state(control: LoxoneControl) -> str | None:
    candidates = (
        (*LOCK_STATE_CANDIDATES, "locked")
        if normalize_state_name(control.type).startswith("presencedetector")
        else LOCK_STATE_CANDIDATES
    )
    return first_matching_state_name(control, candidates)


def _coerce_int(value: object) -> int | None:
    numeric = coerce_float(value)
    if numeric is None or not isfinite(numeric) or not numeric.is_integer():
        return None
    return int(numeric)


def _binary_device_class(name: str | None) -> BinarySensorDeviceClass | str | None:
    return getattr(BinarySensorDeviceClass, name, name.lower()) if name else None


def _build_smoke_cause_entities(bridge, control: LoxoneControl) -> list[BinarySensorEntity]:
    state_name = first_matching_state_name(control, ("alarmCause",))
    mask = _coerce_int(control.details.get("availableAlarms"))
    if state_name is None or mask is None or mask <= 0:
        return []
    return [
        LoxoneBitmaskBinaryEntity(bridge, control, state_name, label, bit, device_class)
        for bit, label, device_class in SMOKE_ALARM_CAUSES
        if mask & bit
    ]


def _build_power_unit_binary_entities(bridge, control: LoxoneControl) -> list[BinarySensorEntity]:
    states = []
    status_name = first_matching_state_name(control, ("deviceInfo",))
    supply_only = _coerce_int(control.details.get("supplyMode")) == 1
    if status_name is not None:
        states.extend(
            LoxoneBitmaskBinaryEntity(bridge, control, status_name, label, 1 << bit, device_class)
            for bit, (label, device_class) in enumerate(POWER_UNIT_STATUS)
            if not supply_only or bit in {1, 9}
        )

    fuse_name = first_matching_state_name(control, ("fuse",))
    outputs = control.details.get("Outputs") or control.details.get("outputs")
    if fuse_name is not None and isinstance(outputs, list):
        states.extend(
            LoxoneBitmaskBinaryEntity(
                bridge, control, fuse_name, f"Fuse {idx + 1} tripped", 1 << idx, "PROBLEM"
            )
            for idx, output in enumerate(outputs[:7])
            if isinstance(output, str) and output.strip()
        )
    return states


def _build_window_monitor_entities(bridge, control: LoxoneControl) -> list[BinarySensorEntity]:
    state_name = first_matching_state_name(control, ("windowStates",))
    windows = control.details.get("windows")
    if state_name is None or not isinstance(windows, list):
        return []

    used_keys: set[str] = set()
    entities: list[BinarySensorEntity] = []
    for index, item in enumerate(windows, 1):
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("uuid") or "").strip()
        if not key or key in used_keys:
            key = f"index_{index}"
        used_keys.add(key)
        label = str(item.get("name") or f"Opening {index}").strip()
        if not label:
            label = f"Opening {index}"
        entities.append(
            LoxoneWindowMonitorEntity(bridge, control, state_name, label, key, index - 1)
        )
    return entities


def _should_expose_access_entities(
    control: LoxoneControl,
    access_granted_state_name: str | None,
    access_denied_state_name: str | None,
) -> bool:
    if access_granted_state_name is None and access_denied_state_name is None:
        return False

    # A pair of complementary states is strong evidence for access control behavior
    # even when the control type is not one of the expected access blocks.
    if access_granted_state_name is not None and access_denied_state_name is not None:
        return True

    normalized_type = control.type.casefold()
    normalized_name = control.name.casefold()
    for hint in ACCESS_TYPE_HINT_KEYS:
        if hint in normalized_type or hint in normalized_name:
            return True
    return False


def _build_access_binary_entities(
    bridge,
    control: LoxoneControl,
    access_granted_state_name: str | None,
    access_denied_state_name: str | None,
) -> list[BinarySensorEntity]:
    if not _should_expose_access_entities(
        control,
        access_granted_state_name,
        access_denied_state_name,
    ):
        return []

    entities: list[BinarySensorEntity] = []
    if access_granted_state_name is not None:
        entities.append(
            LoxoneAccessBinaryEntity(
                bridge,
                control,
                access_granted_state_name,
                "Access Granted",
                "granted",
                "mdi:shield-check",
            )
        )
    if (
        access_denied_state_name is not None
        and access_denied_state_name != access_granted_state_name
    ):
        entities.append(
            LoxoneAccessBinaryEntity(
                bridge,
                control,
                access_denied_state_name,
                "Access Denied",
                "denied",
                "mdi:shield-alert",
            )
        )
    return entities


def _build_intercom_state_binary_entities(
    bridge,
    control: LoxoneControl,
    intercom_state_roles: Mapping[str, str],
    excluded_state_names: set[str],
) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = []
    for state_name, role in intercom_state_roles.items():
        if state_name in excluded_state_names:
            continue
        entities.append(
            LoxoneIntercomStateBinaryEntity(
                bridge,
                control,
                state_name,
                role,
            )
        )
    return entities


def _build_special_binary_entities(
    bridge,
    control: LoxoneControl,
) -> tuple[list[BinarySensorEntity], set[str]]:
    is_intercom = is_intercom_control(control)
    doorbell_state_name = (
        intercom_doorbell_state_name(control)
        if is_intercom
        else first_matching_state_name(control, DOORBELL_STATE_CANDIDATES)
    )
    intercom_state_roles = intercom_boolean_state_roles(control) if is_intercom else {}
    access_granted_state_name = first_matching_state_name(
        control, ACCESS_GRANTED_STATE_CANDIDATES
    )
    access_denied_state_name = first_matching_state_name(
        control, ACCESS_DENIED_STATE_CANDIDATES
    )
    lock_state_name = _find_lock_state(control)

    entities: list[BinarySensorEntity] = []
    if doorbell_state_name is not None:
        entities.append(LoxoneDoorbellBinaryEntity(bridge, control, doorbell_state_name))
    if lock_state_name is not None:
        entities.append(LoxoneControlLockBinaryEntity(bridge, control, lock_state_name))
    entities.extend(
        _build_access_binary_entities(
            bridge,
            control,
            access_granted_state_name,
            access_denied_state_name,
        )
    )

    excluded_intercom_states = {
        state_name
        for state_name in (
            doorbell_state_name,
            access_granted_state_name,
            access_denied_state_name,
        )
        if state_name is not None
    }
    entities.extend(
        _build_intercom_state_binary_entities(
            bridge,
            control,
            intercom_state_roles,
            excluded_intercom_states,
        )
    )

    ignored_state_names = {
        state_name
        for state_name in (
            doorbell_state_name,
            access_granted_state_name,
            access_denied_state_name,
            lock_state_name,
            *intercom_state_roles.keys(),
        )
        if state_name is not None
    }
    return entities, ignored_state_names


def _build_diagnostic_binary_entities(
    bridge,
    control: LoxoneControl,
    ignored_state_names: set[str],
) -> list[BinarySensorEntity]:
    return [
        LoxoneDiagnosticBinaryEntity(bridge, control, state_name)
        for state_name in control.states
        if state_name not in ignored_state_names and state_is_boolean(control, state_name)
    ]


def _build_control_binary_entities(bridge, control: LoxoneControl) -> list[BinarySensorEntity]:
    entities, ignored_state_names = _build_special_binary_entities(bridge, control)
    for raw_name, label, device_class in EXTRA_BINARY_STATES.get(control.type, ()):
        state_name = first_matching_state_name(control, (raw_name,))
        if state_name is None:
            continue
        entity = LoxoneSingleStateBinaryEntity(bridge, control, state_name, label, device_class)
        if control.type == "Wallbox2" and state_is_boolean(control, state_name):
            entity._attr_unique_id = control_entity_unique_id(
                bridge.serial, control.uuid_action, state_name
            )
        entities.append(entity)
        ignored_state_names.add(state_name)

    if control.type == "WindowMonitor":
        return [*entities, *_build_window_monitor_entities(bridge, control)]

    if control.type == "SmokeAlarm":
        entities.extend(_build_smoke_cause_entities(bridge, control))

    if normalize_state_name(control.type) == "nfccodetouch":
        state_name = first_matching_state_name(control, ("deviceState",))
        if state_name is not None:
            entities.append(
                LoxoneBitmaskBinaryEntity(
                    bridge, control, state_name, "Online", 0x01, "CONNECTIVITY", inverted=True
                )
            )

    if control.type in BINARY_SENSOR_CONTROL_TYPES or normalize_state_name(control.type).startswith(
        "presencedetector"
    ):
        entities.append(LoxoneBinaryEntity(bridge, control))
        return entities

    if control.type in POWER_SUPPLY_CONTROL_TYPES:
        if control.type == "PowerUnit":
            entities.extend(_build_power_unit_binary_entities(bridge, control))
        charging_state_name = _find_power_supply_charging_state(control)
        if charging_state_name is not None:
            entities.append(
                LoxonePowerSupplyChargingBinaryEntity(bridge, control, charging_state_name)
            )
        return entities

    if control.type in HANDLED_CONTROL_TYPES:
        return entities

    entities.extend(
        _build_diagnostic_binary_entities(
            bridge,
            control,
            ignored_state_names,
        )
    )
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(entry)
    entities: list[BinarySensorEntity] = []
    for control in bridge.controls:
        entities.extend(_build_control_binary_entities(bridge, control))

    async_add_entities(entities)


class LoxoneSingleStateBinaryEntity(LoxoneEntity, BinarySensorEntity):
    """Binary entity bound to one named Loxone state."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str | None = None,
        device_class: str | None = None,
    ) -> None:
        super().__init__(bridge, control, state_name if suffix is None else suffix)
        self._state_name = state_name
        if device_class is not None:
            self._attr_device_class = _binary_device_class(device_class)

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))


class LoxoneBinaryEntity(LoxoneEntity, BinarySensorEntity):
    """Primary binary sensor for one Loxone control."""

    def __init__(self, bridge, control: LoxoneControl) -> None:
        super().__init__(bridge, control)
        candidates = PRIMARY_BINARY_STATES.get(control.type)
        self._state_name = first_matching_state_name(
            control, candidates or PRIMARY_BINARY_STATES["PresenceDetector"]
        )
        if normalize_state_name(control.type).startswith("presencedetector"):
            self._attr_device_class = _binary_device_class("OCCUPANCY")

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name) if self._state_name else None
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        if self._state_name is None:
            return None
        value = self.state_value(self._state_name)
        if normalize_state_name(self._state_name) == "level":
            level = _coerce_int(value)
            return level > 0 if level is not None and level >= 0 else None
        numeric = coerce_float(value)
        return None if numeric is not None and not isfinite(numeric) else coerce_bool(value)


class LoxoneBitmaskBinaryEntity(LoxoneSingleStateBinaryEntity):
    """One documented bit of a Loxone state."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        label: str,
        bit: int,
        device_class: str | None = None,
        *,
        inverted: bool = False,
    ) -> None:
        super().__init__(bridge, control, state_name, label, device_class)
        self._bit = bit
        self._inverted = inverted

    @property
    def is_on(self) -> bool | None:
        value = _coerce_int(self.state_value(self._state_name))
        return (bool(value & self._bit) != self._inverted) if value is not None and value >= 0 else None


class LoxoneWindowMonitorEntity(LoxoneSingleStateBinaryEntity):
    """Contact derived from a WindowMonitor opening's indexed state flags."""

    def __init__(
        self, bridge, control: LoxoneControl, state_name: str, label: str, key: str, index: int
    ) -> None:
        super().__init__(bridge, control, state_name, label, "OPENING")
        self._attr_unique_id = control_entity_unique_id(
            bridge.serial, control.uuid_action, f"opening_{key}"
        )
        self._index = index

    def _flags(self) -> int:
        raw_value = self.state_value(self._state_name)
        values = raw_value.split(",") if isinstance(raw_value, str) else raw_value
        if not isinstance(values, (list, tuple)) or self._index >= len(values):
            return 0
        flags = _coerce_int(values[self._index])
        return flags if flags is not None and flags > 0 else 0

    @property
    def is_on(self) -> bool | None:
        flags = self._flags()
        if flags & (WINDOW_OPEN | WINDOW_TILTED):
            return True
        return False if flags & WINDOW_CLOSED else None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        flags = self._flags()
        if flags & (WINDOW_CLOSED | WINDOW_TILTED | WINDOW_OPEN):
            attrs["tilted"] = bool(flags & WINDOW_TILTED)
        if flags & WINDOW_UNLOCKED:
            attrs["locked"] = False
        elif flags & WINDOW_LOCKED:
            attrs["locked"] = True
        return attrs


class LoxoneDoorbellBinaryEntity(LoxoneSingleStateBinaryEntity):
    """Doorbell binary sensor derived from bell/ring style states."""

    _attr_icon = "mdi:doorbell-video"

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name, "Doorbell")
        if is_intercom_control(control):
            self._set_intercom_entity_name("binary_sensor", "intercom_doorbell")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["doorbell_state"] = self._state_name
        return attrs


INTERCOM_ROLE_ICONS = {
    "Doorbell": "mdi:doorbell-video",
    "Proximity": "mdi:walk",
    "Call": "mdi:phone-in-talk",
    "Light": "mdi:lightbulb",
}


class LoxoneIntercomStateBinaryEntity(LoxoneSingleStateBinaryEntity):
    """Binary sensor for role-specific Intercom states."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        role: str,
    ) -> None:
        super().__init__(bridge, control, state_name, role)
        self._role = role
        self._set_intercom_entity_name("binary_sensor", f"intercom_{role.casefold()}")
        self._attr_icon = INTERCOM_ROLE_ICONS.get(role, "mdi:video-wireless")
        if BinarySensorDeviceClass is not None:
            normalized_role = role.casefold()
            if normalized_role == "proximity":
                self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["intercom_role"] = self._role.casefold()
        attrs["intercom_state"] = self._state_name
        return attrs


class LoxoneAccessBinaryEntity(LoxoneSingleStateBinaryEntity):
    """Binary sensor for access granted/denied pulses."""

    def __init__(
        self,
        bridge,
        control: LoxoneControl,
        state_name: str,
        suffix: str,
        access_result: str,
        icon: str,
    ) -> None:
        super().__init__(bridge, control, state_name, suffix)
        self._access_result = access_result
        self._attr_icon = icon

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["access_state"] = self._state_name
        attrs["access_result"] = self._access_result
        return attrs


class LoxonePowerSupplyChargingBinaryEntity(LoxoneSingleStateBinaryEntity):
    """Charging-state binary sensor for PowerSupply controls."""

    _attr_device_class = (
        BinarySensorDeviceClass.BATTERY_CHARGING
        if BinarySensorDeviceClass is not None
        else "battery_charging"
    )

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name, "Charging")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["charging_state"] = self._state_name
        return attrs


class LoxoneControlLockBinaryEntity(LoxoneSingleStateBinaryEntity):
    """Lock-state binary sensor for controls exposing lock states."""

    _attr_icon = "mdi:lock"

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name, "Locked")
        self._reason_state = (
            first_matching_state_name(control, ("infoText",))
            if normalize_state_name(control.type).startswith("presencedetector")
            else None
        )

    def relevant_state_uuids(self):
        uuids = super().relevant_state_uuids()
        if self._reason_state and (uuid := self.control.state_uuid(self._reason_state)):
            uuids.append(uuid)
        return uuids

    @property
    def is_on(self) -> bool | None:
        payload = self.state_value(self._state_name)
        if isinstance(payload, Mapping):
            locked = _coerce_int(payload.get("locked"))
            if locked is not None:
                return locked > 0
            return coerce_bool(payload.get("value"))
        return coerce_bool(payload)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["lock_state"] = self._state_name
        reason = self.state_value(self._reason_state) if self._reason_state else None
        if isinstance(reason, str) and (reason := reason.strip()):
            attrs["lock_reason"] = reason

        payload = self.state_value(self._state_name)
        if isinstance(payload, Mapping):
            locked = _coerce_int(payload.get("locked"))
            if locked is not None:
                attrs["locked_code"] = locked
                source = LOCK_SOURCE_LABELS.get(locked)
                if source is not None:
                    attrs["lock_source"] = source

            reason = payload.get("reason")
            if isinstance(reason, str):
                cleaned = reason.strip()
                if cleaned:
                    attrs["lock_reason"] = cleaned
            elif reason is not None:
                attrs["lock_reason"] = str(reason)

        return attrs


class LoxoneDiagnosticBinaryEntity(LoxoneSingleStateBinaryEntity):
    """Disabled-by-default raw binary sensor for unsupported control types."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name)
