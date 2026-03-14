"""Binary sensor platform for Loxone."""

from __future__ import annotations

from collections.abc import Mapping

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
LOCK_STATE_CANDIDATES = ("jLocked", "isLocked")
ACCESS_TYPE_HINT_KEYS = tuple(hint.casefold() for hint in ACCESS_TYPE_HINTS)
LOCK_SOURCE_LABELS = {
    1: "visualization",
    2: "logic",
}


def _find_power_supply_charging_state(control: LoxoneControl) -> str | None:
    state_name = first_matching_state_name(control, POWER_SUPPLY_CHARGING_STATE_CANDIDATES)
    if state_name is not None:
        return state_name

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if "charg" in normalized:
            return state_name
    return None


def _find_lock_state(control: LoxoneControl) -> str | None:
    return first_matching_state_name(control, LOCK_STATE_CANDIDATES)


def _coerce_int(value: object) -> int | None:
    numeric = coerce_float(value)
    if numeric is None:
        return None
    return int(numeric)


def _lock_entity_suffix(state_name: str) -> str:
    if normalize_state_name(state_name) == normalize_state_name("jLocked"):
        return "J Locked"
    return "Locked"


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

    if control.type in BINARY_SENSOR_CONTROL_TYPES:
        entities.append(LoxoneBinaryEntity(bridge, control))
        return entities

    if control.type in POWER_SUPPLY_CONTROL_TYPES:
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
    bridge = entry_bridge(hass, entry)
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
    ) -> None:
        super().__init__(bridge, control, state_name if suffix is None else suffix)
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))


class LoxoneBinaryEntity(LoxoneEntity, BinarySensorEntity):
    """Primary binary sensor for one Loxone control."""

    @property
    def is_on(self) -> bool | None:
        for state_name in ("active", "presence", "alarm", "value"):
            value = coerce_bool(self.state_value(state_name))
            if value is not None:
                return value
        for state_uuid in self.control.states.values():
            value = coerce_bool(self.bridge.state_value(state_uuid))
            if value is not None:
                return value
        return None


class LoxoneDoorbellBinaryEntity(LoxoneSingleStateBinaryEntity):
    """Doorbell binary sensor derived from bell/ring style states."""

    _attr_icon = "mdi:doorbell-video"

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name, "Doorbell")

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
        super().__init__(bridge, control, state_name, _lock_entity_suffix(state_name))

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
