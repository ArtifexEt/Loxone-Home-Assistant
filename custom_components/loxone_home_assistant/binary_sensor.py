"""Binary sensor platform for Loxone."""

from __future__ import annotations

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
    DOMAIN,
    HANDLED_CONTROL_TYPES,
    POWER_SUPPLY_CONTROL_TYPES,
)
from .entity import (
    LoxoneEntity,
    coerce_bool,
    first_matching_state_name,
    normalize_state_name,
    state_is_boolean,
)
from .models import LoxoneControl

POWER_SUPPLY_CHARGING_STATE_CANDIDATES = (
    "isCharging",
    "charging",
    "chargeActive",
    "batteryCharging",
)
ACCESS_TYPE_HINT_KEYS = tuple(hint.casefold() for hint in ACCESS_TYPE_HINTS)


def _find_power_supply_charging_state(control: LoxoneControl) -> str | None:
    state_name = first_matching_state_name(control, POWER_SUPPLY_CHARGING_STATE_CANDIDATES)
    if state_name is not None:
        return state_name

    for state_name in control.states:
        normalized = normalize_state_name(state_name)
        if "charg" in normalized:
            return state_name
    return None


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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = hass.data[DOMAIN]["bridges"][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    for control in bridge.controls:
        doorbell_state_name = first_matching_state_name(
            control, DOORBELL_STATE_CANDIDATES
        )
        access_granted_state_name = first_matching_state_name(
            control, ACCESS_GRANTED_STATE_CANDIDATES
        )
        access_denied_state_name = first_matching_state_name(
            control, ACCESS_DENIED_STATE_CANDIDATES
        )
        if doorbell_state_name is not None:
            entities.append(
                LoxoneDoorbellBinaryEntity(bridge, control, doorbell_state_name)
            )
        if _should_expose_access_entities(
            control,
            access_granted_state_name,
            access_denied_state_name,
        ):
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

        ignored_state_names = {
            state_name
            for state_name in (
                doorbell_state_name,
                access_granted_state_name,
                access_denied_state_name,
            )
            if state_name is not None
        }

        if control.type in BINARY_SENSOR_CONTROL_TYPES:
            entities.append(LoxoneBinaryEntity(bridge, control))
            continue

        if control.type in POWER_SUPPLY_CONTROL_TYPES:
            charging_state_name = _find_power_supply_charging_state(control)
            if charging_state_name is not None:
                entities.append(
                    LoxonePowerSupplyChargingBinaryEntity(
                        bridge, control, charging_state_name
                    )
                )
            continue

        if control.type in HANDLED_CONTROL_TYPES:
            continue

        for state_name in control.states:
            if state_name in ignored_state_names:
                continue
            if state_is_boolean(control, state_name):
                entities.append(LoxoneDiagnosticBinaryEntity(bridge, control, state_name))

    async_add_entities(entities)


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


class LoxoneDoorbellBinaryEntity(LoxoneEntity, BinarySensorEntity):
    """Doorbell binary sensor derived from bell/ring style states."""

    _attr_icon = "mdi:doorbell-video"

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, "Doorbell")
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["doorbell_state"] = self._state_name
        return attrs


class LoxoneAccessBinaryEntity(LoxoneEntity, BinarySensorEntity):
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
        super().__init__(bridge, control, suffix)
        self._state_name = state_name
        self._access_result = access_result
        self._attr_icon = icon

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["access_state"] = self._state_name
        attrs["access_result"] = self._access_result
        return attrs


class LoxonePowerSupplyChargingBinaryEntity(LoxoneEntity, BinarySensorEntity):
    """Charging-state binary sensor for PowerSupply controls."""

    _attr_device_class = (
        BinarySensorDeviceClass.BATTERY_CHARGING
        if BinarySensorDeviceClass is not None
        else "battery_charging"
    )

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, "Charging")
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["charging_state"] = self._state_name
        return attrs


class LoxoneDiagnosticBinaryEntity(LoxoneEntity, BinarySensorEntity):
    """Disabled-by-default raw binary sensor for unsupported control types."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, bridge, control: LoxoneControl, state_name: str) -> None:
        super().__init__(bridge, control, state_name)
        self._state_name = state_name

    def relevant_state_uuids(self):
        state_uuid = self.control.state_uuid(self._state_name)
        return [state_uuid] if state_uuid else []

    @property
    def is_on(self) -> bool | None:
        return coerce_bool(self.state_value(self._state_name))
